from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

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
WORKSPACE_REPLACED_PLATFORM_TOOL_NAMES = frozenset(
    {
        *WORKSPACE_BUILTIN_TOOL_NAMES,
        *AGENTSCOPE_BUILTIN_TOOL_ALIASES.keys(),
        "list_available_skills",
        "read_skill_instruction",
    }
)

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

DOCKER_WORKSPACE_INIT_RETRY_DELAY_SECONDS = 0.5
K8S_WORKSPACE_IDLE_SECONDS = 1800.0
K8S_WORKSPACE_REAPER_INTERVAL_SECONDS = 60.0


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
) -> list[str]:
    """Collect skill directories: global platform skills + user personal skills.

    When skills_custom is True, only allowlisted global skill ids are included;
    personal skills are always appended (if enabled).
    """
    try:
        from app.core.config import settings

        skills_root = getattr(settings, "SKILLS_DIR", None)
    except Exception:
        return []
    if not skills_root or not os.path.isdir(skills_root):
        return []

    allowlist: set[str] | None = None
    if skills_custom:
        allowlist = {str(s).strip() for s in (allowed_global_skills or []) if str(s).strip()}

    paths: list[str] = []
    from app.utils.skill_metadata import parse_skill_frontmatter
    for entry in sorted(os.listdir(skills_root)):
        skill_dir = os.path.join(skills_root, entry)
        if os.path.isdir(skill_dir) and os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
            if allowlist is not None and entry not in allowlist:
                continue
            # 过滤禁用的技能
            meta = parse_skill_frontmatter(entry, os.path.join(skill_dir, "SKILL.md"))
            if meta.get("enabled", "true") == "false":
                continue
            paths.append(os.path.abspath(skill_dir))

    # 追加用户个人技能路径
    if user_info:
        try:
            from app.services.ai.skill_resolver import get_user_personal_skills_dir

            personal_dir = get_user_personal_skills_dir(user_info)
            if personal_dir and os.path.isdir(personal_dir):
                for entry in sorted(os.listdir(personal_dir)):
                    skill_dir = os.path.join(personal_dir, entry)
                    if os.path.isdir(skill_dir) and os.path.isfile(
                        os.path.join(skill_dir, "SKILL.md")
                    ):
                        meta = parse_skill_frontmatter(entry, os.path.join(skill_dir, "SKILL.md"))
                        if meta.get("enabled", "true") == "false":
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
            return os.path.abspath(str(raw))
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


def _sanitize_skill_dir_name(name: str) -> str:
    """Replicate AgentScope's ``_sanitize_dir_name`` for pre-seeded layout.

    Allowed characters: ASCII letters/digits/underscore (``\\w``), CJK
    unified ideographs (一-鿿), and hyphens. Everything else becomes ``_``.
    Must stay byte-for-byte identical to the third-party implementation so
    the pre-seeded directory names match what AgentScope would produce.
    """
    return re.sub(r"[^\w一-鿿-]", "_", name)


def _hardlink_or_copy2(src: str, dst: str) -> None:
    """copytree ``copy_function``: hard-link first, copy2 on cross-device.

    Pre-seeding session ``skills/`` uses hard links so 400+ sessions share a
    single physical copy of every skill file instead of duplicating disk.
    When the source and destination live on different filesystems (e.g.
    Docker multi-volume mounts), ``os.link`` raises ``EXDEV``; fall back to
    a normal ``shutil.copy2`` so seeding always succeeds. ``copy2`` also
    copies metadata (mtime), keeping the snapshot faithful.
    """
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _preseed_session_skills(workdir: str, skill_paths: list[str]) -> None:
    """Pre-seed ``<workdir>/skills`` with the platform skills before AgentScope.

    AgentScope's ``LocalWorkspace.initialize`` seeds ``skill_paths`` into
    ``<workdir>/skills`` via ``shutil.copytree`` (a full physical copy per
    session), which multiplies disk usage linearly as sessions accumulate.
    This function pre-populates the same directory using hard links toward
    the shared source skill dirs (or ``copy2`` on cross-device) and writes an
    AgentScope-compatible ``.skills`` index, so that when ``initialize`` runs
    it finds every skill's content hash already present and skips all copying
    — eliminating the per-session duplication while keeping full COW-like
    isolation where the filesystem supports hard links.

    Idempotent: if ``<workdir>/skills/.skills`` already exists the seed is
    left untouched, so re-initialisation never rebuilds or overwrites a
    snapshot.

    Hard-link caveat (honest limitation): linked files share the same inode
    as the source. AgentScope never writes into session ``skills/`` and the
    platform's ``create_skills`` writes to the user/global source dirs, so a
    session snapshot stays effectively read-only; however if a source skill
    file were later modified in place, already-linked session copies would
    observe that change. Cross-device mounts fall back to real copies (no
    sharing), which is safe but re-introduces per-session duplication.
    """
    skills_dir = os.path.join(workdir, "skills")
    index_path = os.path.join(skills_dir, ".skills")
    if os.path.isfile(index_path):
        return

    os.makedirs(skills_dir, exist_ok=True)

    # Load any existing index so we never clobber previously seeded skills.
    existing: dict[str, dict[str, str]] = {}
    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        stored = data.get("skills")
        if isinstance(stored, dict):
            existing = {
                str(d): {"hash": str(e.get("hash", "")), "skill_name": str(e.get("skill_name", ""))}
                for d, e in stored.items()
                if isinstance(e, dict)
            }
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("[workspace] Failed to parse existing .skills: %s", exc)

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
        if skill_hash in existing_hashes:
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
        while dir_name in existing_dir_names:
            dir_name = f"{base_dir}_{counter}"
            counter += 1

        dest_path = os.path.join(skills_dir, dir_name)
        if not os.path.realpath(dest_path).startswith(
            os.path.realpath(skills_dir) + os.sep,
        ):
            logger.warning("[workspace] Pre-seed skip %s: path escapes skills_dir", skill_path)
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

        existing[dir_name] = {"hash": skill_hash, "skill_name": agent_name}
        existing_hashes.add(skill_hash)
        existing_agent_names.add(agent_name)
        existing_dir_names.add(dir_name)
        updated = True

    if updated:
        try:
            mtime = os.stat(skills_dir).st_mtime
        except OSError:
            mtime = 0.0
        payload = {"skills_dir_mtime": float(mtime), "skills": existing}
        try:
            with open(index_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, indent=2, ensure_ascii=False))
            logger.info(
                "[workspace] Pre-seeded %d skill(s) via hard links in %s",
                updated,
                skills_dir,
            )
        except Exception as exc:
            logger.warning("[workspace] Failed to write .skills at %s: %s", skills_dir, exc)


SANDBOX_POLICY_LOCAL = "local"
SANDBOX_POLICY_DOCKER = "docker"
SANDBOX_POLICY_K8S = "k8s"
SANDBOX_POLICY_E2B = "e2b"
SANDBOX_POLICY_SSH = "ssh"
DOCKER_WORKSPACE_IDLE_SECONDS = 30 * 60
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


def _personal_skills_dir(host_workdir: str) -> str:
    """Return the canonical personal skills source under a user workspace."""
    return os.path.join(os.path.abspath(host_workdir), "skills")


def _docker_sandbox_skills_dir(host_workdir: str) -> str:
    """Return the Docker-only materialized skills directory.

    This directory intentionally contains the merged public + personal skill
    snapshot used by the sandbox. It must never be used as the personal-skill
    API source.
    """
    return os.path.join(
        os.path.abspath(host_workdir),
        DOCKER_SANDBOX_DIR_NAME,
        "skills",
    )


def _build_docker_workspace_mounts(
    host_workdir: str,
    public_docs_source: str | None = None,
) -> list[tuple[str, str, str]]:
    """Build the Docker bind mounts while keeping source roots separate."""
    mounts = [
        (os.path.abspath(host_workdir), DOCKER_WORKSPACE_LOGICAL_ROOT, "rw"),
        (
            _docker_sandbox_skills_dir(host_workdir),
            DOCKER_WORKSPACE_SKILLS_PATH,
            "rw",
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


def _build_docker_file_tool_mount_mappings(
    backend_workspace_root: str,
    *,
    public_docs_mounted: bool = True,
) -> list[DockerMountMapping]:
    """Build the Docker-to-backend path mapping for host file tools.

    The user workspace is shared with the Docker container. Public docs and
    runtime skills are child mounts and therefore must be resolved before the
    parent ``/workspace`` mount. The merged runtime skills child mount is
    container-only for host file tools; callers should use the backend skills
    path from the resource catalog when they need to read a platform skill.
    """
    from app.utils.fs_paths import get_data_base_dir

    backend_skills_root = None

    return [
        (
            DOCKER_WORKSPACE_PUBLIC_DOCS_PATH,
            os.path.join(get_data_base_dir(), "docs")
            if public_docs_mounted
            else None,
            "ro",
        ),
        (DOCKER_WORKSPACE_SKILLS_PATH, backend_skills_root, "ro"),
        (DOCKER_WORKSPACE_LOGICAL_ROOT, os.path.abspath(backend_workspace_root), "rw"),
    ]


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
        build_container_tool_mcp,
    )
    from agentscope.workspace import DockerWorkspace
    from app.services.ai.runtime.agentscope.docker_workspace import (
        build_docker_workspace_with_extra_binds,
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
    if host_workdir:
        os.makedirs(_docker_sandbox_skills_dir(host_workdir), exist_ok=True)
        os.makedirs(
            os.path.join(host_workdir, DOCKER_PUBLIC_DIR_NAME, "docs"),
            exist_ok=True,
        )
        public_docs_source = _resolve_docker_public_docs_source()
        extra_bind_mounts = _build_docker_workspace_mounts(
            host_workdir,
            public_docs_source,
        )[1:]

    default_mcp = build_container_tool_mcp()

    kwargs: dict[str, Any] = {
        "host_workdir": host_workdir,  # None => ephemeral container
        "default_mcps": [default_mcp],
        "skill_paths": skill_paths,
        "base_image": base_image,
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

        workspace._platform_sandbox_policy = SANDBOX_POLICY_DOCKER
        workspace._platform_execution_backend = SANDBOX_POLICY_DOCKER
        workspace._platform_docker_public_docs_mounted = public_docs_source is not None
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
    """Acquire one process-local Docker workspace reference per user."""
    cache_key = f"{os.path.abspath(root)}::{user_key}::{SANDBOX_POLICY_DOCKER}"
    lock = _docker_workspace_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        workspace = _docker_workspace_cache.get(cache_key)
        if workspace is None or getattr(workspace, "is_alive", True) is False:
            workspace = await _policy_docker_workspace(
                skill_paths,
                workspace_id=user_key,
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
    return 1 if sandbox_ws is not None else 0


async def _acquire_k8s_workspace(
    *,
    root: str,
    user_key: str,
    skill_paths: list[str] | None,
) -> tuple[Any, str]:
    """Acquire one process-local K8s workspace reference per user."""
    cache_key = f"{os.path.abspath(root)}::{user_key}::{SANDBOX_POLICY_K8S}"
    lock = _k8s_workspace_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        workspace = _k8s_workspace_cache.get(cache_key)
        if workspace is None or getattr(workspace, "is_alive", True) is False:
            workspace = await _policy_k8s_workspace(
                skill_paths,
                workspace_id=user_key,
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
    return reaped


async def _docker_workspace_reaper_loop(
    *,
    idle_seconds: float,
    interval_seconds: float,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await reap_idle_docker_workspaces(idle_seconds=idle_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[workspace] Docker workspace reaper iteration failed")


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
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await reap_idle_k8s_workspaces(idle_seconds=idle_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[workspace] K8s workspace reaper iteration failed")


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
    )
    from app.services.ai.runtime.agentscope.workspace_container_mcp import (
        K8S_GATEWAY_VENV_PYTHON,
        build_container_tool_mcp,
    )

    namespace = (
        await _sandbox_config_value(
            "sandbox_k8s_namespace", "agent-sandboxes", config_overrides
        )
    ).strip() or "agent-sandboxes"
    image = (
        await _sandbox_config_value(
            "sandbox_k8s_image", "python:3.11-slim", config_overrides
        )
    ).strip() or "python:3.11-slim"
    existing_pvc = (
        await _sandbox_config_value(
            "sandbox_k8s_existing_pvc", "", config_overrides
        )
    ).strip() or None
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

    # 安全守卫：共享 PVC 模式严禁在未认证/空 user_key 状态下使用
    if existing_pvc and not sandbox_user_key:
        raise ValueError(
            "K8S 共享 PVC 沙箱策略要求必须具备已认证的用户身份（sandbox_user_key 不能为空），"
            "以防止未授权访问或越权挂载持久卷根目录。"
        )

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
        "skill_paths": skill_paths,
    }
    if effective_workspace_id:
        kwargs["workspace_id"] = effective_workspace_id

    workspace = build_k8s_workspace_with_nanzi_adapter(
        K8sWorkspace,
        existing_pvc=existing_pvc,
        sandbox_user_key=sandbox_user_key,
        public_docs_mounted=True,
        local_data_root=workspace_root,
        **kwargs,
    )
    try:
        await workspace.initialize()
    except Exception:
        await _close_workspace_safely(workspace, reason="K8s initialization failure")
        raise

    workspace._platform_sandbox_policy = SANDBOX_POLICY_K8S
    workspace._platform_execution_backend = SANDBOX_POLICY_K8S
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


async def get_local_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
    skills_custom: bool = False,
    allowed_global_skills: list[str] | None = None,
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
    cache_key = f"{workdir}::{skills_fp}::{policy}"
    cached = _workspace_cache.get(cache_key)
    if cached is not None:
        sandbox_cache_key = _workspace_sandbox_refs.get(cache_key)
        if sandbox_cache_key is not None:
            _touch_sandbox_workspace(sandbox_cache_key)
        return cached

    # 命中已有工作区时扫描结果本来就不会被使用；仅初始化时读取技能目录。
    skill_paths = discover_platform_skill_paths(
        user_info=user_info,
        skills_custom=skills_custom,
        allowed_global_skills=allowed_global_skills,
    )

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
            if policy == SANDBOX_POLICY_DOCKER:
                sandbox_user_key = _resolve_sandbox_user_key(
                    user_id=user_id,
                    user_name=user_name,
                    user_info=user_info,
                )
                if not sandbox_user_key:
                    raise RuntimeError(
                        "Docker sandbox requires an authenticated user identity"
                    )
                sandbox_ws, sandbox_cache_key = await _acquire_docker_workspace(
                    root=root,
                    user_key=sandbox_user_key,
                    skill_paths=skill_paths,
                )
            elif policy == SANDBOX_POLICY_K8S:
                sandbox_user_key = _resolve_sandbox_user_key(
                    user_id=user_id,
                    user_name=user_name,
                    user_info=user_info,
                )
                if not sandbox_user_key:
                    raise RuntimeError(
                        "K8s sandbox requires an authenticated user identity"
                    )
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

        # Pre-seed session skills/ with hard links + matching .skills index
        # so AgentScope's initialize() hash-skips all copies instead of
        # duplicating every skill into each session directory.
        _preseed_session_skills(workdir, skill_paths)
        local_ws = LocalWorkspace(
            workdir=workdir,
            skill_paths=skill_paths,
        )
        if policy == SANDBOX_POLICY_DOCKER and sandbox_user_key:
            local_ws.workspace_user_root = os.path.join(root, sandbox_user_key)
            if sandbox_ws is not None:
                sandbox_ws._platform_docker_file_tool_mount_mappings = (
                    _build_docker_file_tool_mount_mappings(
                        local_ws.workspace_user_root,
                        public_docs_mounted=bool(
                            getattr(
                                sandbox_ws,
                                "_platform_docker_public_docs_mounted",
                                False,
                            )
                        ),
                    )
                )
        await local_ws.initialize()
        if sandbox_ws is not None:
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
    )
    sandbox_ws, _local_ws = _normalize_workspace_pair(workspace_pair)
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

    try:
        import aiodocker
    except Exception as exc:  # pragma: no cover - dependency is environment-specific
        raise DockerSandboxUnavailableError(
            f"aiodocker is unavailable: {exc}",
            reason_code="docker_daemon_unavailable",
            user_message="当前后端无法连接 Docker daemon，暂时无法查询沙箱状态。",
        ) from exc

    client: Any | None = None
    container_name = f"as_ws_{sandbox_user_key}"
    try:
        client = aiodocker.Docker()
        container = await client.containers.get(container_name)
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
    try:
        import aiodocker
        client = aiodocker.Docker()
        container_name = f"as_ws_{sandbox_user_key}"
        try:
            container = await client.containers.get(container_name)
            details = await container.show()
            state = details.get("State") if isinstance(details, dict) else {}
            if state.get("Running"):
                await container.stop()
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                logger.warning("[workspace] Failed to stop container %s: %s", container_name, exc)
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

    # 2. 强制删除旧容器
    try:
        import aiodocker
        client = aiodocker.Docker()
        container_name = f"as_ws_{sandbox_user_key}"
        try:
            container = await client.containers.get(container_name)
            await container.delete(force=True)
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                logger.warning("[workspace] Failed to force delete container %s: %s", container_name, exc)
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

    client: Any | None = None
    container_name = f"as_ws_{sandbox_user_key}"
    start_time = time.monotonic()
    try:
        client = aiodocker.Docker()
        container = await client.containers.get(container_name)
        details = await container.show()
        state = details.get("State") if isinstance(details, dict) else {}
        if not state.get("Running"):
            raise DockerSandboxUnavailableError(
                "Docker sandbox container is not running",
                reason_code="docker_container_not_running",
                user_message="Docker 容器未在运行中，请先启动容器后再进入终端。",
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


async def _k8s_workspace_pod_identity(workspace: Any) -> tuple[str | None, str | None]:
    """Return (namespace, pod_name) best-effort for a live k8s workspace."""
    from app.services.config_service import ConfigService

    namespace = getattr(workspace, "_namespace", None) or (
        await ConfigService.get("sandbox_k8s_namespace", "agent-sandboxes")
    ).strip() or "agent-sandboxes"
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

    Read-only: never builds a workspace or creates a Pod. Returns ``idle``
    when no process-local workspace exists for this user. When a workspace is
    cached, prefers the live Pod phase via ``read_k8s_sandbox_pod`` and
    degrades to the cached ``is_alive`` view when the lookup is unavailable.
    """
    user_key = await _k8s_runtime_guard(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
        operation="status",
    )

    root = await resolve_workspace_root()
    cache_key = f"{os.path.abspath(root)}::{user_key}::{SANDBOX_POLICY_K8S}"
    workspace = _k8s_workspace_cache.get(cache_key)
    if workspace is None or getattr(workspace, "is_alive", True) is False:
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
                namespace=namespace or "agent-sandboxes",
                pod_name=pod_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[workspace] k8s status pod probe failed for %s: %s", pod_name, exc)
            pod_info = {"available": False, "found": None, "phase": None, "start_time": None}

        if pod_info.get("available") and pod_info.get("found"):
            phase = pod_info.get("phase")
            live_start = pod_info.get("start_time") or started_at
            if phase == "Running":
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
    )
    sandbox_ws, _local_ws = _normalize_workspace_pair(workspace_pair)
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
    return {
        "status": "stopped",
        "execution_backend": SANDBOX_POLICY_K8S,
        "workspace_id": user_key,
        "pod_name": None,
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
    if backend in {SANDBOX_POLICY_DOCKER, SANDBOX_POLICY_E2B, SANDBOX_POLICY_SSH}:
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
    docker_logical_root = (
        DOCKER_WORKSPACE_LOGICAL_ROOT
        if effective_policy == SANDBOX_POLICY_DOCKER
        else None
    )
    logical_session_workdir = None
    logical_docs_dir = None
    logical_public_docs_dir = None
    if docker_logical_root:
        logical_session_workdir = os.path.join(
            docker_logical_root,
            USER_SESSIONS_DIR_NAME,
            os.path.basename(session_workdir),
        )
        logical_docs_dir = os.path.join(docker_logical_root, USER_DOCS_DIR_NAME)
        if _resolve_docker_public_docs_source() is not None:
            logical_public_docs_dir = DOCKER_WORKSPACE_PUBLIC_DOCS_PATH
    block = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir=session_workdir,
        docs_dir=docs_dir,
        file_tool_names=sorted(file_tools),
        logical_workspace_root=docker_logical_root,
        logical_session_workdir=logical_session_workdir,
        logical_docs_dir=logical_docs_dir,
        logical_public_docs_dir=logical_public_docs_dir,
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


class _DockerLogicalWorkspaceNativeTool:
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

    async def __call__(self, **kwargs: Any) -> Any:
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
        if checker is None:
            return None
        result = checker(mapped_input, context)
        if inspect.isawaitable(result):
            return await result
        return result

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


class _DockerSessionBashNativeTool:
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


async def bind_configured_tools_to_workspace(
    workspace: Any,
    tool_specs: list[Any] | None,
    *,
    user_info: dict[str, Any] | None = None,
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
    docker_host_root = None
    if (
        sandbox_ws is not None
        and getattr(sandbox_ws, "_platform_sandbox_policy", None)
        == SANDBOX_POLICY_DOCKER
        and local_ws is not None
    ):
        docker_host_root = getattr(local_ws, "workspace_user_root", None)
    docker_file_tool_mount_mappings = (
        getattr(sandbox_ws, "_platform_docker_file_tool_mount_mappings", None)
        if sandbox_ws is not None
        else None
    )

    file_access_root = docker_host_root
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
        list_mcps = getattr(sandbox_ws, "list_mcps", None)
        sandbox_bash = await _sandbox_bash_tool_from_mcps(
            list_mcps() if callable(list_mcps) else None
        )

        if sandbox_bash is None and any(
            _workspace_native_name_for_spec(spec) == "Bash" for spec in specs
        ):
            if (
                getattr(sandbox_ws, "_platform_sandbox_policy", None)
                == SANDBOX_POLICY_K8S
            ):
                # Kubernetes 沙箱：Pod/RBAC 正常但网关未能提供 Bash MCP 工具时，
                # 应报 Kubernetes 专属诊断而非遗留的 Docker 文案。常见根因是沙箱内
                # 的 Bash MCP 子进程使用了未安装 mcp 包的系统 Python（应使用网关
                # 虚拟环境 /root/.agentscope/.venv/bin/python）。
                from app.services.ai.runtime.agentscope.k8s_workspace import (
                    K8sSandboxUnavailableError,
                )

                raise K8sSandboxUnavailableError(
                    "Kubernetes sandbox Bash MCP is unavailable",
                    reason_code="k8s_sandbox_mcp_unavailable",
                    user_message=(
                        "Kubernetes 沙箱中的 Bash 工具不可用，Bash 未执行。"
                        "请检查沙箱网关与 Bash MCP 注册状态（K8s 沙箱的 Bash MCP "
                        "子进程需使用网关虚拟环境解释器）。"
                    ),
                )
            raise DockerSandboxUnavailableError(
                "Docker sandbox Bash MCP is unavailable",
                reason_code="docker_workspace_start_failed",
                user_message=(
                    "Docker 沙箱中的 Bash 工具不可用，Bash 未执行。"
                    "请检查容器网关和 MCP 配置。"
                ),
            )

    from app.services.ai.runtime.agentscope.tools import (
        runtime_tool_spec_from_native_agentscope_tool,
    )

    bound: list[Any] = []
    for spec in specs:
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
            docker_host_root
            and native_name in DOCKER_WORKSPACE_FILE_TOOL_NAMES
        ):
            workspace_tool = _DockerLogicalWorkspaceNativeTool(
                workspace_tool,
                docker_host_root,
                mount_mappings=docker_file_tool_mount_mappings,
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
                mount_mappings=docker_file_tool_mount_mappings,
            )
        if (
            docker_host_root
            and native_name == "Bash"
            and local_ws is not None
            and getattr(local_ws, "workdir", None)
        ):
            workspace_tool = _DockerSessionBashNativeTool(
                workspace_tool,
                docker_host_root,
                local_ws.workdir,
            )
        if native_name == "Bash" and getattr(workspace_tool, "name", None) != "Bash":
            workspace_tool = _CanonicalWorkspaceNativeTool(workspace_tool, "Bash")
        rebound = runtime_tool_spec_from_native_agentscope_tool(
            workspace_tool,
            source_type=getattr(spec, "source_type", "system"),
            permission_scope=getattr(spec, "permission_scope", None),
        )
        bound.append(
            replace(
                rebound,
                description=spec.description or rebound.description,
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


def is_workspace_managed_tool_spec(spec: Any) -> bool:
    """Tools replaced by LocalWorkspace builtins or AgentScope skill viewer."""
    name = getattr(spec, "name", "")
    if name in WORKSPACE_REPLACED_PLATFORM_TOOL_NAMES:
        return True
    native_tool = getattr(spec, "native_tool", None)
    native_name = getattr(native_tool, "name", None) if native_tool is not None else None
    return native_name in WORKSPACE_BUILTIN_TOOL_NAMES


async def build_workspace_toolkit(
    workspace: Any,
    tool_specs: list[Any],
    *,
    approval_mode: str | None = None,
    user_info: dict[str, Any] | None = None,
    agent_timeout: Any = None,
):
    """显式合并 LocalWorkspace 内置文件工具与平台工具（Runner 默认不再调用）。

    AgentScope LocalWorkspace 会通过 list_tools() 返回 Bash/Read/Write/Edit/Glob/Grep。
    平台 Runner 现已改为只挂载 agent 配置工具；如需 workspace 内置工具，请在 agent
    后端配置对应别名（如 grep、read_file、exec_command）。
    """
    from app.services.ai.runtime.agentscope.tools import (
        _load_agentscope_toolkit,
        runtime_tool_from_native,
        runtime_tool_from_spec,
    )
    from app.services.ai.runtime.agentscope.tool_timeout import (
        apply_agent_tool_timeout,
        load_agent_max_toolcall_timeout,
        resolve_agent_toolcall_timeout,
    )

    toolkit_cls = _load_agentscope_toolkit()
    global_tool_timeout = await load_agent_max_toolcall_timeout()
    agent_tool_timeout = resolve_agent_toolcall_timeout(global_tool_timeout, agent_timeout)
    tool_specs = apply_agent_tool_timeout(
        tool_specs,
        global_tool_timeout,
        agent_timeout=agent_timeout,
    )
    workspace_tools = await workspace.list_tools()
    workspace_names = {getattr(tool, "name", "") for tool in workspace_tools}
    if workspace_names != set(WORKSPACE_BUILTIN_TOOL_NAMES):
        logger.warning(
            "[workspace] Unexpected workspace tools: %s",
            sorted(workspace_names),
        )

    workspace_root = getattr(workspace, "workspace_user_root", None)
    if workspace_root is None and user_info is not None:
        from app.utils.fs_access import get_user_private_workspace_root

        workspace_root = get_user_private_workspace_root(user_info)
    if workspace_root is None:
        workspace_root = getattr(workspace, "workdir", None)

    runtime_workspace_tools = []
    for tool in workspace_tools:
        tool_name = getattr(tool, "name", "")
        if tool_name in DOCKER_WORKSPACE_FILE_TOOL_NAMES:
            if not workspace_root:
                raise PermissionError(
                    "文件访问被拒绝：无法解析当前用户工作区根目录"
                )
            tool = _WorkspaceFileAccessNativeTool(
                tool,
                user_info=user_info,
                workspace_root=workspace_root,
            )
        runtime_workspace_tools.append(
            runtime_tool_from_native(
                tool,
                approval_mode=approval_mode,
                timeout_seconds=agent_tool_timeout,
            )
        )
    platform_tools = [
        runtime_tool_from_spec(
            spec,
            approval_mode=approval_mode,
        )
        for spec in tool_specs
        if not is_workspace_managed_tool_spec(spec)
    ]
    skills = await workspace.list_skills()
    mcps = await workspace.list_mcps()
    return toolkit_cls(
        tools=[*runtime_workspace_tools, *platform_tools],
        skills_or_loaders=skills,
        mcps=mcps,
    )
