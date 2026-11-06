"""全局视角的数据集授权总览（只读聚合）。

把「谁进得来 / 能看哪些表 / 行级怎么限制 / 什么时候到期」按数据集汇总成一屏，
数据来源与 ChatBI 的判定链是同一套解析（:mod:`dataset_scope_service`），
避免出现「总览说能进、实际查不了」的两套口径。

本模块只做统计，不做任何写操作；入口权限挂在 ``element:metadata:edit`` 上。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metadata import MetaDataset, MetaTable
from app.models.permission import ResourcePermission, Role
from app.models.user import User
from app.services.dataset_scope_service import (
    MODE_LIST,
    ROW_POLICY_CUSTOM,
    ROW_POLICY_NONE,
    SUBJECT_ALL,
    SUBJECT_ROLE,
    _parse_row_policy,
    _parse_scope,
    _subject_of,
    grant_extended_fields,
)

#: 离到期还有几天算「即将到期」
SOON_WINDOW_DAYS = 7

_SUBJECT_ORDER = {SUBJECT_ALL: 0, SUBJECT_ROLE: 1, "user": 2}

#: 列表筛选口径，和前端 tab 一一对应
VALID_FILTERS = {"all", "unauthorized", "soon", "narrowed", "rowlevel", "all_baseline"}
VALID_SORTS = {"risk", "name", "updated"}


def _risk_rank(dataset: dict[str, Any]) -> int:
    """默认排序：先看没人能访问的，再看快到期的，再看收窄的，最后是普通配置。"""
    if dataset["alive"] == 0:
        return 0
    if dataset["soon"] > 0 or dataset["dead"] > 0:
        return 1
    if dataset["narrowed"] > 0:
        return 2
    return 3


def _match_filter(dataset: dict[str, Any], filter_key: str) -> bool:
    if filter_key == "unauthorized":
        return dataset["alive"] == 0
    if filter_key == "soon":
        return dataset["soon"] > 0 or dataset["dead"] > 0
    if filter_key == "narrowed":
        return dataset["narrowed"] > 0
    if filter_key == "rowlevel":
        return dataset["custom_row"] > 0 or dataset["no_row"] > 0
    if filter_key == "all_baseline":
        return dataset["has_all"]
    return True


def _days_left(expire_at: Any, today: date) -> int | None:
    """距离到期还有几天；无到期日返回 None，已过期返回负数。"""
    if not expire_at:
        return None
    if isinstance(expire_at, datetime):
        expire_date = expire_at.date()
    elif isinstance(expire_at, date):
        expire_date = expire_at
    else:
        try:
            expire_date = datetime.fromisoformat(str(expire_at)).date()
        except (TypeError, ValueError):
            return None
    return (expire_date - today).days


def _format_expire(expire_at: Any) -> str | None:
    if not expire_at:
        return None
    if isinstance(expire_at, (datetime, date)):
        return expire_at.strftime("%Y-%m-%d")
    return str(expire_at)[:10]


async def _subject_names(
    conn: AsyncSession, user_ids: set[int], role_ids: set[int]
) -> tuple[dict[int, str], dict[int, str]]:
    """批量取主体名称。总览要能直接看懂「授给了谁」，光有 id 没用。"""
    users: dict[int, str] = {}
    roles: dict[int, str] = {}
    if user_ids:
        rows = (
            await conn.execute(
                select(User.id, User.user_name, User.real_name).where(User.id.in_(user_ids))
            )
        ).all()
        for uid, user_name, real_name in rows:
            display = (real_name or "").strip() or (user_name or "").strip() or f"用户 {uid}"
            if real_name and user_name and real_name != user_name:
                display = f"{real_name}（{user_name}）"
            users[int(uid)] = display
    if role_ids:
        rows = (
            await conn.execute(select(Role.id, Role.name).where(Role.id.in_(role_ids)))
        ).all()
        for rid, name in rows:
            roles[int(rid)] = (name or "").strip() or f"角色 {rid}"
    return users, roles


def _empty_bucket() -> dict[str, Any]:
    return {
        "total": 0,
        "alive": 0,
        "dead": 0,
        "has_all": False,
        "roles": 0,
        "users": 0,
        "narrowed": 0,
        "custom_row": 0,
        "no_row": 0,
        "soon": 0,
        "soon_min_days": None,
    }


def _tick_bucket(bucket: dict[str, Any], subject: str, scope_mode: str,
                 row_policy: str, days_left: int | None) -> None:
    bucket["total"] += 1
    if days_left is not None and days_left < 0:
        bucket["dead"] += 1
        return
    bucket["alive"] += 1
    if subject == SUBJECT_ALL:
        bucket["has_all"] = True
    elif subject == SUBJECT_ROLE:
        bucket["roles"] += 1
    else:
        bucket["users"] += 1
    if scope_mode == MODE_LIST:
        bucket["narrowed"] += 1
    if row_policy == ROW_POLICY_CUSTOM:
        bucket["custom_row"] += 1
    elif row_policy == ROW_POLICY_NONE:
        bucket["no_row"] += 1
    if days_left is not None and days_left <= SOON_WINDOW_DAYS:
        bucket["soon"] += 1
        current = bucket["soon_min_days"]
        bucket["soon_min_days"] = days_left if current is None else min(current, days_left)


async def build_dataset_permission_overview(
    conn: AsyncSession,
    *,
    keyword: str = "",
    filter_key: str = "all",
    page: int = 1,
    page_size: int = 20,
    sort: str = "risk",
) -> dict[str, Any]:
    """返回数据集授权总览：全局 summary + 过滤排序后的分页列表。

    数据集数量可能上千（含测试库），所以列表分页、summary 始终按全量算——
    管理员打开先看整体风险，再翻具体数据集。
    """
    today = datetime.now().date()
    if filter_key not in VALID_FILTERS:
        filter_key = "all"
    if sort not in VALID_SORTS:
        sort = "risk"

    dataset_rows = (
        await conn.execute(
            select(
                MetaDataset.id,
                MetaDataset.name,
                MetaDataset.display_name,
                MetaDataset.data_source,
                MetaDataset.updated_at,
            ).order_by(MetaDataset.id)
        )
    ).all()

    table_counts = dict(
        (
            await conn.execute(
                select(MetaTable.dataset_id, func.count()).group_by(MetaTable.dataset_id)
            )
        ).all()
    )

    # 一次把全量 metadata 授权捞出来，避免按数据集逐个查（N+1）
    grant_rows = (
        await conn.execute(
            select(
                ResourcePermission.id,
                ResourcePermission.resource_id,
                ResourcePermission.user_id,
                ResourcePermission.role_id,
            ).where(
                ResourcePermission.resource_type == "metadata",
                ResourcePermission.enabled == True,  # noqa: E712 - 与判定链口径一致
            )
        )
    ).all()

    extended = await grant_extended_fields(conn, [int(row[0]) for row in grant_rows])

    parsed: list[dict[str, Any]] = []
    user_ids: set[int] = set()
    role_ids: set[int] = set()
    for permission_id, resource_id, row_user_id, row_role_id in grant_rows:
        if resource_id is None:
            continue
        info = extended.get(int(permission_id), {})
        subject = _subject_of(row_user_id, row_role_id, info.get("subject_type"))
        if subject is None:
            continue
        scope_mode, table_ids, _col_rules = _parse_scope(info.get("scope"))
        expire_at = info.get("expire_at")
        parsed.append(
            {
                "dataset_id": int(resource_id),
                "subject": subject,
                "user_id": int(row_user_id) if row_user_id is not None else None,
                "role_id": int(row_role_id) if row_role_id is not None else None,
                "scope_mode": scope_mode,
                "table_count": len(table_ids),
                "row_policy": _parse_row_policy(info.get("row_policy")),
                "row_condition": info.get("row_condition"),
                "expire_at": expire_at,
                "days_left": _days_left(expire_at, today),
            }
        )
        if parsed[-1]["user_id"] is not None:
            user_ids.add(parsed[-1]["user_id"])
        if parsed[-1]["role_id"] is not None:
            role_ids.add(parsed[-1]["role_id"])

    user_names, role_names = await _subject_names(conn, user_ids, role_ids)

    buckets: dict[int, dict[str, Any]] = {}
    details: dict[int, list[dict[str, Any]]] = {}
    for item in parsed:
        bucket = buckets.setdefault(item["dataset_id"], _empty_bucket())
        _tick_bucket(bucket, item["subject"], item["scope_mode"], item["row_policy"], item["days_left"])
        if item["subject"] == SUBJECT_ALL:
            who = "所有成员（全员基线）"
        elif item["subject"] == SUBJECT_ROLE:
            who = role_names.get(item["role_id"] or 0, f"角色 {item['role_id']}")
        else:
            who = user_names.get(item["user_id"] or 0, f"用户 {item['user_id']}")
        details.setdefault(item["dataset_id"], []).append(
            {
                "subject": item["subject"],
                "subject_id": item["role_id"] if item["subject"] == SUBJECT_ROLE else item["user_id"],
                "who": who,
                "scope": item["scope_mode"],
                "table_count": item["table_count"],
                "row_policy": item["row_policy"],
                "row_condition": item["row_condition"],
                "expire_at": _format_expire(item["expire_at"]),
                "days_left": item["days_left"],
                "expired": bool(item["days_left"] is not None and item["days_left"] < 0),
            }
        )

    datasets: list[dict[str, Any]] = []
    for ds_id, name, display_name, data_source, updated_at in dataset_rows:
        bucket = buckets.get(int(ds_id), _empty_bucket())
        grants = details.get(int(ds_id), [])
        grants.sort(
            key=lambda g: (
                1 if g["expired"] else 0,
                _SUBJECT_ORDER.get(g["subject"], 9),
                g["who"],
            )
        )
        datasets.append(
            {
                "id": int(ds_id),
                "name": name,
                "display_name": display_name or name,
                "data_source": data_source,
                "table_count": int(table_counts.get(int(ds_id), 0)),
                "updated_at": updated_at.strftime("%Y-%m-%d %H:%M") if isinstance(updated_at, datetime) else None,
                "_updated_raw": updated_at,
                **bucket,
                "grants": grants,
            }
        )

    # summary 永远按全量算：筛选是看局部，风险得看整体
    unauthorized = sum(1 for d in datasets if d["total"] == 0)
    stale = sum(1 for d in datasets if d["total"] > 0 and d["alive"] == 0)
    summary = {
        "datasets": len(datasets),
        "granted": sum(1 for d in datasets if d["alive"] > 0),
        "unauthorized": unauthorized,
        "stale": stale,
        "nobody": unauthorized + stale,
        "soon": sum(1 for d in datasets if d["soon"] > 0),
        "narrowed": sum(1 for d in datasets if d["narrowed"] > 0),
        "rowlevel": sum(1 for d in datasets if d["custom_row"] > 0 or d["no_row"] > 0),
        "all_baseline": sum(1 for d in datasets if d["has_all"]),
    }

    keyword_norm = keyword.strip().lower()
    filtered = []
    for ds in datasets:
        if not _match_filter(ds, filter_key):
            continue
        if keyword_norm:
            haystack = " ".join(
                [ds["display_name"] or "", ds["name"] or "", ds["data_source"] or ""]
                + [g["who"] for g in ds["grants"]]
            ).lower()
            if keyword_norm not in haystack:
                continue
        filtered.append(ds)

    if sort == "name":
        filtered.sort(key=lambda d: (d["display_name"] or "").lower())
    elif sort == "updated":
        filtered.sort(key=lambda d: d["_updated_raw"] or datetime.min, reverse=True)
    else:
        filtered.sort(key=lambda d: (_risk_rank(d), d["id"]))

    page_size = max(1, min(int(page_size or 20), 200))
    page = max(1, int(page or 1))
    start = (page - 1) * page_size
    page_items = filtered[start : start + page_size]
    for item in page_items:
        item.pop("_updated_raw", None)

    return {
        "today": today.isoformat(),
        "summary": summary,
        "total": len(filtered),
        "page": page,
        "page_size": page_size,
        "datasets": page_items,
    }
