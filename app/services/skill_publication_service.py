"""Personal skill publication snapshots and platform materialization."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.permission import ResourcePermission, UserRoleRelation
from app.models.skill_publication import SkillPublication, SkillPublicationVersion
from app.models.user import User
from app.services.ai.skill_resolver import (
    get_user_personal_skills_dir,
    resolve_personal_skill_dir,
)
from app.services.portal_notification_service import PortalNotificationService
from app.services.skill_rename_service import SkillRenameError, validate_skill_id
from app.utils.skill_metadata import parse_skill_frontmatter

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_SNAPSHOT_SIZE = 20 * 1024 * 1024


@dataclass(frozen=True)
class SnapshotStats:
    content_sha256: str
    file_count: int
    total_size: int


@dataclass(frozen=True)
class SnapshotInfo(SnapshotStats):
    snapshot_path: str
    source_path: str
    publication_id: str
    version_number: int


@dataclass(frozen=True)
class MaterializeResult:
    materialized_path: str
    content_sha256: str
    file_count: int
    total_size: int


class PublicationNotFoundError(LookupError):
    pass


class PublicationConflictError(RuntimeError):
    pass


def publication_snapshot_root() -> str:
    """Return the durable snapshot root beside the configured platform skills root."""

    root = os.path.join(os.path.dirname(os.path.abspath(settings.SKILLS_DIR)), "skill_publication_snapshots")
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


def _validate_skill_id(skill_id: str) -> None:
    """校验技能 ID（格式/长度/保留字），口径收敛到 skill_rename_service.validate_skill_id。

    此前这里是第三份正则（无长度上限、无保留字），与端点/重命名两处口径漂移时，同一个 ID
    可能在端点上合法、在发布服务里非法（或反过来），报错时机取决于用户走了哪条路。
    """
    try:
        validate_skill_id(skill_id)
    except SkillRenameError as exc:
        raise ValueError(str(exc)) from exc


def _assert_inside(root: str, path: str) -> None:
    if os.path.commonpath([os.path.abspath(root), os.path.abspath(path)]) != os.path.abspath(root):
        raise ValueError("skill path escapes root")


def _is_inside(root: str, path: str) -> bool:
    """非抛出版本：用于「不可信的记录位置」，越界时跳过而不是中断整个操作。"""
    try:
        _assert_inside(root, path)
    except ValueError:
        return False
    return True


def _walk_files(root: str):
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ValueError("skill snapshot root does not exist")

    for current, dirnames, filenames in os.walk(root_path, followlinks=False):
        current_path = Path(current)
        dirnames[:] = sorted(dirnames)
        filenames[:] = sorted(filenames)
        for name in [*dirnames, *filenames]:
            if name.startswith("."):
                raise ValueError("hidden files are not publishable")
            path = current_path / name
            if path.is_symlink():
                raise ValueError("symlink files are not publishable")
            _assert_inside(str(root_path), str(path))
        for name in filenames:
            yield current_path / name


def validate_snapshot_tree(root: str) -> SnapshotStats:
    """Validate a skill tree and calculate a deterministic content fingerprint."""

    root_path = Path(root).resolve()
    skill_md = root_path / "SKILL.md"
    if not skill_md.is_file() or skill_md.is_symlink():
        raise ValueError("SKILL.md is required")

    hasher = hashlib.sha256()
    file_count = 0
    total_size = 0
    files = sorted(_walk_files(str(root_path)), key=lambda path: path.relative_to(root_path).as_posix())
    for path in files:
        size = path.stat().st_size
        if size > MAX_FILE_SIZE:
            raise ValueError("skill file exceeds 20MB")
        total_size += size
        if total_size > MAX_SNAPSHOT_SIZE:
            raise ValueError("skill snapshot exceeds 20MB")
        relative = path.relative_to(root_path).as_posix().encode("utf-8")
        hasher.update(relative)
        hasher.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        file_count += 1
    return SnapshotStats(hasher.hexdigest(), file_count, total_size)


def copy_tree_contents(source: Path, target: Path) -> None:
    """把技能目录内容复制到 target（target 需已存在或可创建）。

    发布快照与点对点分享快照共用这一份实现。两处都必须拒绝符号链接：
    快照会被复制进别人的个人技能目录，跟随链接会把技能目录之外的任意文件
    （例如密钥、其它用户的技能）一并带过去。
    """
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        destination = target / entry.name
        if entry.is_symlink():
            raise ValueError("symlink files are not publishable")
        if entry.is_dir():
            shutil.copytree(entry, destination, symlinks=False)
        else:
            shutil.copy2(entry, destination)


# 兼容旧引用（本模块历史调用与外部可能仍在用私有名）
_copy_tree_contents = copy_tree_contents


def create_personal_skill_snapshot(
    *,
    user: dict[str, Any],
    skill_id: str,
    publication_id: str,
    version_number: int,
) -> SnapshotInfo:
    """Copy the current personal skill into an immutable, validated snapshot."""

    _validate_skill_id(skill_id)
    if not publication_id or int(version_number) < 1:
        raise ValueError("invalid publication version")
    personal_root = get_user_personal_skills_dir(user)
    if not personal_root:
        raise ValueError("user workspace is unavailable")
    # 兼容历史遗留的 .seed 布局（发布对象也是存量技能）
    source_path = resolve_personal_skill_dir(
        personal_root, skill_id
    ) or os.path.abspath(os.path.join(personal_root, skill_id))
    _assert_inside(personal_root, source_path)
    if not os.path.isdir(source_path):
        raise FileNotFoundError("personal skill does not exist")
    stats = validate_snapshot_tree(source_path)

    publication_root = Path(publication_snapshot_root()) / publication_id
    publication_root.mkdir(parents=True, exist_ok=True)
    destination = publication_root / f"v{int(version_number)}"
    if destination.exists():
        raise FileExistsError("publication snapshot already exists")
    temporary = Path(tempfile.mkdtemp(prefix=f".v{int(version_number)}-", dir=str(publication_root)))
    try:
        _copy_tree_contents(Path(source_path), temporary)
        copied_stats = validate_snapshot_tree(str(temporary))
        if copied_stats != stats:
            raise ValueError("snapshot changed while copying")
        os.replace(str(temporary), str(destination))
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return SnapshotInfo(
        snapshot_path=str(destination),
        source_path=source_path,
        publication_id=publication_id,
        version_number=int(version_number),
        content_sha256=stats.content_sha256,
        file_count=stats.file_count,
        total_size=stats.total_size,
    )


def _validate_platform_skill_id(platform_skill_id: str) -> None:
    _validate_skill_id(platform_skill_id)


def _write_platform_metadata(
    skill_dir: Path,
    *,
    platform_skill_id: str,
    version_number: int,
    publication_id: str | None = None,
) -> None:
    skill_md = skill_dir / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")
    metadata = [
        f"platform_skill_id: {platform_skill_id}",
        f"publication_version: {int(version_number)}",
        # 审核通过是平台有意的「上架」动作，落库必须可用。此前原样保留源技能的
        # enabled，于是作者若禁用了自己的私有副本，发布出来的平台技能默认禁用：
        # 审核双方都看到「已通过并发布」，运行时却谁都加载不到。
        "enabled: true",
    ]
    if publication_id:
        metadata.append(f"publication_id: {publication_id}")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if match:
        frontmatter = [
            line
            for line in match.group(1).splitlines()
            if not line.lower().startswith(
                ("platform_skill_id:", "publication_version:", "publication_id:", "enabled:")
            )
        ]
        frontmatter_text = "\n".join([*frontmatter, *metadata])
        replacement = f"---\n{frontmatter_text}\n---\n"
        skill_md.write_text(replacement + content[match.end():], encoding="utf-8")
    else:
        metadata_text = "\n".join(metadata)
        skill_md.write_text(f"---\n{metadata_text}\n---\n\n{content}", encoding="utf-8")


class SnapshotFileError(ValueError):
    """审核快照内文件访问被拒绝（不存在、非文本、过大或越界）。"""


#: 审核预览允许的文本扩展名（可执行脚本同样要能看，否则等于没审）
SNAPSHOT_PREVIEW_EXTENSIONS = frozenset(
    {
        ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".sql",
        ".csv", ".tsv", ".env", ".html", ".htm", ".css", ".xml", ".rst",
    }
)

#: 单个预览文件的大小上限
SNAPSHOT_PREVIEW_MAX_BYTES = 1024 * 1024


def resolve_snapshot_file(snapshot_root: str, relative_path: str) -> str:
    """把审核快照内的相对路径解析为**确定落在快照目录内**的真实文件路径。

    审核面板需要逐个打开包内文件（``scripts/*.py`` 这类可执行内容也在其中），因此
    这里必须自己把关：``realpath`` 解析完成后再比对前缀，软链接指到快照外也会被拒；
    同时限制为文本扩展名与单文件大小，避免把快照当任意文件读取器或 OOM 入口。

    raises SnapshotFileError: 路径越界、文件不存在、非文本或超出大小上限。
    """
    if not snapshot_root or not os.path.isdir(snapshot_root):
        raise SnapshotFileError("发布快照不存在或已被清理")

    root = os.path.realpath(snapshot_root)
    raw = str(relative_path or "").strip()
    if not raw:
        raise SnapshotFileError("缺少文件路径")
    if os.path.isabs(raw):
        raise SnapshotFileError("只允许快照内的相对路径")

    candidate = os.path.realpath(os.path.join(root, raw))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise SnapshotFileError("非法的文件路径")
    if not os.path.isfile(candidate):
        raise SnapshotFileError("文件不存在")

    _, ext = os.path.splitext(candidate.lower())
    if ext not in SNAPSHOT_PREVIEW_EXTENSIONS:
        raise SnapshotFileError("该文件类型不支持在线预览")
    if os.path.getsize(candidate) > SNAPSHOT_PREVIEW_MAX_BYTES:
        raise SnapshotFileError("单个文件超出 1MB 预览上限")
    return candidate


def read_snapshot_text_file(snapshot_root: str, relative_path: str) -> str:
    """按 :func:`resolve_snapshot_file` 的约束读取快照内文本文件内容。"""
    candidate = resolve_snapshot_file(snapshot_root, relative_path)
    with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def cleanup_publication_snapshot(snapshot_path: str | None) -> None:
    """删除某个**已终结**版本（驳回 / 撤销 / 下架）的物化快照目录，尽力而为。

    这三条路径上的快照都不会再被任何流程使用：``approve_publication`` 只接受 ``PENDING``
    版本，作者重新提交时 ``create_personal_skill_snapshot`` 会为新版本号重新物化一份。
    此前从不清理，于是每驳回/撤销一次就在快照根下留一份 20MB 级的孤儿副本，长期只增不减。

    **不动 approve 之后的目录**：已发布版本的快照是重新物化的唯一来源（下架后重新上架、
    版本回溯都靠它），删除等于把平台技能变成不可恢复的空中楼阁。

    安全约束：目标必须严格位于 :func:`publication_snapshot_root` 之下且不等于该根目录本身
    （防目录穿越 / 误删整个快照库）；越界只记日志不抛异常——清理是收尾动作，不能反过来让
    驳回/撤销失败。``skill_publication_versions.snapshot_path`` 列是 NOT NULL，故保留原值
    作为历史记录（该目录此后不再存在，读取侧已按 ``os.path.isdir`` 兜底）。
    """
    if not snapshot_path:
        return
    try:
        root = os.path.realpath(publication_snapshot_root())
        target = os.path.realpath(str(snapshot_path))
        if target == root or not target.startswith(root + os.sep):
            logger.warning("[SkillPublication] 拒绝清理快照根之外的目录: %s", snapshot_path)
            return
        if os.path.isdir(target):
            shutil.rmtree(target)
            # 该 publication 下已无任何版本快照时，顺手收掉空壳目录
            parent = os.path.dirname(target)
            if parent.startswith(root + os.sep) and os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
    except OSError as exc:  # pragma: no cover - 仅日志
        logger.warning("[SkillPublication] 快照目录清理失败 %s: %s", snapshot_path, exc)


def remove_materialized_skill(
    *, platform_skill_id: str, materialized_path: str | None = None
) -> list[str]:
    """删除已物化的平台技能目录，返回真正被删掉的路径列表。

    两个都删、都不容越界：
    1. ``materialized_path``（发布时记录的位置，可能是历史遗留的别处）；
    2. ``SKILLS_DIR/<platform_skill_id>``（当前约定位置）。

    只按记录删会漏掉「记录为空」的旧数据（那时还没写 ``materialized_path``）；只按约定
    位置删又会漏掉历史遗留布局，因此两者都删。路径先过 ``_validate_skill_id`` 与
    ``_assert_inside``，任何一步越界都直接抛异常——这是删除操作，宁可失败也不能删到
    技能库之外。
    """
    _validate_skill_id(platform_skill_id)
    skills_root = os.path.abspath(settings.SKILLS_DIR)
    candidates = [os.path.abspath(os.path.join(skills_root, platform_skill_id))]
    if materialized_path:
        recorded = os.path.abspath(str(materialized_path))
        # 记录位置是**库里的字符串**，属于不可信输入：
        # ① 越界（历史部署换过 SKILLS_DIR、或数据被改过）→ 跳过，绝不能删库外的目录；
        # ② 目录名与技能 ID 不一致 → 说明它指的是**另一个**技能，跟着删就是连坐误删。
        # 两种情况都只跳过 + 告警：一条陈旧记录不该让管理员的「下架」整体失败，
        # 约定位置（SKILLS_DIR/<id>）仍然照删，运行时看到的公共技能照样消失。
        if _is_inside(skills_root, recorded) and os.path.basename(recorded) == platform_skill_id:
            candidates.append(recorded)
        else:
            logger.warning(
                "[SkillPublication] 跳过不可信的物化记录: %s (技能 %s，技能库 %s)",
                recorded,
                platform_skill_id,
                skills_root,
            )
    # 先整体校验、再动手删除：任何一条越界都必须在删掉第一条之前失败，避免「删了一半」
    for target in candidates:
        _assert_inside(skills_root, target)
        if target == skills_root:  # 纵深防御：白名单之外再挡一次「删整库」
            raise ValueError("skill path escapes root")
    removed: list[str] = []
    for target in candidates:
        if os.path.isdir(target):
            shutil.rmtree(target)
            removed.append(target)
    return removed


def materialize_snapshot(
    *,
    snapshot_path: str,
    platform_skill_id: str,
    version_number: int,
    publication_id: str | None = None,
) -> MaterializeResult:
    """Atomically replace the active platform directory with a snapshot."""

    _validate_platform_skill_id(platform_skill_id)
    stats = validate_snapshot_tree(snapshot_path)
    skills_root = Path(os.path.abspath(settings.SKILLS_DIR))
    skills_root.mkdir(parents=True, exist_ok=True)
    target = skills_root / platform_skill_id
    staged = Path(tempfile.mkdtemp(prefix=f".{platform_skill_id}-", dir=str(skills_root)))
    backup: Path | None = None
    # ``replaced`` 记录"新内容是否已经就位"。只有它为 True 时才能删 ``target``：
    # 此前不管是哪一步失败都无条件 ``rmtree(target)``，于是**准备阶段**（拷快照、校验、
    # 甚至 ``os.replace(target, backup)`` 本身）失败都会把**正在线上服务的**平台技能目录
    # 删掉——审核这次通过失败，代价是线上技能消失，且没有备份可恢复。
    replaced = False
    try:
        _copy_tree_contents(Path(snapshot_path), staged)
        copied_stats = validate_snapshot_tree(str(staged))
        if copied_stats != stats:
            raise ValueError("materialized skill changed while copying")
        if target.exists():
            backup = skills_root / f".{platform_skill_id}.backup-{uuid.uuid4().hex}"
            os.replace(str(target), str(backup))
        os.replace(str(staged), str(target))
        replaced = True
        _write_platform_metadata(
            target,
            platform_skill_id=platform_skill_id,
            version_number=version_number,
            publication_id=publication_id,
        )
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        if replaced and target.exists():
            # 只清理"我们自己刚放上去的那一份"，随后把备份放回原位
            shutil.rmtree(target, ignore_errors=True)
        if backup is not None and backup.exists():
            os.replace(str(backup), str(target))
        raise
    return MaterializeResult(
        materialized_path=str(target),
        content_sha256=stats.content_sha256,
        file_count=stats.file_count,
        total_size=stats.total_size,
    )


def _user_id(user: dict[str, Any]) -> int:
    try:
        return int(user.get("user_id") or user.get("id"))
    except (TypeError, ValueError):
        raise ValueError("invalid user id")


def _meta_for_personal_skill(*, user: dict[str, Any], skill_id: str) -> dict[str, str]:
    personal_root = get_user_personal_skills_dir(user)
    if not personal_root:
        raise ValueError("user workspace is unavailable")
    skill_dir = resolve_personal_skill_dir(personal_root, skill_id) or os.path.join(
        personal_root, skill_id
    )
    if not os.path.isdir(skill_dir) or not os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
        raise FileNotFoundError("personal skill does not exist or is missing SKILL.md")
    return parse_skill_frontmatter(skill_id, os.path.join(skill_dir, "SKILL.md"))


def _platform_skill_id(name: str, publication_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.lower()).strip("-") or "skill"
    return f"public-{slug[:80]}-{publication_id.replace('-', '')[:8]}"


def _publication_payload(publication: SkillPublication, version: SkillPublicationVersion | None) -> dict[str, Any]:
    effective_status = publication.status
    # REVOKED 是「管理员下架」：作者必须看到「已下架」+ 下架原因，而不是继续显示「已发布」。
    if version is not None and version.status in {"REJECTED", "WITHDRAWN", "REVOKED"}:
        effective_status = version.status
    # 下架字段用 getattr 读取：DB 列一定存在，但轻量对象/单测替身可能没有这几个新列，
    # 不能因为少一个审计字段就让整个发布列表 500。
    revoked_at = getattr(publication, "revoked_at", None)
    return {
        "publication_id": publication.id,
        "version_id": version.id if version else None,
        "skill_id": publication.source_personal_skill_id,
        "platform_skill_id": publication.platform_skill_id,
        "name": publication.name,
        "description": publication.description or "",
        "publication_status": effective_status,
        "version_number": version.version_number if version else publication.current_version,
        "version_status": version.status if version else None,
        "current_public_version": publication.current_version,
        "pending_version": version.version_number if version and version.status == "PENDING" else None,
        "last_review_comment": version.review_comment if version else None,
        "content_sha256": version.content_sha256 if version else None,
        "withdrawn_by": version.withdrawn_by if version else None,
        "withdrawn_at": version.withdrawn_at.isoformat() if version and version.withdrawn_at else None,
        "revoked_by": getattr(publication, "revoked_by", None),
        "revoked_at": revoked_at.isoformat() if revoked_at else None,
    }


async def _versions_for_publication(session: AsyncSession, publication_id: str) -> list[SkillPublicationVersion]:
    result = await session.execute(
        select(SkillPublicationVersion)
        .where(SkillPublicationVersion.publication_id == publication_id)
        .order_by(desc(SkillPublicationVersion.version_number))
    )
    return list(result.scalars().all())


async def _skill_publication_reviewer_ids(session: AsyncSession) -> list[int]:
    direct_reviewer_ids = select(ResourcePermission.user_id).where(
        ResourcePermission.resource_type == "element",
        ResourcePermission.resource_id == "element:skills:admin",
        ResourcePermission.enabled.is_(True),
        ResourcePermission.user_id.is_not(None),
        ResourcePermission.user_id > 0,
    )
    role_reviewer_ids = (
        select(UserRoleRelation.user_id)
        .join(ResourcePermission, ResourcePermission.role_id == UserRoleRelation.role_id)
        .where(
            ResourcePermission.resource_type == "element",
            ResourcePermission.resource_id == "element:skills:admin",
            ResourcePermission.enabled.is_(True),
            ResourcePermission.role_id.is_not(None),
            ResourcePermission.role_id > 0,
        )
    )
    result = await session.execute(
        select(User.id)
        .where(
            User.status == 1,
            or_(
                User.role == "admin",
                User.id.in_(direct_reviewer_ids),
                User.id.in_(role_reviewer_ids),
            ),
        )
        .order_by(User.id.asc())
    )
    return [int(user_id) for user_id in result.scalars().all()]


async def notify_skill_publication_reviewers(
    session: AsyncSession,
    *,
    publication: SkillPublication,
    version: SkillPublicationVersion,
    submitted_by: dict[str, Any],
) -> None:
    reviewer_ids = await _skill_publication_reviewer_ids(session)
    submitter_name = str(
        submitted_by.get("real_name")
        or submitted_by.get("user_name")
        or submitted_by.get("user_id")
        or "用户"
    )
    for reviewer_id in reviewer_ids:
        await PortalNotificationService.create(
            session,
            user_id=reviewer_id,
            title=f"技能发布待审核：{publication.name}",
            content=(
                f"用户 {submitter_name} 提交了个人技能「{publication.name}」"
                f" v{version.version_number}，请前往技能工作台的“待审核”Tab 处理。"
            ),
            level="info",
            category="skill_publication",
            resource_type="skill_publication",
            resource_id=version.id,
            metadata={
                "action": "open_skill_publication_review",
                "publication_id": publication.id,
                "version_id": version.id,
                "version_number": version.version_number,
            },
        )


async def withdraw_personal_skill_publication(
    session: AsyncSession,
    *,
    user: dict[str, Any],
    skill_id: str,
) -> dict[str, Any]:
    """Withdraw the current user's pending publication without deleting its audit record."""

    user_id = _user_id(user)
    result = await session.execute(
        select(SkillPublicationVersion, SkillPublication)
        .join(SkillPublication, SkillPublication.id == SkillPublicationVersion.publication_id)
        .where(
            SkillPublication.source_user_id == user_id,
            SkillPublication.source_personal_skill_id == skill_id,
        )
        .order_by(desc(SkillPublicationVersion.version_number))
        # 行锁：两个并发的撤销/提交不会各自读到 PENDING 再双双写状态
        .with_for_update()
    )
    row = result.first()
    if not row:
        raise PublicationNotFoundError("publication not found")
    version, publication = row
    if version.status != "PENDING":
        raise PublicationConflictError("publication version is no longer pending")

    version.status = "WITHDRAWN"
    version.withdrawn_by = user_id
    version.withdrawn_at = datetime.now()
    if publication.current_version is None:
        publication.status = "UNPUBLISHED"
    else:
        publication.status = "PUBLISHED"
    publication.updated_at = datetime.now()
    await session.flush()
    # 撤销后该快照再无用途（重新提交会为新版本号重新物化），顺手清掉，避免孤儿副本堆积。
    # 用 getattr 容错：少数调用方/单测传的是轻量对象，缺字段时跳过清理即可，不能因此 500。
    cleanup_publication_snapshot(getattr(version, "snapshot_path", None))
    return _publication_payload(publication, version)


async def submit_personal_skill(session: AsyncSession, *, user: dict[str, Any], skill_id: str) -> dict[str, Any]:
    """Create a pending immutable snapshot for the current user's personal skill."""

    user_id = _user_id(user)
    meta = _meta_for_personal_skill(user=user, skill_id=skill_id)
    publication_result = await session.execute(
        select(SkillPublication)
        .where(
            SkillPublication.source_user_id == user_id,
            SkillPublication.source_personal_skill_id == skill_id,
        )
        .order_by(desc(SkillPublication.updated_at))
    )
    publication = publication_result.scalars().first()
    if publication is None:
        publication = SkillPublication(
            id=str(uuid.uuid4()),
            source_user_id=user_id,
            source_personal_skill_id=skill_id,
            name=meta.get("name") or skill_id,
            description=meta.get("description") or "",
            status="PENDING",
        )
        session.add(publication)
        await session.flush()
    else:
        publication.name = meta.get("name") or publication.name or skill_id
        publication.description = meta.get("description") or publication.description or ""

    versions = await _versions_for_publication(session, publication.id)
    pending = next((version for version in versions if version.status == "PENDING"), None)
    source_root = get_user_personal_skills_dir(user)
    if not source_root:
        raise ValueError("user workspace is unavailable")
    resolved_source = resolve_personal_skill_dir(source_root, skill_id) or os.path.join(
        source_root, skill_id
    )
    source_stats = validate_snapshot_tree(resolved_source)
    if pending is not None:
        if pending.content_sha256 == source_stats.content_sha256:
            return _publication_payload(publication, pending)
        raise PublicationConflictError("a different publication version is already pending")

    next_version = (max((version.version_number for version in versions), default=0) + 1)
    snapshot = create_personal_skill_snapshot(
        user=user,
        skill_id=skill_id,
        publication_id=publication.id,
        version_number=next_version,
    )
    version = SkillPublicationVersion(
        id=str(uuid.uuid4()),
        publication_id=publication.id,
        version_number=next_version,
        status="PENDING",
        snapshot_path=snapshot.snapshot_path,
        content_sha256=snapshot.content_sha256,
        file_count=snapshot.file_count,
        total_size=snapshot.total_size,
        submitted_by=user_id,
    )
    session.add(version)
    # 已经上线过的谱系重新提交新版本时，**线上版本仍然在线**（current_version 非空），
    # publication.status 必须保持 PUBLISHED：此前无条件写 PENDING，会让「下架」彻底不可用
    # ——下架要求 status=PUBLISHED，管理端的可下架列表也只列 PUBLISHED，于是作者提交一次
    # 新版本，管理员就再也下架不了正在线上服务的那一版（真实故障）。是否有待审版本由
    # **版本行**表达（payload.pending_version / version_status），不靠这条汇总状态。
    publication.status = "PUBLISHED" if publication.current_version else "PENDING"
    await session.flush()
    await notify_skill_publication_reviewers(
        session,
        publication=publication,
        version=version,
        submitted_by=user,
    )
    return _publication_payload(publication, version)


async def list_my_publication_summaries(
    session: AsyncSession,
    *,
    user_id: int,
    skill_ids: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    query = select(SkillPublication).where(SkillPublication.source_user_id == int(user_id))
    if skill_ids:
        query = query.where(SkillPublication.source_personal_skill_id.in_(skill_ids))
    result = await session.execute(query)
    summaries: dict[str, dict[str, Any]] = {}
    for publication in result.scalars().all():
        versions = await _versions_for_publication(session, publication.id)
        latest = versions[0] if versions else None
        summaries[publication.source_personal_skill_id] = _publication_payload(publication, latest)
    return summaries


async def list_pending_publications(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        select(SkillPublicationVersion, SkillPublication)
        .join(SkillPublication, SkillPublication.id == SkillPublicationVersion.publication_id)
        .where(SkillPublicationVersion.status == "PENDING")
        .order_by(SkillPublicationVersion.submitted_at.asc())
    )
    return [_publication_payload(publication, version) for version, publication in result.all()]


async def get_publication_version(
    session: AsyncSession,
    *,
    version_id: str,
    include_snapshot: bool = False,
) -> dict[str, Any]:
    result = await session.execute(
        select(SkillPublicationVersion, SkillPublication)
        .join(SkillPublication, SkillPublication.id == SkillPublicationVersion.publication_id)
        .where(SkillPublicationVersion.id == version_id)
    )
    row = result.first()
    if not row:
        raise PublicationNotFoundError("publication version not found")
    version, publication = row
    payload = _publication_payload(publication, version)
    payload.update(
        {
            "snapshot_path": version.snapshot_path if include_snapshot else None,
            "file_count": version.file_count,
            "total_size": version.total_size,
            "submitted_by": version.submitted_by,
            "submitted_at": version.submitted_at.isoformat() if version.submitted_at else None,
            "reviewed_by": version.reviewed_by,
            "reviewed_at": version.reviewed_at.isoformat() if version.reviewed_at else None,
            "review_comment": version.review_comment,
            "materialized_path": version.materialized_path,
        }
    )
    return payload


async def approve_publication(
    session: AsyncSession,
    *,
    version_id: str,
    reviewer: dict[str, Any],
) -> dict[str, Any]:
    result = await session.execute(
        select(SkillPublicationVersion, SkillPublication)
        .join(SkillPublication, SkillPublication.id == SkillPublicationVersion.publication_id)
        .where(SkillPublicationVersion.id == version_id)
        # 行锁：两位审核人同时点"通过"时，第二个必须等第一个提交后再读，
        # 从而看到 APPROVED 并得到 409，而不是也去物化一遍（两份新目录 + 状态互相覆盖）
        .with_for_update()
    )
    row = result.first()
    if not row:
        raise PublicationNotFoundError("publication version not found")
    version, publication = row
    if version.status != "PENDING":
        raise PublicationConflictError("publication version is no longer pending")
    reviewer_id = _user_id(reviewer)
    platform_id = publication.platform_skill_id or _platform_skill_id(publication.name, publication.id)
    materialized = materialize_snapshot(
        snapshot_path=version.snapshot_path,
        platform_skill_id=platform_id,
        version_number=version.version_number,
        publication_id=publication.id,
    )
    await session.execute(
        update(SkillPublicationVersion)
        .where(
            SkillPublicationVersion.publication_id == publication.id,
            SkillPublicationVersion.status == "APPROVED",
        )
        .values(status="SUPERSEDED")
    )
    now = datetime.now()
    publication.platform_skill_id = platform_id
    publication.current_version = version.version_number
    publication.status = "PUBLISHED"
    # 重新上架要清掉上一次下架的审计痕迹：否则管理端会一边显示"已发布"、一边带着
    # revoked_at/revoked_by（"这版什么时候被下架的"变成误导信息）。
    if getattr(publication, "revoked_at", None) is not None:
        publication.revoked_at = None
        publication.revoked_by = None
    publication.updated_at = now
    version.status = "APPROVED"
    version.reviewed_by = reviewer_id
    version.reviewed_at = now
    version.published_at = now
    version.materialized_path = materialized.materialized_path
    await session.flush()
    return _publication_payload(publication, version)


async def reject_publication(
    session: AsyncSession,
    *,
    version_id: str,
    reviewer: dict[str, Any],
    comment: str,
) -> dict[str, Any]:
    if not (comment or "").strip():
        raise ValueError("reject comment is required")
    result = await session.execute(
        select(SkillPublicationVersion, SkillPublication)
        .join(SkillPublication, SkillPublication.id == SkillPublicationVersion.publication_id)
        .where(SkillPublicationVersion.id == version_id)
        .with_for_update()  # 与"通过"同一把锁：驳回 vs 通过不能再各写一半
    )
    row = result.first()
    if not row:
        raise PublicationNotFoundError("publication version not found")
    version, publication = row
    if version.status != "PENDING":
        raise PublicationConflictError("publication version is no longer pending")
    version.status = "REJECTED"
    version.reviewed_by = _user_id(reviewer)
    version.reviewed_at = datetime.now()
    version.review_comment = comment.strip()
    if publication.current_version is None:
        # 从未发布过的谱系：驳回后既没有线上版本、也没有待审版本，状态是 UNPUBLISHED。
        # 此前写回 "PENDING"，于是列表里出现「状态待审、却一条待审版本都没有」的幽灵记录。
        publication.status = "UNPUBLISHED"
    await session.flush()
    # 驳回的版本永远不会再被 approve（approve 只接受 PENDING），快照就此作废，顺手清掉
    cleanup_publication_snapshot(getattr(version, "snapshot_path", None))
    return _publication_payload(publication, version)


async def list_published_publications(session: AsyncSession) -> list[dict[str, Any]]:
    """列出**当前已发布**的公共技能，供管理端「下架」列表使用。

    判据是**版本事实**：``version.status == "APPROVED"`` 且版本号正是
    ``publication.current_version``。刻意不再要求 ``publication.status == "PUBLISHED"``
    ——那条汇总状态会被"作者又提交了一版"改写成 PENDING（老数据里已经被写坏），
    用它过滤会让仍然在线、且必须能下架的技能从列表里消失。``SUPERSEDED`` 的历史版本、
    ``REJECTED``/``WITHDRAWN``/``REVOKED`` 的版本都不该出现在可下架列表里
    （对它们下架没有意义，只会得到 409）。
    """
    result = await session.execute(
        select(SkillPublicationVersion, SkillPublication)
        .join(SkillPublication, SkillPublication.id == SkillPublicationVersion.publication_id)
        .where(
            SkillPublicationVersion.status == "APPROVED",
            SkillPublicationVersion.version_number == SkillPublication.current_version,
        )
        .order_by(desc(SkillPublicationVersion.published_at))
    )
    return [
        {
            "publication_id": publication.id,
            "version_id": version.id,
            "skill_id": publication.source_personal_skill_id,
            "platform_skill_id": publication.platform_skill_id,
            "name": publication.name,
            "description": publication.description or "",
            "publication_status": "PUBLISHED",
            "version_number": version.version_number,
            "source_user_id": publication.source_user_id,
            "submitted_by": version.submitted_by,
            "published_at": version.published_at.isoformat() if version.published_at else None,
            "reviewed_by": version.reviewed_by,
            "file_count": version.file_count,
            "total_size": version.total_size,
        }
        for version, publication in result.all()
    ]


def _assert_current_published(version: SkillPublicationVersion, publication: SkillPublication) -> None:
    """下架前的状态校验：只有「当前已发布版本」能下架，其余一律 409（而不是 500）。"""
    if version.status == "REVOKED":
        raise PublicationConflictError("publication version is already revoked")
    # 只看版本事实，不看 publication.status：那条汇总状态可能因为"又提交了一版"而被写成
    # PENDING（老缺陷留下的数据），此处若跟着它判，正在线上服务的版本就永远下架不掉。
    if (
        version.status != "APPROVED"
        or publication.current_version != version.version_number
    ):
        raise PublicationConflictError("publication version is not the current published version")


async def revoke_published_version(
    session: AsyncSession,
    *,
    version_id: str,
    reviewer: dict[str, Any],
    comment: str,
) -> dict[str, Any]:
    """下架一个已发布的平台技能版本，返回该发布谱系的 payload。

    语义（与「删除个人技能」「删除平台技能」都不同）：
    * **删除已物化的平台技能目录**——技能必须真的从公共库消失，否则运行时照样能扫到；
    * ``skill_publication_versions.status = "REVOKED"``、``skill_publications.status =
      "UNPUBLISHED"``，写 ``revoked_at`` / ``revoked_by``；
    * **保留** ``platform_skill_id`` 与 ``snapshot_path``：作者重新提交、审核通过时复用同一个
      ``platform_skill_id``（``approve_publication`` 只在它为 None 时才新生成），目录名因此
      保持稳定，历史引用（智能体白名单、会话挂载）在重新上架后重新生效；
    * 下架原因写入 ``review_comment``，作者端 ``_publication_payload`` 会把它作为
      ``last_review_comment`` 展示，状态显示为 ``REVOKED``（已下架）。

    重复下架是**幂等**的：第二次调用命中 ``_assert_current_published`` 后返回 409，不会 500。
    """
    if not (comment or "").strip():
        raise ValueError("revoke comment is required")
    result = await session.execute(
        select(SkillPublicationVersion, SkillPublication)
        .join(SkillPublication, SkillPublication.id == SkillPublicationVersion.publication_id)
        .where(SkillPublicationVersion.id == version_id)
        # 行锁：并发下架（或"下架 vs 重新提交"）时第二个必须重读到最新状态
        .with_for_update()
    )
    row = result.first()
    if not row:
        raise PublicationNotFoundError("publication version not found")
    version, publication = row
    _assert_current_published(version, publication)

    platform_id = publication.platform_skill_id
    if not platform_id:
        # 已发布版本必然有平台技能 ID；缺失说明数据被人工改坏，明确报冲突而不是删错目录
        raise PublicationConflictError("published version has no platform skill id")
    # 先校验再改库：ID 不合法要立刻失败，不能先写状态再抛异常
    try:
        validate_skill_id(platform_id)
    except SkillRenameError as exc:
        # 数据被人工改坏（平台技能 ID 非法）：报冲突让管理员人工核对，既不要 500，也不能删错目录
        raise PublicationConflictError(
            f"published version has an illegal platform skill id: {exc}"
        ) from exc
    # 记录物化位置后置空（下面删目录还要用原始值）
    recorded_path = version.materialized_path

    now = datetime.now()
    version.status = "REVOKED"
    version.review_comment = comment.strip()
    version.reviewed_by = _user_id(reviewer)
    version.reviewed_at = now
    publication.status = "UNPUBLISHED"
    publication.current_version = None
    publication.revoked_at = now
    publication.revoked_by = _user_id(reviewer)
    publication.updated_at = now
    await session.flush()
    # 先 flush 再删目录：flush 失败时目录原样保留（整个事务回滚，DB 与磁盘一致）；
    # 反过来的顺序一旦 flush 失败，就会出现「目录已删、库里仍写着已发布」。
    remove_materialized_skill(
        platform_skill_id=platform_id, materialized_path=recorded_path
    )
    # 目录已删，物化位置如实置空（下架后由重新审核重新物化）
    version.materialized_path = None
    return _publication_payload(publication, version)


async def mark_platform_skill_publications_revoked(
    session: AsyncSession,
    *,
    platform_skill_id: str,
    actor_user_id: int | None = None,
    comment: str | None = None,
) -> int:
    """把某个平台技能名下**仍处于 PUBLISHED** 的发布谱系标记为 REVOKED，返回条数。

    删除平台技能目录时复用下架语义，而不是物理删发布记录：

    * 删除记录会毁掉审核审计轨迹（谁提交、谁审核、审核意见全没了），也没有任何备份；
    * ``platform_skill_id`` 是唯一索引，记录删掉后作者重新提交同一技能会拿到一个**全新的**
      平台技能 ID，历史发布与技能对不上，智能体白名单/会话挂载也永远回不来；
    * 保留 ``platform_skill_id`` + ``snapshot_path``，删除后重新提交并通过审核即可复用同一
      ID 重新物化——「技能没了但发布能力还在」正是下架的定义。

    非 PUBLISHED（PENDING/UNPUBLISHED/REVOKED）的谱系一律不动。
    """
    result = await session.execute(
        select(SkillPublication).where(
            SkillPublication.platform_skill_id == platform_skill_id,
            SkillPublication.status == "PUBLISHED",
        )
    )
    publications = list(result.scalars().all())
    if not publications:
        return 0
    now = datetime.now()
    for publication in publications:
        publication.status = "UNPUBLISHED"
        publication.current_version = None
        publication.revoked_at = now
        publication.revoked_by = actor_user_id
        publication.updated_at = now
    publication_ids = [publication.id for publication in publications]
    versions = await session.execute(
        select(SkillPublicationVersion).where(
            SkillPublicationVersion.publication_id.in_(publication_ids),
            SkillPublicationVersion.status == "APPROVED",
        )
    )
    for version in versions.scalars().all():
        version.status = "REVOKED"
        version.reviewed_at = now
        version.materialized_path = None
        if comment and not (version.review_comment or "").strip():
            version.review_comment = comment
    await session.commit()
    return len(publications)


async def list_active_platform_skills_for_personal_skill(
    session: AsyncSession, *, user_id: int, skill_id: str
) -> list[str]:
    """返回该个人技能当前在平台上**仍处于发布态**的 ``platform_skill_id`` 列表。

    删除个人副本时用它给用户一句实话：个人技能删掉后平台上的那一份照样在跑，要下架得走
    技能管理端的下架功能（不能让用户以为「删了个人技能 = 技能没了」）。
    """
    result = await session.execute(
        select(SkillPublication.platform_skill_id).where(
            SkillPublication.source_user_id == int(user_id),
            SkillPublication.source_personal_skill_id == skill_id,
            SkillPublication.status == "PUBLISHED",
            SkillPublication.platform_skill_id.isnot(None),
        )
    )
    return [str(item) for item in result.scalars().all() if item]
