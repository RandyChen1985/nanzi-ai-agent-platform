#!/usr/bin/env python3
"""清理元数据同步测试遗留的数据集（Sync Test / Fail Test / Test Sync）。

这些记录来自 ``tests/services/test_metadata_rag_sync.py`` 与
``tests/services/test_metadata_fixes.py``：测试直接使用生产同款
``AsyncSessionLocal`` 并 ``commit``，于是每跑一次 pytest，就会在开发库里留下
``sync_test_xxxxxx``、``fail_test_xxxxxx``、``test_sync_ds_xxxxxx`` 等数据集。
它们会出现在数据门户里，也会被菜单、检索和巡检当成真实资产。

本脚本只做**数据清理**，不改表结构，也不会调用 RAGFlow 删除远端知识库：

* 测试里的 ``RagFlowClient`` 是 mock 的，``rag_dataset_id`` 只有空值或
  ``rag_kb_123`` 这种假 ID，远端本来就没有对应知识库；
* 如果未来出现真实 ID，脚本会打印出来提醒人工确认，但绝不擅自删远端数据。

安全约定
--------
* 默认 **dry-run**，只统计并打印样例；显式加 ``--apply`` 才写库；
* 名称匹配严格限定为「测试前缀 + 6 位 uuid hex」，不会因为名字里带 ``test``
  就误删 ``test_ds``、``test_ds22``、``znt2_test_ds`` 这类人工数据集；
* ``--apply`` 前会把待删清单写入 ``data/cleanup-backups/`` 的 JSON 清单；
* 同时清理这些数据集关联的表、字段、指标、关系、漂移告警、变更日志、
  资源权限与审计日志；
* 清理后失效数据门户的 Redis 缓存/向量，避免界面继续展示已删除的数据集。

用法::

    # 预览（默认，安全）
    python scripts/cleanup_sync_test_metadata.py

    # 真正清理
    python scripts/cleanup_sync_test_metadata.py --apply

    # 连同类测试残留的 secret_ds_xxxxxx 一起清理（默认不碰）
    python scripts/cleanup_sync_test_metadata.py --include-secret-test --apply

    # 保留变更日志（默认会删除与测试数据集相关的变更日志）
    python scripts/cleanup_sync_test_metadata.py --keep-changelog --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
# app.core.config 的 env_file 是相对路径，必须在导入 app 之前切到仓库根目录。
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, inspect, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.orm import AsyncSessionLocal, engine  # noqa: E402
from app.core.redis import close_redis, get_redis  # noqa: E402
from app.models.changelog import MetaChangelog  # noqa: E402
from app.models.metadata import (  # noqa: E402
    MetaColumn,
    MetaDataset,
    MetaMetric,
    MetaRelationship,
    MetaSchemaDriftAlert,
    MetaTable,
    MetadataAuditLog,
)
from app.models.permission import ResourcePermission  # noqa: E402
# Role.users 用字符串引用 "User"，导入 User 才能完成 mapper 配置。
from app.models.user import User  # noqa: E402,F401

# 测试代码里的命名：f"sync_test_{uuid.uuid4().hex[:6]}" 等。
DEFAULT_PREFIXES: tuple[str, ...] = (
    "sync_test_",
    "fail_test_",
    "del_test_",
    "test_sync_ds_",
)
SECRET_TEST_PREFIXES: tuple[str, ...] = ("secret_ds_",)

BACKUP_DIR = ROOT / "data" / "cleanup-backups"


@dataclass(frozen=True)
class DatasetRow:
    id: int
    name: str
    display_name: str | None
    created_at: datetime | None
    rag_dataset_id: str | None

    @property
    def prefix(self) -> str:
        for prefix in DEFAULT_PREFIXES + SECRET_TEST_PREFIXES:
            if self.name.startswith(prefix):
                return prefix
        return "other"


@dataclass
class RelatedRows:
    table_ids: list[int]
    column_ids: list[int]
    metric_ids: list[int]
    relationship_ids: list[int]
    drift_alert_ids: list[int]
    permission_ids: list[int]
    audit_log_ids: list[int]
    changelog_ids: list[int]

    def summary(self) -> dict[str, int]:
        return {
            "meta_tables": len(self.table_ids),
            "meta_columns": len(self.column_ids),
            "meta_metrics": len(self.metric_ids),
            "meta_relationships": len(self.relationship_ids),
            "meta_schema_drift_alerts": len(self.drift_alert_ids),
            "resource_permissions": len(self.permission_ids),
            "metadata_audit_logs": len(self.audit_log_ids),
            "meta_changelog": len(self.changelog_ids),
        }


def build_name_regex(prefixes: Sequence[str]) -> re.Pattern[str]:
    # 只认测试代码生成的「前缀 + 6 位小写 hex」；不做模糊匹配，防止误伤人工数据。
    return re.compile(r"^(?:" + "|".join(re.escape(p) for p in prefixes) + r")[0-9a-f]{6}$")


def _chunks(values: Sequence[int], size: int = 500) -> Iterable[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


async def _table_exists(db: AsyncSession, table_name: str) -> bool:
    """不同部署版本的表可能缺失；缺表时跳过对应清理，而不是整脚本失败。"""

    def _has_table(sync_session) -> bool:
        return inspect(sync_session.connection()).has_table(table_name)

    return bool(await db.run_sync(_has_table))


async def _load_targets(db: AsyncSession, regex: re.Pattern[str]) -> list[DatasetRow]:
    result = await db.execute(
        select(
            MetaDataset.id,
            MetaDataset.name,
            MetaDataset.display_name,
            MetaDataset.created_at,
            MetaDataset.rag_dataset_id,
        )
    )
    targets = [
        DatasetRow(
            id=row.id,
            name=row.name,
            display_name=row.display_name,
            created_at=row.created_at,
            rag_dataset_id=row.rag_dataset_id,
        )
        for row in result
        if regex.match(row.name or "")
    ]
    targets.sort(key=lambda item: (item.created_at or datetime.min, item.id))
    return targets


async def _load_related(
    db: AsyncSession,
    dataset_ids: Sequence[int],
    *,
    include_changelog: bool,
) -> RelatedRows:
    if not dataset_ids:
        return RelatedRows([], [], [], [], [], [], [], [])

    dataset_id_set = {str(value) for value in dataset_ids}

    table_rows = (
        await db.execute(
            select(MetaTable.id, MetaTable.dataset_id, MetaTable.physical_name).where(
                MetaTable.dataset_id.in_(dataset_ids)
            )
        )
    ).all()
    table_ids = [row.id for row in table_rows]
    table_id_set = {str(value) for value in table_ids}

    column_ids: list[int] = []
    metric_ids: list[int] = []
    relationship_ids: list[int] = []
    drift_alert_ids: list[int] = []
    if table_ids:
        column_ids = list(
            (
                await db.execute(
                    select(MetaColumn.id).where(MetaColumn.table_id.in_(table_ids))
                )
            ).scalars()
        )
        relationship_ids = list(
            (
                await db.execute(
                    select(MetaRelationship.id).where(
                        (MetaRelationship.source_table_id.in_(table_ids))
                        | (MetaRelationship.target_table_id.in_(table_ids))
                    )
                )
            ).scalars()
        )
    metric_ids = list(
        (
            await db.execute(
                select(MetaMetric.id).where(MetaMetric.dataset_id.in_(dataset_ids))
            )
        ).scalars()
    )
    drift_alert_ids = list(
        (
            await db.execute(
                select(MetaSchemaDriftAlert.id).where(
                    MetaSchemaDriftAlert.dataset_id.in_(dataset_ids)
                )
            )
        ).scalars()
    )

    permission_ids: list[int] = []
    if await _table_exists(db, ResourcePermission.__tablename__):
        permission_rows = (
            await db.execute(
                select(ResourcePermission.id, ResourcePermission.resource_id).where(
                    ResourcePermission.resource_type == "metadata"
                )
            )
        ).all()
        permission_ids = [
            row.id for row in permission_rows if str(row.resource_id) in dataset_id_set
        ]

    audit_log_ids: list[int] = []
    if await _table_exists(db, MetadataAuditLog.__tablename__):
        audit_log_ids = list(
            (
                await db.execute(
                    select(MetadataAuditLog.id).where(
                        MetadataAuditLog.metadata_dataset_id.in_(dataset_ids)
                    )
                )
            ).scalars()
        )

    changelog_ids: list[int] = []
    if include_changelog and await _table_exists(db, MetaChangelog.__tablename__):
        # 变更日志的 resource_id 口径不统一：dataset 用纯 id，table 可能是
        # "表 id" 或 "数据集 id:物理表名"，这里逐条在 Python 侧判断，避免
        # 依赖不同数据库的字符串/排序规则差异。
        column_id_set = {str(value) for value in column_ids}
        metric_id_set = {str(value) for value in metric_ids}
        relationship_id_set = {str(value) for value in relationship_ids}
        changelog_rows = (
            await db.execute(
                select(
                    MetaChangelog.id,
                    MetaChangelog.resource_type,
                    MetaChangelog.resource_id,
                )
            )
        ).all()
        for row in changelog_rows:
            resource_id = str(row.resource_id)
            resource_type = (row.resource_type or "").lower()
            if resource_type == "dataset" and resource_id in dataset_id_set:
                changelog_ids.append(row.id)
            elif resource_type == "table" and (
                resource_id in table_id_set
                or resource_id.split(":", 1)[0] in dataset_id_set
            ):
                changelog_ids.append(row.id)
            elif resource_type == "column" and resource_id in column_id_set:
                changelog_ids.append(row.id)
            elif resource_type == "metric" and resource_id in metric_id_set:
                changelog_ids.append(row.id)
            elif resource_type == "relationship" and resource_id in relationship_id_set:
                changelog_ids.append(row.id)

    return RelatedRows(
        table_ids=table_ids,
        column_ids=column_ids,
        metric_ids=metric_ids,
        relationship_ids=relationship_ids,
        drift_alert_ids=drift_alert_ids,
        permission_ids=permission_ids,
        audit_log_ids=audit_log_ids,
        changelog_ids=changelog_ids,
    )


def _write_backup(
    datasets: Sequence[DatasetRow],
    related: RelatedRows,
    *,
    prefixes: Sequence[str],
) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"sync-test-metadata-{stamp}.json"
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "database": {
            "type": settings.normalized_database_type,
            "host": settings.MYSQL_HOST
            if settings.normalized_database_type == "mysql"
            else settings.POSTGRES_HOST,
            "database": settings.MYSQL_DB
            if settings.normalized_database_type == "mysql"
            else settings.POSTGRES_DB,
        },
        "selection": {
            "prefixes": list(prefixes),
            "count": len(datasets),
        },
        "datasets": [
            {
                "id": item.id,
                "name": item.name,
                "display_name": item.display_name,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "rag_dataset_id": item.rag_dataset_id,
            }
            for item in datasets
        ],
        "related_counts": related.summary(),
        "related_ids": {
            "meta_tables": related.table_ids,
            "meta_columns": related.column_ids,
            "meta_metrics": related.metric_ids,
            "meta_relationships": related.relationship_ids,
            "meta_schema_drift_alerts": related.drift_alert_ids,
            "resource_permissions": related.permission_ids,
            "metadata_audit_logs": related.audit_log_ids,
            "meta_changelog": related.changelog_ids,
        },
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


async def _delete_related(db: AsyncSession, related: RelatedRows) -> None:
    for ids, model in (
        (related.changelog_ids, MetaChangelog),
        (related.permission_ids, ResourcePermission),
        (related.audit_log_ids, MetadataAuditLog),
        (related.relationship_ids, MetaRelationship),
        (related.drift_alert_ids, MetaSchemaDriftAlert),
        (related.column_ids, MetaColumn),
        (related.metric_ids, MetaMetric),
    ):
        for batch in _chunks(ids):
            await db.execute(
                delete(model)
                .where(model.id.in_(list(batch)))
                .execution_options(synchronize_session=False)
            )


async def _delete_targets(db: AsyncSession, related: RelatedRows, dataset_ids: Sequence[int]) -> None:
    for batch in _chunks(related.table_ids):
        await db.execute(
            delete(MetaTable)
            .where(MetaTable.id.in_(list(batch)))
            .execution_options(synchronize_session=False)
        )
    for batch in _chunks(list(dataset_ids)):
        await db.execute(
            delete(MetaDataset)
            .where(MetaDataset.id.in_(list(batch)))
            .execution_options(synchronize_session=False)
        )


async def _clear_redis(target_ids: Sequence[int]) -> int:
    """清理数据门户菜单/导航缓存，以及这些数据集的本地向量 key。"""
    redis = await get_redis()
    if not redis:
        print("Redis: 未启用或连接失败，跳过缓存清理")
        return 0

    target_id_set = {str(value) for value in target_ids}
    prefixes = (
        "agent:dataset_menu:*",
        "agent:dataset_navigation:*",
        "agent:dataset_navigation_click_rank:*",
        "agent:dataset_navigation_click_meta:*",
        "agent:dataset_navigation_recent_questions:*",
    )
    deleted = 0
    pending: list[str] = []

    async def flush() -> None:
        nonlocal deleted
        if pending:
            deleted += int(await redis.delete(*pending))
            pending.clear()

    try:
        for pattern in prefixes:
            async for key in redis.scan_iter(match=pattern, count=500):
                pending.append(key)
                if len(pending) >= 500:
                    await flush()
        async for key in redis.scan_iter(match="metadata:dataset:*", count=500):
            # key 形如 metadata:dataset:<dataset_id>:table:<name>
            parts = str(key).split(":")
            if len(parts) >= 3 and parts[2] in target_id_set:
                pending.append(key)
                if len(pending) >= 500:
                    await flush()
        await flush()
    finally:
        await close_redis()
    return deleted


def _print_report(
    datasets: Sequence[DatasetRow],
    related: RelatedRows,
    *,
    prefix_counts: dict[str, int],
    apply: bool,
    backup_path: Path | None,
) -> None:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== 清理同步测试元数据数据集（{mode}）===")
    print(
        "数据库: "
        f"{settings.normalized_database_type} / "
        f"{settings.MYSQL_HOST if settings.normalized_database_type == 'mysql' else settings.POSTGRES_HOST}"
        f" / {settings.MYSQL_DB if settings.normalized_database_type == 'mysql' else settings.POSTGRES_DB}"
    )
    print(f"匹配数据集: {len(datasets)}")
    for prefix, count in prefix_counts.items():
        if count:
            print(f"  - {prefix:<14} {count}")
    if datasets:
        oldest = datasets[0]
        newest = datasets[-1]
        print(
            "时间范围: "
            f"{(oldest.created_at or '未知')} ~ {(newest.created_at or '未知')}"
        )
        print("样例（最多 10 条）:")
        for item in datasets[:10]:
            print(
                f"  id={item.id:<12} name={item.name:<24} "
                f"display={item.display_name or ''}"
            )
        if len(datasets) > 10:
            print(f"  ... 其余 {len(datasets) - 10} 条见备份清单")
    print("关联记录:")
    for name, count in related.summary().items():
        print(f"  - {name:<28} {count}")

    rag_ids = sorted({item.rag_dataset_id for item in datasets if item.rag_dataset_id})
    if rag_ids:
        print(f"RAGFlow ID: {', '.join(rag_ids)}")
        print("  注意：测试里 RagFlowClient 是 mock，默认不删除远端知识库；")
        print("  如有真实 ID，请人工确认后到 RAGFlow 控制台处理。")

    if backup_path:
        print(f"备份清单: {backup_path}")
    elif datasets:
        print(f"备份清单: --apply 时写入 {BACKUP_DIR}")


async def _run(args: argparse.Namespace) -> int:
    prefixes = list(DEFAULT_PREFIXES)
    if args.include_secret_test:
        prefixes.extend(SECRET_TEST_PREFIXES)
    regex = build_name_regex(prefixes)

    async with AsyncSessionLocal() as db:
        datasets = await _load_targets(db, regex)
        prefix_counts = {
            prefix: sum(1 for item in datasets if item.name.startswith(prefix))
            for prefix in prefixes
        }

        if not datasets:
            print("没有匹配到同步测试遗留数据集，无需清理。")
            return 0

        related = await _load_related(
            db,
            [item.id for item in datasets],
            include_changelog=not args.keep_changelog,
        )

        backup_path: Path | None = None
        if args.apply:
            backup_path = _write_backup(datasets, related, prefixes=prefixes)
            await _delete_related(db, related)
            await _delete_targets(db, related, [item.id for item in datasets])
            await db.commit()

        _print_report(
            datasets,
            related,
            prefix_counts=prefix_counts,
            apply=args.apply,
            backup_path=backup_path,
        )

        if args.apply:
            # 提交成功后再清缓存，避免回滚时把缓存白清一遍。
            deleted_keys = 0
            if not args.skip_redis:
                deleted_keys = await _clear_redis([item.id for item in datasets])
            print(f"已删除数据集: {len(datasets)}")
            print(f"已清理 Redis 缓存/向量 key: {deleted_keys}")
            print("完成。测试再跑时仍可能重新生成同类数据，建议定期执行本脚本。")
        else:
            print("这是 dry-run，没有修改任何数据；确认无误后加 --apply 执行删除。")

    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="清理元数据同步测试遗留的 Sync Test / Fail Test / Test Sync 数据集。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python scripts/cleanup_sync_test_metadata.py\n"
            "  python scripts/cleanup_sync_test_metadata.py --apply\n"
            "  python scripts/cleanup_sync_test_metadata.py --include-secret-test --apply\n"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正删除；不加时只做 dry-run 预览",
    )
    parser.add_argument(
        "--include-secret-test",
        action="store_true",
        help="连同类测试残留 secret_ds_xxxxxx 一起清理（默认不碰）",
    )
    parser.add_argument(
        "--keep-changelog",
        action="store_true",
        help="保留 meta_changelog 中与测试数据集相关的变更日志",
    )
    parser.add_argument(
        "--skip-redis",
        action="store_true",
        help="不清理 Redis 菜单/导航缓存与本地向量 key",
    )
    return parser.parse_args(argv)


async def main() -> int:
    args = parse_args()
    try:
        return await _run(args)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n已取消，未提交任何删除。", file=sys.stderr)
        raise SystemExit(130)
