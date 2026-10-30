from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import fnmatch
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Iterable

from app.services.ai.runtime.agentscope.tool_timeout import (
    apply_sandbox_gateway_timeout,
)
from app.services.ai.tools.registry import AGENTSCOPE_BUILTIN_TOOL_ALIASES

logger = logging.getLogger(__name__)

WORKSPACE_BUILTIN_TOOL_NAMES = frozenset(
    {"Bash", "Read", "Write", "Edit", "Glob", "Grep"}
)
DOCKER_WORKSPACE_LOGICAL_ROOT = "/workspace"
DOCKER_WORKSPACE_FILE_TOOL_NAMES = frozenset(
    WORKSPACE_BUILTIN_TOOL_NAMES - {"Bash"}
)
WORKSPACE_PROMPT_TOOL_NAMES = WORKSPACE_BUILTIN_TOOL_NAMES

_workspace_cache: dict[str, Any] = {}
_docker_workspace_cache: dict[str, Any] = {}
_docker_workspace_refcounts: dict[str, int] = {}
_docker_workspace_locks: dict[str, asyncio.Lock] = {}
_docker_workspace_last_used: dict[str, float] = {}
_k8s_workspace_cache: dict[str, Any] = {}
_k8s_workspace_refcounts: dict[str, int] = {}
_k8s_workspace_locks: dict[str, asyncio.Lock] = {}
_k8s_workspace_last_used: dict[str, float] = {}
_k8s_workspace_reaper_task: asyncio.Task[None] | None = None
_workspace_sandbox_refs: dict[str, str] = {}
_docker_workspace_reaper_task: asyncio.Task[None] | None = None
#: 用户级（``<root>/<user_key>``）宿主机准备锁：见 :func:`_sandbox_host_prepare_lock`。
_sandbox_host_prepare_locks: dict[str, asyncio.Lock] = {}


def _sandbox_host_prepare_lock(host_workdir: str) -> asyncio.Lock:
    """取该用户工作区的「宿主机准备」锁（键即 ``<root>/<user_key>``）。

    细粒度的缓存键锁只保证「同一技能集合不会被并发建两次」，但同一用户的**多个技能
    集合**会并发读写同一份宿主目录：个人技能源、公共文档目录、以及历史单份布局的一次性
    迁移（``os.rename``）。两个协程同时判断「历史目录还在、没有兄弟目录」再各自 rename，
    就会有一个撞 FileNotFoundError（虽有兜底，但语义靠运气）。这里把这段准备动作串起来。

    只覆盖**同步的目录准备**（不含 await），因此不可能自我重入死锁；构建容器/ Pod 的
    长耗时部分仍在各自的缓存键锁里并行执行。
    """
    key = os.path.abspath(host_workdir)
    lock = _sandbox_host_prepare_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _sandbox_host_prepare_locks[key] = lock
    return lock


DOCKER_WORKSPACE_INIT_RETRY_DELAY_SECONDS = 0.5
K8S_WORKSPACE_IDLE_SECONDS = 900.0
K8S_WORKSPACE_REAPER_INTERVAL_SECONDS = 60.0

#: 沙箱空闲回收可配置：分钟（docker/k8s 共用）。默认 15 分钟，对应上方 900s。
SANDBOX_IDLE_TIME_KEY = "sandbox_idle_time"
SANDBOX_IDLE_TIME_DEFAULT_MINUTES = 15


async def resolve_effective_sandbox_idle_seconds(*, default_seconds: float) -> float:
    """读取 ``sandbox_idle_time``（分钟）并换算为秒；非法/未配置时回退到默认秒。

    这是「沙箱空闲多久被回收」的**唯一真相来源**：回收器、执行期心跳与前端浮标文案
    都必须经过它，避免展示口径与实际回收口径漂移（历史上浮标把 30 分钟写死在组件里，
    管理台改成 15 分钟后界面仍在说 30m）。
    """
    from app.services.config_service import ConfigService

    try:
        raw = await ConfigService.get(
            SANDBOX_IDLE_TIME_KEY,
            str(SANDBOX_IDLE_TIME_DEFAULT_MINUTES),
        )
        minutes = float(str(raw or "0").strip())
        if minutes > 0:
            return minutes * 60.0
    except (TypeError, ValueError):
        pass
    return float(default_seconds)


#: 兼容既有引用（历史测试与调用点仍按旧名访问）；新代码请用公共名。
_effective_sandbox_idle_seconds = resolve_effective_sandbox_idle_seconds


class DockerSandboxUnavailableError(RuntimeError):
    """Docker 沙箱未能初始化，调用方不得回退到宿主 Bash。"""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "docker_workspace_start_failed",
        user_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.user_message = user_message or (
            "Docker 沙箱不可用，Bash 未执行。请检查 Docker daemon、镜像和权限。"
        )


def _docker_init_reason_code(exc: BaseException) -> str:
    """把 Docker 初始化异常归一为前端/API 可识别的稳定错误码。"""
    text = str(exc).lower()
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return "docker_daemon_unavailable"
    if isinstance(exc, PermissionError) or any(
        marker in text
        for marker in (
            "permission denied",
            "access denied",
            "unauthorized",
            "docker socket",
        )
    ):
        return "docker_daemon_unavailable"
    if any(
        marker in text
        for marker in (
            "no such image",
            "image not found",
            "manifest unknown",
            "pull access denied",
        )
    ):
        return "docker_image_unavailable"
    return "docker_workspace_start_failed"


def _docker_init_is_retryable(exc: BaseException) -> bool:
    """只重试连接瞬断和容器创建竞争，不重试权限/镜像/配置错误。"""
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return True
    text = str(exc).lower()
    return "connection reset" in text or "container is already in use" in text


WORKSPACE_USER_KEY_SEP = "__"
USER_DOCS_DIR_NAME = "docs"
USER_SESSIONS_DIR_NAME = "sessions"
DOCKER_SANDBOX_DIR_NAME = "sandbox"
DOCKER_PUBLIC_DIR_NAME = "public"
DOCKER_WORKSPACE_SKILLS_PATH = "/workspace/skills"
DOCKER_WORKSPACE_PERSONAL_SKILLS_PATH = "/workspace/personal-skills"
DOCKER_WORKSPACE_PUBLIC_DOCS_PATH = "/workspace/public/docs"
DockerMountMapping = tuple[str, str | None, str]
DOCKER_CONTAINER_ONLY_PATH_PREFIXES = ("/tmp", "/proc", "/sys", "/dev", "/root")


def _clean_key_part(value: str | None, fallback_prefix: str) -> str:
    raw = value or f"{fallback_prefix}_{uuid.uuid4().hex[:12]}"
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return cleaned or f"{fallback_prefix}_{uuid.uuid4().hex[:12]}"


def extract_workspace_identity(
    *,
    user_id: str | int | None = None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> tuple[str | int | None, str | None]:
    """Resolve workspace identity from explicit args or user_info."""
    resolved_user_id = user_id
    resolved_user_name = user_name
    if user_info:
        if resolved_user_id is None:
            resolved_user_id = user_info.get("user_id") or user_info.get("id")
        if not resolved_user_name:
            raw_name = user_info.get("user_name") or user_info.get("username")
            resolved_user_name = str(raw_name).strip() if raw_name else None
    if resolved_user_name:
        resolved_user_name = str(resolved_user_name).strip() or None
    return resolved_user_id, resolved_user_name


def resolve_workspace_user_key(
    *,
    user_id: str | int | None,
    user_name: str | None = None,
) -> str:
    """Build a readable, stable workspace directory key: user_name__user_id."""
    from app.services.ai.conversation_identity import require_user_id

    uid_str = require_user_id(user_id)

    raw_name = (user_name or "").strip()
    if raw_name:
        name_part = _clean_key_part(raw_name, "user")
        id_part = _clean_key_part(uid_str, "user")
        return f"{name_part}{WORKSPACE_USER_KEY_SEP}{id_part}"

    return _clean_key_part(uid_str, "user")


def build_workspace_key(trace_id: str | None, conversation_id: str | None = None) -> str:
    trace_part = _clean_key_part(trace_id, "trace")
    if not conversation_id:
        return trace_part
    return f"{trace_part}__{_clean_key_part(conversation_id, 'conversation')}"


def default_workspace_root() -> str:
    for candidate in ("/app/data/agent_workspaces", "data/agent_workspaces"):
        if candidate == "/app/data/agent_workspaces" and not os.path.exists("/app/data"):
            continue
        return os.path.abspath(candidate)
    return os.path.abspath("data/agent_workspaces")


def discover_platform_skill_paths(
    user_info: dict[str, Any] | None = None,
    *,
    skills_custom: bool = False,
    allowed_global_skills: list[str] | None = None,
    forbidden_skill_ids: list[str] | None = None,
) -> list[str]:
    """Collect skill directories: global platform skills + user personal skills.

    When skills_custom is True, only allowlisted global skill ids are included;
    personal skills are always appended (if enabled).

    forbidden_skill_ids 是权限侧的禁用集合：同样只作用于全局技能，个人技能不受影响。
    """
    try:
        from app.core.config import settings

        skills_root = getattr(settings, "SKILLS_DIR", None)
    except Exception:
        return []
    if not skills_root or not os.path.isdir(skills_root):
        return []

    forbidden: set[str] = {
        str(s).strip() for s in (forbidden_skill_ids or []) if str(s).strip()
    }

    allowlist: set[str] | None = None
    if skills_custom:
        allowlist = {str(s).strip() for s in (allowed_global_skills or []) if str(s).strip()}

    paths: list[str] = []
    from app.utils.skill_metadata import is_skill_enabled, parse_skill_frontmatter

    for entry in sorted(os.listdir(skills_root)):
        skill_dir = os.path.join(skills_root, entry)
        if os.path.isdir(skill_dir) and os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
            if allowlist is not None and entry not in allowlist:
                continue
            if entry in forbidden:
                continue
            # 过滤禁用的技能
            meta = parse_skill_frontmatter(entry, os.path.join(skill_dir, "SKILL.md"))
            if not is_skill_enabled(meta):
                continue
            paths.append(os.path.abspath(skill_dir))

    # 追加用户个人技能路径
    if user_info:
        try:
            from app.services.ai.skill_resolver import get_user_personal_skills_dir

            personal_dir = get_user_personal_skills_dir(user_info)
            if personal_dir and os.path.isdir(personal_dir):
                # 个人技能源需兼容历史遗留的 .seed 布局：否则这些技能不会进入
                # skill_paths，AgentScope 既不会预铺、也不会把它们注入模型上下文
                # （比"列表为空"更严重——模型会以为自己没有这个技能）。
                from app.services.ai.skill_resolver import iter_personal_skill_dirs

                for entry, skill_dir in iter_personal_skill_dirs(personal_dir):
                    skill_md = os.path.join(skill_dir, "SKILL.md")
                    if not os.path.isfile(skill_md):
                        continue
                    meta = parse_skill_frontmatter(entry, skill_md)
                    if not is_skill_enabled(meta):
                        continue
                    abs_path = os.path.abspath(skill_dir)
                    if abs_path not in paths:
                        paths.append(abs_path)
        except Exception as exc:
            logger.debug("[workspace] Failed to load personal skill paths: %s", exc)

    return paths


async def resolve_workspace_root(*, ensure_exists: bool = True) -> str:
    try:
        from app.services.config_service import ConfigService

        raw = await ConfigService.get("agentscope_workspace_root")
        if raw:
            configured = os.path.abspath(str(raw))
            # 个人技能源由 default_workspace_root() 拼接（不读该配置），二者不一致时
            # 沙箱工作区与个人技能目录会指向不同位置 → 技能列表为空、写入写到别处。
            fallback_root = default_workspace_root()
            if configured != fallback_root:
                logger.warning(
                    "[workspace] agentscope_workspace_root=%s 与个人技能源所用的默认工作区根 %s "
                    "不一致：沙箱工作区会用配置值，而个人技能仍按默认根拼接，可能导致个人技能列表为空。"
                    "生产环境请勿修改该配置，除非同时迁移 agent_workspaces 目录。",
                    configured,
                    fallback_root,
                )
            return configured
    except Exception as exc:
        logger.warning("[workspace] Failed to load agentscope_workspace_root: %s", exc)
    root = default_workspace_root()
    if ensure_exists:
        os.makedirs(root, exist_ok=True)
    return root


def resolve_user_sessions_dir(
    *,
    root: str,
    user_id: str | int | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> str:
    """用户级会话目录容器：agent_workspaces/{user_key}/sessions。"""
    resolved_user_id, resolved_user_name = extract_workspace_identity(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    uid = resolve_workspace_user_key(
        user_id=resolved_user_id,
        user_name=resolved_user_name,
    )
    return os.path.join(os.path.abspath(root), uid, USER_SESSIONS_DIR_NAME)


def resolve_legacy_session_workdir(
    *,
    root: str,
    user_id: str | int | None,
    conversation_id: str,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> str:
    """旧版会话目录：agent_workspaces/{user_key}/{conversation_id}（兼容历史数据）。"""
    resolved_user_id, resolved_user_name = extract_workspace_identity(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    uid = resolve_workspace_user_key(
        user_id=resolved_user_id,
        user_name=resolved_user_name,
    )
    cid = _clean_key_part(conversation_id, "conversation")
    return os.path.join(os.path.abspath(root), uid, cid)


def resolve_session_workdir(
    *,
    root: str,
    user_id: str | int | None,
    conversation_id: str,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> str:
    resolved_user_id, resolved_user_name = extract_workspace_identity(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    uid = resolve_workspace_user_key(
        user_id=resolved_user_id,
        user_name=resolved_user_name,
    )
    cid = _clean_key_part(conversation_id, "conversation")
    return os.path.join(os.path.abspath(root), uid, USER_SESSIONS_DIR_NAME, cid)


def resolve_user_docs_dir(
    *,
    root: str,
    user_id: str | int | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> str:
    """用户级文档目录：agent_workspaces/{user_key}/docs（跨会话集中存放 AI 落盘文件）。"""
    resolved_user_id, resolved_user_name = extract_workspace_identity(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    uid = resolve_workspace_user_key(
        user_id=resolved_user_id,
        user_name=resolved_user_name,
    )
    return os.path.join(os.path.abspath(root), uid, USER_DOCS_DIR_NAME)


def resolve_user_workspace_root(
    *,
    root: str,
    user_id: str | int | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> str | None:
    """Return the per-user workspace root when it exists on disk."""
    resolved_user_id, resolved_user_name = extract_workspace_identity(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    user_key = resolve_workspace_user_key(
        user_id=resolved_user_id,
        user_name=resolved_user_name,
    )
    user_root = os.path.normpath(os.path.join(os.path.abspath(root), user_key))
    if os.path.isdir(user_root):
        return user_root
    return None


def _resolve_identity_user_id(user_info: dict | None) -> Any:
    """从原始 ``user_info`` 里取用户主键。

    生产侧 ``current_user_info`` 塞进 contextvar 的是**原始** user_info（键为
    ``user_id`` / ``user_name`` / ``role``），因此键顺序必须包含 ``user_id``——
    此前只找 ``u_id_val`` / ``id``，这个回退实际是死代码，只能靠 agent ctx 兜底。
    """
    info = user_info or {}
    return info.get("u_id_val") or info.get("id") or info.get("user_id") or info.get("uid")


def _sanitize_skill_dir_name(name: str) -> str:
    """Replicate AgentScope's ``_sanitize_dir_name`` for pre-seeded layout.

    Allowed characters: ASCII letters/digits/underscore (``\\w``), CJK
    unified ideographs (一-鿿), and hyphens. Everything else becomes ``_``.
    Must stay byte-for-byte identical to the third-party implementation so
    the pre-seeded directory names match what AgentScope would produce.
    """
    return re.sub(r"[^\w一-鿿-]", "_", name)


def _hardlink_or_copy2(src: str, dst: str) -> None:
    """copytree ``copy_function``: always a real copy.

    历史上这里优先 ``os.link``（硬链接），让 400+ 个会话共享同一份物理文件以节省磁盘。
    但会话 workdir 位于**用户私有可写根**内，而硬链接与源技能共享 inode：模型或用户对
    会话副本的 ``Write`` / ``Edit`` / ``Bash >`` 会直接改写**平台技能源文件**，影响所有用户。
    只读约定挡不住同 inode 的写入，因此改为真实复制（``copy2``，保留 mtime）。
    代价是每会话一份物理副本、磁盘占用上升——这是为正确性付出的、已确认接受的代价。
    """
    shutil.copy2(src, dst)


def _forbidden_skill_hashes(forbidden_skill_ids: Iterable[str] | None) -> set[str]:
    """被禁技能在会话快照里的内容哈希指纹。

    为什么以**禁用集合**为权威、而不是"当前请求可用的技能集合"：``workdir`` 是会话级
    共享的，而 ``skills_fp`` 是按智能体区分的（cache_key 含它），同一个会话里不同智能体
    各有自己的可用集合。拿当前请求的可用集合去删共享目录，会把**别的智能体**铺好的技能
    一起删掉（已实际发生过的缺陷）。禁用集合才是本功能真正要清的目标。

    源目录已不存在的技能直接跳过——拿不到指纹时宁可留着，也不能猜着删。
    """
    hashes: set[str] = set()
    try:
        from app.core.config import settings

        root = getattr(settings, "SKILLS_DIR", None)
    except Exception:
        return hashes
    if not root:
        return hashes
    for skill_id in forbidden_skill_ids or ():
        skill_id = str(skill_id).strip()
        if not skill_id:
            continue
        try:
            with open(os.path.join(root, skill_id, "SKILL.md"), "rb") as fh:
                content = fh.read().decode("utf-8")
        except Exception as exc:
            logger.warning("[workspace] Cannot hash forbidden skill %s for pruning: %s", skill_id, exc)
            continue
        hashes.add(hashlib.sha256(content.encode("utf-8")).hexdigest())
    return hashes


def _personal_skill_hash_index(user_info: dict | None) -> dict[str, set[str]]:
    """当前用户**启用中**的个人技能：``技能 id -> {SKILL.md 内容哈希}``。

    用途见 :func:`_entry_is_forbidden`：账本里只记了 ``skill_id``，而个人技能可以与公共
    技能同名（产品口径是"禁的是平台发布的那一份，用户自建的同名技能照旧可用"）。要判断
    某条账本记录是不是"同 id 的个人副本"，只能拿内容哈希比：个人副本的哈希正常情况下就
    与平台那一份不同，被禁/改版之后更不会相等。

    只收**启用中**的个人技能：个人技能自己写了 ``enabled: false`` 时同样不该被这个保护伞
    留下（沙箱侧靠 ``skill_paths`` 过滤，这里靠哈希索引过滤）。
    """
    hashes: dict[str, set[str]] = {}
    if not user_info:
        return hashes
    try:
        from app.services.ai.skill_resolver import (
            get_user_personal_skills_dir,
            iter_personal_skill_dirs,
        )

        personal_dir = get_user_personal_skills_dir(user_info)
        if not personal_dir or not os.path.isdir(personal_dir):
            return hashes
        from app.utils.skill_metadata import is_skill_enabled, parse_skill_frontmatter

        for entry, skill_dir in iter_personal_skill_dirs(personal_dir):
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            if not is_skill_enabled(parse_skill_frontmatter(entry, skill_md)):
                continue
            with open(skill_md, "rb") as fh:
                content = fh.read().decode("utf-8", errors="ignore")
            key = str(entry).strip()
            if key:
                hashes.setdefault(key, set()).add(
                    hashlib.sha256(content.encode("utf-8")).hexdigest()
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] Failed to index personal skills for pruning: %s", exc)
        return {}
    return hashes


def _disabled_platform_skill_ids(skill_ids: Iterable[str]) -> set[str]:
    """给定 id 里，被管理端 ``enabled: false`` 禁用的**公共**技能。

    管理端「禁用」改的是 SKILL.md 的 frontmatter，**不进**权限禁用集合
    （``forbidden_skills``），因此只看权限集合的清理路径完全看不到它：沙箱侧靠公共技能库
    内容指纹失效能修掉，但宿主会话目录（``<workdir>/skills/.seed``）里的旧副本会一直留着，
    模型用 Read/Glob 以相对路径就能读到。这里把两种"禁用"渠道合成同一个「对任何智能体都
    不可用」的判据。

    只解析调用方给的 id（通常就是会话账本里那几个），不做全库扫描。
    """
    disabled: set[str] = set()
    wanted = {str(item).strip() for item in (skill_ids or ()) if str(item).strip()}
    if not wanted:
        return disabled
    try:
        from app.core.config import settings

        root = getattr(settings, "SKILLS_DIR", None)
    except Exception:  # noqa: BLE001
        return disabled
    if not root:
        return disabled
    from app.utils.skill_metadata import is_skill_enabled, parse_skill_frontmatter

    for skill_id in wanted:
        skill_md = os.path.join(str(root), skill_id, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        try:
            if not is_skill_enabled(parse_skill_frontmatter(skill_id, skill_md)):
                disabled.add(skill_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[workspace] Failed to read enabled state of %s: %s", skill_id, exc)
    return disabled


def _entry_is_forbidden(
    entry: Any,
    forbidden_ids: set[str],
    *,
    personal_hashes: dict[str, set[str]] | None = None,
) -> bool:
    """索引条目是否**确定**属于被禁的公共技能。

    只按内容哈希判定会连坐个人技能：把平台技能目录逐字节复制成个人技能后哈希相同，
    那个会话快照会被静默删除，而产品口径是"禁的是平台发布的那一份"、个人技能照旧可用。
    索引里记了来源 ``skill_id`` 就按 id 复核；旧索引没有该字段时退回哈希判定，
    行为与改动前保持一致（不会因为升级而漏清理）。

    ``personal_hashes``（见 :func:`_personal_skill_hash_index`）给出"同 id 的个人副本"的
    内容哈希：命中就**不算**被禁。**判定以 id 为权威、内容哈希只用于区分来源**——此前要求
    "账本哈希 == 该技能当前文件的哈希"才是被禁，于是"技能内容改过之后才禁用"永远清不掉
    （账本记的是播种时的哈希，两侧对不上），会话里会永久残留一份可读的被禁技能。
    """
    if not forbidden_ids:
        return False
    entry = entry or {}
    recorded = str(entry.get("skill_id") or "").strip()
    if not recorded:
        return True
    if recorded not in forbidden_ids:
        return False
    entry_hash = str(entry.get("hash") or "")
    if entry_hash and entry_hash in (personal_hashes or {}).get(recorded, set()):
        return False
    return True


def _prune_stale_session_skills(
    skill_root: str,
    index_path: str,
    forbidden_hashes: set[str] | None,
    unavailable_skill_ids: Iterable[str] | None = None,
    *,
    personal_hashes: dict[str, set[str]] | None = None,
) -> int:
    """清理会话技能快照里**已不可用**的技能，返回清理条数。

    权限可以在进程存活期间被收紧，而"权限收紧之前"铺好的技能会一直留在会话目录里，
    agent 直接读文件就绕过了禁用。这里的 ``unavailable_skill_ids`` 是「对任何智能体都
    不可用」的公共技能 id 集合，由调用方合成两条渠道：

    * 权限禁用集合（``forbidden_skills``，用户级 + 角色级）；
    * 管理端 ``enabled: false``（frontmatter）——见 :func:`_disabled_platform_skill_ids`。

    **判定以来源 id 为权威**：账本里记了 ``skill_id``（= 源目录 basename）就按 id 删，
    内容哈希只用来区分"同 id 的个人副本"（见 :func:`_entry_is_forbidden`）。早期实现要求
    "账本哈希 == 该技能**当前**文件哈希"，于是「内容改过之后才禁用」两侧永远对不上，
    会话里会永久残留一份可读的被禁技能；反过来只认哈希也会因为改名/改版而漏清。
    老账本（无 ``skill_id``）没有来源信息，只能退回内容哈希判定，与改动前一致。

    ``forbidden_hashes`` 为空时**不再直接返回 0**：来源 id 已知的条目不需要哈希也能清
    （源文件暂时读不出来时尤其重要）。两条依据都为空才什么都不做。
    """
    unavailable_ids = {
        str(skill_id).strip()
        for skill_id in (unavailable_skill_ids or ())
        if str(skill_id).strip()
    }
    if not forbidden_hashes and not unavailable_ids:
        return 0

    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.warning("[workspace] Failed to read session skills ledger for pruning: %s", exc)
        return 0

    stored = data.get("skills")
    if not isinstance(stored, dict):
        return 0

    stale = []
    for dir_name, entry in stored.items():
        if not _entry_is_forbidden(
            entry, unavailable_ids, personal_hashes=personal_hashes
        ):
            continue
        # 老账本没有 skill_id：只能退回内容哈希判定（与改动前一致）
        if not str((entry or {}).get("skill_id") or "").strip():
            entry_hash = str((entry or {}).get("hash") or "")
            if entry_hash not in (forbidden_hashes or set()):
                continue
        stale.append(dir_name)
    if not stale:
        return 0

    for dir_name in stale:
        shutil.rmtree(os.path.join(skill_root, dir_name), ignore_errors=True)
        stored.pop(dir_name, None)

    # 账本必须同步，否则下次播种会以为内容仍在而跳过复制，留下悬空条目
    try:
        data["skills"] = stored
        with open(index_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except Exception as exc:
        logger.warning("[workspace] Failed to sync session skills ledger after pruning: %s", exc)

    logger.info("[workspace] Pruned %d unavailable session skill(s): %s", len(stale), stale)
    return len(stale)


def _prune_removed_session_skills(
    skill_root: str,
    skills: dict[str, dict[str, str]],
    current_skill_ids: set[str],
    *,
    source_roots: Iterable[str] | None = None,
) -> list[str]:
    """清理会话技能快照里**来源目录确实已不存在**的技能，返回被清理的目录名。

    播种逻辑只做「新增/改版」，从不删：公共技能被删除（或下架）之后，旧副本会一直留在
    会话的 ``.seed`` 里，模型一个 ``ls`` 就能读到甚至执行——平台侧已经认为「这个技能
    不存在」，会话目录里却还在。这里按账本里的 ``skill_id``（= 来源目录 basename）判断
    来源是否还在。

    **判据刻意不是「不在本次可用集合里」**（那是上一版实现，被现有契约测试否掉）：
    同一会话里不同智能体各有自己的可用集合，窄白名单那次播种绝不能删掉别的智能体铺下的
    技能（``test_preseed_does_not_delete_another_agents_skills_in_the_same_session``）。
    因此只有「既不在本次可用集合里、**且**在所有已知技能源根下都找不到」才删——真正被
    删除的技能落在这里，白名单收窄与源目录暂时读不到都不会。

    只动**账本里登记过**的条目：``.seed`` 下未登记的目录可能来自别处（历史数据或
    人工放置），不属于本函数的职责范围，宁可不删。``skill_id`` 缺失的老账本条目同样
    跳过——无法判断来源就不能删。

    ``current_skill_ids`` 为空集表示"本次没有可用集合可参考"（缓存命中路径就是这种情形：
    那里只拿到了会话，没有该智能体的白名单），此时**只按来源是否存在**判定——删一个条目
    仍然要求它在所有已知技能源根下都找不到，因此不会误伤同会话里别的智能体的技能。
    """
    if not skills:
        # 没有任何账本条目时一律不动。
        return []
    roots = [
        str(root)
        for root in (source_roots or ())
        if root and os.path.isdir(str(root))
    ]
    if not roots:
        return []

    def _source_exists(skill_id: str) -> bool:
        for root in roots:
            # 来源目录**存在**即算"技能仍在"：SKILL.md 暂时读不到（正在复制、IO 抖动、
            # 权限位异常）时宁可留着等下次再判，也不能把会话副本误删成"技能消失"。
            # 真正被删除的技能连目录都不会剩（删除路径走 rmtree）。
            if os.path.isdir(os.path.join(root, skill_id)):
                return True
            # 个人技能源可能仍是历史 ``.seed`` 布局（见 skill_resolver.iter_personal_skill_dirs）
            if os.path.isdir(
                os.path.join(root, SANDBOX_SKILL_SEED_DIR_NAME, skill_id)
            ):
                return True
        return False

    stale = []
    for dir_name, entry in skills.items():
        skill_id = str((entry or {}).get("skill_id") or "").strip()
        if not skill_id:
            continue
        if current_skill_ids and skill_id in current_skill_ids:
            continue
        if _source_exists(skill_id):
            continue
        stale.append(dir_name)
    if not stale:
        return []
    for dir_name in stale:
        shutil.rmtree(os.path.join(skill_root, dir_name), ignore_errors=True)
        skills.pop(dir_name, None)
    logger.info(
        "[workspace] Pruned %d session skill(s) whose source disappeared: %s",
        len(stale),
        stale,
    )
    return stale


def _session_skill_source_roots(user_info: dict | None) -> list[str]:
    """会话技能快照的可能来源根：平台公共技能库 + 当前用户的个人技能库。

    用于判断"账本里这条记录的来源还在不在"（见 :func:`_prune_removed_session_skills`）。
    只列**存在**的目录——任何根都读不到时那个函数会直接放弃清理，宁可少清一次。
    """
    roots: list[str] = []
    try:
        from app.core.config import settings

        skills_dir = getattr(settings, "SKILLS_DIR", None)
        if skills_dir and os.path.isdir(str(skills_dir)):
            roots.append(str(skills_dir))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] Failed to resolve platform skills root: %s", exc)
    try:
        from app.utils.fs_access import get_platform_skills_root

        platform_root = get_platform_skills_root()
        if platform_root and os.path.isdir(str(platform_root)):
            roots.append(str(platform_root))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] Failed to resolve platform skills root: %s", exc)
    if user_info:
        try:
            from app.services.ai.skill_resolver import get_user_personal_skills_dir

            personal_dir = get_user_personal_skills_dir(user_info)
            if personal_dir and os.path.isdir(str(personal_dir)):
                roots.append(str(personal_dir))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[workspace] Failed to resolve personal skills root: %s", exc)
    # 去重但保持顺序
    return list(dict.fromkeys(roots))


def _prune_removed_session_skills_from_ledger(
    workdir: str, index_path: str, user_info: dict | None
) -> int:
    """缓存命中路径上的"来源已消失"清理（配合 :func:`_prune_removed_session_skills`）。

    为什么必须在这条路径上也清：工作区缓存命中时**不会**走播种逻辑，而"删除/下架技能"
    既不进权限禁用集合、也不改会话目录，于是源目录已消失的技能会永久留在会话 ``.seed``
    里（模型 `Glob("**/SKILL.md")` 就能翻出来）。判据只有一条——来源目录在所有已知技能
    源根下都不存在，因此不需要该智能体的白名单（传空集合即可）。
    """
    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.debug("[workspace] Failed to read session skills ledger: %s", exc)
        return 0
    skills = data.get("skills")
    if not isinstance(skills, dict):
        return 0
    roots = _session_skill_source_roots(user_info)
    if not roots:
        return 0
    removed = _prune_removed_session_skills(
        _session_skill_seed_dir(workdir),
        skills,
        set(),
        source_roots=roots,
    )
    if not removed:
        return 0
    try:
        data["skills"] = skills
        with open(index_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except Exception as exc:
        logger.warning(
            "[workspace] Failed to sync session skills ledger after removal pruning: %s", exc
        )
    return len(removed)


async def _prune_forbidden_session_skills(workdir: str, user_info: dict | None) -> None:
    """在工作区缓存命中时也清一次会话快照里已不可用的技能。

    工作区缓存的 key 是 ``workdir::skills_fp::policy``，命中即直接 return，不会走播种逻辑；
    而权限可以在进程存活期间被收紧，于是"禁用前铺好的快照"会一直留在磁盘上，被 Read/Glob
    或 Bash 直接读到——禁用形同虚设。这里补上这一半，覆盖三种"平台已认为不可用"：

    ① 权限禁用（``forbidden_skills``，用户级 + 角色级）；
    ② 管理端 ``enabled: false``（frontmatter，不进权限集合，原先完全漏掉）；
    ③ 来源目录已被删除/下架（原先只有播种路径会清，缓存命中时永久残留）。
    """
    skill_root = _session_skill_seed_dir(workdir)
    index_path = _session_skills_ledger_path(workdir)
    legacy_index_path = _legacy_session_skills_index_path(workdir)
    if not os.path.isfile(index_path) and not os.path.isfile(legacy_index_path):
        return
    prune_index_path = index_path if os.path.isfile(index_path) else legacy_index_path
    # ③ 与权限无关，先做：即便权限查询失败也不影响"源已删除"这条硬事实
    _prune_removed_session_skills_from_ledger(workdir, prune_index_path, user_info)
    try:
        from app.services.ai.skills.policy import get_forbidden_skill_ids

        forbidden_skill_ids = await get_forbidden_skill_ids(_resolve_identity_user_id(user_info))
    except Exception as exc:
        logger.warning("[workspace] Failed to resolve forbidden skills for pruning: %s", exc)
        forbidden_skill_ids = set()
    # ② 管理端禁用只解析账本里出现过的 id，不做全库扫描
    ledger_ids = _read_session_skills_ledger(workdir)
    unavailable_ids = {
        str(item).strip() for item in (forbidden_skill_ids or ()) if str(item).strip()
    }
    unavailable_ids |= _disabled_platform_skill_ids(
        str((entry or {}).get("skill_id") or "").strip()
        for entry in ledger_ids.values()
    )
    if not unavailable_ids:
        return
    _prune_stale_session_skills(
        skill_root,
        prune_index_path,
        _forbidden_skill_hashes(unavailable_ids),
        unavailable_ids,
        personal_hashes=_personal_skill_hash_index(user_info),
    )
    _prune_session_skill_partitions(workdir)


def _session_skill_seed_dir(workdir: str) -> str:
    """平台维护的技能快照目录：AgentScope 的 seed 模板（``<workdir>/skills/.seed``）。

    平台刻意**只维护 seed**、不写平铺技能：平铺会触发 AgentScope 的
    ``_migrate_skill_layout``，它每次 ``initialize()`` 把平铺搬进 ``.seed`` 的同时还会吃掉
    平台的账本，两边来回打架。seed 非空时 ``_setup_skills`` 直接跳过、迁移也无事可做。
    """
    return os.path.join(workdir, "skills", SANDBOX_SKILL_SEED_DIR_NAME)


def _session_skills_ledger_path(workdir: str) -> str:
    return os.path.join(workdir, SESSION_SKILLS_LEDGER_NAME)


def _legacy_session_skills_index_path(workdir: str) -> str:
    return os.path.join(workdir, "skills", LEGACY_SESSION_SKILLS_INDEX_NAME)


def _read_session_skills_ledger(workdir: str) -> dict[str, dict[str, str]]:
    """读平台账本；老会话的账本仍在 ``skills/.skills``（读取回退，不丢已铺技能）。"""
    for path in (
        _session_skills_ledger_path(workdir),
        _legacy_session_skills_index_path(workdir),
    ):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.warning("[workspace] Failed to parse session skills ledger %s: %s", path, exc)
            continue
        stored = data.get("skills")
        if isinstance(stored, dict):
            return {
                str(dir_name): {
                    "hash": str(entry.get("hash", "")),
                    "skill_name": str(entry.get("skill_name", "")),
                    # 保留来源技能 id（旧账本没有这个字段，读取时为 ""）
                    "skill_id": str(entry.get("skill_id", "")),
                }
                for dir_name, entry in stored.items()
                if isinstance(entry, dict)
            }
    return {}


def _is_agentscope_skill_partition(skills_dir: str, entry: str) -> bool:
    """``skills/<entry>`` 是否是 AgentScope 装备出来的分区（带它自己的 ``.index``）。"""
    return os.path.isfile(os.path.join(skills_dir, entry, ".index"))


def _migrate_flat_session_skills(workdir: str) -> int:
    """把老会话的平铺技能搬进 ``.seed``（由平台自己完成 AgentScope 的迁移）。

    平铺目录会让 AgentScope 每次 ``initialize()`` 都搬一次，并在搬的同时丢弃平台的账本；
    平台先搬完，它的迁移就无事可做。目录名保持不变（``_1`` 之类的区分继续有效）。
    """
    skills_dir = os.path.join(workdir, "skills")
    if not os.path.isdir(skills_dir):
        return 0
    seed_dir = _session_skill_seed_dir(workdir)
    moved = 0
    for entry in sorted(os.listdir(skills_dir)):
        if entry.startswith(".") or _is_agentscope_skill_partition(skills_dir, entry):
            continue
        path = os.path.join(skills_dir, entry)
        if not os.path.isdir(path) or not os.path.isfile(os.path.join(path, "SKILL.md")):
            continue
        os.makedirs(seed_dir, exist_ok=True)
        target = os.path.join(seed_dir, entry)
        try:
            if os.path.exists(target):
                # seed 里已有同名条目（平台新铺过）：以 seed 为准，丢掉旧平铺副本
                shutil.rmtree(path)
            else:
                shutil.move(path, target)
            moved += 1
        except OSError as exc:
            logger.warning("[workspace] Failed to move flat session skill %s: %s", path, exc)
    if moved:
        logger.info("[workspace] Migrated %d flat session skill(s) into %s", moved, seed_dir)
    return moved


def _prune_session_skill_partitions(workdir: str) -> int:
    """删掉过期的 AgentScope 分区（含其 ``.index``）。

    分区是 ``.seed`` 的整份副本，seed 一变它就过期。当前生产没有任何调用方读取技能清单
    （见 ``tests/ai/runtime/test_agentscope_workspace_toolkit.py`` 的防回归契约），删掉只省
    磁盘；若将来接回清单，AgentScope 会在 ``list_skills()`` 的 reconcile 里重新装备。
    """
    skills_dir = os.path.join(workdir, "skills")
    if not os.path.isdir(skills_dir):
        return 0
    removed = 0
    for entry in sorted(os.listdir(skills_dir)):
        if entry.startswith(".") or not _is_agentscope_skill_partition(skills_dir, entry):
            continue
        try:
            shutil.rmtree(os.path.join(skills_dir, entry))
            removed += 1
        except OSError as exc:
            logger.warning("[workspace] Failed to drop stale skill partition %s: %s", entry, exc)
    if removed:
        logger.info("[workspace] Dropped %d stale session skill partition(s)", removed)
    return removed


def _same_origin_skill_dir(
    existing: dict[str, dict[str, str]], skill_id: str
) -> str | None:
    """在索引里找同一个来源技能（``skill_id``）已占用的目录名。

    索引里的 ``skill_id`` 是后加的字段，老会话没有（读到空串）——那种情况只能走
    「新技能」分支，靠 ``--prune-stale`` 之类的清理重建，而不是在这里猜。
    """
    if not skill_id:
        return None
    for dir_name, entry in existing.items():
        if entry.get("skill_id") == skill_id:
            return dir_name
    return None


def _preseed_session_skills(
    workdir: str,
    skill_paths: list[str],
    forbidden_skill_ids: Iterable[str] | None = None,
    user_info: dict | None = None,
) -> None:
    """把平台技能快照铺进 ``<workdir>/skills/.seed``（AgentScope 的 seed 模板）。

    AgentScope 的 ``LocalWorkspace.initialize`` 会把 ``skill_paths`` 逐个 ``copytree`` 进会话
    目录（每会话一份完整物理副本），并在这一过程中把平铺技能迁移进 ``.seed``、顺手丢掉平台
    写在 ``skills/`` 里的账本。平台改为**直接维护 ``.seed``**：seed 非空时 ``_setup_skills``
    直接跳过复制、迁移也无事可做，两边不再来回打架。

    平台账本放在会话根（``<workdir>/.session_skills.json``）而不是 ``skills/`` 里：后者会被
    AgentScope 当成技能条目处理（``.skills`` 就曾被它的迁移逻辑吃掉）。

    幂等：每次调用都按内容哈希做差集，只有新增/改版技能会被复制进来；老会话的平铺布局由
    ``_migrate_flat_session_skills`` 一次性搬进 ``.seed``。

    同源改版就地替换：技能按来源 id（账本里的 ``skill_id``）匹配，改版时复用原目录名，而不是
    把旧副本留成 ``<name>_1``。

    隔离：播种始终做真实复制（见 ``_hardlink_or_copy2``）。早年用硬链接共享 inode，从会话副本
    写入会顺着 inode 改到平台技能源、影响所有用户；省下的磁盘不值得这个代价。

    技能一变，AgentScope 已装备的分区副本就过期了，平台顺手删掉它（见
    ``_prune_session_skill_partitions``）：当前生产没有任何调用方读技能清单，删掉只省磁盘，
    将来接回时 AgentScope 会自己重新装备。
    """
    seed_dir = _session_skill_seed_dir(workdir)
    index_path = _session_skills_ledger_path(workdir)
    legacy_index_path = _legacy_session_skills_index_path(workdir)
    # 账本还在老位置（skills/.skills）就顺手搬过来——留在那儿迟早被 AgentScope 的迁移吃掉。
    ledger_moved = os.path.isfile(legacy_index_path) and not os.path.isfile(index_path)
    migrated = _migrate_flat_session_skills(workdir)
    os.makedirs(seed_dir, exist_ok=True)

    # 先清掉"禁用之前"铺进来的技能（直接读文件即可绕过禁用），再走下面的 hash 差集——不能
    # 早退。早退会让新增/改版技能只能等 AgentScope 自己复制，而它按 frontmatter name 命名，
    # 改版时会留下 <name>_1 旧副本。账本可能还在老位置，按实际存在的那份清理。
    pruned = 0
    prune_index_path = index_path if os.path.isfile(index_path) else legacy_index_path
    if os.path.isfile(prune_index_path):
        # 两种"禁用"都要清：权限禁用集合 + 管理端 enabled=false（原先只有前者）。
        # 同 id 的个人技能靠内容哈希识别并保留（见 _entry_is_forbidden）。
        unavailable_ids = {
            str(item).strip() for item in (forbidden_skill_ids or ()) if str(item).strip()
        }
        unavailable_ids |= _disabled_platform_skill_ids(
            str((entry or {}).get("skill_id") or "").strip()
            for entry in _read_session_skills_ledger(workdir).values()
        )
        pruned = _prune_stale_session_skills(
            seed_dir,
            prune_index_path,
            _forbidden_skill_hashes(unavailable_ids),
            unavailable_ids,
            personal_hashes=_personal_skill_hash_index(user_info),
        )

    # 以**清理之后**的账本为权威重新读：否则刚被删掉的被禁条目会被内存里的旧副本写回去
    # （账本可能仍在老位置，读取自带回退）
    existing = _read_session_skills_ledger(workdir)
    existing_hashes: set[str] = {e.get("hash", "") for e in existing.values()}
    existing_agent_names: set[str] = {e.get("skill_name", "") for e in existing.values()}
    existing_dir_names: set[str] = set(existing.keys())

    # Parse SKILL.md frontmatter the same way AgentScope's frontmatter.loads
    # does, so `name` and the content hash match exactly.
    try:
        import frontmatter
    except Exception:
        frontmatter = None

    updated = False
    for skill_path in skill_paths:
        skill_md_path = os.path.join(skill_path, "SKILL.md")
        try:
            with open(skill_md_path, "rb") as fh:
                raw = fh.read()
            content_str = raw.decode("utf-8")
        except Exception as exc:
            logger.warning("[workspace] Pre-seed skip unreadable skill %s: %s", skill_path, exc)
            continue

        # parse name/description
        name: str | None = None
        if frontmatter is not None:
            try:
                parsed = frontmatter.loads(content_str)
                name = str(parsed.get("name") or "") or None
                description = str(parsed.get("description") or "") or None
            except Exception:
                name = None
                description = None
        else:
            # Fallback: minimal line-based frontmatter parse for name only.
            description = None
            match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content_str, re.DOTALL)
            if match:
                for line in match.group(1).splitlines():
                    if ":" in line:
                        key, value = line.split(":", 1)
                        if key.strip().lower() == "name":
                            name = value.strip().strip('"').strip("'")
        if not name or not description:
            logger.warning(
                "[workspace] Pre-seed skip %s: SKILL.md missing name/description",
                skill_path,
            )
            continue

        skill_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
        # 来源 id：清理被禁技能时要按 id 复核（否则"逐字节复制平台技能"的个人技能会因为
        # 哈希相同被一起删掉），同源改版时也靠它找到旧目录做就地替换。
        skill_id = os.path.basename(os.path.normpath(skill_path))
        origin_dir = _same_origin_skill_dir(existing, skill_id)

        if skill_hash in existing_hashes:
            # 内容没变：顺手补上老索引缺失的 skill_id（自愈，让后续改版能就地替换）
            if origin_dir is None:
                for dir_name, entry in existing.items():
                    if entry.get("hash") == skill_hash and not entry.get("skill_id"):
                        entry["skill_id"] = skill_id
                        updated = True
                        break
            continue

        if origin_dir is not None:
            # 同一个技能改版：复用原目录名就地替换，避免旧副本变成 <name>_1 留在清单里
            entry = existing[origin_dir]
            dest_path = os.path.join(seed_dir, origin_dir)
            if not os.path.realpath(dest_path).startswith(
                os.path.realpath(seed_dir) + os.sep,
            ):
                logger.warning(
                    "[workspace] Pre-seed skip %s: path escapes seed dir", skill_path
                )
                continue
            try:
                if os.path.isdir(dest_path):
                    shutil.rmtree(dest_path)
                elif os.path.exists(dest_path):
                    os.remove(dest_path)
                shutil.copytree(
                    skill_path,
                    dest_path,
                    copy_function=_hardlink_or_copy2,
                    dirs_exist_ok=False,
                )
            except Exception as exc:
                logger.warning(
                    "[workspace] Pre-seed failed to refresh skill %s: %s",
                    skill_path,
                    exc,
                )
                continue
            existing_hashes.discard(entry.get("hash", ""))
            existing_hashes.add(skill_hash)
            entry["hash"] = skill_hash
            entry["skill_id"] = skill_id
            updated = True
            continue

        # Resolve agent-facing name conflict (mirror AgentScope loop)
        agent_name = name
        counter = 1
        while agent_name in existing_agent_names:
            agent_name = f"{name} ({counter})"
            counter += 1

        # Resolve directory name conflict (mirror AgentScope loop)
        base_dir = _sanitize_skill_dir_name(name)
        dir_name = base_dir
        counter = 1
        while dir_name in existing_dir_names or os.path.exists(os.path.join(seed_dir, dir_name)):
            dir_name = f"{base_dir}_{counter}"
            counter += 1

        dest_path = os.path.join(seed_dir, dir_name)
        if not os.path.realpath(dest_path).startswith(
            os.path.realpath(seed_dir) + os.sep,
        ):
            logger.warning("[workspace] Pre-seed skip %s: path escapes seed dir", skill_path)
            continue

        try:
            shutil.copytree(
                skill_path,
                dest_path,
                copy_function=_hardlink_or_copy2,
                dirs_exist_ok=False,
            )
        except Exception as exc:
            logger.warning("[workspace] Pre-seed failed to copy skill %s: %s", skill_path, exc)
            continue

        existing[dir_name] = {
            "hash": skill_hash,
            "skill_name": agent_name,
            "skill_id": skill_id,
        }
        existing_hashes.add(skill_hash)
        existing_agent_names.add(agent_name)
        existing_dir_names.add(dir_name)
        updated = True

    # 来源已经消失的技能（公共技能被删除/下架、个人技能被删）必须从快照里清掉，否则模型
    # 仍能在会话目录里读到平台认为不存在的技能。放在复制之后：这样"改版"走的就地替换路径
    # 已经更新过账本，不会被这里误删。判据是"源目录在磁盘上确实不存在"，不是"不在本次
    # 白名单里"（见函数 docstring）。
    current_skill_ids = {
        os.path.basename(os.path.normpath(str(path)))
        for path in skill_paths
        if str(path).strip()
    }
    source_roots = {
        os.path.dirname(os.path.abspath(str(path)))
        for path in skill_paths
        if str(path).strip()
    }
    try:
        from app.utils.fs_access import get_platform_skills_root

        platform_root = get_platform_skills_root()
    except Exception:  # noqa: BLE001
        platform_root = None
    if platform_root:
        source_roots.add(str(platform_root))
    removed = _prune_removed_session_skills(
        seed_dir, existing, current_skill_ids, source_roots=sorted(source_roots)
    )
    if removed:
        updated = True

    if updated or migrated or pruned or ledger_moved:
        try:
            mtime = os.stat(seed_dir).st_mtime
        except OSError:
            mtime = 0.0
        payload = {"skills_dir_mtime": float(mtime), "skills": existing}
        try:
            with open(index_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, indent=2, ensure_ascii=False))
            logger.info(
                "[workspace] Session skill snapshot synced: %d skill(s) in %s",
                len(existing),
                seed_dir,
            )
        except Exception as exc:
            logger.warning("[workspace] Failed to write session skills ledger %s: %s", index_path, exc)
        if ledger_moved and os.path.isfile(legacy_index_path):
            try:
                os.remove(legacy_index_path)
            except OSError as exc:
                logger.warning(
                    "[workspace] Failed to drop legacy skills index %s: %s",
                    legacy_index_path,
                    exc,
                )
        # 快照变了，AgentScope 已装备的分区副本随之过期（当前无人读，只省磁盘）。
        # pruned 也要算：只清 .seed 不清分区，被禁技能的副本会继续留在会话目录里可读。
        if updated or migrated or pruned:
            _prune_session_skill_partitions(workdir)


SANDBOX_POLICY_LOCAL = "local"
SANDBOX_POLICY_DOCKER = "docker"
SANDBOX_POLICY_K8S = "k8s"
SANDBOX_POLICY_E2B = "e2b"
SANDBOX_POLICY_SSH = "ssh"
DOCKER_WORKSPACE_IDLE_SECONDS = 15 * 60
DOCKER_WORKSPACE_REAPER_INTERVAL_SECONDS = 60
KNOWN_SANDBOX_POLICIES = frozenset(
    {
        SANDBOX_POLICY_LOCAL,
        SANDBOX_POLICY_DOCKER,
        SANDBOX_POLICY_K8S,
        SANDBOX_POLICY_E2B,
        SANDBOX_POLICY_SSH,
    }
)

#: 沙箱策略 → Bash 看到的逻辑工作区根。``local`` 不在表里：它没有逻辑根，
#: Bash 与宿主文件工具共用后端真实路径。
#:
#: - docker / k8s：平台把宿主用户工作区挂到 ``/workspace``；
#: - e2b：AgentScope ``E2BWorkspace`` 的 workdir（``_e2b/_constants.SANDBOX_WORKDIR``），
#:   平台没有覆盖它，所以是 ``/home/user/workspace`` 而不是 ``/workspace``；
#: - ssh：取系统配置 ``sandbox_ssh_remote_workdir``（默认 ``/workspace``），
#:   由 :func:`resolve_sandbox_logical_root` 异步读取。
SANDBOX_POLICY_LOGICAL_ROOTS: dict[str, str] = {
    SANDBOX_POLICY_DOCKER: DOCKER_WORKSPACE_LOGICAL_ROOT,
    SANDBOX_POLICY_K8S: DOCKER_WORKSPACE_LOGICAL_ROOT,
    SANDBOX_POLICY_E2B: "/home/user/workspace",
}

#: 沙箱内技能目录的布局形态（技能根固定是 ``<逻辑根>/skills``）：
#:
#: - ``seed``：AgentScope 工作区基类的 ``skills/.seed`` 模板 + 各智能体分区。
#:   docker / k8s 由平台自己维护 ``.seed``，e2b 交给 AgentScope ``_setup_skills``；
#: - ``flat``：平台自研 ``SshWorkspace`` 的 ``skills/<id>/SKILL.md`` 平铺复制（无 ``.seed``）。
SANDBOX_SKILL_LAYOUTS: dict[str, str] = {
    SANDBOX_POLICY_DOCKER: "seed",
    SANDBOX_POLICY_K8S: "seed",
    SANDBOX_POLICY_E2B: "seed",
    SANDBOX_POLICY_SSH: "flat",
}

#: 个人技能**源目录**以只读方式挂进沙箱的策略。e2b / ssh 只把技能复制进
#: ``skills/`` 副本，没有源目录挂载，提示词不能声称 ``/workspace/personal-skills``。
SANDBOX_PERSONAL_SKILLS_MOUNTED_POLICIES = frozenset(
    {SANDBOX_POLICY_DOCKER, SANDBOX_POLICY_K8S}
)

#: 沙箱内技能目录名（对齐 AgentScope ``DEFAULT_SKILLS_DIR``）。
SANDBOX_SKILLS_DIR_NAME = "skills"


def sandbox_policy_is_sandbox(policy: str | None) -> bool:
    """该策略是否真的把 Bash 放进隔离环境。

    与 ``assemble_step._effective_policy_is_sandbox`` / ``agent_service`` 预检同口径
    （local 以外都算沙箱）。历史教训：这里曾只认 docker/k8s，导致 e2b/ssh 被当成
    「Bash 跑在后端」，提示词给出后端技能路径，与真实执行环境相反。
    """
    return str(policy or "").strip().lower() in (
        SANDBOX_POLICY_DOCKER,
        SANDBOX_POLICY_K8S,
        SANDBOX_POLICY_E2B,
        SANDBOX_POLICY_SSH,
    )


def sandbox_skill_layout(policy: str | None) -> str:
    """沙箱内技能目录布局；``local`` 与未知策略按 ``seed`` 返回（调用方只在沙箱内使用）。"""
    return SANDBOX_SKILL_LAYOUTS.get(str(policy or "").strip().lower(), "seed")


def sandbox_personal_skills_path(policy: str | None) -> str | None:
    """个人技能源目录在沙箱内的只读挂载点；未挂载（e2b/ssh/local）返回 None。"""
    if str(policy or "").strip().lower() not in SANDBOX_PERSONAL_SKILLS_MOUNTED_POLICIES:
        return None
    return DOCKER_WORKSPACE_PERSONAL_SKILLS_PATH


async def resolve_sandbox_logical_root(
    policy: str | None,
    *,
    config_overrides: Mapping[str, Any] | None = None,
) -> str | None:
    """该策略下 Bash 的逻辑工作区根；``local`` 返回 None（Bash 直接用后端真实路径）。

    提示词、目录清单、工具元数据展示统一走这里，别再各自硬编码 ``/workspace``
    ——e2b 的根是 ``/home/user/workspace``，ssh 的根由管理员配置。
    """
    normalized = str(policy or "").strip().lower()
    if normalized == SANDBOX_POLICY_SSH:
        remote = (
            await _sandbox_config_value(
                "sandbox_ssh_remote_workdir",
                DOCKER_WORKSPACE_LOGICAL_ROOT,
                config_overrides,
            )
        ).strip()
        return remote or DOCKER_WORKSPACE_LOGICAL_ROOT
    return SANDBOX_POLICY_LOGICAL_ROOTS.get(normalized)



def _resolve_docker_sandbox_host_workdir(
    workspace_root: str | None,
    sandbox_user_key: str | None,
) -> str | None:
    """计算下发给宿主机 Docker Daemon 的沙箱挂载真实物理路径。

    当平台部署在 Docker 容器内（DooD 架构）时：
    1. 优先读取环境变量 HOST_DATA_DIR / AGENTSCOPE_WORKSPACE_HOST_ROOT；
    2. 若配置了 HOST_DATA_DIR 且容器内 workspace_root 以 /app/data 开头，
       自动将其映射为宿主机对应的真实物理路径；
    3. 宿主机直跑环境下直接使用 os.path.abspath(workspace_root)。
    """
    if not workspace_root or not sandbox_user_key:
        return None

    abs_root = os.path.abspath(workspace_root)
    host_data_dir = (
        os.getenv("HOST_DATA_DIR", "").strip()
        or os.getenv("AGENTSCOPE_WORKSPACE_HOST_ROOT", "").strip()
    )

    if host_data_dir:
        # 如果容器内根路径以 /app/data 开头（标准 Docker 镜像内工作目录）
        if abs_root.startswith("/app/data"):
            rel_part = os.path.relpath(abs_root, "/app/data")
            if rel_part and rel_part != ".":
                return os.path.join(host_data_dir, rel_part, sandbox_user_key)
            return os.path.join(host_data_dir, sandbox_user_key)
        # 如果 host_data_dir 直接作为宿主机 workspace root
        if not abs_root.startswith(host_data_dir):
            return os.path.join(host_data_dir, sandbox_user_key)

    return os.path.join(abs_root, sandbox_user_key)


def _sandbox_skills_host_workdir(root: str, user_key: str, policy: str) -> str:
    """沙箱技能目录 / 指纹文件所在的**工作区根**（必须与创建侧同一口径）。

    为什么单独抽一个函数：DooD（平台自己跑在容器里）部署下，创建沙箱时下发给 Docker
    Daemon 的挂载源是 ``HOST_DATA_DIR`` 映射后的**宿主机物理路径**
    （:func:`_resolve_docker_sandbox_host_workdir`），技能目录、``.seed`` 与指纹文件全都
    写在那个路径上。核对 / 回收侧若图省事用容器内路径 ``<root>/<user_key>``，读到的就是
    **创建侧从不写的那棵树**：``recorded`` 恒为空 → 核对判「最新」→ 本轮新增的权限治理
    在这类部署上静默失效；GC 反过来还会作用在真实的租户目录上而判据取自影子目录。

    K8s 走 PVC 内的容器路径（subPath 由 Daemon 侧解析），不做宿主机映射。
    """
    if str(policy or "") == SANDBOX_POLICY_K8S:
        return os.path.join(os.path.abspath(root), user_key)
    return (
        _resolve_docker_sandbox_host_workdir(root, user_key)
        or os.path.join(os.path.abspath(root), user_key)
    )


def _personal_skills_dir(host_workdir: str) -> str:
    """Return the canonical personal skills source under a user workspace."""
    return os.path.join(os.path.abspath(host_workdir), "skills")


def _sandbox_skills_dir(host_workdir: str, layout: str | None = None) -> str:
    """Return the Docker-only materialized skills directory.

    This directory intentionally contains the merged public + personal skill
    snapshot used by the sandbox. It must never be used as the personal-skill
    API source.

    ``layout`` = 技能集合令牌：每个技能集合一份独立目录，容器名/Pod 名同源。
    共用一份目录时，两个集合的容器会互相 rmtree 对方的 ``.seed``、互相把半成品
    记成"最新"，而且模型能在 ``.seed`` 里读到不属于自己的技能。缺省（``None``）
    即历史单份布局 ``sandbox/skills``。
    """
    name = "skills" if not layout else f"skills-{layout}"
    return os.path.join(
        os.path.abspath(host_workdir),
        DOCKER_SANDBOX_DIR_NAME,
        name,
    )


def _migrate_legacy_sandbox_skills_dir(host_workdir: str, layout: str | None) -> None:
    """把历史单份布局 ``sandbox/skills`` 整体迁移到带令牌的技能目录。

    分片之前所有技能集合共用 ``sandbox/skills``：里面既有 seed 模板，也有 AgentScope
    已装备的分区（agent 用技能 API **自建**的技能就落在分区里，平台技能库里没有它们）。
    直接改用新目录会让这些自建技能凭空消失，所以在该用户**还没有任何带令牌目录**时，
    把历史目录整体改名过来（``os.rename`` 同一文件系统内是原子的；已在运行的容器按
    inode 持有 bind mount，不受影响）。

    已经存在 ``skills-*`` 兄弟目录时不动历史目录：那可能是另一个技能集合正在用的目录，
    删改它等于破坏别人的沙箱。此时退化为各自使用新目录（另见「历史目录不自动清理」）。
    """
    if not layout:
        return
    legacy = _sandbox_skills_dir(host_workdir)
    target = _sandbox_skills_dir(host_workdir, layout)
    if os.path.exists(target) or not os.path.isdir(legacy):
        return
    parent = os.path.dirname(target)
    try:
        siblings = [name for name in os.listdir(parent) if name.startswith("skills-")]
    except OSError:
        return
    if siblings:
        return
    try:
        os.rename(legacy, target)
    except OSError as exc:  # noqa: BLE001
        logger.warning(
            "[workspace] Failed to migrate legacy skills dir %s -> %s: %s",
            legacy,
            target,
            exc,
        )
        return
    logger.info("[workspace] Migrated legacy sandbox skills dir %s -> %s", legacy, target)


def _build_docker_workspace_mounts(
    host_workdir: str,
    public_docs_source: str | None = None,
    layout: str | None = None,
) -> list[tuple[str, str, str]]:
    """Build the Docker bind mounts while keeping source roots separate.

    Three mounts, in order:

    1. the user workspace at ``/workspace`` (rw);
    2. the **merged runtime skills snapshot** at ``/workspace/skills`` (rw) —
       ``<workdir>/sandbox/skills[-<layout>]``, which AgentScope fills with a
       one-off ``.seed`` template. It shadows ``<workdir>/skills`` underneath
       ``/workspace``, so the personal-skill source is *not* reachable there;
    3. the **personal skill source** at ``/workspace/personal-skills`` (ro) —
       mounted read-only so a sandboxed agent can execute a skill's bundled
       scripts straight from the source, without waiting for the next ``.seed``
       rebuild or copying files around first.

    ``layout``（技能集合令牌）让物理沙箱与缓存条目一一对应：不同技能集合的容器
    各自挂载自己的 skills 目录，不再互相覆盖 ``.seed``/分区。
    """
    mounts = [
        (os.path.abspath(host_workdir), DOCKER_WORKSPACE_LOGICAL_ROOT, "rw"),
        (
            _sandbox_skills_dir(host_workdir, layout),
            DOCKER_WORKSPACE_SKILLS_PATH,
            "rw",
        ),
        (
            _personal_skills_dir(host_workdir),
            DOCKER_WORKSPACE_PERSONAL_SKILLS_PATH,
            "ro",
        ),
    ]
    if public_docs_source:
        mounts.append(
            (
                os.path.abspath(public_docs_source),
                DOCKER_WORKSPACE_PUBLIC_DOCS_PATH,
                "ro",
            ),
        )
    return mounts


def _build_sandbox_file_tool_mount_mappings(
    backend_workspace_root: str,
    *,
    public_docs_mounted: bool = True,
) -> list[DockerMountMapping]:
    """Build the Docker-to-backend path mapping for host file tools.

    The user workspace is shared with the Docker container. Public docs, the
    merged runtime skills and the personal-skill source are child mounts and
    therefore must be resolved before the parent ``/workspace`` mount. The
    merged runtime skills mount is container-only for host file tools; callers
    should use the backend skills path from the resource catalog when they need
    to read a platform skill. The personal-skill mount, by contrast, *is* the
    user workspace's ``skills/`` directory, so it maps straight back (read-only)
    and a sandbox path can be handed to Read/Grep unchanged.
    """
    from app.utils.fs_paths import get_data_base_dir

    backend_skills_root = None
    backend_personal_skills_root = os.path.join(
        os.path.abspath(backend_workspace_root), "skills"
    )

    return [
        (
            DOCKER_WORKSPACE_PUBLIC_DOCS_PATH,
            os.path.join(get_data_base_dir(), "docs")
            if public_docs_mounted
            else None,
            "ro",
        ),
        (DOCKER_WORKSPACE_PERSONAL_SKILLS_PATH, backend_personal_skills_root, "ro"),
        (DOCKER_WORKSPACE_SKILLS_PATH, backend_skills_root, "ro"),
        (DOCKER_WORKSPACE_LOGICAL_ROOT, os.path.abspath(backend_workspace_root), "rw"),
    ]


#: AgentScope 的 seed 模板目录名（对应其 ``SKILL_SEED_DIR``）。内容是
#: ``_setup_skills`` 一次性打包出来的技能副本；平台靠下面的指纹比对在公共技能
#: 变更后主动作废它，让下一次 initialize() 重新打包。
SANDBOX_SKILL_SEED_DIR_NAME = ".seed"
#: 上一版 ``.seed`` 的条目名账本（点开头，不会被当成技能条目）
SANDBOX_SKILL_MANIFEST_NAME = ".seed-manifest.json"
#: 会话技能快照的平台账本（``<workdir>/.session_skills.json``）。刻意放在会话根而不是
#: ``skills/`` 里：AgentScope 会把 ``skills/`` 下的非目录条目当成技能条目处理
#: （``.skills`` 就曾被它的迁移逻辑吃掉），账本放在外面才不会被误动。
SESSION_SKILLS_LEDGER_NAME = ".session_skills.json"
#: 账本的历史位置（``skills/.skills``）：只用于读取回退与迁移后清理。
LEGACY_SESSION_SKILLS_INDEX_NAME = ".skills"
#: 上一次打包 seed 时公共技能库的指纹，落在 ``<workdir>/sandbox/`` 下（不进
#: skills 目录，避免被 AgentScope 当成技能条目）。
DOCKER_SANDBOX_SKILLS_FINGERPRINT_NAME = ".public_skills_fingerprint"


def _list_visible_entries(path: str) -> list[str]:
    """列出目录下的非隐藏条目；目录不存在或不可读时返回空列表。"""
    try:
        return sorted(name for name in os.listdir(path) if not name.startswith("."))
    except OSError:
        return []


def _sandbox_skill_manifest_path(host_workdir: str, layout: str | None = None) -> str:
    return os.path.join(
        _sandbox_skills_dir(host_workdir, layout), SANDBOX_SKILL_MANIFEST_NAME
    )


def _write_sandbox_skill_manifest(
    host_workdir: str, entries: list[str], layout: str | None = None
) -> None:
    """记下上一版 ``.seed`` 里都有哪些条目。

    分区同步需要区分「这个目录是 seed 提供的技能」和「这个目录是 agent 自己用技能 API
    建的」——前者在技能被删/被禁用后必须清掉，后者绝不能碰。账本在作废 ``.seed`` 之前
    写入，所以即使 ``.seed`` 已被删除也仍然知道上一版装了哪些。
    """
    path = _sandbox_skill_manifest_path(host_workdir, layout)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _atomic_write_text(
            path, json.dumps({"entries": sorted(entries)}, ensure_ascii=False)
        )
    except OSError as exc:  # noqa: BLE001
        logger.debug("[workspace] Failed to write skill manifest %s: %s", path, exc)


def _read_sandbox_skill_manifest(host_workdir: str, layout: str | None = None) -> set[str]:
    """读取上一版 ``.seed`` 的条目名；缺失或损坏时返回空集合（退化为只认公共技能）。"""
    path = _sandbox_skill_manifest_path(host_workdir, layout)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    entries = data.get("entries")
    if not isinstance(entries, list):
        return set()
    return {str(item) for item in entries if str(item).strip()}


def _atomic_write_text(path: str, content: str) -> None:
    """同目录临时文件 + ``os.replace`` 的原子写。

    治理状态文件（指纹/账本）都是「读一次判一次」的判据：并发读者（同一用户另一会话、
    另一 worker）读到半截 JSON 会退化成"没有账本"，读到空指纹会误判成"已变更"而多一次
    全量重建。Windows 上 ``os.replace`` 对已存在目标也可用，因此不需要额外分支。
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle_fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _dir_tree_fingerprint(root: str | None) -> str | None:
    """目录树的内容指纹；目录不存在时返回 ``None``。

    用「相对路径 + 大小 + mtime」而不是文件内容：技能包可能带几 MB 脚本与二进制资源，
    而容器初始化是热路径，不值得全量读一遍——任何内容改动都会改 mtime。

    刻意跳过点开头条目与 ``__pycache__``（口径与 :func:`_list_visible_entries` 一致）：
    编辑器临时文件与字节码缓存不是技能内容，把它们算进指纹会让"什么都没改"也触发一次
    全量重建。
    """
    if not root or not os.path.isdir(root):
        return None
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and name != "__pycache__"
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root)
            digest.update(
                f"{rel}|{stat.st_size}|{stat.st_mtime_ns}\n".encode("utf-8")
            )
    return digest.hexdigest()


def _platform_public_skills_fingerprint() -> str | None:
    """公共技能库的内容指纹；技能库不可用时返回 ``None``。"""
    from app.utils.fs_access import get_platform_skills_root

    return _dir_tree_fingerprint(get_platform_skills_root())


async def _sandbox_skills_seed_fingerprint(
    host_workdir: str,
    skill_paths: list[str] | None = None,
    layout: str | None = None,
) -> str | None:
    """沙箱技能 seed 的完整指纹 = 技能集合令牌 + 公共技能库 + 个人技能库。

    **为什么不能只 hash 公共技能库**：``.seed`` 里装的是 ``skill_paths``（公共 + 个人）
    减去禁用项，但原先的指纹只覆盖公共库的**文件内容**，于是下面三件事都不会让它失效：

    * **管理员禁用某个公共技能**——只改权限、不动技能文件，指纹不变；
    * **个人技能被删除或改版**——个人技能库根本不在指纹里；
    * **智能体的技能白名单变了**——``skill_paths`` 变了而磁盘一个字节都没动。

    后果是 AgentScope 的 ``_setup_skills``（``.seed`` 非空即跳过）永不刷新，沙箱里
    一直躺着平台已经认为「不存在/已禁用/不属于该智能体」的技能目录；而提示词恰好教
    模型去 ``.seed`` 里 ``ls -a`` 找技能文件，于是出现「平台说没有、沙箱里能翻到甚至
    能执行」。

    **为什么禁用集合刻意不进指纹**：禁用（权限禁用 ``forbidden_skills`` 或管理端
    ``enabled: false``）必然改变 :func:`discover_platform_skill_paths` 的输出——它同时
    过滤这两种禁用——于是 ``skill_paths`` 变、令牌变，指纹里的 ``skills:<令牌>`` 一项
    已经覆盖了权限变更。再单独塞一份禁用集合只会引入第二个真相来源：创建侧可能因为
    「没有用户/智能体上下文」拿到空集（例如沙箱预热接口、权限服务抖动时的 fail-open），
    核对侧拿到真集合，两边算出的指纹**永远不相等** → 每轮对话都逐出并重建容器
    （实测 3 轮 3 台）。这类"两个来源各取一次"的漂移是本项目已经踩过的坑，因此这里
    只保留**同一份** ``skill_paths`` 作为技能集合的唯一真相来源。

    会话目录（``<workdir>/skills/.seed``）里"禁用之前"铺下的副本由
    :func:`_prune_forbidden_session_skills` 按技能 id 直接清理，与指纹无关。
    """
    # 目录树指纹是**同步 IO**（逐个文件 stat + 读内容做 hash）：技能库大时要几十毫秒，
    # 而这条路径每轮对话都会走，直接算会把事件循环卡住。放线程里跑，不阻塞其他请求。
    public = await asyncio.to_thread(_platform_public_skills_fingerprint)
    if public is None:
        return None

    digest = hashlib.sha256()
    if layout is None and skill_paths is not None:
        layout = _skill_paths_cache_token(skill_paths)
    if layout:
        # 技能集合进指纹：白名单增删一个技能不会改任何文件，只有它能触发失效。
        digest.update(f"skills:{layout}\n".encode("utf-8"))
    digest.update(f"public:{public}\n".encode("utf-8"))
    personal = await asyncio.to_thread(
        _dir_tree_fingerprint, _personal_skills_dir(host_workdir)
    )
    digest.update(f"personal:{personal or '-'}\n".encode("utf-8"))
    return digest.hexdigest()


def _sandbox_skills_fingerprint_path(host_workdir: str, layout: str | None = None) -> str:
    """指纹文件路径；带 layout 时按技能集合分文件（互不覆盖）。"""
    name = DOCKER_SANDBOX_SKILLS_FINGERPRINT_NAME
    if layout:
        name = f"{name}-{layout}"
    return os.path.join(host_workdir, DOCKER_SANDBOX_DIR_NAME, name)


def _read_sandbox_skills_fingerprint(
    host_workdir: str, layout: str | None = None
) -> str | None:
    try:
        with open(
            _sandbox_skills_fingerprint_path(host_workdir, layout), "r", encoding="utf-8"
        ) as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value or None


def _write_sandbox_skills_fingerprint(
    host_workdir: str, fingerprint: str, layout: str | None = None
) -> None:
    path = _sandbox_skills_fingerprint_path(host_workdir, layout)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _atomic_write_text(path, fingerprint)
    except OSError as exc:  # noqa: BLE001
        logger.warning("[workspace] Failed to record skills fingerprint: %s", exc)


def _public_skill_directory_names() -> set[str]:
    """公共技能在 ``.seed`` / 分区里可能出现的**目录名**（= 源目录 basename）。

    刻意**不再**把 SKILL.md frontmatter 的 ``name`` 算进来。AgentScope 打包 seed 用的
    是 ``arcname=os.path.basename(os.path.abspath(path))``（``_base.py`` 的
    ``_setup_skills``），只有**通过 AgentScope 技能 API 新建**的技能才按 frontmatter
    ``name`` 的 sanitize 结果命名——而后者正是本函数要保护、绝不能删的「agent 自建
    技能」。把 frontmatter name 当公共技能名，会让「目录名恰好等于某个公共技能
    frontmatter name 的自建技能」在公共技能被删/被禁时被当成残留清掉。
    """
    from app.utils.fs_access import get_platform_skills_root

    root = get_platform_skills_root()
    if not root or not os.path.isdir(root):
        return set()
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return set()
    return {
        entry
        for entry in entries
        if not entry.startswith(".")
        and os.path.isfile(os.path.join(root, entry, "SKILL.md"))
    }


def _invalidate_stale_sandbox_skill_seed(
    host_workdir: str, fingerprint: str | None = None, layout: str | None = None
) -> tuple[bool, str | None]:
    """技能集合或权限变了就作废沙箱技能模板，返回 ``(是否已作废, 当前指纹)``。

    AgentScope 的 ``_setup_skills`` 只在 ``skills/.seed`` 为空时打包一次，之后「假定
    上一次运行或用户才是真相来源」永不刷新：新加的技能、改过的脚本、被删除或被禁用的
    技能、以及智能体白名单的增删都不会反映到沙箱里。这里在容器初始化**之前**比对指纹，
    不一致就删掉 ``.seed``——随后的 ``initialize()`` 会按当前 ``skill_paths`` 重新打包，
    顺带把被禁技能挡在包外。

    ``fingerprint`` 由调用方用 :func:`_sandbox_skills_seed_fingerprint` 算好传入（它需要
    await 权限查询）；不传时退化为只看公共技能库内容指纹（老行为，供同步轻量调用与
    测试使用）。指纹不存在（老部署首次升级）同样视为不一致，让历史快照一次性对齐。
    ``layout`` 是技能集合令牌：技能目录与指纹文件都按它分片，缺省即历史单份布局。
    """
    if fingerprint is None:
        fingerprint = _platform_public_skills_fingerprint()
    if fingerprint is None:
        return False, None
    if _read_sandbox_skills_fingerprint(host_workdir, layout) == fingerprint:
        return False, fingerprint

    seed_dir = os.path.join(
        _sandbox_skills_dir(host_workdir, layout), SANDBOX_SKILL_SEED_DIR_NAME
    )
    # 删之前记下这一版 seed 里都有什么：分区同步要靠它区分「seed 提供的技能」
    # 与「agent 自己用技能 API 建的技能」，只清前者，后者必须原样保留。
    _write_sandbox_skill_manifest(host_workdir, _list_visible_entries(seed_dir), layout)
    if os.path.isdir(seed_dir):
        shutil.rmtree(seed_dir, ignore_errors=True)
    logger.info(
        "[workspace] Sandbox skill seed invalidated for %s (skill set or permissions changed)",
        seed_dir,
    )
    return True, fingerprint


def _sync_public_skills_into_sandbox_partitions(
    host_workdir: str, layout: str | None = None
) -> tuple[int, list[str]]:
    """把重建后的 ``.seed`` 同步进已装备的分区，返回 ``(处理条目数, 失败明细)``。

    AgentScope 的分区只在首次出现时从 seed 复制一次（``isdir(partition)`` 即退出），
    所以仅重建 seed 还不够：已有分区的 agent 仍会读到旧技能。这里逐个分区——删掉
    「上一版 seed 装过、这一版没有」的条目（技能被删除/被禁用都属于这种），再从新
    seed 覆盖一遍。

    **纳入个人技能**：seed 里装的是公共 + 个人技能，只按公共技能名单清理会让**被删掉
    的个人技能**永久留在老分区里（模型 ``ls`` 就能翻到）。因此判据改为「上一版 seed
    的账本」（:func:`_read_sandbox_skill_manifest`），并保留公共技能名单作为账本缺失
    时的兜底。分区里其它条目属于 agent 自建技能，一律保留。

    **失败明细必须向上冒泡**：此前 copytree 失败只 warning 然后照常提交指纹，于是
    「这一次没同步成功」被记成「已是最新」，该分区永久缺这个技能且不会重试。
    """
    skills_dir = _sandbox_skills_dir(host_workdir, layout)
    seed_dir = os.path.join(skills_dir, SANDBOX_SKILL_SEED_DIR_NAME)
    if not os.path.isdir(seed_dir):
        return 0, []
    seed_names = set(_list_visible_entries(seed_dir))
    previously_seeded = _read_sandbox_skill_manifest(host_workdir, layout)
    # 公共技能名单**只在账本缺失时**当兜底（本功能上线前就装备好的分区没有账本）。
    # 账本存在时以账本为权威：否则「目录名恰好与某个公共技能同名的 agent 自建技能」
    # 会被当平台残留删掉，而自建技能只存在于这一份里。
    public_names = set() if previously_seeded else _public_skill_directory_names()
    if not seed_names and not public_names:
        return 0, []

    synced = 0
    failed: list[str] = []
    try:
        partitions = sorted(os.listdir(skills_dir))
    except OSError:
        return 0, []
    for entry in partitions:
        if entry == SANDBOX_SKILL_SEED_DIR_NAME or entry.startswith("."):
            continue
        partition = os.path.join(skills_dir, entry)
        if not os.path.isdir(partition):
            continue
        present = _list_visible_entries(partition)
        # 先清残留：上一版 seed 装过、这一版没有的条目（含被删的个人技能），
        # 以及名字落在公共技能名单里但已不在新 seed 的（无账本时的兜底）。
        for name in present:
            if name in seed_names:
                continue
            if name not in previously_seeded and name not in public_names:
                continue
            if name not in previously_seeded:
                logger.info(
                    "[workspace] 无账本兜底：按公共技能名单清掉分区残留 %s/%s",
                    entry,
                    name,
                )
            stale = os.path.join(partition, name)
            try:
                if os.path.isdir(stale) and not os.path.islink(stale):
                    shutil.rmtree(stale)
                else:
                    os.remove(stale)
            except OSError as exc:  # noqa: BLE001
                if os.path.exists(stale):
                    # 删不掉也要冒泡：否则指纹会被提交，这个「平台已认为不存在」的
                    # 技能就永久留在分区里，而提示词恰好教模型去分区里翻技能。
                    logger.warning(
                        "[workspace] Failed to drop stale skill %s: %s", stale, exc
                    )
                    failed.append(f"{entry}/{name}")
                    continue
            synced += 1
        # 再从新 seed 覆盖全部条目（公共 + 个人）。用 staging + os.replace 原子替换：
        # 同用户另一个会话/容器可能正在同一个挂载目录里跑 Bash，先 rmtree 再 copytree
        # 会让它读到「目录不存在」或半个技能。
        for name in sorted(seed_names):
            source = os.path.join(seed_dir, name)
            if not os.path.isdir(source):
                continue
            target = os.path.join(partition, name)
            # 点开头命名：既不会被 _list_visible_entries 当成技能条目，也不会被
            # AgentScope 的技能枚举看到（它只认 partition 下的可见条目）。
            staged = os.path.join(partition, f".tmp-{uuid.uuid4().hex}")
            try:
                shutil.copytree(source, staged, copy_function=_hardlink_or_copy2)
                if os.path.isdir(target) and not os.path.islink(target):
                    # 非空目录无法用 os.replace 直接替换（要求目标为空），先删再换；
                    # 文件/符号链接走 os.replace 本身原子路径。
                    shutil.rmtree(target)
                os.replace(staged, target)
            except OSError as exc:  # noqa: BLE001
                shutil.rmtree(staged, ignore_errors=True)
                logger.warning(
                    "[workspace] Failed to sync skill %s into %s: %s",
                    name,
                    partition,
                    exc,
                )
                failed.append(f"{entry}/{name}")
                continue
            synced += 1
    if synced:
        logger.info(
            "[workspace] Synced %d skill entries into sandbox partitions under %s",
            synced,
            skills_dir,
        )
    if failed:
        logger.warning(
            "[workspace] 技能分区同步失败 %d 项（本次不提交指纹，下次启动重试）：%s",
            len(failed),
            ", ".join(failed[:10]),
        )
    return synced, failed


def _commit_sandbox_skills_fingerprint(
    host_workdir: str, fingerprint: str | None, layout: str | None = None
) -> None:
    """打包成功后才记录指纹，避免打包失败被当成「已是最新」而永久跳过刷新。"""
    if not fingerprint:
        return
    seed_dir = os.path.join(
        _sandbox_skills_dir(host_workdir, layout), SANDBOX_SKILL_SEED_DIR_NAME
    )
    try:
        if not os.path.isdir(seed_dir) or not os.listdir(seed_dir):
            return
    except OSError:
        return
    _write_sandbox_skills_fingerprint(host_workdir, fingerprint, layout)


def _parse_sandbox_cache_key(cache_key: str) -> tuple[str, str, str, str] | None:
    """拆解 ``<root>::<user_key>::<skills_token>::<policy>``；格式不符返回 ``None``。

    ``user_key`` 由 ``_clean_key_part`` 归一化（不含 ``:``），技能集合令牌是纯十六进制
    或 ``noskills``，所以从右侧切三段是安全的。
    """
    parts = str(cache_key or "").rsplit("::", 3)
    if len(parts) != 4:
        return None
    root, user_key, layout, policy = parts
    if not root or not user_key or not policy:
        return None
    return root, user_key, layout, policy


async def _sandbox_skills_snapshot_is_current(
    cache_key: str,
    *,
    skill_paths: list[str] | None,
) -> bool:
    """该沙箱里的技能快照是否仍与当前技能集合 / 权限一致。

    「容器还活着」≠「容器里的技能集合是对的」：缓存键只由工作区 + 白名单 + 策略决定，
    管理员禁用/下架技能、公共技能内容改动、个人技能删除都不会改缓存键，于是缓存命中会
    把**老快照**直接交给本次会话——沙箱里依然能 ``ls`` 到、能执行那些技能，直到空闲
    回收（默认 15 分钟）为止。这个函数就是那个核对点，覆盖两种过期：

    ① 技能集合令牌变了（白名单或禁用集合变化 → 挂载目录/容器名都该换）；
    ② 令牌没变但公共库/个人库内容变了。

    禁用（权限禁用 / 管理端 ``enabled: false``）走的是 ①：两者都会改变
    :func:`discover_platform_skill_paths` 的输出，于是令牌必变——因此这里**不需要**再传
    一份禁用集合，也就不会出现"建沙箱用一份、比指纹用另一份"的漂移（见
    :func:`_sandbox_skills_seed_fingerprint` 的说明）。

    任何一项**无法判定**（指纹文件缺失、公共技能库读不到）都判为「最新」——宁可少失效
    一次，也不能因为判定不了就每轮重建容器。
    """
    parsed = _parse_sandbox_cache_key(cache_key)
    if parsed is None:
        return True
    root, user_key, layout, policy = parsed
    if skill_paths is not None and _skill_paths_cache_token(skill_paths) != layout:
        return False
    # 与创建侧同口径解析路径：DooD 下必须是宿主路径，否则指纹恒读不到（判「最新」）
    host_workdir = _sandbox_skills_host_workdir(root, user_key, policy)
    recorded = _read_sandbox_skills_fingerprint(host_workdir, layout or None)
    if not recorded:
        return True
    current = await _sandbox_skills_seed_fingerprint(
        host_workdir,
        skill_paths,
        layout or None,
    )
    if not current:
        return True
    return current == recorded


async def _evict_sandbox_cache_entry(cache_key: str, *, reason: str) -> int:
    """按缓存键里的策略后缀逐出一台沙箱（Docker / K8s 各自的逐出实现）。"""
    if str(cache_key).endswith(f"::{SANDBOX_POLICY_K8S}"):
        return await _evict_k8s_workspace_cache_entry(cache_key, reason=reason)
    return await _evict_docker_workspace_cache_entry(cache_key, reason=reason)


#: 技能集合目录被回收的最小"年龄"（秒）：刚建好就被回收的目录留着，避免
#: 「切换智能体 → 立即重建 → 再被回收」的抖动把目录反复删了又建。
SANDBOX_SKILLS_DIR_MIN_AGE_SECONDS = 600.0


def _sandbox_skills_dir_is_disposable(host_workdir: str, layout: str) -> bool:
    """该技能集合目录里是否**只有平台可再生**的内容（没有 agent 自建技能）。

    分区（``skills-<layout>/<agent_id>/``）里既可能有平台铺的技能，也可能有 agent 用
    技能 API 自建的技能——后者只存在于这个目录里，删了就真丢了。判据刻意只认
    「当前 seed ∪ 上一版账本」这两个**平台自己的记录**：任何不在里面的条目都当作自建
    技能，整目录保留（刻意的保守，宁可漏收也不丢数据）。

    为什么不再把「公共技能目录名」算进判据：目录名相同不等于同源。agent 完全可以在自己
    的分区里建一个与某个公共技能**同名**的技能（公共技能不在它白名单里时尤其容易发生），
    按名字判就会把它当平台残留，连同整个技能集合目录一起 ``rmtree``——那是不可再生的
    用户数据（已用探针复现）。账本缺失的老分区可能因此少收一次残留，但这换来的是
    「绝不误删自建技能」，方向与 :func:`_sync_public_skills_into_sandbox_partitions`
    的兜底一致。
    """
    skills_dir = _sandbox_skills_dir(host_workdir, layout)
    if not os.path.isdir(skills_dir):
        return False
    seed_dir = os.path.join(skills_dir, SANDBOX_SKILL_SEED_DIR_NAME)
    managed = set(_list_visible_entries(seed_dir))
    managed |= _read_sandbox_skill_manifest(host_workdir, layout)
    for entry in _list_visible_entries(skills_dir):
        if entry == SANDBOX_SKILL_SEED_DIR_NAME:
            continue
        partition = os.path.join(skills_dir, entry)
        if not os.path.isdir(partition) or os.path.islink(partition):
            return False
        for name in _list_visible_entries(partition):
            if name not in managed:
                return False
    return True


def collect_orphan_sandbox_skills_dir(cache_key: str) -> str | None:
    """回收一台已逐出沙箱的技能集合目录（磁盘），返回被删目录或 ``None``。

    ``sandbox/skills-<令牌>`` 是按技能集合分片的物化目录：白名单每变一次就多一份，
    容器被回收后目录却会一直留着（不回收是有原因的——同用户另一台容器可能正挂着它）。
    因此在**逐出这台沙箱之后**、且满足下面全部条件时才删：

    1. 该用户 + 该令牌没有任何活跃缓存条目（进程内别的会话没在用同一集合）；
    2. 目录年龄超过 :data:`SANDBOX_SKILLS_DIR_MIN_AGE_SECONDS`；
    3. 目录里没有 agent 自建技能（见 :func:`_sandbox_skills_dir_is_disposable`）。

    历史单份布局 ``sandbox/skills`` 不在此列（可能装的是旧版本自建技能，见
    :func:`_migrate_legacy_sandbox_skills_dir`）。任何异常都只告警不抛出——回收是
    尽力而为的清理，绝不能影响沙箱逐出本身。
    """
    parsed = _parse_sandbox_cache_key(cache_key)
    if parsed is None:
        return None
    root, user_key, layout, policy = parsed
    if not layout:
        return None
    prefix = f"{os.path.abspath(root)}::{user_key}::"
    marker = f"::{layout}::"
    for key in list(_docker_workspace_cache) + list(_k8s_workspace_cache):
        if isinstance(key, str) and key.startswith(prefix) and marker in key:
            return None
    # 与创建侧同口径：DooD 下技能目录在宿主路径上，用容器路径会回收错的树
    host_workdir = _sandbox_skills_host_workdir(root, user_key, policy)
    skills_dir = _sandbox_skills_dir(host_workdir, layout)
    if not os.path.isdir(skills_dir):
        return None
    try:
        age = time.time() - os.path.getmtime(skills_dir)
    except OSError:
        return None
    if age < SANDBOX_SKILLS_DIR_MIN_AGE_SECONDS:
        return None
    try:
        if not _sandbox_skills_dir_is_disposable(host_workdir, layout):
            return None
    except OSError as exc:  # noqa: BLE001
        logger.debug("[workspace] skills dir inspect failed for %s: %s", skills_dir, exc)
        return None
    # 「先改名再删」：多个副本（或同一进程的多个会话）可能同时判定这份目录是孤儿，
    # 直接 rmtree 会互相踩到半删状态；改名是原子操作，抢不到的那个直接放弃，
    # 抢到的那个再从容删掉自己独占的目录。
    claim = f"{skills_dir}.gc-{uuid.uuid4().hex}"
    try:
        os.replace(skills_dir, claim)
    except OSError as exc:  # noqa: BLE001
        logger.debug("[workspace] skills dir claim skipped for %s: %s", skills_dir, exc)
        return None
    try:
        shutil.rmtree(claim)
    except OSError as exc:  # noqa: BLE001
        logger.warning("[workspace] Failed to reclaim skills dir %s: %s", claim, exc)
        return None
    # 指纹旁文件单独存放（在 ``sandbox/`` 下），一并清掉；账本在目录里已随目录删除
    try:
        os.remove(_sandbox_skills_fingerprint_path(host_workdir, layout))
    except OSError:
        pass
    logger.info("[workspace] Reclaimed orphan sandbox skills dir: %s", skills_dir)
    return skills_dir


def _resolve_docker_host_data_path(service_path: str) -> str:
    """Map a platform-container data path to the Docker daemon's host path."""
    abs_path = os.path.abspath(service_path)
    host_data_dir = (
        os.getenv("HOST_DATA_DIR", "").strip()
        or os.getenv("AGENTSCOPE_WORKSPACE_HOST_ROOT", "").strip()
    )
    if not host_data_dir:
        return abs_path

    data_root = "/app/data"
    if abs_path == data_root or abs_path.startswith(f"{data_root}{os.sep}"):
        relative = os.path.relpath(abs_path, data_root)
        return os.path.join(
            os.path.abspath(host_data_dir),
            relative if relative != "." else "",
        )
    return abs_path


def _resolve_docker_public_docs_source() -> str | None:
    """Return the public docs source path for a Docker bind mount."""
    try:
        from app.utils.fs_paths import get_data_base_dir

        service_path = os.path.join(get_data_base_dir(), "docs")
    except Exception as exc:  # pragma: no cover - defensive startup guard
        logger.warning("[workspace] Failed to resolve public docs path: %s", exc)
        return None

    # The service-side path is the existence check. In DooD deployments the
    # mapped host path may intentionally be invisible inside the platform
    # container, while still being visible to the Docker daemon.
    if not os.path.isdir(service_path):
        return None
    return _resolve_docker_host_data_path(service_path)


async def _policy_docker_workspace(
    skill_paths: list[str] | None,
    *,
    workspace_id: str | None = None,
    sandbox_user_key: str | None = None,
    workspace_root: str | None = None,
) -> Any:
    """Build an initialized DockerWorkspace (containerized sandbox).

    Runs in a container built from ``base_image`` (or the AgentScope default).
    Bash/file tools are exposed through the container's inline FastMCP stdio
    server seeded via ``default_mcps`` (see workspace_container_mcp). The user
    workspace is mounted at ``/workspace`` and receives isolated child mounts
    for the merged runtime skills and read-only public docs.
    """
    from app.services.config_service import ConfigService
    from app.services.ai.runtime.agentscope.workspace_container_mcp import (
        GATEWAY_EXTRA_PIP,
        build_container_tool_mcp,
    )
    from agentscope.workspace import DockerWorkspace
    from app.services.ai.runtime.agentscope.docker_workspace import (
        build_docker_workspace_with_extra_binds,
    )
    from app.services.ai.runtime.agentscope.docker_template_patch import (
        SANDBOX_NODE_VERSION,
    )

    DEFAULT_DOCKER_BASE_IMAGE = "python:3.11-slim"
    base_image = (
        await ConfigService.get("sandbox_docker_base_image", "")
    ).strip() or DEFAULT_DOCKER_BASE_IMAGE
    host_workdir = _resolve_docker_sandbox_host_workdir(
        workspace_root=workspace_root,
        sandbox_user_key=sandbox_user_key,
    )

    extra_bind_mounts: list[tuple[str, str, str]] = []
    public_docs_source: str | None = None
    seed_invalidated = False
    skills_fingerprint: str | None = None
    #: 技能集合令牌 = 这一台沙箱的身份（目录 / 指纹 / 容器名同源）。缺省即单份历史布局。
    layout = _skill_paths_cache_token(skill_paths)
    if host_workdir:
        # 用户级准备锁：同一用户的多个技能集合会并发写同一份宿主目录（个人技能源、
        # 公共文档目录、历史布局的一次性迁移），这一段必须串行。
        async with _sandbox_host_prepare_lock(host_workdir):
            _migrate_legacy_sandbox_skills_dir(host_workdir, layout)
            os.makedirs(_sandbox_skills_dir(host_workdir, layout), exist_ok=True)
            # 个人技能源目录也要存在：它以只读 bind mount 挂进沙箱（/workspace/personal-skills），
            # 源目录缺失时 Docker 会直接创建空目录（root 属主），反而更难排查。
            os.makedirs(_personal_skills_dir(host_workdir), exist_ok=True)
            os.makedirs(
                os.path.join(host_workdir, DOCKER_PUBLIC_DIR_NAME, "docs"),
                exist_ok=True,
            )
        # 技能集合或权限变了就作废上一次的 seed 模板，让 initialize() 重新打包
        # （AgentScope 自己不刷新，见 _invalidate_stale_sandbox_skill_seed）。
        # 指纹含技能集合令牌、个人技能库与公共库：禁用（权限禁用 / enabled=false）
        # 会改 discover_platform_skill_paths 的输出 → 令牌变 → 这里必失效。
        seed_invalidated, skills_fingerprint = _invalidate_stale_sandbox_skill_seed(
            host_workdir,
            await _sandbox_skills_seed_fingerprint(host_workdir, skill_paths, layout),
            layout,
        )
        public_docs_source = _resolve_docker_public_docs_source()
        extra_bind_mounts = _build_docker_workspace_mounts(
            host_workdir,
            public_docs_source,
            layout,
        )[1:]

    default_mcp = build_container_tool_mcp()

    kwargs: dict[str, Any] = {
        "host_workdir": host_workdir,  # None => ephemeral container
        "default_mcps": [default_mcp],
        # AgentScope 2.0.9 起网关镜像以 ``--no-deps`` 安装 agentscope，容器内跑
        # 内置 Bash/Read/Write 工具所需的依赖不再被自动带入，须显式补齐（否则
        # 沙箱 Bash 报 "HTTP 500: No module named 'docstring_parser'"）。此处的
        # 取值必须与 docker_prebuild._prepare_context 完全一致：extra_pip 会写入
        # requirements.txt 并参与镜像 tag 的 SHA256 计算，不一致会导致预构建的
        # 镜像无法被运行时命中，退化为每次现场构建。
        "extra_pip": list(GATEWAY_EXTRA_PIP),
        "skill_paths": skill_paths,
        "base_image": base_image,
        # 沙箱内置 node/npm/npx（写死在 docker_template_patch.SANDBOX_NODE_VERSION）：
        # 技能的 scripts/*.js 与 npx 启动的 MCP 服务都依赖它，而基座
        # python:3.11-slim 并不自带。此处取值必须与 docker_prebuild._prepare_context
        # 完全一致——它参与镜像 tag 的 SHA256，不一致会让预构建的镜像永远命不中。
        "node_version": SANDBOX_NODE_VERSION,
    }
    if workspace_id:
        kwargs["workspace_id"] = workspace_id

    for attempt in range(2):
        workspace = build_docker_workspace_with_extra_binds(
            DockerWorkspace,
            extra_bind_mounts=extra_bind_mounts,
            **kwargs,
        )
        try:
            await workspace.initialize()
        except Exception as exc:  # noqa: BLE001
            await _close_workspace_safely(workspace, reason="Docker initialization failure")
            if attempt == 0 and _docker_init_is_retryable(exc):
                await asyncio.sleep(DOCKER_WORKSPACE_INIT_RETRY_DELAY_SECONDS)
                continue
            reason_code = _docker_init_reason_code(exc)
            raise DockerSandboxUnavailableError(
                str(exc),
                reason_code=reason_code,
            ) from exc
        await apply_sandbox_gateway_timeout(workspace)

        # initialize() 里 AgentScope 已按最新 skill_paths 重新打包 seed；这一步把
        # 公共技能同步进（首次装备时就冻结的）既有分区，再记录指纹。顺序不能颠倒：
        # 指纹要在 seed 真的非空之后才写，否则打包失败会被当成「已是最新」永久跳过。
        if host_workdir:
            sync_failed: list[str] = []
            if seed_invalidated:
                _, sync_failed = _sync_public_skills_into_sandbox_partitions(
                    host_workdir, layout
                )
            if not sync_failed:
                _commit_sandbox_skills_fingerprint(
                    host_workdir, skills_fingerprint, layout
                )
        workspace._platform_sandbox_policy = SANDBOX_POLICY_DOCKER
        workspace._platform_execution_backend = SANDBOX_POLICY_DOCKER
        workspace._platform_sandbox_public_docs_mounted = public_docs_source is not None
        workspace._platform_workspace_id = workspace_id or getattr(
            workspace, "workspace_id", None
        )
        container = getattr(workspace, "_container", None)
        workspace._platform_container_id = getattr(container, "id", None)
        from datetime import datetime, timezone
        workspace._platform_started_at = datetime.now(timezone.utc).isoformat()
        return workspace

    raise AssertionError("Docker workspace initialization retry loop did not return")


async def _close_workspace_safely(workspace: Any, *, reason: str) -> None:
    """初始化失败时尽力释放已创建的沙箱或临时凭据。"""
    close = getattr(workspace, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[workspace] Failed to close workspace after %s error_type=%s",
            reason,
            type(exc).__name__,
        )


def _skill_paths_cache_token(skill_paths: list[str] | None) -> str:
    """技能集合的稳定令牌：`「同一用户 + 不同技能集合」的沙箱身份`。

    沙箱的**物理身份**（容器名 / Pod 名 / 挂载的技能目录）与缓存条目必须是 1:1：
    缓存键含令牌而容器名只按用户唯一时，同一用户的第二个技能集合会在
    ``create_or_replace`` 里把第一个集合**正在用的容器 stop+delete+create**，而第一个
    条目仍留在缓存里、``is_alive`` 仍为 True（它只是个布尔属性，不反映容器真实状态），
    于是那个智能体后续所有 Bash 都打在已删除的容器上。反过来说，物理身份若只按用户
    唯一，两个集合还会共用同一份 ``.seed``，出现「看不到自己白名单里的技能、却看得到
    别人的」以及禁用治理互相失效。

    因此令牌同时被用于：缓存键、容器/Pod 名、技能目录名、指纹。它必须区分
    **来源**（同名技能可能同时存在于公共库与个人库，内容不同），所以哈希里带
    ``abspath`` 而不是只取 basename；workspace 根对同一用户固定，不会因此抖动。
    """
    entries = sorted(
        {
            f"{os.path.basename(os.path.abspath(path))}|{os.path.abspath(path)}"
            for path in (skill_paths or [])
            if str(path).strip()
        }
    )
    if not entries:
        return "noskills"
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()[:12]


def _docker_workspace_cache_key(
    root: str, user_key: str, skill_paths: list[str] | None
) -> str:
    """Docker 沙箱缓存键（含技能集合令牌）。

    获取与查询（``is_already_alive`` / 逐出 / 状态）必须用同一个函数算键：任何一处
    手写键格式都会退化成"永远未命中"，表现为沙箱状态查询失灵或 lazy 代理重复创建。
    """
    return (
        f"{os.path.abspath(root)}::{user_key}::"
        f"{_skill_paths_cache_token(skill_paths)}::{SANDBOX_POLICY_DOCKER}"
    )


def _k8s_workspace_cache_key(
    root: str, user_key: str, skill_paths: list[str] | None
) -> str:
    """K8s 沙箱缓存键（含技能集合令牌），语义同 Docker 版。"""
    return (
        f"{os.path.abspath(root)}::{user_key}::"
        f"{_skill_paths_cache_token(skill_paths)}::{SANDBOX_POLICY_K8S}"
    )


def _sandbox_workspace_id(user_key: str, skill_paths: list[str] | None) -> str:
    """沙箱的物理标识 = 用户 + 技能集合令牌（容器名 / Pod 名 / 挂载目录共用）。

    AgentScope 用 ``workspace_id`` 推导容器名（``as_ws_<id>``）与 Pod 名
    （``as-ws-<sanitize(id)>``），所以把令牌放进这里，物理沙箱才与缓存条目一一对应。

    **先压缩用户键再拼令牌**：K8s 的 ``_k8s_safe_name`` 只保留前 63 字符（合法化是
    1:1 字符替换，不会变长），若用户名很长，令牌会被一起截掉——同一用户的多个技能集合
    就撞成同一个 Pod 名（第二个 Pod 直接 AlreadyExists）。预留 ``as-ws-`` 前缀与
    分隔符的长度后压缩用户键，令牌就一定能存活；完整身份另有容器/Pod 标签
    （``agentscope.workspace.id``）承载。
    """
    token = _skill_paths_cache_token(skill_paths)
    return f"{_sandbox_user_key_prefix(user_key, len(token))}-{token}"


def _sandbox_user_key_prefix(user_key: str, token_len: int) -> str:
    """把用户键压到「拼上令牌后仍不超过 63 字符」，超长部分用哈希尾缀保唯一。

    不做简单截断：两个前缀相同的长用户键（例如 SSO 长邮箱）被截成同一前缀后，拼上
    相同令牌就是同一个容器/Pod 名——Docker 侧表现为互相 ``stop+delete``，K8s 侧表现为
    Pod ``AlreadyExists``。这里用 ``sha256(user_key)`` 的前 8 位替换被裁掉的部分，
    32 位熵加上必须共享的前缀，实际不可能相撞。
    """
    budget = max(1, 63 - len("as-ws-") - token_len - 1)
    if len(user_key) <= budget:
        return user_key
    digest = hashlib.sha256(user_key.encode("utf-8")).hexdigest()[:8]
    keep = max(1, budget - len(digest) - 1)
    return f"{user_key[:keep]}-{digest}"


def _conversation_workdir(root: str, user_key: str, conversation_id: str) -> str:
    """会话工作区绝对路径（与 :func:`resolve_session_workdir` 同一套拼法）。

    单独抽出来是因为只读入口（状态/终端）手里只有已解析好的 ``user_key``，
    不该再要求调用方把 user_id/user_name/user_info 传一遍。
    """
    return os.path.join(
        os.path.abspath(root),
        user_key,
        USER_SESSIONS_DIR_NAME,
        _clean_key_part(conversation_id, "conversation"),
    )


def _conversation_sandbox_workspace_id(
    *,
    root: str | None,
    user_key: str,
    conversation_id: str | None,
    policy: str,
) -> str | None:
    """该会话**当前挂着**的沙箱 ``workspace_id``（精确到技能集合），取不到返回 None。

    同一用户可能同时有多台沙箱（每个技能集合一台）。只读入口（终端/状态）如果没有
    技能上下文，只能"取某一台"，用户点终端就可能进到**另一个**智能体的沙箱。会话的
    工作区登记（``_workspace_sandbox_refs``）里存着「会话+技能集合+策略 → 沙箱缓存键」，
    据此可以算回该会话真正在用的那一台。
    """
    if not root or not str(conversation_id or "").strip():
        return None
    workdir = _conversation_workdir(root, user_key, str(conversation_id).strip())
    workspace_prefix = f"{workdir}::"
    policy_suffix = f"::{policy}"
    cache = (
        _docker_workspace_cache
        if policy == SANDBOX_POLICY_DOCKER
        else _k8s_workspace_cache
    )
    for workspace_cache_key, sandbox_cache_key in list(_workspace_sandbox_refs.items()):
        if not isinstance(workspace_cache_key, str) or not isinstance(sandbox_cache_key, str):
            continue
        if not workspace_cache_key.startswith(workspace_prefix):
            continue
        if not sandbox_cache_key.endswith(policy_suffix):
            continue
        workspace = cache.get(sandbox_cache_key)
        if workspace is None:
            continue
        workspace_id = getattr(workspace, "_platform_workspace_id", None) or getattr(
            workspace, "workspace_id", None
        )
        if workspace_id:
            return str(workspace_id)
    return None


def _find_cached_docker_workspace(
    root: str, user_key: str, *, conversation_id: str | None = None
) -> Any:
    """取该用户在给定 root 下的活跃 Docker 沙箱缓存条目。

    给了 ``conversation_id`` 就优先返回**该会话正在用的那一台**（精确到技能集合）；
    同一用户存在多个技能集合的沙箱时，没有会话上下文才退化为「返回其中一个」。
    """
    prefix = f"{os.path.abspath(root)}::{user_key}::"
    workspace_id = _conversation_sandbox_workspace_id(
        root=root,
        user_key=user_key,
        conversation_id=conversation_id,
        policy=SANDBOX_POLICY_DOCKER,
    )
    for key, workspace in _docker_workspace_cache.items():
        if not key.startswith(prefix):
            continue
        if workspace_id is None:
            return workspace
        cached_id = getattr(workspace, "_platform_workspace_id", None) or getattr(
            workspace, "workspace_id", None
        )
        if str(cached_id or "") == workspace_id:
            return workspace
    if workspace_id is None:
        return None
    # 会话登记里有但缓存对象已不在：退回"任意一台"，保持入口可用性（状态/终端只是
    # 只读观测，宁可给一台该用户的沙箱，也不要报"沙箱不存在"）。
    return _find_cached_docker_workspace(root, user_key)


def _find_cached_k8s_workspace(
    root: str, user_key: str, *, conversation_id: str | None = None
) -> Any:
    """取该用户在给定 root 下的活跃 K8s 沙箱缓存条目（语义同 Docker 版）。"""
    prefix = f"{os.path.abspath(root)}::{user_key}::"
    workspace_id = _conversation_sandbox_workspace_id(
        root=root,
        user_key=user_key,
        conversation_id=conversation_id,
        policy=SANDBOX_POLICY_K8S,
    )
    for key, workspace in _k8s_workspace_cache.items():
        if not key.startswith(prefix):
            continue
        if workspace_id is None:
            return workspace
        cached_id = getattr(workspace, "_platform_workspace_id", None) or getattr(
            workspace, "workspace_id", None
        )
        if str(cached_id or "") == workspace_id:
            return workspace
    if workspace_id is None:
        return None
    return _find_cached_k8s_workspace(root, user_key)


def _docker_container_name(workspace_id: str) -> str:
    """与 AgentScope ``DockerWorkspace`` 一致的容器名（``as_ws_<workspace_id>``）。"""
    return f"as_ws_{workspace_id}"


def _sandbox_workspace_id_pattern(user_key: str) -> str:
    """匹配某用户所有可能的 workspace_id：历史 ``<user_key>`` 或 ``<用户键前缀>-<令牌>``。

    令牌只有两种长度（``noskills`` 8 位 / 12 位十六进制），所以这个模式既不会误配
    ``a`` 与 ``a-b`` 这种前缀重合的用户，也能覆盖带令牌的新命名。用户键过长时
    :func:`_sandbox_user_key_prefix` 会压缩它（保住令牌 + 哈希尾缀），因此这里把两种
    令牌长度对应的前缀形态都列进候选；用户键不长时前缀就是它自身。
    """
    tokens = r"(?:noskills|[0-9a-f]{12})"
    variants = {re.escape(user_key)}
    for token_len in {12, len("noskills")}:
        prefix = _sandbox_user_key_prefix(user_key, token_len)
        variants.add(rf"{re.escape(prefix)}-{tokens}")
    return rf"(?:{'|'.join(sorted(variants))})"


async def _docker_sandbox_container_names(
    client: Any,
    *,
    user_key: str,
    root: str | None = None,
    conversation_id: str | None = None,
) -> list[str]:
    """该用户当前所有沙箱容器的名字（每技能集合一台，可能是多台）。

    只读入口（状态/终端/停止/重启）没有技能集合上下文，容器名又带令牌，所以不能
    再拼 ``as_ws_<user_key>``。这里按 AgentScope 的容器标签
    （``agentscope.workspace.id`` + ``agentscope.workspace=true``）反查，顺序为：
    会话登记里确认在用的那一台 → 进程内缓存已知的 → 标签扫描命中的（按创建时间倒序）
    → 历史无令牌命名（老部署兜底）。
    """
    names: list[str] = []
    if root and conversation_id:
        exact_id = _conversation_sandbox_workspace_id(
            root=root,
            user_key=user_key,
            conversation_id=conversation_id,
            policy=SANDBOX_POLICY_DOCKER,
        )
        if exact_id:
            names.append(_docker_container_name(exact_id))

    workspace = (
        _find_cached_docker_workspace(root, user_key, conversation_id=conversation_id)
        if root
        else None
    )
    if workspace is not None:
        wid = getattr(workspace, "_platform_workspace_id", None) or getattr(
            workspace, "workspace_id", None
        )
        if wid:
            names.append(_docker_container_name(str(wid)))

    pattern = _sandbox_workspace_id_pattern(user_key)
    try:
        listed = await client.containers.list(
            all=True, filters={"label": "agentscope.workspace=true"}
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] Docker label scan for %s failed: %s", user_key, exc)
        listed = []
    # 标签扫描命中的容器按创建时间倒序：技能集合变了之后，**最新**的那台才是当前技能
    # 集合对应的沙箱；没有 Created 字段（老 API/测试替身）时保持列表原序。
    scanned: list[tuple[float, int, str]] = []
    for index, item in enumerate(listed or []):
        raw = getattr(item, "_container", None)
        labels = raw.get("Labels") if isinstance(raw, dict) else None
        labels = labels if isinstance(labels, dict) else {}
        wid = str(labels.get("agentscope.workspace.id") or "")
        if not wid or not re.fullmatch(pattern, wid):
            continue
        name = ""
        raw_names = raw.get("Names") if isinstance(raw, dict) else None
        if isinstance(raw_names, list) and raw_names:
            name = str(raw_names[0]).lstrip("/")
        if not name:
            name = str(getattr(item, "id", "") or "")
        if not name or name in names:
            continue
        try:
            created = float(raw.get("Created") or 0) if isinstance(raw, dict) else 0.0
        except (TypeError, ValueError):
            created = 0.0
        scanned.append((-created, index, name))
    for _neg_created, _index, name in sorted(scanned):
        if name not in names:
            names.append(name)

    legacy = _docker_container_name(user_key)
    if legacy not in names:
        names.append(legacy)
    return names


async def _docker_first_existing_container(
    client: Any,
    *,
    user_key: str,
    root: str | None = None,
    conversation_id: str | None = None,
) -> tuple[Any, str]:
    """返回该用户第一个存在的容器对象与名字；都不存在时返回 ``(None, 首选名)``。"""
    names = await _docker_sandbox_container_names(
        client, user_key=user_key, root=root, conversation_id=conversation_id
    )
    for name in names:
        try:
            return await client.containers.get(name), name
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) == 404:
                continue
            raise
    return None, names[0] if names else _docker_container_name(user_key)


def _k8s_pod_name(workspace_id: str) -> str:
    """与 AgentScope ``K8sWorkspace`` 一致的 Pod 名。

    复刻 ``agentscope.workspace._k8s._constants._k8s_safe_name``：小写化 + 非
    ``[a-z0-9-]`` 归一为 ``-`` + 截断 63 + 去尾部 ``-``。此前平台自己写成
    ``f"as-ws-{user_key.replace('_','-')}"``，用户名含大写（``Alice``）时平台算出的
    Pod 名与真实 Pod 名不一致，状态查询与终端会报「找不到 Pod」。
    """
    safe = re.sub(r"[^a-z0-9-]", "-", str(workspace_id or "").lower())
    return f"as-ws-{safe}"[:63].rstrip("-")


def _resolve_sandbox_user_key(
    *,
    user_id: str | int | None,
    user_name: str | None,
    user_info: dict[str, Any] | None,
) -> str | None:
    """Return a stable user scope, or None when no authenticated identity exists."""
    resolved_user_id, resolved_user_name = extract_workspace_identity(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    if resolved_user_id is None and not resolved_user_name:
        return None
    return resolve_workspace_user_key(
        user_id=resolved_user_id,
        user_name=resolved_user_name,
    )


async def _acquire_docker_workspace(
    *,
    root: str,
    user_key: str,
    skill_paths: list[str] | None,
) -> tuple[Any, str]:
    """Acquire one process-local Docker workspace reference per (user, skill set).

    缓存条目与**物理沙箱**（容器名 + 挂载的技能目录）一一对应：两条不同技能集合的
    条目各自拥有自己的容器与目录。此前只把令牌放进缓存键、容器名仍按用户唯一，
    于是第二条条目会在 ``create_or_replace`` 里把第一条正在用的容器删掉重建，而第一条
    仍被当作可用（``is_alive`` 只是布尔属性），后续 Bash 全部打在已删除的容器上。
    """
    cache_key = _docker_workspace_cache_key(root, user_key, skill_paths)
    lock = _docker_workspace_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        workspace = _docker_workspace_cache.get(cache_key)
        if workspace is None or getattr(workspace, "is_alive", True) is False:
            workspace = await _policy_docker_workspace(
                skill_paths,
                workspace_id=_sandbox_workspace_id(user_key, skill_paths),
                sandbox_user_key=user_key,
                workspace_root=root,
            )
            _docker_workspace_cache[cache_key] = workspace
            _docker_workspace_refcounts[cache_key] = 0
        _docker_workspace_refcounts[cache_key] = (
            _docker_workspace_refcounts.get(cache_key, 0) + 1
        )
        _docker_workspace_last_used[cache_key] = time.monotonic()
    return workspace, cache_key


async def _release_docker_workspace(cache_key: str, *, reason: str) -> None:
    """Release one conversation reference and close the user container at zero."""
    lock = _docker_workspace_locks.get(cache_key)
    if lock is None:
        return
    workspace = None
    async with lock:
        current = _docker_workspace_refcounts.get(cache_key, 0)
        if current > 1:
            _docker_workspace_refcounts[cache_key] = current - 1
            return
        workspace = _docker_workspace_cache.pop(cache_key, None)
        _docker_workspace_refcounts.pop(cache_key, None)
        _docker_workspace_last_used.pop(cache_key, None)
    if workspace is not None:
        await _close_workspace_safely(workspace, reason=reason)


def _touch_docker_workspace(cache_key: str) -> None:
    if cache_key in _docker_workspace_cache:
        _docker_workspace_last_used[cache_key] = time.monotonic()


async def _evict_docker_workspace_cache_entry(
    cache_key: str,
    *,
    reason: str,
    expected_last_used: float | None = None,
) -> int:
    """Evict one user Docker workspace and all conversation cache entries using it."""
    lock = _docker_workspace_locks.get(cache_key)
    if lock is None:
        return 0

    cached_pairs: list[Any] = []
    async with lock:
        if (
            expected_last_used is not None
            and _docker_workspace_last_used.get(cache_key) != expected_last_used
        ):
            return 0
        sandbox_ws = _docker_workspace_cache.pop(cache_key, None)
        _docker_workspace_refcounts.pop(cache_key, None)
        _docker_workspace_last_used.pop(cache_key, None)
        for workspace_cache_key, sandbox_ref in list(_workspace_sandbox_refs.items()):
            if sandbox_ref != cache_key:
                continue
            cached = _workspace_cache.pop(workspace_cache_key, None)
            _workspace_sandbox_refs.pop(workspace_cache_key, None)
            if cached is not None:
                cached_pairs.append(cached)

    to_close: list[Any] = []
    if sandbox_ws is not None:
        to_close.append(sandbox_ws)
    for cached in cached_pairs:
        sandbox, local = _normalize_workspace_pair(cached)
        to_close.extend((sandbox, local))

    closed_ids: set[int] = set()
    for workspace in to_close:
        if workspace is None or id(workspace) in closed_ids:
            continue
        closed_ids.add(id(workspace))
        await _close_workspace_safely(workspace, reason=reason)
    # 容器已经关掉，这个技能集合的物化目录如果没有任何活跃条目、也不含 agent 自建技能，
    # 就可以回收（否则白名单每变一次都会在磁盘上多留一份，只增不减）。
    try:
        collect_orphan_sandbox_skills_dir(cache_key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] orphan skills dir reclaim skipped: %s", exc)
    return 1 if sandbox_ws is not None else 0


async def _acquire_k8s_workspace(
    *,
    root: str,
    user_key: str,
    skill_paths: list[str] | None,
) -> tuple[Any, str]:
    """Acquire one process-local K8s workspace reference per (user, skill set).

    与 Docker 侧同一约定：Pod 名与 subPath 技能目录都带技能集合令牌，物理沙箱与缓存
    条目一一对应（否则第二个集合会 ``_ensure_pod`` 掉第一个集合正在用的 Pod）。
    """
    cache_key = _k8s_workspace_cache_key(root, user_key, skill_paths)
    lock = _k8s_workspace_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        workspace = _k8s_workspace_cache.get(cache_key)
        if workspace is None or getattr(workspace, "is_alive", True) is False:
            workspace = await _policy_k8s_workspace(
                skill_paths,
                workspace_id=_sandbox_workspace_id(user_key, skill_paths),
                sandbox_user_key=user_key,
                workspace_root=root,
            )
            _k8s_workspace_cache[cache_key] = workspace
            _k8s_workspace_refcounts[cache_key] = 0
        _k8s_workspace_refcounts[cache_key] = (
            _k8s_workspace_refcounts.get(cache_key, 0) + 1
        )
        _k8s_workspace_last_used[cache_key] = time.monotonic()
    return workspace, cache_key


async def _release_k8s_workspace(cache_key: str, *, reason: str) -> None:
    """Release one conversation reference and close the user Pod when active count reaches zero."""
    lock = _k8s_workspace_locks.get(cache_key)
    if lock is None:
        return
    workspace = None
    async with lock:
        current = _k8s_workspace_refcounts.get(cache_key, 0)
        if current > 1:
            _k8s_workspace_refcounts[cache_key] = current - 1
            return
        workspace = _k8s_workspace_cache.pop(cache_key, None)
        _k8s_workspace_refcounts.pop(cache_key, None)
        _k8s_workspace_last_used.pop(cache_key, None)
    if workspace is not None:
        await _close_workspace_safely(workspace, reason=reason)


def _touch_k8s_workspace(cache_key: str) -> None:
    if cache_key in _k8s_workspace_cache:
        _k8s_workspace_last_used[cache_key] = time.monotonic()


async def _evict_k8s_workspace_cache_entry(
    cache_key: str,
    *,
    reason: str,
    expected_last_used: float | None = None,
) -> int:
    """Evict one user K8s workspace and all conversation cache entries using it."""
    lock = _k8s_workspace_locks.get(cache_key)
    if lock is None:
        return 0

    cached_pairs: list[Any] = []
    async with lock:
        if (
            expected_last_used is not None
            and _k8s_workspace_last_used.get(cache_key) != expected_last_used
        ):
            return 0
        sandbox_ws = _k8s_workspace_cache.pop(cache_key, None)
        _k8s_workspace_refcounts.pop(cache_key, None)
        _k8s_workspace_last_used.pop(cache_key, None)
        for workspace_cache_key, sandbox_ref in list(_workspace_sandbox_refs.items()):
            if sandbox_ref != cache_key:
                continue
            cached = _workspace_cache.pop(workspace_cache_key, None)
            _workspace_sandbox_refs.pop(workspace_cache_key, None)
            if cached is not None:
                cached_pairs.append(cached)

    to_close: list[Any] = []
    if sandbox_ws is not None:
        to_close.append(sandbox_ws)
    for cached in cached_pairs:
        sandbox, local = _normalize_workspace_pair(cached)
        to_close.extend((sandbox, local))

    closed_ids: set[int] = set()
    for workspace in to_close:
        if workspace is None or id(workspace) in closed_ids:
            continue
        closed_ids.add(id(workspace))
        await _close_workspace_safely(workspace, reason=reason)
    try:
        collect_orphan_sandbox_skills_dir(cache_key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] orphan skills dir reclaim skipped: %s", exc)
    return 1 if sandbox_ws is not None else 0


async def reap_idle_k8s_workspaces(
    *,
    idle_seconds: float = K8S_WORKSPACE_IDLE_SECONDS,
    now: float | None = None,
) -> int:
    """Close user K8s workspaces idle for at least ``idle_seconds``."""
    if idle_seconds < 0:
        raise ValueError("idle_seconds must be non-negative")
    current = time.monotonic() if now is None else now
    stale_keys = [
        cache_key
        for cache_key, last_used in list(_k8s_workspace_last_used.items())
        if current - last_used >= idle_seconds
    ]

    reaped = 0
    for cache_key in stale_keys:
        lock = _k8s_workspace_locks.get(cache_key)
        if lock is None:
            continue
        async with lock:
            last_used = _k8s_workspace_last_used.get(cache_key)
            if last_used is None or current - last_used < idle_seconds:
                continue
        reaped += await _evict_k8s_workspace_cache_entry(
            cache_key,
            reason="K8s workspace idle timeout",
            expected_last_used=last_used,
        )
    _prune_idle_workspace_locks()
    return reaped


async def _release_sandbox_workspace(cache_key: str, *, reason: str) -> None:
    """Polymorphic release for any sandbox reference (Docker or K8s).

    The channel is resolved from the cache_key policy suffix (never a
    substring scan), so a user identity containing ``k8s`` can never be
    misrouted. Unknown/absent keys are a safe no-op.
    """
    k8s_key = f"::{SANDBOX_POLICY_K8S}"
    if cache_key and str(cache_key).endswith(k8s_key):
        await _release_k8s_workspace(cache_key, reason=reason)
    elif (
        cache_key in _k8s_workspace_cache
        or cache_key in _k8s_workspace_refcounts
        or cache_key in _k8s_workspace_last_used
    ):
        await _release_k8s_workspace(cache_key, reason=reason)
    else:
        await _release_docker_workspace(cache_key, reason=reason)


def _touch_sandbox_workspace(cache_key: str) -> None:
    """Polymorphic touch for any sandbox reference (Docker or K8s)."""
    _touch_docker_workspace(cache_key)
    _touch_k8s_workspace(cache_key)


#: 执行期心跳的最小间隔（秒）。真正的心跳间隔取 ``max(它, idle/3)``：既保证远小于
#: 空闲阈值，又不会把 ``sandbox_idle_time`` 调得很小时变成高频写字典。
SANDBOX_ACTIVITY_HEARTBEAT_MIN_SECONDS = 5.0


def _touch_sandbox_workspaces_for_user(
    sandbox_user_key: str,
    *,
    policy: str | None = None,
) -> int:
    """刷新该用户名下所有活跃缓存条目的活跃时间，返回命中的条目数。

    只读入口（终端）手里没有技能集合上下文，因此按 ``::<user_key>::`` 精确匹配
    （两侧都是分隔符，前缀重合的其它用户不会被吃掉），可选再按策略后缀过滤。
    """
    touched = 0
    needle = f"::{sandbox_user_key}::"
    for cache, touch in (
        (_docker_workspace_cache, _touch_docker_workspace),
        (_k8s_workspace_cache, _touch_k8s_workspace),
    ):
        for cache_key in list(cache.keys()):
            if not isinstance(cache_key, str):
                continue
            if needle not in cache_key and not cache_key.endswith(f"::{sandbox_user_key}"):
                continue
            if policy and not cache_key.endswith(f"::{policy}"):
                continue
            touch(cache_key)
            touched += 1
    return touched


def _touch_sandbox_activity(
    *,
    cache_key: str | None = None,
    sandbox_user_key: str | None = None,
    policy: str | None = None,
) -> None:
    """按缓存键（首选）或用户键刷新沙箱活跃时间。"""
    if cache_key:
        _touch_sandbox_workspace(cache_key)
        return
    if sandbox_user_key:
        _touch_sandbox_workspaces_for_user(sandbox_user_key, policy=policy)


async def _conversation_stale_skills_snapshot(
    *,
    root: str,
    user_key: str,
    conversation_id: str | None,
    policy: str,
    user_info: dict[str, Any] | None,
) -> tuple[str, list[str]] | None:
    """会话沙箱的技能快照是否已过期；过期返回 ``(沙箱缓存键, 当前技能集合)``。

    **为什么只读入口也要核对**：终端 / ``exec`` 只按标签定位容器就执行命令，此前完全不
    核对技能快照——管理员禁用/下架技能之后，用户仍能在那台"技能集合已过期"的沙箱里
    ``ls`` / ``cat`` 甚至执行被禁技能的脚本，直到空闲回收（默认 15 分钟），而这是唯一
    连"换技能集合就换沙箱"都拦不住的入口。这里把
    :func:`_sandbox_skills_snapshot_is_current` 接上去。

    技能集合必须从**会话当时的工作区缓存键**恢复：键是 ``<workdir>::<skills_fp>::<policy>``，
    而 ``skills_fp`` 恰好就是 ``all`` 或 ``custom:<允许列表>``——只读入口手里没有智能体
    上下文，缓存键是"这条会话当时按哪个白名单建的沙箱"的唯一在进程内的记录。

    取不到记录（进程刚重启、纯宿主策略、或键格式不认识）时返回 ``None``：判不了就放行，
    与 :func:`_sandbox_skills_snapshot_is_current` 的取舍一致——不能因为判定不了就把用户
    挡在终端外面。
    """
    if not root or not str(conversation_id or "").strip():
        return None
    workdir = _conversation_workdir(root, user_key, str(conversation_id).strip())
    prefix = f"{workdir}::"
    suffix = f"::{policy}"
    for workspace_key, sandbox_key in list(_workspace_sandbox_refs.items()):
        if not isinstance(workspace_key, str) or not isinstance(sandbox_key, str):
            continue
        if not workspace_key.startswith(prefix) or not workspace_key.endswith(suffix):
            continue
        skills_fp = workspace_key[len(prefix) : len(workspace_key) - len(suffix)]
        skills_custom = skills_fp.startswith("custom:")
        allowed_global_skills = (
            [item for item in skills_fp[len("custom:") :].split(",") if item]
            if skills_custom
            else None
        )
        try:
            from app.services.ai.skills.policy import get_forbidden_skill_ids

            forbidden_skill_ids = await get_forbidden_skill_ids(
                _resolve_identity_user_id(user_info)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[workspace] 只读入口核对技能快照时读取禁用集合失败，本次放行: %s", exc
            )
            return None
        skill_paths = discover_platform_skill_paths(
            user_info=user_info,
            skills_custom=skills_custom,
            allowed_global_skills=allowed_global_skills,
            forbidden_skill_ids=forbidden_skill_ids,
        )
        if await _sandbox_skills_snapshot_is_current(sandbox_key, skill_paths=skill_paths):
            return None
        return sandbox_key, skill_paths
    return None


@contextlib.asynccontextmanager
async def _sandbox_activity_heartbeat(
    *,
    cache_key: str | None = None,
    sandbox_user_key: str | None = None,
    policy: str | None = None,
) -> Any:
    """执行期间周期性刷新沙箱活跃时间，避免长命令被空闲回收中断。

    空闲回收只看 ``last_used``，而它此前只在「拉起沙箱」「缓存命中」「Bash 调用**开始
    前**」刷新：一条执行超过 ``sandbox_idle_time``（默认 15 分钟）的命令，或者终端里
    跑的长任务，会在中途被 reaper 关闭容器/Pod（终端侧甚至只在命令**结束后**才刷新）。

    心跳只影响空闲判定，不影响权限逐出（``_evict_*`` 是显式调用，与 ``last_used``
    无关），所以「技能快照过期 → 立刻作废沙箱」的语义不受影响。
    """
    touch_kwargs: dict[str, Any] = {
        "cache_key": cache_key,
        "sandbox_user_key": sandbox_user_key,
        "policy": policy,
    }
    _touch_sandbox_activity(**touch_kwargs)

    stop = asyncio.Event()

    async def _beat() -> None:
        while True:
            try:
                idle_seconds = await resolve_effective_sandbox_idle_seconds(
                    default_seconds=DOCKER_WORKSPACE_IDLE_SECONDS
                )
            except Exception:  # noqa: BLE001 - 读配置失败按默认阈值心跳
                idle_seconds = float(DOCKER_WORKSPACE_IDLE_SECONDS)
            interval = max(SANDBOX_ACTIVITY_HEARTBEAT_MIN_SECONDS, idle_seconds / 3.0)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                try:
                    _touch_sandbox_activity(**touch_kwargs)
                except Exception:  # noqa: BLE001 - 心跳失败不影响命令执行
                    logger.debug("[workspace] sandbox heartbeat touch failed", exc_info=True)
                continue
            return

    beat_task = asyncio.create_task(_beat())
    try:
        yield
    finally:
        stop.set()
        beat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await beat_task


async def reap_idle_docker_workspaces(
    *,
    idle_seconds: float = DOCKER_WORKSPACE_IDLE_SECONDS,
    now: float | None = None,
) -> int:
    """Close user Docker workspaces idle for at least ``idle_seconds``."""
    if idle_seconds < 0:
        raise ValueError("idle_seconds must be non-negative")
    current = time.monotonic() if now is None else now
    stale_keys = [
        cache_key
        for cache_key, last_used in list(_docker_workspace_last_used.items())
        if current - last_used >= idle_seconds
    ]

    reaped = 0
    for cache_key in stale_keys:
        lock = _docker_workspace_locks.get(cache_key)
        if lock is None:
            continue
        async with lock:
            last_used = _docker_workspace_last_used.get(cache_key)
            if last_used is None or current - last_used < idle_seconds:
                continue
        reaped += await _evict_docker_workspace_cache_entry(
            cache_key,
            reason="Docker workspace idle timeout",
            expected_last_used=last_used,
        )
    _prune_idle_workspace_locks()
    return reaped


def _prune_idle_workspace_locks() -> int:
    """丢掉「缓存条目已不在、且当前没人持有」的锁对象，返回清理数量。

    锁字典按缓存键累积，而缓存键含技能集合令牌——白名单每变一次就多一把锁，永不回收
    （长跑进程里是缓慢泄漏）。**只清理没被持有的锁**：正被 ``async with`` 持有的锁一旦
    从字典移除，同一键的下一次取用会新建一把锁，两把锁不再互斥，重建工作区时会出现
    double-build（两套容器/目录）。回收器迭代时正是"空闲"的时机，因此在这里顺手清理。
    """
    removed = 0
    for locks, cache in (
        (_docker_workspace_locks, _docker_workspace_cache),
        (_k8s_workspace_locks, _k8s_workspace_cache),
    ):
        for cache_key, lock in list(locks.items()):
            if cache_key in cache or lock.locked():
                continue
            locks.pop(cache_key, None)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# 孤儿沙箱对账（进程重启 / 命名演进后脱离进程内注册表的历史容器与 Pod）
# ---------------------------------------------------------------------------
#: 空闲回收只遍历进程内字典，因此「缓存里没有」的容器/Pod 永远不会被回收：进程重启
#: 丢缓存、技能集合令牌变化（升级或白名单变更）都会留下再也无人认领的沙箱，旧命名
#: （``as_ws_<user>``）连「同名 create_or_replace 顶掉」这条兜底都蹭不到。
#: 这里按 AgentScope 打在容器/Pod 上的 label 反向对账：凡是不在本进程注册表中、且
#: **物理创建时间**已超过 ``SANDBOX_ORPHAN_MIN_AGE_SECONDS`` 的，一律回收。
#:
#: ⚠️ 该对账只在**单副本**部署下安全：注册表是进程内的，另一个副本正在用的沙箱在本
#: 进程看来同样"无人认领"。多副本部署必须把 ``sandbox_orphan_reconcile`` 设为 false
#: （或先把活跃沙箱注册表搬到 Redis），否则副本之间会互删正在使用的沙箱。
SANDBOX_ORPHAN_RECONCILE_KEY = "sandbox_orphan_reconcile"
#: 只有创建时间超过它才允许回收：新建中的沙箱（含另一路并发刚拉起的）绝不动。
SANDBOX_ORPHAN_MIN_AGE_SECONDS = 1800.0
#: 对账周期：与空闲回收同一循环，但按更长的间隔执行（默认 10 分钟）。
SANDBOX_ORPHAN_RECONCILE_INTERVAL_SECONDS = 600.0


async def _orphan_reconcile_enabled() -> bool:
    """读取 ``sandbox_orphan_reconcile``（默认开启）。读不到时按开启处理。"""
    from app.services.config_service import ConfigService

    try:
        raw = await ConfigService.get(SANDBOX_ORPHAN_RECONCILE_KEY, "true")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] orphan reconcile flag unreadable: %s", exc)
        return True
    return str(raw if raw is not None else "true").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


def _live_sandbox_workspace_ids(cache: dict[str, Any]) -> set[str]:
    """本进程注册表中仍然活跃的 ``workspace_id`` 集合（容器名/Pod 名的判据）。"""
    workspace_ids: set[str] = set()
    for workspace in list(cache.values()):
        workspace_id = getattr(workspace, "_platform_workspace_id", None) or getattr(
            workspace, "workspace_id", None
        )
        if workspace_id:
            workspace_ids.add(str(workspace_id))
    return workspace_ids


def _container_label(container: Any, key: str) -> str:
    """从 aiodocker 容器对象上读一个 label（兼容测试替身与真实返回）。"""
    raw = getattr(container, "_container", None)
    labels = raw.get("Labels") if isinstance(raw, dict) else None
    labels = labels if isinstance(labels, dict) else {}
    return str(labels.get(key) or "")


def _container_name(container: Any) -> str:
    raw = getattr(container, "_container", None)
    names = raw.get("Names") if isinstance(raw, dict) else None
    if isinstance(names, list) and names:
        return str(names[0]).lstrip("/")
    return str(getattr(container, "id", "") or "")


async def reconcile_orphan_docker_workspaces(
    *,
    min_age_seconds: float = SANDBOX_ORPHAN_MIN_AGE_SECONDS,
    now: float | None = None,
) -> int:
    """回收本进程不再认知的 Docker 沙箱容器，返回回收数量。

    任何一步失败都只告警：对账是尽力而为的清理，绝不能影响正常的沙箱创建与回收。
    """
    try:
        import aiodocker
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] aiodocker unavailable for orphan reconcile: %s", exc)
        return 0

    current = time.time() if now is None else now
    live_ids = _live_sandbox_workspace_ids(_docker_workspace_cache)
    client: Any | None = None
    try:
        client = aiodocker.Docker()
        listed = await client.containers.list(
            all=True, filters={"label": "agentscope.workspace=true"}
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[workspace] orphan container scan failed: %s", exc)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()
        return 0

    removed = 0
    try:
        for item in listed or []:
            workspace_id = _container_label(item, "agentscope.workspace.id")
            if not workspace_id or workspace_id in live_ids:
                continue
            raw = getattr(item, "_container", None)
            try:
                created = float(raw.get("Created") or 0) if isinstance(raw, dict) else 0.0
            except (TypeError, ValueError):
                created = 0.0
            # 拿不到创建时间就保守跳过：宁可留一台孤儿，也不能误删刚拉起的沙箱。
            if created <= 0 or current - created < min_age_seconds:
                continue
            name = _container_name(item)
            try:
                await item.delete(force=True)
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "status", None) != 404:
                    logger.warning(
                        "[workspace] Failed to reclaim orphan container %s: %s",
                        name,
                        exc,
                    )
                continue
            removed += 1
            logger.warning(
                "[workspace] Reclaimed orphan docker sandbox container=%s workspace_id=%s age=%.0fs",
                name,
                workspace_id,
                current - created,
            )
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()
    return removed


async def reconcile_orphan_k8s_workspaces(
    *,
    min_age_seconds: float = SANDBOX_ORPHAN_MIN_AGE_SECONDS,
    now: float | None = None,
) -> int:
    """回收本进程不再认知的 K8s 沙箱 Pod，返回回收数量（语义同 Docker 版）。"""
    try:
        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio import config as k8s_async_config
    except ImportError:
        return 0

    from app.services.ai.runtime.agentscope.k8s_workspace import resolve_sandbox_namespace

    try:
        from app.services.config_service import ConfigService

        namespace = resolve_sandbox_namespace(
            await ConfigService.get("sandbox_k8s_namespace", "")
        )
    except Exception:  # noqa: BLE001
        namespace = resolve_sandbox_namespace(None)

    try:
        k8s_async_config.load_incluster_config()
    except Exception:
        try:
            await k8s_async_config.load_kube_config()
        except Exception:
            return 0

    current = time.time() if now is None else now
    live_ids = _live_sandbox_workspace_ids(_k8s_workspace_cache)
    removed = 0
    try:
        async with k8s_client.ApiClient() as api_client:
            core_v1 = k8s_client.CoreV1Api(api_client)
            pods = await core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector="agentscope.workspace=true",
            )
            for pod in getattr(pods, "items", None) or []:
                metadata = getattr(pod, "metadata", None)
                labels = getattr(metadata, "labels", None) or {}
                workspace_id = str(labels.get("agentscope.workspace.id") or "")
                pod_name = str(getattr(metadata, "name", "") or "")
                if not pod_name or not workspace_id or workspace_id in live_ids:
                    continue
                try:
                    created = float(metadata.creation_timestamp.timestamp())
                except Exception:  # noqa: BLE001
                    created = 0.0
                if created <= 0 or current - created < min_age_seconds:
                    continue
                try:
                    await core_v1.delete_namespaced_pod(
                        name=pod_name, namespace=namespace
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[workspace] Failed to reclaim orphan pod %s: %s", pod_name, exc
                    )
                    continue
                removed += 1
                logger.warning(
                    "[workspace] Reclaimed orphan k8s sandbox pod=%s namespace=%s workspace_id=%s age=%.0fs",
                    pod_name,
                    namespace,
                    workspace_id,
                    current - created,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[workspace] orphan pod scan failed: %s", exc)
    return removed


async def _docker_workspace_reaper_loop(
    *,
    idle_seconds: float,
    interval_seconds: float,
) -> None:
    next_reconcile_at = time.monotonic() + SANDBOX_ORPHAN_RECONCILE_INTERVAL_SECONDS
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            # 空闲时长每次迭代实时读取配置（分钟），无需重启即对 docker 生效
            effective_idle = await resolve_effective_sandbox_idle_seconds(
                default_seconds=idle_seconds
            )
            await reap_idle_docker_workspaces(idle_seconds=effective_idle)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[workspace] Docker workspace reaper iteration failed")

        if time.monotonic() < next_reconcile_at:
            continue
        next_reconcile_at = time.monotonic() + SANDBOX_ORPHAN_RECONCILE_INTERVAL_SECONDS
        try:
            if await _orphan_reconcile_enabled():
                await reconcile_orphan_docker_workspaces()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[workspace] Docker orphan reconcile failed")


def start_docker_workspace_reaper(
    *,
    idle_seconds: float = DOCKER_WORKSPACE_IDLE_SECONDS,
    interval_seconds: float = DOCKER_WORKSPACE_REAPER_INTERVAL_SECONDS,
) -> asyncio.Task[None]:
    """Start the process-local Docker idle reaper once."""
    global _docker_workspace_reaper_task
    if _docker_workspace_reaper_task is not None and not _docker_workspace_reaper_task.done():
        return _docker_workspace_reaper_task
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    _docker_workspace_reaper_task = asyncio.create_task(
        _docker_workspace_reaper_loop(
            idle_seconds=idle_seconds,
            interval_seconds=interval_seconds,
        )
    )
    return _docker_workspace_reaper_task


async def stop_docker_workspace_reaper() -> None:
    """Stop the idle reaper and close remaining cached Docker workspaces."""
    global _docker_workspace_reaper_task
    task = _docker_workspace_reaper_task
    _docker_workspace_reaper_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    for cache_key in list(_docker_workspace_cache):
        await _evict_docker_workspace_cache_entry(
            cache_key,
            reason="application shutdown",
        )


async def _k8s_workspace_reaper_loop(
    *,
    idle_seconds: float,
    interval_seconds: float,
) -> None:
    next_reconcile_at = time.monotonic() + SANDBOX_ORPHAN_RECONCILE_INTERVAL_SECONDS
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            # 空闲时长每次迭代实时读取配置（分钟），无需重启即对 k8s 生效
            effective_idle = await resolve_effective_sandbox_idle_seconds(
                default_seconds=idle_seconds
            )
            await reap_idle_k8s_workspaces(idle_seconds=effective_idle)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[workspace] K8s workspace reaper iteration failed")

        if time.monotonic() < next_reconcile_at:
            continue
        next_reconcile_at = time.monotonic() + SANDBOX_ORPHAN_RECONCILE_INTERVAL_SECONDS
        try:
            if await _orphan_reconcile_enabled():
                await reconcile_orphan_k8s_workspaces()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[workspace] K8s orphan reconcile failed")


def start_k8s_workspace_reaper(
    *,
    idle_seconds: float = K8S_WORKSPACE_IDLE_SECONDS,
    interval_seconds: float = K8S_WORKSPACE_REAPER_INTERVAL_SECONDS,
) -> asyncio.Task[None]:
    """Start the process-local Kubernetes idle reaper once (idempotent)."""
    global _k8s_workspace_reaper_task
    if _k8s_workspace_reaper_task is not None and not _k8s_workspace_reaper_task.done():
        return _k8s_workspace_reaper_task
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    _k8s_workspace_reaper_task = asyncio.create_task(
        _k8s_workspace_reaper_loop(
            idle_seconds=idle_seconds,
            interval_seconds=interval_seconds,
        )
    )
    return _k8s_workspace_reaper_task


async def stop_k8s_workspace_reaper() -> None:
    """Stop the K8s idle reaper and close remaining cached K8s workspaces."""
    global _k8s_workspace_reaper_task
    task = _k8s_workspace_reaper_task
    _k8s_workspace_reaper_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    for cache_key in list(_k8s_workspace_cache):
        await _evict_k8s_workspace_cache_entry(
            cache_key,
            reason="application shutdown",
        )


async def _policy_e2b_workspace(
    skill_paths: list[str] | None,
    config_overrides: Mapping[str, Any] | None = None,
) -> Any:
    """Build an initialized E2BWorkspace (E2B cloud sandbox)."""
    from app.services.ai.runtime.agentscope.workspace_container_mcp import (
        build_container_tool_mcp,
    )
    from agentscope.workspace import E2BWorkspace

    template = (await _sandbox_config_value(
        "sandbox_e2b_template", "", config_overrides
    )).strip() or None
    api_key = (await _sandbox_config_value(
        "sandbox_e2b_api_key", "", config_overrides
    )).strip() or None
    timeout_raw = (await _sandbox_config_value(
        "sandbox_e2b_timeout_seconds", "300", config_overrides
    )).strip() or "300"

    try:
        timeout_seconds = int(timeout_raw)
    except (TypeError, ValueError):
        timeout_seconds = 300
    if timeout_seconds <= 0:
        timeout_seconds = 300

    kwargs: dict[str, Any] = {
        "timeout_seconds": timeout_seconds,
        "default_mcps": [build_container_tool_mcp()],
        "skill_paths": skill_paths,
    }
    if template:
        kwargs["template"] = template
    if api_key:
        kwargs["api_key"] = api_key

    workspace = E2BWorkspace(**kwargs)
    try:
        await workspace.initialize()
    except Exception:
        await _close_workspace_safely(workspace, reason="E2B initialization failure")
        raise
    await apply_sandbox_gateway_timeout(workspace)
    return workspace


async def _policy_ssh_workspace(
    skill_paths: list[str] | None,
    config_overrides: Mapping[str, Any] | None = None,
) -> Any:
    """Build an initialized SshWorkspace (remote host reached over SSH).

    The platform host connects to a remote sandbox host through its own
    ``ssh`` CLI.  Bash/file tools are exposed through the SSH inline
    FastMCP stdio server seeded via ``default_mcps`` (see
    workspace_ssh.build_ssh_tool_mcp).  Key authentication is used
    directly; password authentication requires the ``sshpass`` CLI on
    the platform host.
    """
    from app.services.ai.runtime.agentscope.workspace_ssh import (
        SshWorkspace,
        _have_sshpass,
        build_ssh_tool_mcp,
    )

    auth_type = (
        await _sandbox_config_value(
            "sandbox_ssh_auth_type", "password", config_overrides
        )
    ).strip().lower() or "password"
    if auth_type == "private_key":
        # 兼容早期迁移/手工配置中的旧值，运行时统一使用 SshWorkspace 识别的 key。
        auth_type = "key"
    password = (
        await _sandbox_config_value(
            "sandbox_ssh_password", "", config_overrides
        )
    ).strip()
    private_key = (
        await _sandbox_config_value(
            "sandbox_ssh_private_key", "", config_overrides
        )
    ).strip()
    remote_workdir = (
        await _sandbox_config_value(
            "sandbox_ssh_remote_workdir", "/workspace", config_overrides
        )
    ).strip() or "/workspace"

    if auth_type == "password" and not password:
        raise RuntimeError(
            "sandbox_policy=ssh with password auth requires a non-empty password"
        )
    if auth_type == "password" and not _have_sshpass():
        raise RuntimeError(
            "sandbox_policy=ssh with password auth requires the 'sshpass' "
            "CLI on the platform host (or use a private key instead)"
        )
    if auth_type == "key" and not private_key:
        # Not fatal: ssh may still authenticate with the platform host's own
        # default identity or ssh-agent.  Warn loudly anyway — the resulting
        # "Permission denied (publickey,password)" is otherwise baffling, since
        # the operator believes the configured key is being used.
        logger.warning(
            "[SshPolicy] auth_type=key but sandbox_ssh_private_key is empty: "
            "falling back to the platform host's default identities/ssh-agent"
        )

    kwargs: dict[str, Any] = {
        "host": (await _sandbox_config_value(
            "sandbox_ssh_host", "", config_overrides
        )).strip(),
        "port": int(
            (
                await _sandbox_config_value(
                    "sandbox_ssh_port", "22", config_overrides
                )
            ).strip() or "22"
        ),
        "auth_type": auth_type,
        "remote_workdir": remote_workdir,
        "skill_paths": skill_paths,
    }
    user = (await _sandbox_config_value(
        "sandbox_ssh_user", "", config_overrides
    )).strip()
    if user:
        kwargs["user"] = user
    if auth_type == "password" and password:
        kwargs["password"] = password
    elif auth_type == "key" and private_key:
        kwargs["private_key"] = private_key

    # Defer the tool MCP construction until after the key is materialized
    # so the inline server can reference the private-key temp file.
    workspace = SshWorkspace(**kwargs)
    try:
        workspace._materialize_key()
        workspace._materialize_password()
        kwargs_extra = {
            "default_mcps": [
                build_ssh_tool_mcp(
                    host=workspace.host,
                    port=workspace.port,
                    user=workspace.user,
                    auth_type=workspace.auth_type,
                    password_file_path=workspace._local_password_path,
                    private_key_path=workspace._local_key_path,
                    remote_workdir=workspace.remote_workdir,
                )
            ]
        }
        # The sandbox tool MCP is not a constructor arg; adopt it as the
        # default so initialize()'s _restore_or_seed_mcps() seeds it into
        # self._mcps when there is no remote .mcp yet.
        workspace.default_mcps = kwargs_extra["default_mcps"]

        await workspace.initialize()
    except Exception:
        await _close_workspace_safely(workspace, reason="SSH initialization failure")
        raise
    await apply_sandbox_gateway_timeout(workspace)
    return workspace


async def _policy_k8s_workspace(
    skill_paths: list[str] | None,
    config_overrides: Mapping[str, Any] | None = None,
    *,
    sandbox_user_key: str | None = None,
    workspace_id: str | None = None,
    workspace_root: str | None = None,
) -> Any:
    """Build an initialized K8sWorkspace (containerized sandbox on Kubernetes)."""
    from agentscope.workspace import K8sWorkspace
    from app.services.ai.runtime.agentscope.k8s_workspace import (
        build_k8s_workspace_with_nanzi_adapter,
        ensure_k8s_public_data_subdirs,
        evaluate_k8s_workspace_mount_config,
        parse_existing_pvc_config,
        resolve_sandbox_namespace,
        resolve_shared_pvc,
    )
    from app.services.ai.runtime.agentscope.workspace_container_mcp import (
        GATEWAY_EXTRA_PIP,
        K8S_GATEWAY_VENV_PYTHON,
        build_container_tool_mcp,
    )

    # K8s 专用:Ensure 后端数据根(private PVC)公共目录存在,避免空 PVC 下
    # 沙箱 subPath docs mount 失败、且后端 Grep/Glob 访问
    # /app/data/docs 报 "Directory not found"。刻意仅在本策略分支执行,
    # 不影响 Docker 策略以 isdir(data_root/docs) 作公共文档挂载判断。
    ensure_k8s_public_data_subdirs()

    # 命名空间：留空或仍是历史默认值(agent-sandboxes)时自动跟随平台自身命名空间。
    # Kubernetes PVC 是命名空间级的，只有与平台同命名空间才能共享平台数据卷，
    # 从而让沙箱内 /workspace 看到用户工作区（与 Docker 沙箱对齐）。
    namespace = resolve_sandbox_namespace(
        await _sandbox_config_value("sandbox_k8s_namespace", "", config_overrides)
    )
    image = (
        await _sandbox_config_value(
            "sandbox_k8s_image", "python:3.11-slim", config_overrides
        )
    ).strip() or "python:3.11-slim"
    raw_existing_pvc = (
        await _sandbox_config_value(
            "sandbox_k8s_existing_pvc", "", config_overrides
        )
    ).strip()
    # 共享数据卷解析优先级：显式指定 > 自动探测平台自身数据卷（零配置，推荐）> 独立空卷。
    # 自动探测而非硬编码 PVC 名，是为了兼容自定义 PVC 名/自定义部署。
    explicit_pvc, _isolation_requested = parse_existing_pvc_config(raw_existing_pvc)
    if explicit_pvc and not sandbox_user_key:
        raise ValueError(
            "K8S 共享 PVC 沙箱策略要求必须具备已认证的用户身份（sandbox_user_key 不能为空），"
            "以防止未授权访问或越权挂载持久卷根目录。"
        )
    if sandbox_user_key:
        pvc_resolution = await resolve_shared_pvc(raw_existing_pvc)
    else:
        # 无用户身份（例如管理员连通性测试）：不挂载共享卷，退回独立空 PVC。
        pvc_resolution = {
            "pvc": None,
            "source": "isolated" if _isolation_requested else "unavailable",
        }
    existing_pvc = pvc_resolution["pvc"]
    if pvc_resolution["source"] == "auto":
        logger.info(
            "[workspace] K8s 沙箱自动共享平台数据卷（subPath 挂载用户工作区）：%s",
            existing_pvc,
        )
    for _mount_warning in evaluate_k8s_workspace_mount_config(
        namespace=namespace,
        pvc=existing_pvc,
        pvc_source=pvc_resolution["source"],
    ):
        logger.warning("[workspace] K8s 沙箱工作区挂载配置：%s", _mount_warning)
    storage_class = (
        await _sandbox_config_value(
            "sandbox_k8s_storage_class", "", config_overrides
        )
    ).strip() or None
    storage_size = (
        await _sandbox_config_value(
            "sandbox_k8s_storage_size", "1Gi", config_overrides
        )
    ).strip() or "1Gi"
    cpu_request = (
        await _sandbox_config_value(
            "sandbox_k8s_cpu_request", "100m", config_overrides
        )
    ).strip() or None
    memory_request = (
        await _sandbox_config_value(
            "sandbox_k8s_memory_request", "128Mi", config_overrides
        )
    ).strip() or None
    cpu_limit = (
        await _sandbox_config_value(
            "sandbox_k8s_cpu_limit", "1000m", config_overrides
        )
    ).strip() or None
    memory_limit = (
        await _sandbox_config_value(
            "sandbox_k8s_memory_limit", "1Gi", config_overrides
        )
    ).strip() or None
    delete_pvc_raw = (
        await _sandbox_config_value(
            "sandbox_k8s_delete_pvc_on_close", "false", config_overrides
        )
    ).strip().lower()
    delete_pvc_on_close = delete_pvc_raw in ("1", "true", "yes", "on")

    # 资源保障 (requests) 与上限 (limits) 规范组装
    resources: dict[str, Any] = {}
    requests: dict[str, str] = {}
    limits: dict[str, str] = {}
    if cpu_request:
        requests["cpu"] = cpu_request
    if memory_request:
        requests["memory"] = memory_request
    if requests:
        resources["requests"] = requests

    if cpu_limit:
        limits["cpu"] = cpu_limit
    if memory_limit:
        limits["memory"] = memory_limit
    if limits:
        resources["limits"] = limits

    effective_workspace_id = workspace_id or (
        f"nanzi-ws-{sandbox_user_key}" if sandbox_user_key else None
    )

    kwargs: dict[str, Any] = {
        "namespace": namespace,
        "image": image,
        "storage_class": storage_class,
        "storage_size": storage_size,
        "delete_pvc_on_close": delete_pvc_on_close,
        "resources": resources or None,
        # Kubernetes sandbox images (python:3.11-slim) do not ship ``mcp`` in
        # the system Python; the gateway venv at
        # ``/root/.agentscope/.venv/bin/python`` does. Pin the inline
        # ``sandbox`` MCP server to the gateway venv interpreter, otherwise
        # Bash MCP registration fails inside the sandbox gateway (HTTP 500)
        # and no usable Bash tool is exposed to the platform.
        "default_mcps": [
            build_container_tool_mcp(interpreter=K8S_GATEWAY_VENV_PYTHON)
        ],
        # K8s 冷启动 bootstrap 只装 _GATEWAY_BASE_REQUIREMENTS + extra_pip：
        # 补上 agentscope 工具链核心依赖，避免网关加载 Bash/MCP 工具时报
        # "HTTP 500: No module named 'xxx'"（预置镜像另由 BASE_REQS 保证）。
        "extra_pip": list(GATEWAY_EXTRA_PIP),
        "skill_paths": skill_paths,
    }
    if effective_workspace_id:
        kwargs["workspace_id"] = effective_workspace_id

    # 技能治理与 Docker 策略对齐：K8s 下宿主用户工作区经 subPath 挂进沙箱
    # （agent_workspaces/<key> → /workspace），技能根同样落在
    # <host_workdir>/sandbox/skills[-<layout>]，因此可以直接复用同一套指纹失效 /
    # seed 重建 / 分区同步逻辑，不必为 K8s 另写一份（此前 K8s 分支完全没有这段治理）。
    k8s_host_workdir = (
        os.path.join(os.path.abspath(workspace_root), sandbox_user_key)
        if workspace_root and sandbox_user_key
        else None
    )
    #: 技能集合令牌：与 Pod 名、subPath 目录同源（见 _skill_paths_cache_token）。
    layout = _skill_paths_cache_token(skill_paths)
    seed_invalidated = False
    skills_fingerprint: str | None = None
    if k8s_host_workdir:
        # 与 Docker 同一把用户级准备锁（键 = 宿主工作区路径），避免"两个技能集合同时
        # 迁移历史目录 / 争建个人技能源"。
        async with _sandbox_host_prepare_lock(k8s_host_workdir):
            _migrate_legacy_sandbox_skills_dir(k8s_host_workdir, layout)
            os.makedirs(_sandbox_skills_dir(k8s_host_workdir, layout), exist_ok=True)
            # 个人技能源目录也要存在：它以只读 subPath 挂进沙箱
            # （/workspace/personal-skills），源目录缺失时挂载会失败。
            os.makedirs(_personal_skills_dir(k8s_host_workdir), exist_ok=True)
        # 技能集合或权限变了就作废上一次的 seed 模板，让 initialize() 重新打包。
        # 与 Docker 同一套指纹（技能集合令牌 + 个人技能库 + 公共库），否则 K8s 下
        # 「换智能体/禁用/删除技能后沙箱仍留着旧副本」的窗口会一直存在。
        seed_invalidated, skills_fingerprint = _invalidate_stale_sandbox_skill_seed(
            k8s_host_workdir,
            await _sandbox_skills_seed_fingerprint(
                k8s_host_workdir, skill_paths, layout
            ),
            layout,
        )

    workspace = build_k8s_workspace_with_nanzi_adapter(
        K8sWorkspace,
        existing_pvc=existing_pvc,
        sandbox_user_key=sandbox_user_key,
        sandbox_skills_layout=layout,
        public_docs_mounted=True,
        local_data_root=workspace_root,
        **kwargs,
    )
    try:
        await workspace.initialize()
    except Exception:
        await _close_workspace_safely(workspace, reason="K8s initialization failure")
        raise
    await apply_sandbox_gateway_timeout(workspace)

    workspace._platform_sandbox_policy = SANDBOX_POLICY_K8S
    workspace._platform_execution_backend = SANDBOX_POLICY_K8S
    # K8s 适配器固定挂载公共文档（subPath docs → /workspace/public/docs），与 Docker
    # 的 _platform_sandbox_public_docs_mounted 语义一致，供目录清单与文件工具挂载
    # 映射共同判断。
    workspace._platform_sandbox_public_docs_mounted = True
    if k8s_host_workdir:
        # 顺序与 Docker 一致：initialize() 里 AgentScope 已按最新 skill_paths 重新
        # 打包 seed，先确认 seed 真的非空，再同步分区并记录指纹，否则打包失败会被
        # 当成「已是最新」而永久跳过。同步失败时不提交指纹，让下次启动重试。
        sync_failed: list[str] = []
        if seed_invalidated:
            _, sync_failed = _sync_public_skills_into_sandbox_partitions(
                k8s_host_workdir, layout
            )
        if not sync_failed:
            _commit_sandbox_skills_fingerprint(
                k8s_host_workdir, skills_fingerprint, layout
            )
    return workspace


async def _sandbox_config_value(
    key: str,
    default: str,
    config_overrides: Mapping[str, Any] | None,
) -> str:
    """读取沙箱配置，测试请求未覆盖时回退到持久化配置。

    管理端接口会对密钥脱敏。页面没有修改密钥时会把带 ``****`` 的展示值
    一并提交，此时必须忽略该展示值并从服务端读取真实密钥；空字符串则保留，
    以支持显式清空配置并回退到环境变量的场景。
    """
    from app.services.config_service import ConfigService

    if config_overrides is not None and key in config_overrides:
        value = config_overrides[key]
        if value is not None:
            text = str(value)
            if "****" not in text:
                return text
    value = await ConfigService.get(key, default)
    return default if value is None else str(value)


async def build_sandbox_workspace_for_test(
    policy: str,
    config_overrides: Mapping[str, Any] | None = None,
) -> Any:
    """按指定的临时配置初始化 E2B/SSH 沙箱，用于管理员连通性测试。"""
    normalized = str(policy or "").strip().lower()
    if normalized == SANDBOX_POLICY_E2B:
        return await _policy_e2b_workspace([], config_overrides)
    if normalized == SANDBOX_POLICY_SSH:
        return await _policy_ssh_workspace([], config_overrides)
    if normalized == SANDBOX_POLICY_K8S:
        return await _policy_k8s_workspace([], config_overrides)
    raise ValueError("仅支持 k8s、e2b 或 ssh 沙箱连接测试")


#: 当前正在执行的 Bash 工具在事件流/时间线中的节点 id（即模型侧 tool_call_id）。
#: 由 ``BashSandboxParentLinkMiddleware`` 在 Bash 工具调用进入 AgentScope 执行链时写入，
#: 供 ``LazySandboxBashNativeTool`` 把沙箱拉起进度挂到该 Bash 卡片下方。
#: 仅 Bash 路径消耗；其余触发 ensure_ready 的路径（文件工具、agent 构建预检等）不读取，
#: 天然回退到 preparation 节点。
current_bash_tool_parent_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_bash_tool_parent_id",
)


class LazySandboxWorkspaceProxy:
    """惰性沙箱代理：在未真正执行 Bash 前延迟拉起沙箱容器/Pod。"""

    def __init__(
        self,
        *,
        policy: str,
        root: str,
        user_key: str,
        skill_paths: list[str] | None,
        local_ws: Any | None = None,
        cache_key: str | None = None,
    ) -> None:
        self.policy = policy
        self.root = root
        self.user_key = user_key
        self.skill_paths = skill_paths
        self.local_ws = local_ws
        self.cache_key = cache_key
        self._real_sandbox_ws: Any | None = None
        self._sandbox_cache_key: str | None = None
        self._lock = asyncio.Lock()
        self._platform_sandbox_policy = policy
        self._platform_execution_backend = policy

    @property
    def is_alive(self) -> bool:
        if self._real_sandbox_ws is not None:
            return bool(getattr(self._real_sandbox_ws, "is_alive", True))
        return False

    async def ensure_ready(
        self,
        event_queue: Any | None = None,
        *,
        parent_id: str | None = None,
        log_id: str | None = None,
    ) -> Any:
        """确保沙箱拉起并就绪，带 SSE 日志推流。

        ``parent_id`` / ``log_id`` 为可选覆盖：由 Bash 路径（
        ``LazySandboxBashNativeTool``）传入 Bash 卡片节点 id，把拉起进度挂到
        该 Bash 卡片下方；缺省时维持 preparation 节点（文件工具、agent 构建预检等
        非 Bash 触发的保持原行为）。
        """
        if self._real_sandbox_ws is not None and getattr(self._real_sandbox_ws, "is_alive", True):
            return self._real_sandbox_ws

        async with self._lock:
            if self._real_sandbox_ws is not None and getattr(self._real_sandbox_ws, "is_alive", True):
                return self._real_sandbox_ws

            # 解析用于 SSE 推流的 event_queue
            queue = event_queue
            if queue is None:
                try:
                    from app.core.context import get_current_agent_context

                    ctx = get_current_agent_context()
                    queue = getattr(ctx, "event_queue", None)
                except Exception:
                    queue = None

            log_id = log_id or "workspace:sandbox"
            target_parent_id = parent_id or "preparation:auth_context_capability"
            if queue is not None:
                try:
                    await queue.put({
                        "type": "log",
                        "id": log_id,
                        "parent_id": target_parent_id,
                        "title": "沙箱工作区准备",
                        "details": "沙箱工作区创建中（正在拉起隔离容器/Pod），请稍候…",
                        "status": "pending",
                        "category": "system",
                        "timestamp": time.time(),
                    })
                except Exception as put_err:
                    logger.debug("[LazySandbox] Failed to put pending log: %s", put_err)

            start_t = time.monotonic()
            try:
                if self.policy == SANDBOX_POLICY_DOCKER:
                    ws, s_key = await _acquire_docker_workspace(
                        root=self.root,
                        user_key=self.user_key,
                        skill_paths=self.skill_paths,
                    )
                elif self.policy == SANDBOX_POLICY_K8S:
                    ws, s_key = await _acquire_k8s_workspace(
                        root=self.root,
                        user_key=self.user_key,
                        skill_paths=self.skill_paths,
                    )
                else:
                    raise RuntimeError(f"Unsupported lazy sandbox policy: {self.policy}")

                self._real_sandbox_ws = ws
                self._sandbox_cache_key = s_key
                # 缓存命中时不会重新走 initialize()，这里再对齐一次 gateway 超时，
                # 保证配置变更后旧沙箱也按最新的 agent_max_toolcall_timeout 等待。
                await apply_sandbox_gateway_timeout(ws)

                # 挂载配置
                if (
                    self.policy in (SANDBOX_POLICY_DOCKER, SANDBOX_POLICY_K8S)
                    and self.local_ws is not None
                ):
                    user_root = getattr(self.local_ws, "workspace_user_root", None)
                    if user_root:
                        ws._platform_sandbox_file_tool_mount_mappings = (
                            _build_sandbox_file_tool_mount_mappings(
                                user_root,
                                public_docs_mounted=bool(
                                    getattr(ws, "_platform_sandbox_public_docs_mounted", False)
                                ),
                            )
                        )
                ws._platform_sandbox_policy = self.policy
                ws._platform_execution_backend = self.policy

                # 如果有工作区 cache_key，更新登记
                if self.cache_key:
                    _workspace_cache[self.cache_key] = (ws, self.local_ws)
                    _workspace_sandbox_refs[self.cache_key] = s_key

                elapsed = (time.monotonic() - start_t) * 1000.0
                if queue is not None:
                    try:
                        await queue.put({
                            "type": "log",
                            "id": log_id,
                            "parent_id": target_parent_id,
                            "title": "沙箱工作区准备",
                            "details": f"沙箱工作区准备就绪（{int(elapsed)}ms）",
                            "status": "success",
                            "category": "system",
                            "timestamp": time.time(),
                        })
                    except Exception as put_err:
                        logger.debug("[LazySandbox] Failed to put success log: %s", put_err)

                return ws
            except Exception as exc:
                logger.warning("[LazySandbox] Failed to acquire sandbox: %s", exc)
                if queue is not None:
                    try:
                        await queue.put({
                            "type": "log",
                            "id": log_id,
                            "parent_id": target_parent_id,
                            "title": "沙箱工作区准备",
                            "details": f"沙箱工作区准备失败（{exc}）",
                            "status": "error",
                            "category": "system",
                            "timestamp": time.time(),
                        })
                    except Exception as put_err:
                        logger.debug("[LazySandbox] Failed to put error log: %s", put_err)

                if self.policy == SANDBOX_POLICY_DOCKER:
                    if isinstance(exc, DockerSandboxUnavailableError):
                        raise
                    raise DockerSandboxUnavailableError(
                        str(exc),
                        reason_code=_docker_init_reason_code(exc),
                    ) from exc
                if self.policy == SANDBOX_POLICY_K8S:
                    from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

                    if isinstance(exc, K8sSandboxUnavailableError):
                        raise
                    raise K8sSandboxUnavailableError(
                        str(exc),
                        reason_code="k8s_workspace_start_failed",
                    ) from exc
                raise

    async def close(self) -> None:
        if self._real_sandbox_ws is not None:
            if self._sandbox_cache_key is not None:
                await _release_sandbox_workspace(
                    self._sandbox_cache_key,
                    reason="LazySandboxWorkspaceProxy close",
                )
            else:
                await _close_workspace_safely(
                    self._real_sandbox_ws,
                    reason="LazySandboxWorkspaceProxy close",
                )
            self._real_sandbox_ws = None

    def __getattr__(self, name: str) -> Any:
        """代理属性访问。沙箱未拉起前对常规属性安全返回 None，并在 debug 日志中记录。"""
        if self._real_sandbox_ws is not None:
            return getattr(self._real_sandbox_ws, name)
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        logger.debug("[LazySandboxWorkspaceProxy] Accessing uninitialized attribute %r -> None", name)
        return None


async def get_local_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
    skills_custom: bool = False,
    allowed_global_skills: list[str] | None = None,
    lazy_sandbox: bool = False,
) -> tuple[Any, Any] | None:
    """Return ``(sandbox_ws, local_ws)`` for the conversation.

    Responsibility split (定稿): the sandbox (docker/e2b/ssh) executes Bash;
    Docker additionally receives the user workspace bind plus isolated child
    mounts for runtime skills and public docs. Read/Write/Edit/Glob/Grep are
    served by the host ``local_ws`` (a LocalWorkspace over the session workdir
    in the user's host directory).

    Return value is a 2-tuple ``(sandbox_ws, local_ws)`` whenever a
    ``conversation_id`` is available; either element may be ``None``:
      - local policy            -> ``(None, LocalWorkspace)``
      - docker/e2b/ssh policy   -> ``(sandbox_ws, LocalWorkspace)``
      - no id                   -> ``None``
      - Docker init failure     -> ``DockerSandboxUnavailableError`` (fail-closed)
    """
    if not conversation_id:
        from app.services.config_service import (
            ConfigService,
            resolve_effective_sandbox_policy,
        )

        policy_without_conversation = resolve_effective_sandbox_policy(
            await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL),
            SANDBOX_POLICY_LOCAL,
        )
        if policy_without_conversation in (SANDBOX_POLICY_DOCKER, SANDBOX_POLICY_K8S):
            if policy_without_conversation == SANDBOX_POLICY_DOCKER:
                raise DockerSandboxUnavailableError(
                    "Docker sandbox requires a conversation_id",
                    reason_code="docker_workspace_start_failed",
                    user_message="缺少会话 ID，Docker 沙箱未启动，Bash 未执行。",
                )
            from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError
            raise K8sSandboxUnavailableError(
                "K8s sandbox requires a conversation_id",
                reason_code="k8s_workspace_start_failed",
                user_message="缺少会话 ID，K8S 沙箱未启动，代码未执行。",
            )
        return None

    from app.services.config_service import (
        ConfigService,
        resolve_effective_sandbox_policy,
    )

    root = await resolve_workspace_root()
    workdir = resolve_session_workdir(
        root=root,
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
    )
    os.makedirs(workdir, exist_ok=True)
    skills_fp = (
        f"custom:{','.join(sorted(str(s) for s in (allowed_global_skills or []) if str(s).strip()))}"
        if skills_custom
        else "all"
    )
    policy = resolve_effective_sandbox_policy(
        await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL),
        SANDBOX_POLICY_LOCAL,
    )
    if policy not in KNOWN_SANDBOX_POLICIES:
        logger.warning("[workspace] Unknown sandbox_policy=%r, falling back to local", policy)
        policy = SANDBOX_POLICY_LOCAL
    # Ensure each (workdir, skills, policy) caches independently so an in-flight
    # policy switch never hands back a workspace built under another strategy.
    #
    # 技能集合与权限是**物化进沙箱**的（容器/Pod 里物理存在哪些技能由它决定），所以
    # 每次取用都要核对一次：管理员禁用/下架技能、公共技能内容改动都不改缓存键，
    # 缓存命中会把老快照直接交出去——沙箱里仍能翻到、能执行那些技能（详见
    # ``_sandbox_skills_snapshot_is_current``）。
    from app.services.ai.skills.policy import get_forbidden_skill_ids

    forbidden_skill_ids = await get_forbidden_skill_ids(_resolve_identity_user_id(user_info))
    skill_paths = discover_platform_skill_paths(
        user_info=user_info,
        skills_custom=skills_custom,
        allowed_global_skills=allowed_global_skills,
        forbidden_skill_ids=forbidden_skill_ids,
    )

    cache_key = f"{workdir}::{skills_fp}::{policy}"
    cached = _workspace_cache.get(cache_key)
    if cached is not None:
        sandbox_ws_cached, _ = _normalize_workspace_pair(cached)
        sandbox_cache_key = _workspace_sandbox_refs.get(cache_key)
        if sandbox_cache_key is None:
            # 纯宿主策略，或 lazy 代理尚未拉起：没有物化快照可核对。
            if isinstance(sandbox_ws_cached, LazySandboxWorkspaceProxy) and not lazy_sandbox:
                await sandbox_ws_cached.ensure_ready()
            await _prune_forbidden_session_skills(workdir, user_info)
            return cached
        if await _sandbox_skills_snapshot_is_current(
            sandbox_cache_key,
            skill_paths=skill_paths,
        ):
            if isinstance(sandbox_ws_cached, LazySandboxWorkspaceProxy) and not lazy_sandbox:
                await sandbox_ws_cached.ensure_ready()
            _touch_sandbox_workspace(sandbox_cache_key)
            # 缓存命中不代表权限没变：按当前禁用集合清一次会话快照
            # （见 ``_prune_forbidden_session_skills`` docstring）
            await _prune_forbidden_session_skills(workdir, user_info)
            return cached
        # 快照已过期：先逐出这台沙箱（连带引用它的会话条目），再落到下面按当前技能
        # 集合重建容器/Pod。逐出会打断同技能集合上**正在跑**的其它会话那一次 Bash，
        # 这是权限变更时"宁可报错也不越权执行"的取舍。
        logger.info(
            "[workspace] 沙箱技能快照过期，逐出并重建: %s", sandbox_cache_key
        )
        await _evict_sandbox_cache_entry(
            sandbox_cache_key, reason="skill snapshot stale"
        )
        cached = None

    is_sandbox = policy in (
        SANDBOX_POLICY_DOCKER,
        SANDBOX_POLICY_K8S,
        SANDBOX_POLICY_E2B,
        SANDBOX_POLICY_SSH,
    )

    sandbox_ws = None
    sandbox_cache_key: str | None = None
    sandbox_user_key: str | None = None
    try:
        if is_sandbox:
            if policy in (SANDBOX_POLICY_DOCKER, SANDBOX_POLICY_K8S):
                sandbox_user_key = _resolve_sandbox_user_key(
                    user_id=user_id,
                    user_name=user_name,
                    user_info=user_info,
                )
                if not sandbox_user_key:
                    raise RuntimeError(
                        f"{policy.upper()} sandbox requires an authenticated user identity"
                    )

                # 用与 _acquire_* 完全相同的算键函数判定「沙箱已经活着」：手写键格式
                # （此前少了技能集合令牌）会恒不命中，结果是一律走 lazy 代理，连
                # 「建会话即持有引用」的语义都丢了——没用过 Bash 的会话 refcount 为 0，
                # 别的会话释放/回收就能把它关掉。
                is_already_alive = False
                if policy == SANDBOX_POLICY_DOCKER:
                    d_key = _docker_workspace_cache_key(root, sandbox_user_key, skill_paths)
                    existing_dk = _docker_workspace_cache.get(d_key)
                    if existing_dk is not None and getattr(existing_dk, "is_alive", True) is not False:
                        is_already_alive = True
                elif policy == SANDBOX_POLICY_K8S:
                    k_key = _k8s_workspace_cache_key(root, sandbox_user_key, skill_paths)
                    existing_k8s = _k8s_workspace_cache.get(k_key)
                    if existing_k8s is not None and getattr(existing_k8s, "is_alive", True) is not False:
                        is_already_alive = True

                if lazy_sandbox and not is_already_alive:
                    sandbox_ws = LazySandboxWorkspaceProxy(
                        policy=policy,
                        root=root,
                        user_key=sandbox_user_key,
                        skill_paths=skill_paths,
                        cache_key=cache_key,
                    )
                else:
                    if policy == SANDBOX_POLICY_DOCKER:
                        sandbox_ws, sandbox_cache_key = await _acquire_docker_workspace(
                            root=root,
                            user_key=sandbox_user_key,
                            skill_paths=skill_paths,
                        )
                    else:
                        sandbox_ws, sandbox_cache_key = await _acquire_k8s_workspace(
                            root=root,
                            user_key=sandbox_user_key,
                            skill_paths=skill_paths,
                        )
            elif policy == SANDBOX_POLICY_E2B:
                sandbox_ws = await _policy_e2b_workspace(skill_paths)
            else:
                sandbox_ws = await _policy_ssh_workspace(skill_paths)
        else:
            sandbox_ws = None

        # Host local workspace serving file tools (Read/Write/Edit/Glob/Grep).
        # For the local policy this is the sole workspace; for sandbox policies
        # it backs the file tools against the user's host session workdir.
        from agentscope.workspace import LocalWorkspace

        # Pre-seed session skills/ with real copies + matching .skills index so
        # AgentScope's initialize() hash-skips all copies instead of duplicating
        # every skill into each session directory.
        _preseed_session_skills(workdir, skill_paths, forbidden_skill_ids, user_info)
        local_ws = LocalWorkspace(
            workdir=workdir,
            skill_paths=skill_paths,
        )
        if policy in (SANDBOX_POLICY_DOCKER, SANDBOX_POLICY_K8S) and sandbox_user_key:
            local_ws.workspace_user_root = os.path.join(root, sandbox_user_key)
            if sandbox_ws is not None and not isinstance(sandbox_ws, LazySandboxWorkspaceProxy):
                sandbox_ws._platform_sandbox_file_tool_mount_mappings = (
                    _build_sandbox_file_tool_mount_mappings(
                        local_ws.workspace_user_root,
                        public_docs_mounted=bool(
                            getattr(
                                sandbox_ws,
                                "_platform_sandbox_public_docs_mounted",
                                False,
                            )
                        ),
                    )
                )
        await local_ws.initialize()
        if sandbox_ws is not None:
            if isinstance(sandbox_ws, LazySandboxWorkspaceProxy):
                sandbox_ws.local_ws = local_ws
            else:
                sandbox_ws._platform_sandbox_policy = policy
                sandbox_ws._platform_execution_backend = policy
    except Exception as exc:
        if sandbox_cache_key is not None:
            await _release_sandbox_workspace(
                sandbox_cache_key,
                reason="host LocalWorkspace initialization failure",
            )
        elif sandbox_ws is not None:
            await _close_workspace_safely(
                sandbox_ws,
                reason="host LocalWorkspace initialization failure",
            )
        logger.warning("[workspace] Failed to initialize %s workspace workdir=%s: %s", policy, workdir, exc)
        if policy == SANDBOX_POLICY_DOCKER:
            if isinstance(exc, DockerSandboxUnavailableError):
                raise
            raise DockerSandboxUnavailableError(
                str(exc),
                reason_code=_docker_init_reason_code(exc),
            ) from exc
        if policy == SANDBOX_POLICY_K8S:
            from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError
            if isinstance(exc, K8sSandboxUnavailableError):
                raise
            raise K8sSandboxUnavailableError(
                str(exc),
                reason_code="k8s_workspace_start_failed",
            ) from exc
        return None

    pair = (sandbox_ws, local_ws)
    _workspace_cache[cache_key] = pair
    if sandbox_cache_key is not None:
        _workspace_sandbox_refs[cache_key] = sandbox_cache_key
    return pair


async def ensure_docker_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> Any:
    """Ensure the current user's Docker workspace container is running.

    This is a lifecycle-only operation: it initializes or reuses the same
    process-local workspace used by the chat runner and never executes a tool
    command inside the container.
    """
    if not str(conversation_id or "").strip():
        raise DockerSandboxUnavailableError(
            "conversation_id is required",
            reason_code="docker_workspace_start_failed",
            user_message="缺少会话 ID，无法启动当前用户的 Docker 沙箱。",
        )

    from app.services.config_service import (
        ConfigService,
        resolve_effective_sandbox_policy,
    )

    policy = resolve_effective_sandbox_policy(
        await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL),
        SANDBOX_POLICY_LOCAL,
    )
    if policy != SANDBOX_POLICY_DOCKER:
        raise DockerSandboxUnavailableError(
            f"Docker workspace requested while effective policy is {policy!r}",
            reason_code="docker_policy_not_effective",
            user_message="当前不是 Docker 沙箱模式，无需启动用户 Docker 容器。",
        )

    sandbox_user_key = _resolve_sandbox_user_key(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    if not sandbox_user_key:
        raise DockerSandboxUnavailableError(
            "Docker sandbox requires an authenticated user identity",
            reason_code="docker_workspace_identity_required",
            user_message="缺少当前用户身份，无法启动用户 Docker 沙箱。",
        )

    workspace_pair = await get_local_workspace(
        user_id=user_id,
        conversation_id=str(conversation_id).strip(),
        user_name=user_name,
        user_info=user_info,
        lazy_sandbox=False,
    )
    sandbox_ws, _local_ws = _normalize_workspace_pair(workspace_pair)
    if isinstance(sandbox_ws, LazySandboxWorkspaceProxy):
        sandbox_ws = await sandbox_ws.ensure_ready()
    if (
        sandbox_ws is None
        or getattr(sandbox_ws, "_platform_sandbox_policy", None)
        != SANDBOX_POLICY_DOCKER
    ):
        raise DockerSandboxUnavailableError(
            "Docker workspace was not bound to the current session",
            reason_code="docker_workspace_start_failed",
        )
    return sandbox_ws


async def docker_workspace_status(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect the current user's Docker workspace without initializing it.

    The UI can be remounted after the container was started by an earlier
    request, so process-local Vue state is not authoritative.  This function
    only performs Docker's ``GET /containers/{name}/json`` lookup; it never
    builds an image, creates a container, or executes a command.
    """
    if not str(conversation_id or "").strip():
        raise DockerSandboxUnavailableError(
            "conversation_id is required",
            reason_code="docker_workspace_status_failed",
            user_message="缺少会话 ID，无法查询当前用户的 Docker 沙箱。",
        )

    from app.services.config_service import (
        ConfigService,
        resolve_effective_sandbox_policy,
    )

    policy = resolve_effective_sandbox_policy(
        await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL),
        SANDBOX_POLICY_LOCAL,
    )
    if policy != SANDBOX_POLICY_DOCKER:
        raise DockerSandboxUnavailableError(
            f"Docker workspace status requested while effective policy is {policy!r}",
            reason_code="docker_policy_not_effective",
            user_message="当前不是 Docker 沙箱模式，无需查询用户 Docker 容器。",
        )

    sandbox_user_key = _resolve_sandbox_user_key(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    if not sandbox_user_key:
        raise DockerSandboxUnavailableError(
            "Docker sandbox requires an authenticated user identity",
            reason_code="docker_workspace_identity_required",
            user_message="缺少当前用户身份，无法查询用户 Docker 沙箱。",
        )

    root = await resolve_workspace_root()

    try:
        import aiodocker
    except Exception as exc:  # pragma: no cover - dependency is environment-specific
        raise DockerSandboxUnavailableError(
            f"aiodocker is unavailable: {exc}",
            reason_code="docker_daemon_unavailable",
            user_message="当前后端无法连接 Docker daemon，暂时无法查询沙箱状态。",
        ) from exc

    client: Any | None = None
    # 容器名带技能集合令牌。优先用会话上下文精确算出「该会话正在用的那一台」，再退回
    # 按容器标签反查该用户的沙箱——同一用户可能有多台（每技能集合一台）。
    container_name = _docker_container_name(sandbox_user_key)
    try:
        client = aiodocker.Docker()
        container, container_name = await _docker_first_existing_container(
            client,
            user_key=sandbox_user_key,
            root=root,
            conversation_id=conversation_id,
        )
        if container is None:
            return {
                "status": "stopped",
                "execution_backend": SANDBOX_POLICY_DOCKER,
                "workspace_id": sandbox_user_key,
                "container_id": None,
            }
        details = await container.show()
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "status", None) == 404:
            return {
                "status": "stopped",
                "execution_backend": SANDBOX_POLICY_DOCKER,
                "workspace_id": sandbox_user_key,
                "container_id": None,
            }
        reason_code = _docker_init_reason_code(exc)
        raise DockerSandboxUnavailableError(
            f"Docker workspace status lookup failed for {container_name}: {exc}",
            reason_code=reason_code,
            user_message="当前后端无法查询 Docker 沙箱状态，请检查 Docker daemon 和权限。",
        ) from exc
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass

    state = details.get("State") if isinstance(details, dict) else None
    running = bool(state.get("Running")) if isinstance(state, dict) else False
    started_at = state.get("StartedAt") if isinstance(state, dict) else None
    uptime_seconds: int | None = None
    if running and started_at:
        try:
            from datetime import datetime, timezone
            clean_ts = re.sub(r'(\.\d{6})\d+', r'\1', str(started_at).replace("Z", "+00:00"))
            dt = datetime.fromisoformat(clean_ts)
            uptime_seconds = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
        except Exception:
            uptime_seconds = None

    return {
        "status": "running" if running else "stopped",
        "execution_backend": SANDBOX_POLICY_DOCKER,
        "workspace_id": sandbox_user_key,
        "container_id": (
            details.get("Id")
            if isinstance(details, dict)
            else getattr(container, "id", None)
        ),
        "started_at": started_at if running else None,
        "uptime_seconds": uptime_seconds if running else None,
    }


async def _evict_all_workspaces_for_user(sandbox_user_key: str, *, reason: str) -> None:
    """逐出指定用户在所有 workspace_root 下的 Docker 沙箱缓存。"""
    matching_keys = [
        k for k in list(_docker_workspace_cache.keys())
        if f"::{sandbox_user_key}::" in k or k.endswith(f"::{sandbox_user_key}")
    ]
    for key in matching_keys:
        try:
            await _evict_docker_workspace_cache_entry(key, reason=reason)
        except Exception as exc:
            logger.warning("[workspace] Failed to evict cache key %s: %s", key, exc)


async def stop_docker_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """停止当前用户的 Docker 沙箱容器并清理运行时缓存。"""
    if not str(conversation_id or "").strip():
        raise DockerSandboxUnavailableError(
            "conversation_id is required",
            reason_code="docker_workspace_stop_failed",
            user_message="缺少会话 ID，无法停止当前用户的 Docker 沙箱。",
        )

    from app.services.config_service import (
        ConfigService,
        resolve_effective_sandbox_policy,
    )

    policy = resolve_effective_sandbox_policy(
        await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL),
        SANDBOX_POLICY_LOCAL,
    )
    if policy != SANDBOX_POLICY_DOCKER:
        raise DockerSandboxUnavailableError(
            f"Docker workspace stop requested while effective policy is {policy!r}",
            reason_code="docker_policy_not_effective",
            user_message="当前不是 Docker 沙箱模式，无需停止 Docker 容器。",
        )

    sandbox_user_key = _resolve_sandbox_user_key(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    if not sandbox_user_key:
        raise DockerSandboxUnavailableError(
            "Docker sandbox requires an authenticated user identity",
            reason_code="docker_workspace_identity_required",
            user_message="缺少当前用户身份，无法停止用户 Docker 沙箱。",
        )

    # 1. 逐出进程内缓存
    await _evict_all_workspaces_for_user(sandbox_user_key, reason="user requested container stop")

    # 2. 尝试停止 Docker 实体容器
    #    一个用户可能有多台沙箱（每技能集合一台），容器名带令牌，因此按标签反查全部候选
    #    并逐台停掉——只按 `<user_key>` 拼一个名字会漏掉真正在跑的那台。
    try:
        import aiodocker
        client = aiodocker.Docker()
        try:
            for container_name in await _docker_sandbox_container_names(
                client, user_key=sandbox_user_key
            ):
                try:
                    container = await client.containers.get(container_name)
                    details = await container.show()
                    state = details.get("State") if isinstance(details, dict) else {}
                    if state.get("Running"):
                        await container.stop()
                except Exception as exc:  # noqa: BLE001
                    if getattr(exc, "status", None) != 404:
                        logger.warning(
                            "[workspace] Failed to stop container %s: %s",
                            container_name,
                            exc,
                        )
        finally:
            await client.close()
    except Exception as exc:
        logger.warning("[workspace] Docker client error during stop: %s", exc)

    return {
        "status": "stopped",
        "execution_backend": SANDBOX_POLICY_DOCKER,
        "workspace_id": sandbox_user_key,
        "container_id": None,
    }


async def restart_docker_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """删除当前用户的旧 Docker 容器并重新拉起全新的沙箱容器。"""
    if not str(conversation_id or "").strip():
        raise DockerSandboxUnavailableError(
            "conversation_id is required",
            reason_code="docker_workspace_restart_failed",
            user_message="缺少会话 ID，无法重启当前用户的 Docker 沙箱。",
        )

    from app.services.config_service import (
        ConfigService,
        resolve_effective_sandbox_policy,
    )

    policy = resolve_effective_sandbox_policy(
        await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL),
        SANDBOX_POLICY_LOCAL,
    )
    if policy != SANDBOX_POLICY_DOCKER:
        raise DockerSandboxUnavailableError(
            f"Docker workspace restart requested while effective policy is {policy!r}",
            reason_code="docker_policy_not_effective",
            user_message="当前不是 Docker 沙箱模式，无需重启 Docker 容器。",
        )

    sandbox_user_key = _resolve_sandbox_user_key(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    if not sandbox_user_key:
        raise DockerSandboxUnavailableError(
            "Docker sandbox requires an authenticated user identity",
            reason_code="docker_workspace_identity_required",
            user_message="缺少当前用户身份，无法重启用户 Docker 沙箱。",
        )

    # 1. 逐出进程内缓存
    await _evict_all_workspaces_for_user(sandbox_user_key, reason="user requested container restart")

    # 2. 强制删除旧容器（同上：一个用户可能有多台，逐台删）
    try:
        import aiodocker
        client = aiodocker.Docker()
        try:
            for container_name in await _docker_sandbox_container_names(
                client, user_key=sandbox_user_key
            ):
                try:
                    container = await client.containers.get(container_name)
                    await container.delete(force=True)
                except Exception as exc:  # noqa: BLE001
                    if getattr(exc, "status", None) != 404:
                        logger.warning(
                            "[workspace] Failed to force delete container %s: %s",
                            container_name,
                            exc,
                        )
        finally:
            await client.close()
    except Exception as exc:
        logger.warning("[workspace] Docker client error during restart container delete: %s", exc)

    # 3. 重新拉起并初始化容器
    workspace = await ensure_docker_workspace(
        user_id=user_id,
        conversation_id=str(conversation_id).strip(),
        user_name=user_name,
        user_info=user_info,
    )
    return docker_workspace_runtime_metadata(workspace)


async def exec_docker_workspace_command(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    command: str,
    workdir: str | None = None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在当前用户的 Docker 容器内执行命令，供命令行终端交互访问。"""
    if not str(conversation_id or "").strip():
        raise DockerSandboxUnavailableError(
            "conversation_id is required",
            reason_code="docker_workspace_exec_failed",
            user_message="缺少会话 ID，无法在 Docker 沙箱中执行命令。",
        )

    cmd_clean = str(command or "").strip()
    if not cmd_clean:
        return {
            "stdout": "",
            "stderr": "",
            "output": "",
            "exit_code": 0,
            "duration_ms": 0,
            "workdir": workdir or DOCKER_WORKSPACE_LOGICAL_ROOT,
        }

    from app.services.config_service import (
        ConfigService,
        resolve_effective_sandbox_policy,
    )

    policy = resolve_effective_sandbox_policy(
        await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL),
        SANDBOX_POLICY_LOCAL,
    )
    if policy != SANDBOX_POLICY_DOCKER:
        raise DockerSandboxUnavailableError(
            f"Docker workspace exec requested while effective policy is {policy!r}",
            reason_code="docker_policy_not_effective",
            user_message="当前不是 Docker 沙箱模式，无法使用 Docker 终端。",
        )

    sandbox_user_key = _resolve_sandbox_user_key(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    if not sandbox_user_key:
        raise DockerSandboxUnavailableError(
            "Docker sandbox requires an authenticated user identity",
            reason_code="docker_workspace_identity_required",
            user_message="缺少当前用户身份，无法访问 Docker 沙箱。",
        )

    try:
        import aiodocker
    except Exception as exc:
        raise DockerSandboxUnavailableError(
            f"aiodocker is unavailable: {exc}",
            reason_code="docker_daemon_unavailable",
            user_message="当前后端无法连接 Docker daemon，暂时无法执行命令。",
        ) from exc

    root = await resolve_workspace_root()

    client: Any | None = None
    # 容器名带技能集合令牌。优先用会话上下文精确算出「该会话正在用的那一台」，再退回
    # 按标签反查——否则用户点终端可能进到同一用户另一个技能集合的沙箱。
    container_name = _docker_container_name(sandbox_user_key)
    start_time = time.monotonic()
    try:
        client = aiodocker.Docker()
        container, container_name = await _docker_first_existing_container(
            client,
            user_key=sandbox_user_key,
            root=root,
            conversation_id=conversation_id,
        )
        if container is None:
            raise DockerSandboxUnavailableError(
                f"Docker sandbox container not found for {sandbox_user_key}",
                reason_code="docker_container_not_found",
                user_message="未找到运行中的 Docker 沙箱容器，请先启动沙箱后再进入终端。",
            )
        details = await container.show()
        state = details.get("State") if isinstance(details, dict) else {}
        if not state.get("Running"):
            raise DockerSandboxUnavailableError(
                "Docker sandbox container is not running",
                reason_code="docker_container_not_running",
                user_message="Docker 容器未在运行中，请先启动容器后再进入终端。",
            )

        # 只读入口（终端）也要核对技能快照：禁用/下架之后，这台沙箱里的技能集合可能
        # 已经"平台认为不可用"，但它还能被 cat / 执行。过期就逐出并拒绝执行，与对话
        # 取用路径同一取舍（宁可报错也不越权执行）。
        stale = await _conversation_stale_skills_snapshot(
            root=root,
            user_key=sandbox_user_key,
            conversation_id=conversation_id,
            policy=SANDBOX_POLICY_DOCKER,
            user_info=user_info,
        )
        if stale is not None:
            stale_key, _stale_paths = stale
            await _evict_sandbox_cache_entry(
                stale_key, reason="terminal_docker_skills_stale"
            )
            raise DockerSandboxUnavailableError(
                "Docker sandbox skills snapshot is stale",
                reason_code="docker_sandbox_skills_stale",
                user_message=(
                    "该沙箱的技能集合已变更（技能被禁用/下架或白名单调整），已重置沙箱。"
                    "请先发送一条消息拉起新沙箱，再使用终端。"
                ),
            )

        effective_workdir = (workdir or "").strip() or DOCKER_WORKSPACE_LOGICAL_ROOT
        pwd_marker = f"__NANZI_PWD_{uuid.uuid4().hex}__"
        wrapped_command = (
            f"{{\n"
            f"{cmd_clean}\n"
            f"}}\n"
            f"__NZ_RET=$?\n"
            f"printf '\\n{pwd_marker}:%s\\n' \"$PWD\"\n"
            f"exit $__NZ_RET"
        )
        # 终端命令可能长时间运行：执行期间心跳刷新活跃时间，避免容器被空闲回收
        # （此前只在命令结束后才 touch，长命令跑到一半会被 reaper 关掉）。
        async with _sandbox_activity_heartbeat(
            sandbox_user_key=sandbox_user_key,
            policy=SANDBOX_POLICY_DOCKER,
        ):
            exec_obj = await container.exec(
                cmd=["/bin/bash", "-c", wrapped_command],
                workdir=effective_workdir,
                stdout=True,
                stderr=True,
                stdin=False,
                tty=False,
            )
            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []

            async with exec_obj.start(detach=False) as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        break
                    stream_type = getattr(msg, "stream", 1)
                    raw_data = getattr(msg, "data", msg)
                    if isinstance(raw_data, (bytes, bytearray)):
                        text = raw_data.decode("utf-8", errors="replace")
                    else:
                        text = str(raw_data or "")
                    if stream_type == 2:
                        stderr_chunks.append(text)
                    else:
                        stdout_chunks.append(text)

            inspect_info = await exec_obj.inspect()
            exit_code = inspect_info.get("ExitCode", 0)
        duration_ms = max(0, int((time.monotonic() - start_time) * 1000))

        full_stdout = "".join(stdout_chunks)
        full_stderr = "".join(stderr_chunks)
        final_workdir = effective_workdir

        # 解析并清除 marker，提取命令执行后的真实工作目录
        if pwd_marker in full_stdout:
            parts = full_stdout.split(f"{pwd_marker}:")
            full_stdout = parts[0].rstrip("\r\n")
            if len(parts) > 1:
                tail = parts[1].splitlines()
                if tail and tail[0].strip():
                    final_workdir = tail[0].strip()

        if full_stdout and full_stderr:
            full_output = f"{full_stdout}\n{full_stderr}"
        else:
            full_output = full_stdout if full_stdout else full_stderr

        # 刷新活跃时间
        for k in list(_docker_workspace_cache.keys()):
            if f"::{sandbox_user_key}::" in k or k.endswith(f"::{sandbox_user_key}"):
                _touch_docker_workspace(k)

        return {
            "stdout": full_stdout,
            "stderr": full_stderr,
            "output": full_output,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "workdir": final_workdir,
            "container_id": details.get("Id") if isinstance(details, dict) else getattr(container, "id", None),
        }
    except DockerSandboxUnavailableError:
        raise
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            raise DockerSandboxUnavailableError(
                "Docker sandbox container not found",
                reason_code="docker_container_not_running",
                user_message="Docker 容器不存在或已销毁，请先启动容器。",
            ) from exc
        reason_code = _docker_init_reason_code(exc)
        raise DockerSandboxUnavailableError(
            f"Docker exec failed for {container_name}: {exc}",
            reason_code=reason_code,
            user_message=f"命令执行失败：{exc}",
        ) from exc
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Kubernetes sandbox runtime family (mirrors the Docker runtime family above)
# ---------------------------------------------------------------------------


async def _k8s_runtime_guard(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None,
    user_info: dict[str, Any] | None,
    operation: str,
) -> str:
    """Validate session / effective policy / identity for a k8s runtime op.

    Returns the resolved ``sandbox_user_key``. Raises
    ``K8sSandboxUnavailableError`` with user-friendly text otherwise.
    """
    from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

    if not str(conversation_id or "").strip():
        raise K8sSandboxUnavailableError(
            f"K8s sandbox {operation} requires a conversation_id",
            reason_code=f"k8s_workspace_{operation}_failed",
            user_message="缺少会话 ID，无法操作当前用户的 Kubernetes 沙箱。",
        )

    from app.services.config_service import (
        ConfigService,
        resolve_effective_sandbox_policy,
    )

    policy = resolve_effective_sandbox_policy(
        await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL),
        SANDBOX_POLICY_LOCAL,
    )
    if policy != SANDBOX_POLICY_K8S:
        raise K8sSandboxUnavailableError(
            f"K8s sandbox {operation} requested while effective policy is {policy!r}",
            reason_code="k8s_policy_not_effective",
            user_message="当前不是 Kubernetes 沙箱模式，无需操作沙箱 Pod。",
        )

    user_key = _resolve_sandbox_user_key(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    if not user_key:
        raise K8sSandboxUnavailableError(
            f"K8s sandbox {operation} requires an authenticated user identity",
            reason_code="k8s_identity_required",
            user_message="缺少当前用户身份，无法操作 Kubernetes 沙箱。",
        )
    return user_key


async def _evict_all_k8s_workspaces_for_user(sandbox_user_key: str, *, reason: str) -> None:
    """Evict the user's K8s workspaces under every workspace root (and close Pods)."""
    matching_keys = [
        k for k in list(_k8s_workspace_cache.keys())
        if f"::{sandbox_user_key}::" in k or k.endswith(f"::{sandbox_user_key}")
    ]
    for key in matching_keys:
        try:
            await _evict_k8s_workspace_cache_entry(key, reason=reason)
        except Exception as exc:
            logger.warning("[workspace] Failed to evict k8s cache key %s: %s", key, exc)


async def _delete_k8s_sandbox_pods_for_user(sandbox_user_key: str) -> list[str]:
    """删掉该用户在集群里**真实存在**的所有沙箱 Pod，返回删掉的 Pod 名列表。

    与 Docker 的 ``_docker_sandbox_container_names`` 对齐：Pod 名带技能集合令牌，没法
    拼出来，只能按 AgentScope 的标签（``agentscope.workspace.id``）反查。只遍历进程内
    缓存是不够的——进程重启过、Pod 由别的副本创建、或缓存已被逐出时，接口会照样回
    ``status="stopped"``，而 Pod 仍占着 CPU/内存继续跑（"停止沙箱"变成一句假话），
    要等孤儿回收器按最小存活时长兜底。
    """
    from app.services.ai.runtime.agentscope.k8s_workspace import (
        delete_k8s_sandbox_pod,
        find_k8s_sandbox_pod_names,
        resolve_sandbox_namespace,
    )

    try:
        from app.services.config_service import ConfigService

        namespace = resolve_sandbox_namespace(
            await ConfigService.get("sandbox_k8s_namespace", "")
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] k8s namespace resolve failed: %s", exc)
        namespace = resolve_sandbox_namespace(None)

    names: list[str] = []
    try:
        names.extend(
            await find_k8s_sandbox_pod_names(
                namespace, [_sandbox_workspace_id_pattern(sandbox_user_key)]
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[workspace] k8s sandbox pod label scan failed: %s", exc)

    # 标签扫描不可用时退回缓存里已知的 Pod 名，尽量别漏
    for key in list(_k8s_workspace_cache.keys()):
        if f"::{sandbox_user_key}::" not in key and not key.endswith(
            f"::{sandbox_user_key}"
        ):
            continue
        workspace = _k8s_workspace_cache.get(key)
        pod_name = getattr(workspace, "_pod_name", None) or getattr(
            workspace, "pod_name", None
        )
        if pod_name:
            names.append(str(pod_name))

    deleted: list[str] = []
    for pod_name in dict.fromkeys(names):
        try:
            if await delete_k8s_sandbox_pod(namespace, pod_name):
                deleted.append(pod_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[workspace] Failed to delete pod %s: %s", pod_name, exc)
    if deleted:
        logger.info(
            "[workspace] Deleted k8s sandbox pods for %s: %s",
            sandbox_user_key,
            ", ".join(deleted),
        )
    return deleted


async def _k8s_workspace_pod_identity(workspace: Any) -> tuple[str | None, str | None]:
    """Return (namespace, pod_name) best-effort for a live k8s workspace."""
    from app.services.config_service import ConfigService
    from app.services.ai.runtime.agentscope.k8s_workspace import (
        resolve_sandbox_namespace,
    )

    namespace = getattr(workspace, "_namespace", None) or resolve_sandbox_namespace(
        await ConfigService.get("sandbox_k8s_namespace", "")
    )
    pod_name = getattr(workspace, "_pod_name", None) or getattr(workspace, "pod_name", None)
    return namespace, pod_name


def _uptime_from_started_at(started_at: str | None) -> int | None:
    """Seconds since ``started_at`` (UTC ISO), or None when unavailable."""
    if not started_at:
        return None
    try:
        from datetime import datetime, timezone

        started = datetime.fromisoformat(str(started_at))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    except Exception:
        return None


def k8s_workspace_metadata(workspace: Any) -> dict[str, Any]:
    """Return safe, user-facing metadata for an initialized K8s workspace.

    ``started_at`` reflects the process-recorded ``_platform_started_at`` set
    by ``_record_k8s_started_at`` during ensure/restart (live Pod start time
    is preferred by ``k8s_workspace_status``, which queries the Pod itself).
    """
    pod_name = getattr(workspace, "_pod_name", None) or getattr(workspace, "pod_name", None)
    started_at = getattr(workspace, "_platform_started_at", None)
    return {
        "status": "running" if getattr(workspace, "is_alive", True) else "stopped",
        "execution_backend": getattr(
            workspace,
            "_platform_execution_backend",
            SANDBOX_POLICY_K8S,
        ),
        "workspace_id": getattr(workspace, "workspace_id", None),
        "pod_name": pod_name,
        "started_at": started_at,
        "uptime_seconds": _uptime_from_started_at(started_at),
    }


def _record_k8s_started_at(workspace: Any) -> None:
    """Best-effort record of the Pod start time on the workspace object.

    Called on ensure/restart success so the running duration stays visible even
    when a live Pod probe is unavailable (mirrors Docker's
    ``_platform_started_at`` semantics).
    """
    from datetime import datetime, timezone

    if getattr(workspace, "_platform_started_at", None):
        return
    workspace._platform_started_at = datetime.now(timezone.utc).isoformat()


async def k8s_workspace_status(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect the current user's K8s workspace Pod without initializing it.

    Read-only: never builds a workspace or creates a Pod. When a process-local
    workspace is cached, prefers the live Pod phase via ``read_k8s_sandbox_pod``
    and degrades to the cached ``is_alive`` view when the lookup is unavailable.
    When nothing is cached (multi-worker forwarding / cache already reaped),
    reflects a best-effort Pod probe by name so a live Pod is still reported as
    running instead of a misleading ``idle``.
    """
    user_key = await _k8s_runtime_guard(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
        operation="status",
    )

    root = await resolve_workspace_root()
    workspace = _find_cached_k8s_workspace(root, user_key, conversation_id=conversation_id)
    if workspace is None or getattr(workspace, "is_alive", True) is False:
        # 进程内无活跃缓存（多 worker 转发、缓存已被回收/reaper 清理等场景）：
        # 仍按约定 Pod 名做一次只读探测真实状态，绝不创建/修改任何资源。
        probed = await _k8s_probe_workspace_pod(user_key)
        if probed is not None:
            return probed
        return {
            "status": "idle",
            "running": False,
            "execution_backend": SANDBOX_POLICY_K8S,
            "workspace_id": user_key,
            "pod_name": None,
            "started_at": None,
            "uptime_seconds": None,
        }

    namespace, pod_name = await _k8s_workspace_pod_identity(workspace)
    started_at = getattr(workspace, "_platform_started_at", None)

    if pod_name:
        try:
            from app.services.ai.runtime.agentscope.k8s_workspace import (
                read_k8s_sandbox_pod,
            )

            pod_info = await read_k8s_sandbox_pod(
                namespace=namespace,
                pod_name=pod_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[workspace] k8s status pod probe failed for %s: %s", pod_name, exc)
            pod_info = {"available": False, "found": None, "phase": None, "start_time": None}

        if pod_info.get("available") and pod_info.get("found"):
            phase = pod_info.get("phase")
            live_start = pod_info.get("start_time") or started_at
            if phase == "Running":
                if pod_info.get("deleting"):
                    # Terminating：phase 仍为 Running，但 Pod 正在删除，映射为 stopping
                    return {
                        "status": "stopping",
                        "running": False,
                        "execution_backend": SANDBOX_POLICY_K8S,
                        "workspace_id": user_key,
                        "pod_name": pod_name,
                        "started_at": None,
                        "uptime_seconds": None,
                    }
                return {
                    "status": "running",
                    "running": True,
                    "execution_backend": SANDBOX_POLICY_K8S,
                    "workspace_id": user_key,
                    "pod_name": pod_name,
                    "started_at": live_start,
                    "uptime_seconds": _uptime_from_started_at(live_start),
                }
            if phase == "Pending":
                return {
                    "status": "starting",
                    "running": False,
                    "execution_backend": SANDBOX_POLICY_K8S,
                    "workspace_id": user_key,
                    "pod_name": pod_name,
                    "started_at": None,
                    "uptime_seconds": None,
                }
            # Succeeded / Failed / Unknown -> stopped view
            return {
                "status": "stopped",
                "running": False,
                "execution_backend": SANDBOX_POLICY_K8S,
                "workspace_id": user_key,
                "pod_name": pod_name,
                "started_at": None,
                "uptime_seconds": None,
            }
        if pod_info.get("available") and pod_info.get("found") is False:
            # Confirmed absent (e.g. deleted out-of-band): idle view.
            return {
                "status": "idle",
                "running": False,
                "execution_backend": SANDBOX_POLICY_K8S,
                "workspace_id": user_key,
                "pod_name": pod_name,
                "started_at": None,
                "uptime_seconds": None,
            }
        # unavailable probe -> degrade to cached view below

    is_alive = bool(getattr(workspace, "is_alive", True))
    return {
        "status": "running" if is_alive else "stopped",
        "running": is_alive,
        "execution_backend": SANDBOX_POLICY_K8S,
        "workspace_id": user_key,
        "pod_name": pod_name,
        "started_at": started_at,
        "uptime_seconds": _uptime_from_started_at(started_at) if is_alive else None,
    }


async def _k8s_probe_workspace_pod(user_key: str) -> dict[str, Any] | None:
    """Best-effort live Pod probe for a user key without a process-local workspace.

    AgentScope ``K8sWorkspace`` builds the Pod name from ``workspace_id``; 平台现在把
    技能集合令牌放进 ``workspace_id``（物理沙箱与缓存条目一一对应），所以这里不再
    自己拼名字，而是按 AgentScope 的 Pod 标签（``agentscope.workspace.id``）反查该用户
    真实存在的那个 Pod，再退回历史命名 ``as-ws-<user_key>``。
    Reflection is read-only and never mutates cluster state. Returns a status
    dict when the Pod is found, or ``None`` when absent / probe unavailable
    (callers fall back to ``idle``).
    """
    from app.services.ai.runtime.agentscope.k8s_workspace import (
        find_k8s_sandbox_pod_name,
        read_k8s_sandbox_pod,
        resolve_sandbox_namespace,
    )
    from app.services.config_service import ConfigService

    namespace = resolve_sandbox_namespace(
        await ConfigService.get("sandbox_k8s_namespace", "")
    )
    pattern = _sandbox_workspace_id_pattern(user_key)
    try:
        discovered = await find_k8s_sandbox_pod_name(namespace, [pattern])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] k8s pod label lookup failed for %s: %s", user_key, exc)
        discovered = None
    pod_name = discovered or _k8s_pod_name(user_key)
    try:
        info = await read_k8s_sandbox_pod(namespace=namespace, pod_name=pod_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[workspace] k8s best-effort pod probe failed for %s: %s", pod_name, exc)
        return None

    if not info.get("available") or info.get("found") is not True:
        return None

    phase = info.get("phase")
    if phase == "Running":
        if info.get("deleting"):
            # Terminating：phase 仍为 Running，但 Pod 正在删除，映射为 stopping
            return {
                "status": "stopping",
                "running": False,
                "execution_backend": SANDBOX_POLICY_K8S,
                "workspace_id": user_key,
                "pod_name": pod_name,
                "started_at": None,
                "uptime_seconds": None,
            }
        return {
            "status": "running",
            "running": True,
            "execution_backend": SANDBOX_POLICY_K8S,
            "workspace_id": user_key,
            "pod_name": pod_name,
            "started_at": info.get("start_time"),
            "uptime_seconds": _uptime_from_started_at(info.get("start_time")),
        }
    if phase == "Pending":
        return {
            "status": "starting",
            "running": False,
            "execution_backend": SANDBOX_POLICY_K8S,
            "workspace_id": user_key,
            "pod_name": pod_name,
            "started_at": None,
            "uptime_seconds": None,
        }
    return {
        "status": "stopped",
        "running": False,
        "execution_backend": SANDBOX_POLICY_K8S,
        "workspace_id": user_key,
        "pod_name": pod_name,
        "started_at": None,
        "uptime_seconds": None,
    }


async def ensure_k8s_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start (warm up) or reuse the current user's K8s sandbox workspace.

    Mirrors ``ensure_docker_workspace``: goes through ``get_local_workspace``
    so the Pod shares the exact cache key with the next chat turn.
    """
    user_key = await _k8s_runtime_guard(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
        operation="ensure",
    )

    workspace_pair = await get_local_workspace(
        user_id=user_id,
        conversation_id=str(conversation_id).strip(),
        user_name=user_name,
        user_info=user_info,
        lazy_sandbox=False,
    )
    sandbox_ws, _local_ws = _normalize_workspace_pair(workspace_pair)
    if isinstance(sandbox_ws, LazySandboxWorkspaceProxy):
        sandbox_ws = await sandbox_ws.ensure_ready()
    if (
        sandbox_ws is None
        or getattr(sandbox_ws, "_platform_sandbox_policy", None)
        != SANDBOX_POLICY_K8S
    ):
        from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

        raise K8sSandboxUnavailableError(
            "K8s workspace was not bound to the current session",
            reason_code="k8s_workspace_ensure_failed",
            user_message="Kubernetes 沙箱未成功绑定当前会话，请稍后重试。",
        )

    _record_k8s_started_at(sandbox_ws)
    return k8s_workspace_metadata(sandbox_ws)


async def stop_k8s_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stop the current user's K8s sandbox: evict caches and close the Pod."""
    user_key = await _k8s_runtime_guard(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
        operation="stop",
    )
    await _evict_all_k8s_workspaces_for_user(
        user_key,
        reason="user requested sandbox stop",
    )
    # 缓存逐出只关掉"本进程认识的"工作区；集群里真实存在的 Pod 必须按标签再删一遍，
    # 否则进程重启过/别的副本创建的 Pod 会一直跑着，而接口回的是 "stopped"。
    deleted_pods = await _delete_k8s_sandbox_pods_for_user(user_key)
    return {
        "status": "stopped",
        "execution_backend": SANDBOX_POLICY_K8S,
        "workspace_id": user_key,
        "pod_name": None,
        "deleted_pods": deleted_pods,
        "started_at": None,
        "uptime_seconds": None,
    }


async def restart_k8s_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Delete the current user's K8s sandbox Pod and recreate it fresh."""
    user_key = await _k8s_runtime_guard(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
        operation="restart",
    )
    await _evict_all_k8s_workspaces_for_user(
        user_key,
        reason="user requested sandbox restart",
    )
    # 重启 = 先真的删掉旧 Pod（含本进程不认识的），再重新物化一台干净的
    await _delete_k8s_sandbox_pods_for_user(user_key)

    workspace_pair = await get_local_workspace(
        user_id=user_id,
        conversation_id=str(conversation_id).strip(),
        user_name=user_name,
        user_info=user_info,
    )
    sandbox_ws, _local_ws = _normalize_workspace_pair(workspace_pair)
    if (
        sandbox_ws is None
        or getattr(sandbox_ws, "_platform_sandbox_policy", None)
        != SANDBOX_POLICY_K8S
    ):
        from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

        raise K8sSandboxUnavailableError(
            "K8s workspace was not recreated for the current session",
            reason_code="k8s_workspace_restart_failed",
            user_message="Kubernetes 沙箱重启失败，请稍后重试。",
        )

    _record_k8s_started_at(sandbox_ws)
    return k8s_workspace_metadata(sandbox_ws)


def docker_workspace_runtime_metadata(workspace: Any) -> dict[str, Any]:
    """Return safe, user-facing metadata for an initialized Docker workspace."""
    container_id = getattr(workspace, "_platform_container_id", None)
    if not container_id:
        container = getattr(workspace, "_container", None)
        container_id = getattr(container, "id", None)
    started_at = getattr(workspace, "_platform_started_at", None)
    return {
        "status": "running" if getattr(workspace, "is_alive", True) else "stopped",
        "execution_backend": getattr(
            workspace,
            "_platform_execution_backend",
            SANDBOX_POLICY_DOCKER,
        ),
        "workspace_id": getattr(workspace, "_platform_workspace_id", None)
        or getattr(workspace, "workspace_id", None),
        "container_id": container_id,
        "started_at": started_at,
        "uptime_seconds": 0 if started_at else None,
    }


def get_workspace_execution_backend(workspace: Any) -> str | None:
    """Return the backend actually bound to the Bash-capable workspace."""
    sandbox_ws, _local_ws = _normalize_workspace_pair(workspace)
    if sandbox_ws is None:
        return None
    backend = getattr(sandbox_ws, "_platform_execution_backend", None)
    if backend in {
        SANDBOX_POLICY_DOCKER,
        SANDBOX_POLICY_E2B,
        SANDBOX_POLICY_SSH,
        SANDBOX_POLICY_K8S,
    }:
        return backend
    return None


async def get_local_workspace_offloader(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
    skills_custom: bool = False,
    allowed_global_skills: list[str] | None = None,
) -> Any | None:
    """Return the host LocalWorkspace used as AgentScope's offloader."""
    workspace = await get_local_workspace(
        user_id=user_id,
        conversation_id=conversation_id,
        user_name=user_name,
        user_info=user_info,
        skills_custom=skills_custom,
        allowed_global_skills=allowed_global_skills,
    )
    return get_workspace_offloader(workspace)


async def build_host_only_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
    skills_custom: bool = False,
    allowed_global_skills: list[str] | None = None,
) -> Any:
    """Build a host-only ``LocalWorkspace`` for sandbox-unavailable degradation.

    Used by the chat runner when a sandbox policy (k8s/docker/e2b/ssh) fails to
    initialize: a pure-conversation turn can still proceed with host-backed file
    tools (Read/Write/Edit/Glob/Grep) and without the sandbox Bash tool. Never
    touches a sandbox and never creates a Pod/container. Mirrors the host
    workspace construction inside ``get_local_workspace`` (root/workdir/skills
    pre-seed + ``LocalWorkspace.initialize``).
    """
    from agentscope.workspace import LocalWorkspace

    root = await resolve_workspace_root()
    workdir = resolve_session_workdir(
        root=root,
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
    )
    os.makedirs(workdir, exist_ok=True)
    from app.services.ai.skills.policy import get_forbidden_skill_ids

    forbidden_skill_ids = await get_forbidden_skill_ids(_resolve_identity_user_id(user_info))
    skill_paths = discover_platform_skill_paths(
        user_info=user_info,
        skills_custom=skills_custom,
        allowed_global_skills=allowed_global_skills,
        forbidden_skill_ids=forbidden_skill_ids,
    )
    _preseed_session_skills(workdir, skill_paths, forbidden_skill_ids, user_info)
    local_ws = LocalWorkspace(workdir=workdir, skill_paths=skill_paths)
    await local_ws.initialize()
    return local_ws


def get_workspace_offloader(workspace: Any) -> Any | None:
    """Extract the host LocalWorkspace from the modern workspace pair.

    The sandbox workspace only owns Bash execution. AgentScope context and
    tool-result offloading must remain on the host LocalWorkspace, which
    implements the ``Offloader`` protocol.
    """
    if isinstance(workspace, (tuple, list)) and len(workspace) == 2:
        return workspace[1]
    return workspace


async def delete_workspace_for_session(
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> None:
    if not conversation_id:
        return
    root = await resolve_workspace_root()
    workdir = resolve_session_workdir(
        root=root,
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
    )
    prefix = f"{workdir}::"
    cached_workspaces: list[tuple[Any, str | None]] = []
    for key in list(_workspace_cache.keys()):
        if key == workdir or (isinstance(key, str) and key.startswith(prefix)):
            cached = _workspace_cache.pop(key, None)
            if cached is not None:
                cached_workspaces.append(
                    (cached, _workspace_sandbox_refs.pop(key, None))
                )

    closed_ids: set[int] = set()
    for cached, sandbox_cache_key in cached_workspaces:
        sandbox_ws, local_ws = _normalize_workspace_pair(cached)
        if sandbox_cache_key is not None:
            await _release_sandbox_workspace(
                sandbox_cache_key,
                reason="session deletion",
            )
            sandbox_ws = None
        for workspace in (sandbox_ws, local_ws):
            if workspace is None or id(workspace) in closed_ids:
                continue
            closed_ids.add(id(workspace))
            await _close_workspace_safely(workspace, reason="session deletion")

    if not os.path.isdir(workdir):
        return
    try:
        shutil.rmtree(workdir)
    except Exception as exc:
        logger.warning("[workspace] Failed to delete workdir=%s: %s", workdir, exc)


def clear_workspace_cache() -> None:
    _workspace_cache.clear()
    _workspace_sandbox_refs.clear()
    _docker_workspace_cache.clear()
    _docker_workspace_refcounts.clear()
    _docker_workspace_locks.clear()
    _docker_workspace_last_used.clear()
    _k8s_workspace_cache.clear()
    _k8s_workspace_refcounts.clear()
    _k8s_workspace_locks.clear()
    _k8s_workspace_last_used.clear()
    # 用户级准备锁只保护同步的目录准备动作，不会被长期持有；这里一并清掉，
    # 避免测试/长生命周期进程里按路径无限累积。
    _sandbox_host_prepare_locks.clear()


def normalize_workspace_tool_names(tool_names: set[str] | frozenset[str]) -> set[str]:
    aliases = {
        "exec_command": "Bash",
        "read_file": "Read",
        "write_file": "Write",
        "search_text": "Grep",
        "edit_file": "Edit",
        "glob_files": "Glob",
    }
    normalized: set[str] = set()
    for name in tool_names:
        canonical = aliases.get(name, name)
        normalized.add(canonical)
    return normalized


def collect_workspace_file_tool_names(tools: list[Any]) -> set[str]:
    names: set[str] = set()
    for tool in tools or []:
        tool_name = getattr(tool, "name", None)
        if tool_name:
            names.add(str(tool_name))
    return normalize_workspace_tool_names(names) & WORKSPACE_PROMPT_TOOL_NAMES


async def append_session_workspace_sandbox_to_system_prompt(
    system_content: str,
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    tools: list[Any],
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> str:
    """Append session workspace + path sandbox guidance when file/shell tools are bound."""
    file_tools = collect_workspace_file_tool_names(tools)
    if not conversation_id or not file_tools:
        return system_content

    if "[Session Workspace & Path Sandbox]" in (system_content or ""):
        return system_content

    from app.services.ai.agent_prompts import AgentServicePrompts

    root = await resolve_workspace_root()
    session_workdir = resolve_session_workdir(
        root=root,
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
    )
    docs_dir = resolve_user_docs_dir(
        root=root,
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    from app.services.config_service import ConfigService, resolve_effective_sandbox_policy

    try:
        effective_policy = resolve_effective_sandbox_policy(
            await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL),
            SANDBOX_POLICY_LOCAL,
        )
    except Exception as exc:  # pragma: no cover - infrastructure fallback
        logger.warning("[workspace] Failed to resolve sandbox policy for prompt: %s", exc)
        effective_policy = SANDBOX_POLICY_LOCAL
    # 逻辑工作区根按策略解析：docker/k8s = /workspace，e2b = /home/user/workspace，
    # ssh = 系统配置的 remote_workdir；local 为空（Bash 直接用后端真实路径）。
    # 提示词、目录清单、工具元数据展示都从这里取，别再各自硬编码 /workspace。
    try:
        sandbox_logical_root = await resolve_sandbox_logical_root(effective_policy)
    except Exception as exc:  # pragma: no cover - 提示词降级
        logger.warning("[workspace] Failed to resolve sandbox logical root: %s", exc)
        sandbox_logical_root = None
    logical_session_workdir = None
    logical_docs_dir = None
    logical_public_docs_dir = None
    if sandbox_logical_root:
        # 只有 docker/k8s 把宿主用户工作区（docs/sessions）挂进沙箱；e2b/ssh 的
        # `<root>/docs`、`<root>/sessions/<id>` 并不存在，不能在提示词里声称。
        if effective_policy in (SANDBOX_POLICY_DOCKER, SANDBOX_POLICY_K8S):
            logical_session_workdir = os.path.join(
                sandbox_logical_root,
                USER_SESSIONS_DIR_NAME,
                os.path.basename(session_workdir),
            )
            logical_docs_dir = os.path.join(sandbox_logical_root, USER_DOCS_DIR_NAME)
            # K8s 适配器固定挂载 subPath docs（k8s_workspace.py 传 public_docs_mounted=True），
            # 与目录清单 resource_catalog_tools 的判定保持一致；否则同一轮里提示词会说
            # 「公共 docs 未挂载」而目录清单说已挂载，模型无所适从。
            if effective_policy == SANDBOX_POLICY_K8S:
                logical_public_docs_dir = DOCKER_WORKSPACE_PUBLIC_DOCS_PATH
            elif _resolve_docker_public_docs_source() is not None:
                logical_public_docs_dir = DOCKER_WORKSPACE_PUBLIC_DOCS_PATH
    # 技能路径提示必须与 Bash 的真实执行环境一致：local 下公共技能就是后端
    # SKILLS_DIR（容器内 /app/data/skills），沙箱内则是 `<逻辑根>/skills` 副本。
    # 取实际值而不是硬编码，宿主开发环境会回退到 ~/.agents/skills。
    from app.utils.fs_access import get_platform_skills_root
    from app.utils.fs_paths import get_data_base_dir

    public_skills_path = get_platform_skills_root() or os.path.join(get_data_base_dir(), "skills")
    personal_skills_path = None
    if not sandbox_logical_root:
        try:
            from app.services.ai.skill_resolver import get_user_personal_skills_dir

            personal_skills_path = get_user_personal_skills_dir(user_info)
        except Exception as exc:  # pragma: no cover - 提示词降级
            logger.debug("[workspace] Failed to resolve personal skills dir for prompt: %s", exc)
    sandbox_skills_dir = (
        os.path.join(sandbox_logical_root, SANDBOX_SKILLS_DIR_NAME)
        if sandbox_logical_root
        else None
    )
    block = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir=session_workdir,
        docs_dir=docs_dir,
        file_tool_names=sorted(file_tools),
        logical_workspace_root=sandbox_logical_root,
        logical_session_workdir=logical_session_workdir,
        logical_docs_dir=logical_docs_dir,
        logical_public_docs_dir=logical_public_docs_dir,
        public_skills_path=public_skills_path,
        personal_skills_path=personal_skills_path,
        sandbox_skills_dir=sandbox_skills_dir,
        sandbox_skill_layout=sandbox_skill_layout(effective_policy),
        personal_skills_container_path=sandbox_personal_skills_path(effective_policy),
    )
    base = (system_content or "").strip()
    if base:
        return f"{base}\n\n{block}"
    return block


def _workspace_native_name_for_spec(spec: Any) -> str | None:
    native_tool = getattr(spec, "native_tool", None)
    native_name = getattr(native_tool, "name", None) if native_tool is not None else None
    if native_name in WORKSPACE_BUILTIN_TOOL_NAMES:
        return str(native_name)
    spec_name = str(getattr(spec, "name", "") or "")
    aliased = AGENTSCOPE_BUILTIN_TOOL_ALIASES.get(spec_name, spec_name)
    if aliased in WORKSPACE_BUILTIN_TOOL_NAMES:
        return aliased
    return None


async def _sandbox_bash_tool_from_mcps(mcps: Any) -> Any | None:
    """Resolve the sandbox ``bash`` tool from the sandbox's MCP collection.

    Docker/E2B return connected ``GatewayMCPClient`` (name="sandbox") whose
    ``get_tool("bash")`` is a stateless HTTP tool (no connect needed). SSH
    returns a local stdio ``MCPClient`` (also name="sandbox") that must be
    ``connect()``-ed before ``get_tool("bash")``; the resulting stateful tool
    stays alive after binding (never closed here).
    """
    try:
        listed = mcps
        if inspect.isawaitable(listed):
            listed = await listed
        if not isinstance(listed, (list, tuple)):
            listed = list(listed or [])
    except Exception:
        return None
    if not listed:
        return None
    for client in listed:
        name = str(getattr(client, "name", "") or "")
        if name != "sandbox":
            continue
        try:
            # Docker/E2B GatewayMCPClient is already connected; SSH stateful
            # MCPClient needs an explicit connect before get_tool.
            is_connected = getattr(
                client,
                "is_connected",
                getattr(client, "connected", False),
            )
            if not bool(is_connected):
                connect = getattr(client, "connect", None)
                if connect is None:
                    continue
                res = connect()
                if inspect.isawaitable(res):
                    await res
            get_tool = getattr(client, "get_tool", None)
            if get_tool is None:
                continue
            tool = get_tool("bash")
            if inspect.isawaitable(tool):
                tool = await tool
            if tool is not None:
                return tool
        except Exception as exc:  # noqa: BLE001
            logger.warning("[workspace] Failed to fetch bash from sandbox MCP %r: %s", name, exc)
            continue
    return None


#: 内置沙箱工具 MCP 的名字（与 ``workspace_container_mcp.CONTAINER_MCP_NAME`` 一致）。
_SANDBOX_TOOL_MCP_NAME = "sandbox"

#: 「已尝试重建内置 sandbox MCP」标记，挂在 workspace 对象上：失败后不每次 Bash 重试。
_SANDBOX_MCP_REBUILD_FLAG = "_platform_sandbox_mcp_rebuild_attempted"
#: 重建用的 per-workspace 锁，避免并发 Bash 同时 remove/add 同一条 MCP。
_SANDBOX_MCP_REBUILD_LOCK = "_platform_sandbox_mcp_rebuild_lock"


def _is_k8s_sandbox(real_ws: Any) -> bool:
    return getattr(real_ws, "_platform_sandbox_policy", None) == SANDBOX_POLICY_K8S


def _build_builtin_sandbox_mcp(real_ws: Any) -> Any:
    """按沙箱策略构造内置 sandbox MCP 客户端（K8s 必须用网关 venv 解释器）。"""
    from app.services.ai.runtime.agentscope.workspace_container_mcp import (
        K8S_GATEWAY_VENV_PYTHON,
        build_container_tool_mcp,
    )

    if _is_k8s_sandbox(real_ws):
        return build_container_tool_mcp(interpreter=K8S_GATEWAY_VENV_PYTHON)
    return build_container_tool_mcp()


def _sandbox_mcp_rebuild_lock(real_ws: Any) -> asyncio.Lock:
    lock = getattr(real_ws, _SANDBOX_MCP_REBUILD_LOCK, None)
    if isinstance(lock, asyncio.Lock):
        return lock
    lock = asyncio.Lock()
    try:
        setattr(real_ws, _SANDBOX_MCP_REBUILD_LOCK, lock)
    except Exception:  # noqa: BLE001 - 只读替身：退化为每次新建锁
        pass
    return lock


def _snapshot_mcp_declarations(real_ws: Any) -> dict[Any, list[Any]] | None:
    """浅拷贝 ``_mcp_specs``（元素是 MCPClient 对象引用，够用于回滚键与顺序）。"""
    specs = getattr(real_ws, "_mcp_specs", None)
    if not isinstance(specs, dict):
        return None
    return {key: list(value) for key, value in specs.items()}


def _restore_mcp_declarations(real_ws: Any, snapshot: dict[Any, list[Any]] | None) -> None:
    if snapshot is None:
        return
    specs = getattr(real_ws, "_mcp_specs", None)
    if not isinstance(specs, dict):
        return
    specs.clear()
    specs.update(snapshot)
    # 重建失败的 half-state 客户端不能留在 live 表里，下次 list_mcps 会按声明重建。
    instances = getattr(real_ws, "_mcp_instances", None)
    if isinstance(instances, dict):
        instances.pop(("", ""), None)


async def _restore_mcp_file(
    backend: Any,
    mcp_path: str | None,
    backup: bytes | None,
) -> None:
    """把 ``.mcp`` 恢复到重建前的内容（无可写后端时静默跳过）。"""
    if backend is None or not mcp_path:
        return
    try:
        if backup is None:
            if await backend.file_exists(mcp_path):
                await backend.delete_path(mcp_path)
        else:
            await backend.write_file(mcp_path, backup)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[workspace] 恢复 MCP 声明文件失败（path=%s）: %s", mcp_path, exc)


async def _rebuild_builtin_sandbox_mcp(real_ws: Any) -> str | None:
    """内置 sandbox MCP 世代失配时重建一次。

    返回 ``None`` 表示"已重建/无需重建"，返回字符串是失败原因（供上抛与日志）。

    为什么会失配：MCP 声明被 AgentScope 持久化在 ``<workdir>/.mcp``（含内联脚本
    全文），且 ``.mcp`` 的优先级高于构造参数 ``default_mcps``。旧版本写下的脚本会
    一直生效——镜像里的 ``mcp`` 升到 2.x 之后 ``from mcp.server.fastmcp import
    FastMCP`` 直接抛 ``ModuleNotFoundError``，gateway 注册返回 500，平台拿不到
    ``bash``，用户侧只剩一句「Bash 工具不可用」。

    事务性：先备份 ``.mcp`` 与内存声明，``add_mcp`` 失败时原样回滚。用户自建的
    MCP 声明一条都不能丢；「空列表」还会被上游当成「该会话明确没有 MCP」而永久
    拦住默认值回落，属于必须避免的次生故障。

    调用方必须持有 ``_sandbox_mcp_rebuild_lock``：重建期间的中间态（sandbox 已被
    remove、新的尚未 add）不能被并发的 ``list_mcps`` 当成「这台沙箱没有 MCP」。
    """
    snapshot = _snapshot_mcp_declarations(real_ws)
    backend = None
    mcp_path = None
    backup: bytes | None = None
    try:
        backend = real_ws.get_backend()
        mcp_path = getattr(real_ws, "_mcp_file", None)
        if backend is not None and mcp_path and await backend.file_exists(mcp_path):
            backup = await backend.read_file(mcp_path)
    except Exception as exc:  # noqa: BLE001
        # 备份失败不阻断重建：只是失败时无从恢复文件内容。
        logger.debug("[workspace] 备份 MCP 声明文件失败: %s", exc)
        backend = None
        mcp_path = None
        backup = None

    try:
        await real_ws.remove_mcp(_SANDBOX_TOOL_MCP_NAME)
        await real_ws.add_mcp(_build_builtin_sandbox_mcp(real_ws))
    except Exception as exc:  # noqa: BLE001
        failed_reason = f"{type(exc).__name__}: {exc}"
        logger.warning("[workspace] 重建内置 sandbox MCP 失败: %s", failed_reason)
        await _restore_mcp_file(backend, mcp_path, backup)
        _restore_mcp_declarations(real_ws, snapshot)
        return failed_reason

    from app.services.ai.runtime.agentscope.workspace_container_mcp import (
        sandbox_mcp_fingerprint,
    )

    logger.info(
        "[workspace] 已重建内置 sandbox MCP（旧声明固化了旧脚本或两侧版本失配），"
        "fingerprint=%s",
        sandbox_mcp_fingerprint(),
    )
    return None


def _mark_sandbox_mcp_rebuild_attempted(real_ws: Any) -> None:
    try:
        setattr(real_ws, _SANDBOX_MCP_REBUILD_FLAG, True)
    except Exception:  # noqa: BLE001 - 只读替身：不标记，下次仍会尝试
        pass


async def _resolve_sandbox_bash_tool(real_ws: Any) -> tuple[Any | None, str]:
    """取沙箱 ``bash``；取不到时重建一次内置 sandbox MCP 再取。

    返回 ``(tool, 失败原因)``：取到时原因为空串。失败原因会带进异常的 message 与
    user_message，让「gateway 500 / ModuleNotFoundError」这类真因不再只剩一句
    「Bash MCP 不可用」。

    并发语义：整段「判定 + 重建 + 重取」在 per-workspace 锁内完成，等锁的协程
    醒来后先重取一次（可能已被别人修好），避免把重建中间态误判成"仍然没有"。
    """
    list_mcps = getattr(real_ws, "list_mcps", None)

    def _list() -> Any:
        return list_mcps() if callable(list_mcps) else None

    tool = await _sandbox_bash_tool_from_mcps(_list())
    if tool is not None:
        return tool, ""

    async with _sandbox_mcp_rebuild_lock(real_ws):
        # 等锁期间可能已被并发的 Bash 修好：先重取，避免重复 remove/add。
        tool = await _sandbox_bash_tool_from_mcps(_list())
        if tool is not None:
            return tool, ""

        if getattr(real_ws, _SANDBOX_MCP_REBUILD_FLAG, False):
            return None, "内置 sandbox MCP 上次重建失败，本次未重试（需重启沙箱或清理 .mcp）"

        failure = await _rebuild_builtin_sandbox_mcp(real_ws)
        _mark_sandbox_mcp_rebuild_attempted(real_ws)

        tool = await _sandbox_bash_tool_from_mcps(_list())
        if tool is not None:
            return tool, ""
        return None, failure or "重建内置 sandbox MCP 后仍未取到 bash 工具"


async def resolve_sandbox_bash_tool(sandbox_ws: Any) -> Any | None:
    """取沙箱里的 ``bash`` 工具，供脚本工具等「非 AgentScope 内置工具」复用。

    与 Bash 工具走同一条通道的意义：脚本工具因此自动继承沙箱策略
    （docker/k8s/e2b/ssh）、超时、取消与命令策略，不需要第二套执行原语。

    惰性代理会在这里真正拉起容器/Pod；调用方负责判断 ``sandbox_ws`` 是否为空
    （为空表示 local 策略，应退回用户工作区内的本机子进程）。
    """
    if sandbox_ws is None:
        return None
    real_ws = sandbox_ws
    if isinstance(sandbox_ws, LazySandboxWorkspaceProxy):
        real_ws = await sandbox_ws.ensure_ready()
    list_mcps = getattr(real_ws, "list_mcps", None)
    return await _sandbox_bash_tool_from_mcps(list_mcps() if callable(list_mcps) else None)


def _normalize_docker_mount_mappings(
    host_root: str,
    mount_mappings: list[DockerMountMapping]
    | tuple[DockerMountMapping, ...]
    | None,
) -> list[DockerMountMapping]:
    """Return Docker mounts ordered for deterministic longest-prefix matching."""
    root = os.path.abspath(host_root)
    mappings = list(mount_mappings or [(DOCKER_WORKSPACE_LOGICAL_ROOT, root, "rw")])
    normalized: list[DockerMountMapping] = []
    for sandbox_prefix, backend_root, mode in mappings:
        prefix = str(sandbox_prefix).rstrip("/") or "/"
        normalized.append(
            (
                prefix,
                os.path.abspath(backend_root) if backend_root is not None else None,
                str(mode),
            )
        )
    return sorted(normalized, key=lambda item: len(item[0]), reverse=True)


def _map_docker_workspace_path(
    path: str | None,
    host_root: str,
    *,
    mount_mappings: list[DockerMountMapping]
    | tuple[DockerMountMapping, ...]
    | None = None,
) -> str:
    """Map a model-visible Docker path to a backend file-tool path.

    Child bind mounts win over the parent ``/workspace`` mount. A mount
    without a backend target is container-only and cannot be guessed as a host
    path.
    """
    root = os.path.abspath(host_root)
    raw = "" if path is None else str(path).strip()
    if not raw:
        return root

    mappings = _normalize_docker_mount_mappings(host_root, mount_mappings)
    matched: DockerMountMapping | None = None
    for sandbox_prefix, backend_root, mode in mappings:
        if raw == sandbox_prefix or raw.startswith(f"{sandbox_prefix}/"):
            matched = (sandbox_prefix, backend_root, mode)
            break

    if matched is not None:
        sandbox_prefix, backend_root, _mode = matched
        if backend_root is None:
            raise ValueError(
                f"container-only path cannot be used by host file tools: {path}"
            )
        relative = raw[len(sandbox_prefix):].lstrip("/\\")
        candidate = os.path.abspath(os.path.join(backend_root, relative))
        # Check lexical mount containment here. The final file-tool access
        # guard resolves symlinks and applies the public/private policy; doing
        # realpath containment at this layer would reject authorized public
        # docs symlinks (for example data/docs/FAQ.md).
        root_real = os.path.abspath(backend_root)
    elif os.path.isabs(raw):
        if any(
            raw == prefix or raw.startswith(f"{prefix}/")
            for prefix in DOCKER_CONTAINER_ONLY_PATH_PREFIXES
        ):
            if raw == "/root" or raw.startswith("/root/"):
                raise ValueError(
                    "Docker 宿主机物理路径不能用于文件工具，只接受 /workspace 开头的路径或工作区相对路径"
                )
            raise ValueError(
                f"container-only path cannot be used by host file tools: {path}"
            )
        # Preserve backend service paths such as /app/data/docs supplied for
        # host-side file tools. They are checked by the normal access guard.
        return raw
    else:
        candidate = os.path.abspath(os.path.join(root, raw))
        root_real = os.path.realpath(root)

    candidate_real = os.path.abspath(candidate)
    try:
        is_inside = os.path.commonpath((root_real, candidate_real)) == root_real
    except ValueError:
        is_inside = False
    if not is_inside:
        raise ValueError(f"path escapes Docker workspace: {path}")
    return candidate


def _map_docker_workspace_tool_input(
    tool_name: str,
    tool_input: Mapping[str, Any],
    host_root: str,
    *,
    mount_mappings: list[DockerMountMapping]
    | tuple[DockerMountMapping, ...]
    | None = None,
) -> dict[str, Any]:
    """Translate the shared ``/workspace`` contract for host file tools."""
    mapped = dict(tool_input)
    if tool_name in {"Read", "Write", "Edit"} and "file_path" in mapped:
        mapped["file_path"] = _map_docker_workspace_path(
            mapped.get("file_path"),
            host_root,
            mount_mappings=mount_mappings,
        )

    if tool_name in {"Glob", "Grep"}:
        if mapped.get("path"):
            mapped["path"] = _map_docker_workspace_path(
                mapped["path"],
                host_root,
                mount_mappings=mount_mappings,
            )
        else:
            mapped["path"] = os.path.abspath(host_root)

    if tool_name == "Glob":
        pattern = str(mapped.get("pattern") or "")
        if os.path.isabs(pattern):
            pattern_mapping = next(
                (
                    (sandbox_prefix, backend_root, mode)
                    for sandbox_prefix, backend_root, mode in _normalize_docker_mount_mappings(
                        host_root,
                        mount_mappings,
                    )
                    if pattern == sandbox_prefix
                    or pattern.startswith(f"{sandbox_prefix}/")
                ),
                None,
            )
            if pattern_mapping is not None:
                sandbox_prefix, backend_root, _mode = pattern_mapping
                if backend_root is None:
                    raise ValueError(
                        f"container-only path cannot be used by host file tools: {pattern}"
                    )
                relative_pattern = pattern[len(sandbox_prefix):].lstrip("/\\") or "**"
                if ".." in relative_pattern.replace("\\", "/").split("/"):
                    raise ValueError(f"path escapes Docker workspace: {pattern}")
                mapped["path"] = os.path.abspath(backend_root)
                mapped["pattern"] = relative_pattern

    return mapped


WORKSPACE_ERROR_HEALING_HINT = (
    "\n\n[系统建议] 目标路径不存在、无法访问或权限受限。若您不确定当前环境具体目录结构、平台公共文档（如 data/docs/ 官方手册）与用户工作区（docs/、sessions/）的路径映射或读写权限，"
    "建议优先调用 list_accessible_directories 工具查看当前环境完整目录清单与推荐用途。"
)

_WORKSPACE_ERROR_MARKERS = (
    "filenotfounderror",
    "no such file or directory",
    "file not found",
    "permission denied",
    "permissiondenied",
    "is a directory",
    "not a directory",
    "path escapes",
    "escapes docker workspace",
    "container-only",
)


def enhance_workspace_error_message(text_or_exc: Any) -> str:
    """如果工具报错涉及找不到文件、权限受限或越界，自动追加 list_accessible_directories 自愈建议。"""
    raw = str(text_or_exc)
    lower = raw.lower()
    if "list_accessible_directories" in raw:
        return raw
    if any(marker in lower for marker in _WORKSPACE_ERROR_MARKERS):
        return f"{raw}{WORKSPACE_ERROR_HEALING_HINT}"
    return raw


def _logicalize_docker_workspace_result(result: Any, host_root: str) -> Any:
    """Keep host-tool results in the platform's canonical host namespace."""
    if isinstance(result, str):
        return enhance_workspace_error_message(result)
    return result


class _SandboxLogicalWorkspaceNativeTool:
    """Map legacy ``/workspace`` inputs while preserving real output paths."""

    def __init__(
        self,
        native_tool: Any,
        host_root: str,
        *,
        mount_mappings: list[DockerMountMapping]
        | tuple[DockerMountMapping, ...]
        | None = None,
    ) -> None:
        self._native_tool = native_tool
        self._host_root = os.path.abspath(host_root)
        self._mount_mappings = mount_mappings
        self.name = getattr(native_tool, "name", "")

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._native_tool, attribute)

    def _map(self, tool_input: Mapping[str, Any]) -> dict[str, Any]:
        return _map_docker_workspace_tool_input(
            self.name,
            tool_input,
            self._host_root,
            mount_mappings=self._mount_mappings,
        )

    async def __call__(self, **kwargs: Any) -> Any:
        try:
            mapped_input = self._map(kwargs)
        except Exception as exc:
            msg = enhance_workspace_error_message(exc)
            raise type(exc)(msg) from exc

        try:
            result = self._native_tool(**mapped_input)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            msg = enhance_workspace_error_message(exc)
            raise type(exc)(msg) from exc

        return _logicalize_docker_workspace_result(result, self._host_root)

    async def check_permissions(self, tool_input: dict[str, Any], context: Any) -> Any:
        checker = getattr(self._native_tool, "check_permissions", None)
        if checker is None:
            return None
        result = checker(self._map(tool_input), context)
        if inspect.isawaitable(result):
            return await result
        return result

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        checker = getattr(self._native_tool, "check_read_only", None)
        if checker is None:
            return bool(getattr(self._native_tool, "is_read_only", False))
        result = checker(self._map(tool_input))
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    def match_rule(self, rule_content: str | None, tool_input: dict[str, Any]) -> bool:
        matcher = getattr(self._native_tool, "match_rule", None)
        if matcher is None:
            return rule_content is None
        return bool(matcher(rule_content, self._map(tool_input)))

    def generate_suggestions(self, tool_input: dict[str, Any]) -> list[Any]:
        generator = getattr(self._native_tool, "generate_suggestions", None)
        if generator is None:
            return []
        suggestions = generator(self._map(tool_input))
        return suggestions


def _normalize_workspace_file_tool_input(
    tool_name: str,
    tool_input: Mapping[str, Any],
    workspace_root: str,
    *,
    mount_mappings: list[DockerMountMapping]
    | tuple[DockerMountMapping, ...]
    | None = None,
) -> dict[str, Any]:
    """Normalize host file-tool paths before applying tenant authorization."""
    mapped = dict(tool_input)
    root = os.path.abspath(workspace_root)

    if tool_name in {"Read", "Write", "Edit"}:
        raw_path = mapped.get("file_path")
        if raw_path:
            raw = str(raw_path)
            if raw == DOCKER_WORKSPACE_LOGICAL_ROOT or raw.startswith(
                f"{DOCKER_WORKSPACE_LOGICAL_ROOT}/"
            ):
                raw = _map_docker_workspace_path(
                    raw,
                    root,
                    mount_mappings=mount_mappings,
                )
            elif not os.path.isabs(raw):
                raw = os.path.join(root, raw)
            mapped["file_path"] = os.path.realpath(raw)
        return mapped

    if tool_name in {"Glob", "Grep"}:
        raw_path = mapped.get("path")
        raw = str(raw_path) if raw_path else root
        if raw == DOCKER_WORKSPACE_LOGICAL_ROOT or raw.startswith(
            f"{DOCKER_WORKSPACE_LOGICAL_ROOT}/"
        ):
            raw = _map_docker_workspace_path(
                raw,
                root,
                mount_mappings=mount_mappings,
            )
        elif not os.path.isabs(raw):
            raw = os.path.join(root, raw)
        mapped["path"] = os.path.realpath(raw)
    return mapped


def _is_direct_root_help_pattern(pattern: str) -> bool:
    cleaned = str(pattern or "").strip()
    return bool(
        cleaned
        and "/" not in cleaned
        and "\\" not in cleaned
        and not cleaned.startswith("**")
        and cleaned.endswith(".md")
    )


def _public_runtime_help_scan_kind(
    tool_name: str,
    tool_input: Mapping[str, Any],
) -> str | None:
    """Classify the narrowly scoped service-root help scan, if requested."""
    if tool_name not in {"Glob", "Grep"}:
        return None

    from app.utils.fs_access import get_public_runtime_help_root

    target_path = os.path.realpath(str(tool_input.get("path") or ""))
    if target_path != os.path.realpath(get_public_runtime_help_root()):
        return None

    if tool_name == "Glob":
        if not _is_direct_root_help_pattern(str(tool_input.get("pattern") or "")):
            raise PermissionError(
                "根目录帮助文档仅允许直接匹配 *.md，禁止递归扫描服务目录"
            )
        return "glob"

    raw_glob = str(tool_input.get("glob") or "").strip()
    patterns = [item for item in raw_glob.replace(",", " ").split() if item]
    if any(not _is_direct_root_help_pattern(item) for item in patterns):
        raise PermissionError(
            "根目录帮助文档仅允许直接匹配 *.md，禁止递归扫描服务目录"
        )
    return "grep"


def _is_public_runtime_help_scan(
    tool_name: str,
    tool_input: Mapping[str, Any],
) -> bool:
    return _public_runtime_help_scan_kind(tool_name, tool_input) is not None


def _assert_workspace_file_access(
    tool_name: str,
    tool_input: Mapping[str, Any],
    *,
    user_info: dict[str, Any] | None,
    workspace_root: str,
    mount_mappings: list[DockerMountMapping]
    | tuple[DockerMountMapping, ...]
    | None = None,
) -> dict[str, Any]:
    """Authorize a host file-tool input and return its canonicalized form."""
    from app.utils.fs_access import (
        is_runtime_path_allowed,
        is_runtime_path_writable,
    )

    mapped = _normalize_workspace_file_tool_input(
        tool_name,
        tool_input,
        workspace_root,
        mount_mappings=mount_mappings,
    )
    path_key = "file_path" if tool_name in {"Read", "Write", "Edit"} else "path"
    target_path = mapped.get(path_key)
    if not target_path:
        raise PermissionError("文件访问被拒绝：缺少目标路径")

    # /app 本身不能成为通用文件工具的授权根；仅放行受约束的根目录帮助文档扫描，
    # 后续由本地实现直接枚举一级 *.md，避免 native Grep 递归读取整个服务目录。
    if _public_runtime_help_scan_kind(tool_name, mapped):
        return mapped

    if tool_name in {"Write", "Edit"}:
        allowed = is_runtime_path_writable(str(target_path), user_info)
        operation = "写入"
    else:
        allowed = is_runtime_path_allowed(str(target_path), user_info)
        operation = "读取"
    if not allowed:
        raise PermissionError(
            f"文件访问被拒绝：当前用户无权{operation}该路径 {target_path}"
        )
    return mapped


_PYTHON_GREP_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".bzr",
    "node_modules",
    "__pycache__",
}
_PYTHON_GREP_TYPE_SUFFIXES = {
    "js": (".js", ".jsx", ".mjs", ".cjs"),
    "ts": (".ts", ".tsx", ".mts", ".cts"),
    "py": (".py", ".pyi"),
    "md": (".md", ".markdown"),
    "json": (".json",),
    "yaml": (".yaml", ".yml"),
    "rust": (".rs",),
    "go": (".go",),
    "java": (".java",),
}


def _tool_result_text(result: Any) -> str:
    content = getattr(result, "content", None)
    if isinstance(content, (list, tuple)):
        parts = [str(getattr(block, "text", "") or "") for block in content]
        return "\n".join(part for part in parts if part)
    return str(result or "")


def _is_ripgrep_unavailable(result: Any) -> bool:
    text = _tool_result_text(result).lower()
    return (
        "ripgrep error (code 127)" in text
        or "no such file or directory: 'rg'" in text
        or 'no such file or directory: "rg"' in text
    )


def _python_grep_fallback(
    tool_input: Mapping[str, Any],
    *,
    user_info: dict[str, Any] | None,
    direct_files_only: bool = False,
) -> Any:
    """Use the host Python runtime when native Grep cannot find ripgrep."""
    from agentscope.message import TextBlock, ToolResultState
    from agentscope.tool import ToolChunk
    from app.utils.fs_access import is_runtime_path_allowed

    pattern = str(tool_input.get("pattern") or "").strip()
    if not pattern:
        return ToolChunk(
            content=[TextBlock(text="Grep 调用失败：缺少必填参数 pattern。")],
            state=ToolResultState.ERROR,
            is_last=True,
        )

    output_mode = str(tool_input.get("output_mode") or "files_with_matches")
    if output_mode not in {"content", "files_with_matches", "count"}:
        return ToolChunk(
            content=[TextBlock(text=f"Grep 调用失败：不支持的 output_mode {output_mode}。")],
            state=ToolResultState.ERROR,
            is_last=True,
        )

    flags = re.IGNORECASE if tool_input.get("i") or tool_input.get("case_insensitive") else 0
    if tool_input.get("multiline"):
        flags |= re.MULTILINE | re.DOTALL
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return ToolChunk(
            content=[TextBlock(text=f"Grep 正则表达式无效：{exc}")],
            state=ToolResultState.ERROR,
            is_last=True,
        )

    base_path = os.path.realpath(str(tool_input.get("path") or "."))
    if os.path.isfile(base_path):
        candidates = [base_path]
    elif os.path.isdir(base_path):
        if direct_files_only:
            try:
                candidates = [
                    os.path.join(base_path, filename)
                    for filename in os.listdir(base_path)
                    if os.path.isfile(os.path.join(base_path, filename))
                ]
            except OSError:
                candidates = []
        else:
            candidates = []
            for root, dirs, files in os.walk(base_path, topdown=True):
                dirs[:] = [directory for directory in dirs if directory not in _PYTHON_GREP_EXCLUDED_DIRS]
                candidates.extend(os.path.join(root, filename) for filename in files)
    else:
        return ToolChunk(
            content=[TextBlock(text=f"Directory not found: {base_path}")],
            state=ToolResultState.ERROR,
            is_last=True,
        )

    file_glob = str(tool_input.get("glob") or "").strip()
    glob_patterns = [item for item in file_glob.replace(",", " ").split() if item]
    file_type = str(tool_input.get("type") or "").strip().lower()
    type_suffixes = _PYTHON_GREP_TYPE_SUFFIXES.get(file_type)
    rows: list[str] = []

    context = tool_input.get("context")
    if context is None:
        context = tool_input.get("-C")
    try:
        context_lines = max(0, int(context or 0))
    except (TypeError, ValueError):
        context_lines = 0

    for candidate in sorted(candidates):
        real_candidate = os.path.realpath(candidate)
        if not is_runtime_path_allowed(real_candidate, user_info):
            continue
        relative_candidate = os.path.relpath(candidate, base_path) if os.path.isdir(base_path) else os.path.basename(candidate)
        if glob_patterns and not any(
            fnmatch.fnmatch(relative_candidate, item)
            or fnmatch.fnmatch(os.path.basename(candidate), item)
            for item in glob_patterns
        ):
            continue
        if type_suffixes and not candidate.lower().endswith(type_suffixes):
            continue
        try:
            with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except (OSError, UnicodeError):
            continue
        if "\x00" in text:
            continue

        lines = text.splitlines()
        if tool_input.get("multiline"):
            matches = list(regex.finditer(text))
            matching_indexes = sorted(
                {
                    text.count("\n", 0, match.start())
                    for match in matches
                }
            )
        else:
            matching_indexes = [
                index for index, line in enumerate(lines) if regex.search(line)
            ]
        if not matching_indexes:
            continue

        if output_mode == "files_with_matches":
            rows.append(candidate)
        elif output_mode == "count":
            rows.append(f"{candidate}:{len(matching_indexes)}")
        else:
            indexes = set()
            for index in matching_indexes:
                indexes.update(
                    range(
                        max(0, index - context_lines),
                        min(len(lines), index + context_lines + 1),
                    )
                )
            for index in sorted(indexes):
                separator = ":" if index in matching_indexes else "-"
                rows.append(f"{candidate}{separator}{index + 1}{separator}{lines[index]}")

    try:
        offset = max(0, int(tool_input.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    raw_limit = tool_input.get("head_limit")
    try:
        limit = 250 if raw_limit is None else max(0, int(raw_limit))
    except (TypeError, ValueError):
        limit = 250
    truncated = len(rows) - offset > limit if limit else False
    rows = rows[offset:] if limit == 0 else rows[offset: offset + limit]

    if not rows:
        output = f"No matches found for pattern: {pattern}"
    else:
        output = "\n".join(rows)
        if truncated:
            output += f"\n\n[Showing results with pagination = limit: {limit}]"
    return ToolChunk(
        content=[TextBlock(text=output)],
        state=ToolResultState.SUCCESS,
        is_last=True,
    )


def _python_root_help_glob(tool_input: Mapping[str, Any]) -> Any:
    """Glob only the direct service-root Markdown help files."""
    from agentscope.message import TextBlock, ToolResultState
    from agentscope.tool import ToolChunk
    from app.utils.fs_access import get_public_runtime_help_files

    pattern = str(tool_input.get("pattern") or "").strip()
    matches = [
        path
        for path in get_public_runtime_help_files()
        if fnmatch.fnmatch(os.path.basename(path), pattern)
    ]
    output = "\n".join(matches) if matches else f"No files found for pattern: {pattern}"
    return ToolChunk(
        content=[TextBlock(text=output)],
        state=ToolResultState.SUCCESS,
        is_last=True,
    )


def _missing_required_file_tool_argument(
    tool_name: str,
    tool_input: Mapping[str, Any],
) -> str | None:
    if tool_name in {"Read", "Write", "Edit"} and not str(
        tool_input.get("file_path") or ""
    ).strip():
        return "file_path"
    if tool_name in {"Glob", "Grep"} and not str(
        tool_input.get("pattern") or ""
    ).strip():
        return "pattern"
    return None


class _WorkspaceFileAccessNativeTool:
    """Enforce public/private file boundaries around native workspace tools."""

    def __init__(
        self,
        native_tool: Any,
        *,
        user_info: dict[str, Any] | None,
        workspace_root: str,
        mount_mappings: list[DockerMountMapping]
        | tuple[DockerMountMapping, ...]
        | None = None,
    ) -> None:
        self._native_tool = native_tool
        self._user_info = user_info
        self._workspace_root = os.path.abspath(workspace_root)
        self._mount_mappings = mount_mappings
        self.name = getattr(native_tool, "name", "")

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._native_tool, attribute)

    def _map(self, tool_input: Mapping[str, Any]) -> dict[str, Any]:
        return _assert_workspace_file_access(
            self.name,
            tool_input,
            user_info=self._user_info,
            workspace_root=self._workspace_root,
            mount_mappings=self._mount_mappings,
        )

    def check_path_access(self, tool_input: dict[str, Any]) -> None:
        """Expose the hard path guard to AgentScope's permission phase."""
        if _missing_required_file_tool_argument(self.name, tool_input):
            return
        self._map(tool_input)

    async def _refresh_forbidden_skill_cache(self) -> None:
        """执行前刷新被禁技能集合（带节流），让同步读判定不落在"未命中即放行"的窗口里。

        会话开始时已经预热过一次，但长回合里工具调用可能发生在预热很久之后，那时缓存
        早已过期；不刷新的话，被禁技能的 SKILL.md 会在这一刻重新变得可读。
        """
        try:
            from app.services.ai.skills.policy import prime_forbidden_skill_cache

            await prime_forbidden_skill_cache(user_info=self._user_info)
        except Exception:
            logger.debug(
                "[workspace] priming forbidden skill cache failed", exc_info=True
            )

    async def __call__(self, **kwargs: Any) -> Any:
        await self._refresh_forbidden_skill_cache()
        mapped_input = self._map(kwargs)
        scan_kind = _public_runtime_help_scan_kind(self.name, mapped_input)
        if scan_kind == "glob":
            return await asyncio.to_thread(_python_root_help_glob, mapped_input)
        if scan_kind == "grep":
            return await asyncio.to_thread(
                _python_grep_fallback,
                mapped_input,
                user_info=self._user_info,
                direct_files_only=True,
            )
        result = self._native_tool(**mapped_input)
        if inspect.isawaitable(result):
            result = await result
        if self.name == "Grep" and _is_ripgrep_unavailable(result):
            return await asyncio.to_thread(
                _python_grep_fallback,
                mapped_input,
                user_info=self._user_info,
            )
        return result

    async def check_permissions(self, tool_input: dict[str, Any], context: Any) -> Any:
        missing_argument = _missing_required_file_tool_argument(self.name, tool_input)
        if missing_argument:
            try:
                from agentscope.permission import PermissionBehavior, PermissionDecision
            except Exception:
                raise
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=(
                    f"工具 [{self.name}] 缺少必填参数 {missing_argument}，"
                    "将返回参数错误并允许模型修正后重试。"
                ),
                decision_reason="workspace_tool_missing_argument",
            )
        try:
            mapped_input = self._map(tool_input)
        except (PermissionError, ValueError) as exc:
            try:
                from agentscope.permission import PermissionBehavior, PermissionDecision
            except Exception:
                raise
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=str(exc),
                decision_reason="workspace_path_access_denied",
                bypass_immune=True,
            )
        checker = getattr(self._native_tool, "check_permissions", None)
        if checker is not None:
            result = checker(mapped_input, context)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                return result
        try:
            from agentscope.permission import PermissionBehavior, PermissionDecision
        except Exception:
            return None
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"Workspace tool '{self.name}' access granted.",
            decision_reason="workspace_tool_auto_allow",
        )

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        try:
            mapped_input = self._map(tool_input)
        except (PermissionError, ValueError):
            # AgentScope 会先执行只读 fast path；路径不合法时必须让它继续
            # 到 check_permissions()，由该阶段返回结构化 DENY，而不是把
            # 权限拒绝升级成并发工具 ExceptionGroup。
            return False
        checker = getattr(self._native_tool, "check_read_only", None)
        if checker is None:
            return bool(getattr(self._native_tool, "is_read_only", False))
        result = checker(mapped_input)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    def match_rule(self, rule_content: str | None, tool_input: dict[str, Any]) -> bool:
        matcher = getattr(self._native_tool, "match_rule", None)
        if matcher is None:
            return rule_content is None
        return bool(matcher(rule_content, self._map(tool_input)))

    def generate_suggestions(self, tool_input: dict[str, Any]) -> list[Any]:
        generator = getattr(self._native_tool, "generate_suggestions", None)
        if generator is None:
            return []
        return generator(self._map(tool_input))


class _SandboxSessionBashNativeTool:
    """Keep Docker Bash's default cwd aligned with the current session."""

    def __init__(self, native_tool: Any, host_root: str, session_workdir: str) -> None:
        self._native_tool = native_tool
        self._host_root = os.path.abspath(host_root)
        self._session_workdir = os.path.abspath(session_workdir)
        self.name = getattr(native_tool, "name", "")

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._native_tool, attribute)

    def _default_cwd(self) -> str:
        relative = os.path.relpath(self._session_workdir, self._host_root)
        return "." if relative == "." else relative

    def _map_cwd(self, cwd: Any) -> Any:
        if not cwd:
            return self._default_cwd()
        raw = str(cwd)
        logical_root = DOCKER_WORKSPACE_LOGICAL_ROOT
        if raw == logical_root or raw.startswith(f"{logical_root}/"):
            return raw[len(logical_root):].lstrip("/\\") or "."
        return raw

    async def __call__(self, **kwargs: Any) -> Any:
        mapped = dict(kwargs)
        mapped["cwd"] = self._map_cwd(mapped.get("cwd"))
        result = self._native_tool(**mapped)
        if inspect.isawaitable(result):
            result = await result
        return result


class _CanonicalWorkspaceNativeTool:
    """Expose an MCP-backed workspace tool under the platform tool name."""

    def __init__(self, native_tool: Any, name: str) -> None:
        self._native_tool = native_tool
        self.name = name

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._native_tool, attribute)

    def __call__(self, **kwargs: Any) -> Any:
        return self._native_tool(**kwargs)


# 平台侧要求模型为每次 Bash 调用写 ``description``（时间线行标题的「这条命令在干什么」）。
# AgentScope 内置 Bash 与沙箱 MCP 的 schema 都只把它当可选项，简单命令上模型经常省略，
# 前端于是只剩一句「工具完成 · Bash」。这里在工具描述（模型可见的那层）把要求写死，
# 不依赖模型自觉，也不动 schema 的 required，避免给每次调用增加失败风险。
BASH_INTENT_SUMMARY_MARKER = "[platform:bash-intent-summary]"
BASH_INTENT_SUMMARY_GUIDANCE = (
    f"{BASH_INTENT_SUMMARY_MARKER}\n"
    "REQUIRED: every Bash call MUST pass the `description` argument. The platform "
    "shows it next to the command in the user's execution timeline, so it must "
    "answer \"what is this command for\", not restate the command.\n"
    "- 中文写 10-25 个字，例如「检查 5432 端口是否被占用」；不要写「执行 ls」「查看目录」这类空话。\n"
    "- English: 8-20 words, e.g. \"Check whether port 5432 is already in use\".\n"
    "- 管道、少见参数等复杂命令：写目标，而不是复述参数名。\n"
    "- Never omit it; never copy the command text verbatim."
)
BASH_DESCRIPTION_PARAM_GUIDANCE = (
    "REQUIRED on every call. 10-25 个中文字 / 8-20 English words describing what the "
    "command is for, e.g. 「检查 5432 端口是否被占用」. Shown in the user's execution "
    "timeline; do not restate the command."
)


def _augment_bash_tool_description(description: str) -> str:
    """给 Bash 工具描述追加「必须写 description」的平台要求（幂等）。"""

    base = str(description or "").strip()
    if BASH_INTENT_SUMMARY_MARKER in base:
        return base
    if not base:
        return BASH_INTENT_SUMMARY_GUIDANCE
    return f"{base}\n\n{BASH_INTENT_SUMMARY_GUIDANCE}"


def _augment_bash_parameter_schema(schema: Any) -> Any:
    """把 ``description`` 参数的说明换成平台口径（只改文案，不动 required）。"""

    if not isinstance(schema, dict):
        return schema
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    description_property = properties.get("description")
    if not isinstance(description_property, dict):
        return schema
    if BASH_INTENT_SUMMARY_MARKER in str(description_property.get("description") or ""):
        return schema
    augmented = copy.deepcopy(schema)
    augmented["properties"]["description"]["description"] = (
        f"{BASH_DESCRIPTION_PARAM_GUIDANCE} {BASH_INTENT_SUMMARY_MARKER}"
    )
    return augmented


DEFAULT_BASH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "The command to run in the terminal",
        },
        "description": {
            "type": "string",
            "description": BASH_DESCRIPTION_PARAM_GUIDANCE,
        },
    },
    "required": ["command"],
}


class LazySandboxBashNativeTool:
    """按需拉起沙箱的 Bash 原生工具代理。"""

    is_external_tool: bool = False
    is_state_injected: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    evidence_types: frozenset = frozenset()
    evidence_policy: str = "non_empty"
    evidence_inference_disabled: bool = False

    def __init__(
        self,
        proxy: LazySandboxWorkspaceProxy,
        local_ws: Any | None = None,
        *,
        name: str = "Bash",
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        self.proxy = proxy
        self.local_ws = local_ws
        self.name = name
        self.description = description or "Execute a bash command in the sandbox environment."
        self.input_schema = input_schema or DEFAULT_BASH_INPUT_SCHEMA
        self._real_tool: Any | None = None
        self._lock = asyncio.Lock()

    async def _resolve_real_tool(self) -> Any:
        if self._real_tool is not None:
            return self._real_tool
        async with self._lock:
            if self._real_tool is not None:
                return self._real_tool

            # 本次 Bash 触发的拉起，把进度挂到该 Bash 卡片（模型侧 tool_call_id）下方；
            # 非 Bash 触发（文件工具 / 构建预检）不读取该 ContextVar，保持 preparation 节点。
            bash_node_id = current_bash_tool_parent_id.get(None)
            real_ws = await self.proxy.ensure_ready(
                parent_id=bash_node_id or None,
                log_id=(f"workspace:sandbox:{bash_node_id}" if bash_node_id else None),
            )
            sandbox_bash, bash_failure = await _resolve_sandbox_bash_tool(real_ws)
            if sandbox_bash is None:
                detail = f"（{bash_failure}）" if bash_failure else ""
                if _is_k8s_sandbox(real_ws):
                    from app.services.ai.runtime.agentscope.k8s_workspace import (
                        K8sSandboxUnavailableError,
                    )

                    raise K8sSandboxUnavailableError(
                        f"Kubernetes sandbox Bash MCP is unavailable{detail}",
                        reason_code="k8s_sandbox_mcp_unavailable",
                        user_message=(
                            "Kubernetes 沙箱中的 Bash 工具不可用，Bash 未执行。"
                            "平台已尝试自动重建内置 MCP，仍未恢复；"
                            "请检查沙箱网关与 Bash MCP 注册状态（K8s 沙箱的 Bash MCP "
                            "子进程需使用网关虚拟环境解释器）。"
                        ),
                    )
                raise DockerSandboxUnavailableError(
                    f"Docker sandbox Bash MCP is unavailable{detail}",
                    reason_code="docker_sandbox_mcp_unavailable",
                    user_message=(
                        "Docker 沙箱中的 Bash 工具不可用，Bash 未执行。"
                        "平台已尝试自动重建内置 MCP，仍未恢复；"
                        "请检查容器网关和 MCP 配置。"
                    ),
                )

            sandbox_host_root = (
                getattr(self.local_ws, "workspace_user_root", None)
                if self.local_ws is not None
                else None
            )
            if (
                sandbox_host_root
                and getattr(real_ws, "_platform_sandbox_policy", None)
                in (SANDBOX_POLICY_DOCKER, SANDBOX_POLICY_K8S)
                and self.local_ws is not None
                and getattr(self.local_ws, "workdir", None)
            ):
                sandbox_bash = _SandboxSessionBashNativeTool(
                    sandbox_bash,
                    sandbox_host_root,
                    self.local_ws.workdir,
                )
            if getattr(sandbox_bash, "name", None) != self.name:
                sandbox_bash = _CanonicalWorkspaceNativeTool(sandbox_bash, self.name)
            self._real_tool = sandbox_bash
            return self._real_tool

    def __getattr__(self, item: str) -> Any:
        if self._real_tool is not None:
            return getattr(self._real_tool, item)
        if item.startswith("__") and item.endswith("__"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{item}'")
        return None

    async def check_permissions(self, tool_input: dict[str, Any], context: Any) -> Any:
        if self._real_tool is not None and hasattr(self._real_tool, "check_permissions"):
            res = self._real_tool.check_permissions(tool_input, context)
            if inspect.isawaitable(res):
                res = await res
            if res is not None:
                return res
        try:
            from agentscope.permission import PermissionBehavior, PermissionDecision
        except Exception:
            return None
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Sandbox bash execution is allowed in isolated environment.",
            decision_reason="sandbox_bash_auto_allow",
        )

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        if self._real_tool is not None and hasattr(self._real_tool, "check_read_only"):
            return await self._real_tool.check_read_only(tool_input)
        return False

    def _assert_sandbox_still_registered(self) -> None:
        """Bash 执行前复核：这台沙箱是否还是注册中的那一台。

        「每次 Bash 前核对状态，而不是匹配命令内容」——命令内容级拦截可以被
        ``base64``/变量拼接/``python -c`` 轻松绕过，只能核对**状态**：

        * 沙箱因技能权限变更被逐出（:func:`_sandbox_skills_snapshot_is_current`）、
          被「停止/重启沙箱」清掉、或会话被删除 —— 此时继续执行只会打到一台已经
          删除或技能集合已过期的容器上，必须拒绝并让本轮重建；
        * 沙箱仍在注册表中 —— 顺手 ``touch`` 一次，避免长时间连续 Bash（超过空闲
          回收时长）时被 reaper 误回收。

        注意：一次 Bash 调用**内部**发生的权限变更要等下一次工具调用才会生效（本轮
        结束后的下一次取用会重建沙箱），这是"状态核对"而非"实时拦截"的固有边界。
        """
        cache_key = getattr(self.proxy, "_sandbox_cache_key", None)
        if not cache_key:
            return
        if cache_key in _docker_workspace_cache or cache_key in _k8s_workspace_cache:
            _touch_sandbox_workspace(cache_key)
            return
        if _parse_sandbox_cache_key(cache_key) is None:
            # 非法/测试替身键：无从判断"是否被逐出"，按未知放行（与实际沙箱键一定可解析
            # 的事实互补，避免把替身键误判成已回收）。
            return
        policy = getattr(self.proxy, "policy", None)
        user_message = (
            "沙箱已因技能权限变更或手动停止被回收，本次 Bash 未执行。"
            "请重新执行一次（平台会在下一次自动按最新技能集合重建沙箱）。"
        )
        if policy == SANDBOX_POLICY_K8S or str(cache_key).endswith(f"::{SANDBOX_POLICY_K8S}"):
            from app.services.ai.runtime.agentscope.k8s_workspace import (
                K8sSandboxUnavailableError,
            )

            raise K8sSandboxUnavailableError(
                f"K8s sandbox was evicted before bash execution: {cache_key}",
                reason_code="k8s_sandbox_evicted",
                user_message=user_message,
            )
        raise DockerSandboxUnavailableError(
            f"Docker sandbox was evicted before bash execution: {cache_key}",
            reason_code="docker_sandbox_evicted",
            user_message=user_message,
        )

    async def __call__(self, **kwargs: Any) -> Any:
        real_tool = await self._resolve_real_tool()
        self._assert_sandbox_still_registered()
        # 执行期间持续刷新活跃时间：命令本身可能跑得比 sandbox_idle_time 更久，
        # 只在开始前 touch 一次会让 reaper 误判为空闲并关掉正在使用的沙箱。
        async with _sandbox_activity_heartbeat(
            cache_key=getattr(self.proxy, "_sandbox_cache_key", None)
        ):
            result = real_tool(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        return result


def _bind_script_tool_spec(
    spec: Any,
    *,
    sandbox_ws: Any,
    local_ws: Any,
    user_info: dict[str, Any] | None,
    sandbox_degraded: bool = False,
) -> Any:
    """把脚本工具接到当前会话工作区（沙箱可用则进沙箱）。

    与 Bash 同源：`sandbox_ws` 为空表示 local 策略，退回用户工作区内的本机子进程；
    沙箱不可用时**报错而不是降级到宿主机**，避免"以为在沙箱里跑、其实在宿主上跑"。
    """
    metadata = getattr(spec, "metadata", None) or {}
    config = metadata.get("script_tool_config")
    if config is None:
        return spec

    if sandbox_degraded and sandbox_ws is None:
        # 明确区分「local 部署」（本来就没有沙箱）和「容器部署本轮降级」：
        # 后者绝不能在平台后端进程里跑用户脚本。
        raise PermissionError(
            "脚本工具未执行：当前部署要求脚本在沙箱内运行，但沙箱本轮不可用。"
            "不会退回平台后端进程执行，请稍后重试。"
        )

    workdir = getattr(local_ws, "workdir", None)
    if not workdir:
        raise PermissionError("脚本工具未执行：无法解析当前会话工作区")

    from app.services.ai.tools.script_tool_runtime import (
        ScriptToolRunner,
        render_result_for_model,
    )

    runner = ScriptToolRunner(
        config,
        workdir=workdir,
        sandbox_ws=sandbox_ws,
        sandbox_root=getattr(local_ws, "workspace_user_root", None),
        user_info=user_info,
        conversation_id=getattr(local_ws, "conversation_id", None),
    )

    async def _invoke_script_tool(**kwargs: Any) -> Any:
        result = await runner.run(kwargs)
        return await render_result_for_model(result, workdir=runner.workdir)

    return replace(spec, callable=_invoke_script_tool)


async def bind_configured_tools_to_workspace(
    workspace: Any,
    tool_specs: list[Any] | None,
    *,
    user_info: dict[str, Any] | None = None,
    sandbox_degraded: bool = False,
) -> list[Any]:
    """Bind configured Bash/Read/Write/Edit/Glob/Grep to the session workspace.

    Responsibility split (定稿): Bash binds to the sandbox (docker/e2b/ssh).
    For Docker, host-backed file tools translate the same logical ``/workspace``
    path into the per-user host root before execution. All host-backed file
    tools then enforce the public/private path policy before native execution.

    ``workspace`` may be ``(sandbox_ws, local_ws)`` (modern) or a single
    workspace (legacy local-only). Falls back to returning ``specs`` unchanged
    when no matching workspace / tool is available.
    """
    specs = list(tool_specs or [])
    if workspace is None:
        if any(
            _workspace_native_name_for_spec(spec)
            in DOCKER_WORKSPACE_FILE_TOOL_NAMES
            for spec in specs
        ):
            raise PermissionError(
                "文件访问被拒绝：宿主工作区不可用，文件工具未执行"
            )
        return specs
    if not specs:
        return specs

    sandbox_ws, local_ws = _normalize_workspace_pair(workspace)
    is_lazy = isinstance(sandbox_ws, LazySandboxWorkspaceProxy)
    sandbox_host_root = None
    # 这是「沙箱工具是否改用宿主路径包装」的总开关。K8s 与 Docker 的沙箱内根同样是
    # /workspace，且 workspace_user_root / file_tool_mount_mappings 两种策略都已备好；
    # 若只判 Docker，K8s 下会丢两件事：Bash 默认 cwd 停在 /workspace（而非当前会话目录，
    # 与提示词承诺不一致），以及绝对 Glob.pattern 不被翻译——只有
    # _SandboxLogicalWorkspaceNativeTool 会改写 pattern，_WorkspaceFileAccessNativeTool
    # 只改写 path。
    if (
        sandbox_ws is not None
        and getattr(sandbox_ws, "_platform_sandbox_policy", None)
        in (SANDBOX_POLICY_DOCKER, SANDBOX_POLICY_K8S)
        and local_ws is not None
    ):
        sandbox_host_root = getattr(local_ws, "workspace_user_root", None)
    sandbox_file_tool_mount_mappings = (
        getattr(sandbox_ws, "_platform_sandbox_file_tool_mount_mappings", None)
        if sandbox_ws is not None
        else None
    )

    file_access_root = sandbox_host_root
    if file_access_root is None and local_ws is not None:
        file_access_root = getattr(local_ws, "workspace_user_root", None)
    if file_access_root is None and user_info is not None:
        from app.utils.fs_access import get_user_private_workspace_root

        file_access_root = get_user_private_workspace_root(user_info)
    if file_access_root is None and local_ws is not None:
        file_access_root = getattr(local_ws, "workdir", None)

    # Collect file tools from the host local workspace (Read/Write/Edit/Glob/Grep).
    local_tools: dict[str, Any] = {}
    if local_ws is not None:
        local_tools = await _as_workspace_tool_map(local_ws)
    # Collect bash from the sandbox (docker/e2b/ssh) via its MCP bash tool.
    sandbox_bash: Any | None = None
    if sandbox_ws is not None:
        if is_lazy:
            bash_spec = next(
                (spec for spec in specs if _workspace_native_name_for_spec(spec) == "Bash"),
                None,
            )
            if bash_spec is not None:
                sandbox_bash = LazySandboxBashNativeTool(
                    proxy=sandbox_ws,
                    local_ws=local_ws,
                    name="Bash",
                    description=getattr(bash_spec, "description", ""),
                    input_schema=getattr(bash_spec, "parameters_schema", None),
                )
        else:
            sandbox_bash, bash_failure = await _resolve_sandbox_bash_tool(sandbox_ws)

            if sandbox_bash is None and any(
                _workspace_native_name_for_spec(spec) == "Bash" for spec in specs
            ):
                detail = f"（{bash_failure}）" if bash_failure else ""
                if _is_k8s_sandbox(sandbox_ws):
                    from app.services.ai.runtime.agentscope.k8s_workspace import (
                        K8sSandboxUnavailableError,
                    )

                    raise K8sSandboxUnavailableError(
                        f"Kubernetes sandbox Bash MCP is unavailable{detail}",
                        reason_code="k8s_sandbox_mcp_unavailable",
                        user_message=(
                            "Kubernetes 沙箱中的 Bash 工具不可用，Bash 未执行。"
                            "平台已尝试自动重建内置 MCP，仍未恢复；"
                            "请检查沙箱网关与 Bash MCP 注册状态（K8s 沙箱的 Bash MCP "
                            "子进程需使用网关虚拟环境解释器）。"
                        ),
                    )
                raise DockerSandboxUnavailableError(
                    f"Docker sandbox Bash MCP is unavailable{detail}",
                    reason_code="docker_sandbox_mcp_unavailable",
                    user_message=(
                        "Docker 沙箱中的 Bash 工具不可用，Bash 未执行。"
                        "平台已尝试自动重建内置 MCP，仍未恢复；"
                        "请检查容器网关和 MCP 配置。"
                    ),
                )

    from app.services.ai.runtime.agentscope.tools import (
        runtime_tool_spec_from_native_agentscope_tool,
    )

    bound: list[Any] = []
    for spec in specs:
        if str(getattr(spec, "source_type", "")) == "script_tool":
            bound.append(
                _bind_script_tool_spec(
                    spec,
                    sandbox_ws=sandbox_ws,
                    local_ws=local_ws,
                    user_info=user_info,
                    sandbox_degraded=sandbox_degraded,
                )
            )
            continue
        native_name = _workspace_native_name_for_spec(spec)
        if native_name == "Bash":
            # Sandbox policies: Bash is served only by the sandbox. Local policy:
            # served by the host LocalWorkspace's Bash (a cancellable host
            # subprocess over the session workdir).
            workspace_tool = (
                sandbox_bash if sandbox_ws is not None else local_tools.get("Bash")
            )
        else:
            workspace_tool = local_tools.get(native_name or "")
        if workspace_tool is None:
            if native_name in DOCKER_WORKSPACE_FILE_TOOL_NAMES:
                raise PermissionError(
                    "文件访问被拒绝：宿主文件工具不可用，文件工具未执行"
                )
            bound.append(spec)
            continue
        if native_name == "Bash" and sandbox_ws is None:
            try:
                from app.services.ai.runtime.conversation_run_subprocess import (
                    attach_cancellable_backend,
                )

                attach_cancellable_backend(workspace_tool)
            except Exception:
                pass
        if (
            sandbox_host_root
            and native_name in DOCKER_WORKSPACE_FILE_TOOL_NAMES
        ):
            workspace_tool = _SandboxLogicalWorkspaceNativeTool(
                workspace_tool,
                sandbox_host_root,
                mount_mappings=sandbox_file_tool_mount_mappings,
            )
        if native_name in DOCKER_WORKSPACE_FILE_TOOL_NAMES:
            if not file_access_root:
                raise PermissionError(
                    "文件访问被拒绝：无法解析当前用户工作区根目录"
                )
            workspace_tool = _WorkspaceFileAccessNativeTool(
                workspace_tool,
                user_info=user_info,
                workspace_root=file_access_root,
                mount_mappings=sandbox_file_tool_mount_mappings,
            )
        if (
            sandbox_host_root
            and native_name == "Bash"
            and not is_lazy
            and local_ws is not None
            and getattr(local_ws, "workdir", None)
        ):
            workspace_tool = _SandboxSessionBashNativeTool(
                workspace_tool,
                sandbox_host_root,
                local_ws.workdir,
            )
        if (
            native_name == "Bash"
            and not is_lazy
            and getattr(workspace_tool, "name", None) != "Bash"
        ):
            workspace_tool = _CanonicalWorkspaceNativeTool(workspace_tool, "Bash")
        rebound = runtime_tool_spec_from_native_agentscope_tool(
            workspace_tool,
            source_type=getattr(spec, "source_type", "system"),
            permission_scope=getattr(spec, "permission_scope", None),
        )
        # Bash 的 description 是时间线行标题的来源：沙箱 MCP / 内置 schema 都把它当可选，
        # 这里统一在绑定出口补上平台要求，保证 lazy、沙箱、local 三条路径口径一致。
        final_description = spec.description or rebound.description
        final_parameters_schema = rebound.parameters_schema
        if native_name == "Bash":
            final_description = _augment_bash_tool_description(final_description)
            final_parameters_schema = _augment_bash_parameter_schema(
                final_parameters_schema
            )
        bound.append(
            replace(
                rebound,
                description=final_description,
                parameters_schema=final_parameters_schema,
                evidence_types=getattr(spec, "evidence_types", rebound.evidence_types),
                evidence_policy=getattr(spec, "evidence_policy", rebound.evidence_policy),
                evidence_inference_disabled=bool(
                    getattr(spec, "evidence_inference_disabled", False)
                ),
                timeout_seconds=getattr(spec, "timeout_seconds", None),
                audit_callback=getattr(spec, "audit_callback", None),
            )
        )
    return bound


def _normalize_workspace_pair(workspace: Any) -> tuple[Any, Any]:
    """Extract ``(sandbox_ws, local_ws)`` from tuple or legacy single value."""
    if isinstance(workspace, (tuple, list)) and len(workspace) == 2:
        return workspace[0], workspace[1]
    # Legacy: a single workspace object is treated as the local workspace.
    return None, workspace


async def _as_workspace_tool_map(ws: Any) -> dict[str, Any]:
    """Run ``ws.list_tools()`` (sync or async) and return a name->tool map."""
    list_tools = getattr(ws, "list_tools", None)
    if list_tools is None:
        return {}
    try:
        listed = list_tools()
        if inspect.isawaitable(listed):
            listed = await listed
    except Exception as exc:  # noqa: BLE001
        logger.warning("[workspace] Failed to list workspace tools: %s", exc)
        return {}
    if not isinstance(listed, (list, tuple)):
        return {}
    return {
        str(getattr(tool, "name", "") or ""): tool
        for tool in (listed or [])
        if getattr(tool, "name", None)
    }


async def _k8s_named_pod_identity(user_key: str) -> tuple[str, str]:
    """Return (namespace, pod_name) for a user without a cached workspace.

    Pod 名带技能集合令牌，无从推算，所以优先按 AgentScope 的 Pod 标签
    （``agentscope.workspace.id``）反查该用户真实存在的 Pod；查不到时退回历史命名
    ``as-ws-<user_key>``（老部署 Pod 未带令牌时仍能命中）。
    """
    from app.services.config_service import ConfigService
    from app.services.ai.runtime.agentscope.k8s_workspace import (
        find_k8s_sandbox_pod_name,
        resolve_sandbox_namespace,
    )

    namespace = resolve_sandbox_namespace(
        await ConfigService.get("sandbox_k8s_namespace", "")
    )
    try:
        discovered = await find_k8s_sandbox_pod_name(
            namespace, [_sandbox_workspace_id_pattern(user_key)]
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[workspace] k8s pod label lookup failed for %s: %s", user_key, exc)
        discovered = None
    return namespace, discovered or _k8s_pod_name(user_key)


async def exec_k8s_workspace_command(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    command: str,
    workdir: str | None = None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a one-shot shell command inside the current user's K8s sandbox Pod.

    Mirrors ``exec_docker_workspace_command`` for the Kubernetes backend: the Pod
    is located from the process-local cache (falling back to the derived name),
    and the command runs via the Kubernetes WebSocket exec. Returns the same
    output shape the Docker terminal UI consumes.
    """
    user_key = await _k8s_runtime_guard(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
        operation="exec",
    )

    root = await resolve_workspace_root()
    workspace = _find_cached_k8s_workspace(root, user_key, conversation_id=conversation_id)
    if workspace is not None and getattr(workspace, "is_alive", True):
        namespace, pod_name = await _k8s_workspace_pod_identity(workspace)
    else:
        namespace, pod_name = await _k8s_named_pod_identity(user_key)

    if not pod_name:
        from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

        raise K8sSandboxUnavailableError(
            "unable to locate K8s sandbox Pod",
            reason_code="k8s_pod_not_found",
            user_message="未能定位 Kubernetes 沙箱 Pod，请先启动沙箱后再进入终端。",
        )

    # 与 Docker 终端一致：技能集合过期（禁用/下架/白名单调整）时拒绝在这台 Pod 里执行
    stale = await _conversation_stale_skills_snapshot(
        root=root,
        user_key=user_key,
        conversation_id=conversation_id,
        policy=SANDBOX_POLICY_K8S,
        user_info=user_info,
    )
    if stale is not None:
        from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

        stale_key, _stale_paths = stale
        await _evict_sandbox_cache_entry(stale_key, reason="terminal_k8s_skills_stale")
        raise K8sSandboxUnavailableError(
            "K8s sandbox skills snapshot is stale",
            reason_code="k8s_sandbox_skills_stale",
            user_message=(
                "该沙箱的技能集合已变更（技能被禁用/下架或白名单调整），已重置沙箱。"
                "请先发送一条消息拉起新沙箱，再使用终端。"
            ),
        )

    from app.services.ai.runtime.agentscope.k8s_workspace import exec_k8s_sandbox_command

    # 终端里的一条命令同样可能跑很久：此前只在命令结束后才刷新活跃时间，执行期间
    # 沙箱处于「看起来空闲」状态，会被 reaper 关掉（用户看到终端被踢）。
    async with _sandbox_activity_heartbeat(
        sandbox_user_key=user_key,
        policy=SANDBOX_POLICY_K8S,
    ):
        result = await exec_k8s_sandbox_command(
            namespace=namespace,
            pod_name=pod_name,
            command=command,
            workdir=workdir,
        )
    result["execution_backend"] = SANDBOX_POLICY_K8S
    result["workspace_id"] = user_key
    result["pod_name"] = pod_name
    return result
