"""个人技能点对点分享：快照固化、接收落地与状态流转。

设计要点（与 ``skill_publication_service`` 的发布链路刻意解耦）：

- 分享时立刻把源技能复制成**不可变快照**，等待期间源技能被改、被删都不影响接收。
- 接受时把快照整体复制到接收人个人技能目录：先写 staged 临时目录、校验指纹，
  再原子改名，避免出现「半份技能」。
- 目标 ID 已被占用时自动追加 ``-2``、``-3`` 后缀，不打断用户的接受动作。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.department import Department
from app.models.skill_share import SkillShare
from app.models.user import User
from app.services.ai.skill_resolver import (
    get_user_personal_skills_dir,
    resolve_personal_skill_dir,
)
from app.services.portal_notification_service import PortalNotificationService
from app.services.skill_publication_service import (
    SnapshotStats,
    copy_tree_contents,
    validate_snapshot_tree,
)
from app.services.skill_rename_service import SkillRenameError, validate_skill_id
from app.utils.skill_metadata import parse_skill_frontmatter

MAX_SHARE_TARGETS = 50
MAX_MESSAGE_LENGTH = 200
MAX_TARGET_ID_ATTEMPTS = 50
# 列表分页：单次最多返回多少条（「收到的分享 → 全部」没有分页 UI，靠这个上限兜底）
DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500
SHARE_STATUSES = ("PENDING", "ACCEPTED", "REJECTED", "WITHDRAWN")
_SHARE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ShareSnapshot:
    snapshot_path: str
    content_sha256: str
    file_count: int
    total_size: int


@dataclass(frozen=True)
class MaterializedShare:
    skill_id: str
    materialized_path: str
    content_sha256: str
    file_count: int
    total_size: int


class ShareNotFoundError(LookupError):
    """分享记录不存在，或不属于当前用户（不区分，避免枚举他人记录）。"""


class ShareConflictError(RuntimeError):
    """分享记录当前状态不允许该操作（重复接受、重复撤回等）。"""


class SharePermissionError(PermissionError):
    """记录存在，但当前用户既不是分享人也不是接收人。"""


# ---------------------------------------------------------------------------
# 快照目录与文件操作
# ---------------------------------------------------------------------------

def share_snapshot_root() -> str:
    """分享快照根目录：与平台技能目录同级，和发布快照目录并列。"""

    root = os.path.join(
        os.path.dirname(os.path.abspath(settings.SKILLS_DIR)), "skill_share_snapshots"
    )
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


def _validate_share_id(share_id: str) -> None:
    if not isinstance(share_id, str) or not _SHARE_ID_PATTERN.fullmatch(share_id):
        raise ValueError("invalid share id")


def _assert_inside(root: str, path: str) -> None:
    if os.path.commonpath([os.path.abspath(root), os.path.abspath(path)]) != os.path.abspath(root):
        raise ValueError("share path escapes root")


def create_share_snapshot(*, source_path: str, share_id: str) -> ShareSnapshot:
    """把个人技能目录复制成不可变快照；同一 share_id 重复调用会原子替换旧快照。

    「刷新已有待接受分享」也走这里：先写到临时目录，校验指纹一致后再替换旧目录，
    这样刷新失败时旧快照仍在，接收人不会收到一份坏内容。
    """

    _validate_share_id(share_id)
    source = Path(os.path.abspath(source_path))
    if not source.is_dir():
        raise FileNotFoundError("personal skill does not exist")
    stats = validate_snapshot_tree(str(source))

    root = Path(share_snapshot_root())
    destination = root / share_id
    temporary = Path(tempfile.mkdtemp(prefix=f".{share_id}-", dir=str(root)))
    previous: Path | None = None
    try:
        copy_tree_contents(source, temporary)
        copied = validate_snapshot_tree(str(temporary))
        if copied != stats:
            raise ValueError("skill changed while sharing")
        if destination.exists():
            previous = root / f".{share_id}.old-{uuid.uuid4().hex}"
            os.replace(str(destination), str(previous))
        os.replace(str(temporary), str(destination))
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        if previous is not None and previous.exists() and not destination.exists():
            os.replace(str(previous), str(destination))
        raise
    if previous is not None:
        shutil.rmtree(previous, ignore_errors=True)

    return ShareSnapshot(
        snapshot_path=str(destination),
        content_sha256=stats.content_sha256,
        file_count=stats.file_count,
        total_size=stats.total_size,
    )


def remove_share_snapshot(snapshot_path: str | None) -> None:
    """回收快照目录；路径越界或已不存在时静默跳过（清理是尽力而为）。"""

    if not snapshot_path:
        return
    try:
        _assert_inside(share_snapshot_root(), snapshot_path)
    except ValueError:
        return
    if os.path.isdir(snapshot_path):
        shutil.rmtree(snapshot_path, ignore_errors=True)


def _prepare_skill_id(value: str) -> str:
    try:
        return validate_skill_id(value)
    except SkillRenameError as exc:
        raise ValueError(str(exc)) from exc


def resolve_target_skill_id(skills_root: str, preferred_skill_id: str) -> str:
    """选出接收人技能库里未被占用的 ID：原名 → ``-2`` → ``-3`` …"""

    base = _prepare_skill_id(preferred_skill_id)
    candidates = [base] + [f"{base}-{index}" for index in range(2, MAX_TARGET_ID_ATTEMPTS + 1)]
    for candidate in candidates:
        if not os.path.exists(os.path.join(skills_root, candidate)):
            return candidate
    raise ValueError("target skill id is exhausted")


def materialize_share_snapshot(
    *,
    snapshot_path: str,
    skills_root: str,
    preferred_skill_id: str,
) -> MaterializedShare:
    """把快照复制为接收人的个人技能目录（staged 写入 + 原子改名）。"""

    if not os.path.isdir(snapshot_path):
        raise FileNotFoundError("share snapshot does not exist")
    stats: SnapshotStats = validate_snapshot_tree(snapshot_path)
    root = os.path.abspath(skills_root)
    os.makedirs(root, exist_ok=True)

    last_error: Exception | None = None
    for _ in range(5):
        skill_id = resolve_target_skill_id(root, preferred_skill_id)
        staged = Path(tempfile.mkdtemp(prefix=f".{skill_id}-", dir=root))
        try:
            copy_tree_contents(Path(snapshot_path), staged)
            copied = validate_snapshot_tree(str(staged))
            if copied.content_sha256 != stats.content_sha256:
                raise ValueError("materialized skill changed while copying")
            target = Path(root) / skill_id
            if target.exists():
                # 并发下别人刚占了同一个 ID：换个后缀重试，而不是覆盖
                last_error = FileExistsError(skill_id)
                shutil.rmtree(staged, ignore_errors=True)
                continue
            os.replace(str(staged), str(target))
            return MaterializedShare(
                skill_id=skill_id,
                materialized_path=str(target),
                content_sha256=stats.content_sha256,
                file_count=stats.file_count,
                total_size=stats.total_size,
            )
        except Exception:
            shutil.rmtree(staged, ignore_errors=True)
            raise
    raise last_error or ValueError("failed to materialize shared skill")


# ---------------------------------------------------------------------------
# 记录查询与序列化
# ---------------------------------------------------------------------------

def _user_id(user: dict[str, Any]) -> int:
    raw = (user or {}).get("user_id") or (user or {}).get("id")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("用户身份无效") from exc


def _display_name(user: Any) -> str:
    if isinstance(user, dict):
        return str(user.get("real_name") or user.get("user_name") or user.get("user_id") or "用户")
    return str(getattr(user, "real_name", None) or getattr(user, "user_name", None) or "用户")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _share_payload(row: SkillShare, target: User | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_skill_id": row.source_skill_id,
        "name": row.name,
        "description": row.description or "",
        "message": row.message,
        "status": row.status,
        "target_user_id": row.target_user_id,
        "target_user_name": getattr(target, "user_name", None),
        "target_real_name": getattr(target, "real_name", None),
        "created_at": _iso(row.created_at),
        "responded_at": _iso(row.responded_at),
        "accepted_skill_id": row.accepted_skill_id,
    }


def _received_payload(row: SkillShare, source: User | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_skill_id": row.source_skill_id,
        "source_user_id": row.source_user_id,
        "source_user_name": getattr(source, "user_name", None),
        "source_real_name": getattr(source, "real_name", None),
        "name": row.name,
        "description": row.description or "",
        "message": row.message,
        "status": row.status,
        "file_count": row.file_count,
        "total_size": row.total_size,
        "created_at": _iso(row.created_at),
        "responded_at": _iso(row.responded_at),
        "accepted_skill_id": row.accepted_skill_id,
    }


async def _get_share(session: AsyncSession, share_id: str) -> SkillShare:
    result = await session.execute(
        select(SkillShare).where(SkillShare.id == share_id).with_for_update()
    )
    row = result.scalars().first()
    if row is None:
        raise ShareNotFoundError("share not found")
    return row


async def _load_user(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalars().first()


def _source_skill_path(user: dict[str, Any], skill_id: str) -> str:
    personal_root = get_user_personal_skills_dir(user)
    if not personal_root:
        raise ValueError("用户工作区不可用")
    return resolve_personal_skill_dir(personal_root, skill_id) or os.path.abspath(
        os.path.join(personal_root, skill_id)
    )


async def _notify(
    session: AsyncSession,
    *,
    user_id: int,
    title: str,
    content: str,
    share_id: str,
    skill_id: str,
    for_receiver: bool,
    accepted_skill_id: str | None = None,
    target_tab: str = "received",
) -> None:
    await PortalNotificationService.create(
        session,
        user_id=user_id,
        title=title[:200],
        content=content,
        level="info",
        category="skill_share" if for_receiver else "skill_share_result",
        resource_type="skill_share",
        resource_id=share_id,
        metadata={
            "share_id": share_id,
            "skill_id": skill_id,
            "accepted_skill_id": accepted_skill_id,
            "open": "skill_share",
            # 前端「站内消息」点开后落到「我的技能」的哪个二级页：收到的分享 / 我的分享
            "target_tab": target_tab,
        },
    )


# ---------------------------------------------------------------------------
# 分享人视角：发送 / 列表 / 撤回
# ---------------------------------------------------------------------------

async def share_skill(
    session: AsyncSession,
    *,
    user: dict[str, Any],
    skill_id: str,
    target_user_ids: list[int],
    message: str | None = None,
) -> dict[str, Any]:
    """把当前用户的个人技能分享给指定用户；同一接收人已有 PENDING 时刷新。"""

    user_id = _user_id(user)
    try:
        skill_id = validate_skill_id(skill_id)
    except SkillRenameError as exc:
        raise ValueError(str(exc)) from exc

    targets: list[int] = []
    for raw in target_user_ids or []:
        try:
            target = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("接收人 ID 无效") from exc
        if target not in targets:
            targets.append(target)
    if not targets:
        raise ValueError("请选择接收人")
    if len(targets) > MAX_SHARE_TARGETS:
        raise ValueError(f"一次最多分享给 {MAX_SHARE_TARGETS} 位用户")
    if user_id in targets:
        raise ValueError("不能分享给自己")

    note = (message or "").strip() or None
    if note and len(note) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"附言不能超过 {MAX_MESSAGE_LENGTH} 字")

    source_path = _source_skill_path(user, skill_id)
    if not os.path.isdir(source_path):
        raise FileNotFoundError("personal skill does not exist")
    meta = parse_skill_frontmatter(skill_id, os.path.join(source_path, "SKILL.md"))
    name = meta.get("name") or skill_id
    description = meta.get("description") or ""

    users_result = await session.execute(select(User).where(User.id.in_(targets)))
    users = {int(row.id): row for row in users_result.scalars().all()}
    invalid = [target for target in targets if target not in users or users[target].status != 1]
    if invalid:
        raise ValueError("以下接收人不存在或已停用：" + "、".join(str(item) for item in invalid))

    existing_result = await session.execute(
        select(SkillShare).where(
            SkillShare.source_user_id == user_id,
            SkillShare.source_skill_id == skill_id,
            SkillShare.target_user_id.in_(targets),
            SkillShare.status == "PENDING",
        )
    )
    existing = {int(row.target_user_id): row for row in existing_result.scalars().all()}

    created: list[SkillShare] = []
    refreshed: list[SkillShare] = []
    created_snapshots: list[str] = []
    try:
        for target in targets:
            row = existing.get(target)
            if row is None:
                row = SkillShare(
                    id=str(uuid.uuid4()),
                    source_user_id=user_id,
                    source_skill_id=skill_id,
                    target_user_id=target,
                    status="PENDING",
                    content_sha256="",
                    name=name,
                    description=description,
                    message=note,
                    created_at=datetime.now(),
                )
                session.add(row)
                created.append(row)
            else:
                row.name = name
                row.description = description
                row.message = note
                row.created_at = datetime.now()
                refreshed.append(row)

            snapshot = create_share_snapshot(source_path=source_path, share_id=row.id)
            created_snapshots.append(snapshot.snapshot_path)
            row.snapshot_path = snapshot.snapshot_path
            row.content_sha256 = snapshot.content_sha256
            row.file_count = snapshot.file_count
            row.total_size = snapshot.total_size

            await _notify(
                session,
                user_id=target,
                title=f"{_display_name(user)} 向你分享了技能 {name}",
                content=note or description or f"对方分享了个人技能「{name}」，可在「我的技能 → 收到的分享」中接受。",
                share_id=row.id,
                skill_id=skill_id,
                for_receiver=True,
            )
    except Exception:
        # 磁盘副作用不会随数据库回滚，这里把本轮刚生成的快照清掉，避免孤儿目录
        for snapshot_path in created_snapshots:
            remove_share_snapshot(snapshot_path)
        raise

    await session.flush()
    return {
        "created": [_share_payload(row, users.get(row.target_user_id)) for row in created],
        "refreshed": [_share_payload(row, users.get(row.target_user_id)) for row in refreshed],
    }


async def list_sent_shares(
    session: AsyncSession,
    *,
    user: dict[str, Any],
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    user_id = _user_id(user)
    stmt = (
        select(SkillShare, User)
        .outerjoin(User, User.id == SkillShare.target_user_id)
        .where(SkillShare.source_user_id == user_id)
        .order_by(desc(SkillShare.created_at))
    )
    if status and status in SHARE_STATUSES:
        stmt = stmt.where(SkillShare.status == status)
    result = await session.execute(stmt.offset(max(offset, 0)).limit(max(1, min(limit, MAX_LIST_LIMIT))))
    rows = result.all()

    counts_result = await session.execute(
        select(SkillShare.status, func.count())
        .where(SkillShare.source_user_id == user_id)
        .group_by(SkillShare.status)
    )
    status_counts = {key: 0 for key in SHARE_STATUSES}
    for raw_status, count in counts_result.all():
        if raw_status in status_counts:
            status_counts[raw_status] = int(count)

    return {
        "items": [_share_payload(row, target) for row, target in rows],
        "total": sum(status_counts.values()),
        "pending_count": status_counts["PENDING"],
        "status_counts": status_counts,
    }


async def withdraw_share(
    session: AsyncSession, *, user: dict[str, Any], share_id: str
) -> dict[str, Any]:
    user_id = _user_id(user)
    row = await _get_share(session, share_id)
    if int(row.source_user_id) != user_id:
        raise SharePermissionError("只有分享人可以撤回")
    if row.status != "PENDING":
        raise ShareConflictError("该分享已处理，无法撤回")

    snapshot_path = row.snapshot_path
    row.status = "WITHDRAWN"
    row.responded_at = datetime.now()
    row.snapshot_path = None
    await session.flush()
    remove_share_snapshot(snapshot_path)
    await _notify(
        session,
        user_id=int(row.target_user_id),
        title=f"{_display_name(user)} 撤回了技能分享 {row.name}",
        content=f"「{row.name}」的分享已被撤回，无需再处理。",
        share_id=row.id,
        skill_id=row.source_skill_id,
        for_receiver=False,
    )
    return {"id": row.id, "status": row.status}


# ---------------------------------------------------------------------------
# 接收人视角：列表 / 接受 / 拒绝
# ---------------------------------------------------------------------------

async def list_received_shares(
    session: AsyncSession,
    *,
    user: dict[str, Any],
    status: str = "PENDING",
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    user_id = _user_id(user)
    stmt = (
        select(SkillShare, User)
        .outerjoin(User, User.id == SkillShare.source_user_id)
        .where(SkillShare.target_user_id == user_id)
        .order_by(desc(SkillShare.created_at))
    )
    if status != "all":
        stmt = stmt.where(SkillShare.status == status if status in SHARE_STATUSES else "PENDING")
    result = await session.execute(
        stmt.offset(max(offset, 0)).limit(max(1, min(limit, MAX_LIST_LIMIT)))
    )
    rows = result.all()

    pending_result = await session.execute(
        select(func.count())
        .select_from(SkillShare)
        .where(SkillShare.target_user_id == user_id, SkillShare.status == "PENDING")
    )
    return {
        "items": [_received_payload(row, source) for row, source in rows],
        "pending_count": int(pending_result.scalar() or 0),
    }


async def accept_share(
    session: AsyncSession, *, user: dict[str, Any], share_id: str
) -> dict[str, Any]:
    user_id = _user_id(user)
    row = await _get_share(session, share_id)
    if int(row.target_user_id) != user_id:
        raise SharePermissionError("只有接收人可以接受该分享")
    if row.status != "PENDING":
        raise ShareConflictError("该分享已处理")

    snapshot_path = row.snapshot_path
    if not snapshot_path or not os.path.isdir(snapshot_path):
        raise ShareConflictError("分享内容已损坏，请让分享人重新分享")

    receiver_root = get_user_personal_skills_dir(user)
    if not receiver_root:
        raise ValueError("用户工作区不可用")
    try:
        materialized = materialize_share_snapshot(
            snapshot_path=snapshot_path,
            skills_root=receiver_root,
            preferred_skill_id=row.source_skill_id,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise ShareConflictError("分享内容已损坏，请让分享人重新分享") from exc

    row.status = "ACCEPTED"
    row.accepted_skill_id = materialized.skill_id
    row.responded_at = datetime.now()
    row.snapshot_path = None
    await session.flush()
    remove_share_snapshot(snapshot_path)

    source_user = await _load_user(session, int(row.source_user_id))
    await _notify(
        session,
        user_id=int(row.source_user_id),
        title=f"{_display_name(user)} 已接受你分享的 {row.name}",
        content=f"技能已复制到对方个人技能库（ID：{materialized.skill_id}）。",
        share_id=row.id,
        skill_id=row.source_skill_id,
        for_receiver=False,
        accepted_skill_id=materialized.skill_id,
        target_tab="sent",
    )
    return {
        "share_id": row.id,
        "accepted_skill_id": materialized.skill_id,
        "skill": {
            "id": materialized.skill_id,
            "name": row.name,
            "description": row.description or "",
            "scope": "personal",
            "source_user_name": getattr(source_user, "user_name", None),
            "source_real_name": getattr(source_user, "real_name", None),
            "file_count": materialized.file_count,
            "total_size": materialized.total_size,
        },
    }


async def reject_share(
    session: AsyncSession, *, user: dict[str, Any], share_id: str
) -> dict[str, Any]:
    user_id = _user_id(user)
    row = await _get_share(session, share_id)
    if int(row.target_user_id) != user_id:
        raise SharePermissionError("只有接收人可以拒绝该分享")
    if row.status != "PENDING":
        raise ShareConflictError("该分享已处理")

    snapshot_path = row.snapshot_path
    row.status = "REJECTED"
    row.responded_at = datetime.now()
    row.snapshot_path = None
    await session.flush()
    remove_share_snapshot(snapshot_path)

    source_user = await _load_user(session, int(row.source_user_id))
    await _notify(
        session,
        user_id=int(row.source_user_id),
        title=f"{_display_name(user)} 拒绝了技能分享 {row.name}",
        content=f"对方拒绝了「{row.name}」的分享。",
        share_id=row.id,
        skill_id=row.source_skill_id,
        for_receiver=False,
        target_tab="sent",
    )
    return {"id": row.id, "status": row.status}


# ---------------------------------------------------------------------------
# 选人搜索与菜单计数
# ---------------------------------------------------------------------------

async def search_share_targets(
    session: AsyncSession, *, user: dict[str, Any], keyword: str, limit: int = 20
) -> list[dict[str, Any]]:
    user_id = _user_id(user)
    text = (keyword or "").strip()
    if not text:
        return []
    pattern = f"%{text}%"
    result = await session.execute(
        select(User)
        .where(
            User.id != user_id,
            User.status == 1,
            or_(User.user_name.like(pattern), User.real_name.like(pattern)),
        )
        .order_by(User.user_name.asc())
        .limit(max(1, min(limit, 50)))
    )
    found = result.scalars().all()

    codes = {row.dept_code for row in found if getattr(row, "dept_code", None)}
    dept_names: dict[str, str] = {}
    if codes:
        dept_rows = await session.execute(
            select(Department.code, Department.name).where(Department.code.in_(codes))
        )
        dept_names = {code: name for code, name in dept_rows.all()}

    return [
        {
            "user_id": int(row.id),
            "user_name": row.user_name,
            "real_name": row.real_name,
            "department": dept_names.get(getattr(row, "dept_code", None) or ""),
        }
        for row in found
    ]


async def share_menu_counts(
    session: AsyncSession, *, user: dict[str, Any]
) -> dict[str, int]:
    user_id = _user_id(user)

    async def _count(*conditions) -> int:
        result = await session.execute(
            select(func.count()).select_from(SkillShare).where(and_(*conditions))
        )
        return int(result.scalar() or 0)

    received_pending = await _count(
        SkillShare.target_user_id == user_id, SkillShare.status == "PENDING"
    )
    sent_pending = await _count(
        SkillShare.source_user_id == user_id, SkillShare.status == "PENDING"
    )
    sent_total = await _count(SkillShare.source_user_id == user_id)
    return {
        "received_pending": received_pending,
        "sent_pending": sent_pending,
        "sent_total": sent_total,
    }


async def annotate_personal_skills(
    session: AsyncSession, *, user: dict[str, Any]
) -> dict[str, dict]:
    """给个人技能列表补充 ``shared_from`` 与 ``share_count``。

    ``shared_from`` 只认「我接受的分享且落地 ID 仍存在」这一种来源，避免把
    「我分享出去被别人接受」误算成我的技能来源。
    """

    user_id = _user_id(user)
    accepted_result = await session.execute(
        select(SkillShare).where(
            SkillShare.target_user_id == user_id,
            SkillShare.status == "ACCEPTED",
            SkillShare.accepted_skill_id.isnot(None),
        )
    )
    accepted_rows = accepted_result.scalars().all()

    source_ids = {int(row.source_user_id) for row in accepted_rows}
    source_users: dict[int, User] = {}
    if source_ids:
        users_result = await session.execute(select(User).where(User.id.in_(source_ids)))
        source_users = {int(item.id): item for item in users_result.scalars().all()}

    shared_from: dict[str, dict] = {}
    for row in accepted_rows:
        owner = source_users.get(int(row.source_user_id))
        shared_from[row.accepted_skill_id] = {
            "share_id": row.id,
            "source_user_id": int(row.source_user_id),
            "source_user_name": getattr(owner, "user_name", None),
            "source_real_name": getattr(owner, "real_name", None),
            "shared_at": _iso(row.created_at),
        }

    counts_result = await session.execute(
        select(SkillShare.source_skill_id, func.count())
        .where(
            SkillShare.source_user_id == user_id,
            SkillShare.status.in_(("PENDING", "ACCEPTED")),
        )
        .group_by(SkillShare.source_skill_id)
    )
    share_counts = {skill_id: int(count) for skill_id, count in counts_result.all()}
    return {"shared_from": shared_from, "share_counts": share_counts}
