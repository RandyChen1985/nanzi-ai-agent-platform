"""System tools: list current user's accessible datasets and knowledge bases."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.ai.tools.tool_compat import tool
from app.services.ai.knowledge_catalog import fetch_authorized_knowledge_catalog
from app.services.metadata_service import MetadataService
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)


def _context_user_name(ctx: Any) -> Optional[str]:
    dims = getattr(ctx, "user_dimensions", None) or {}
    if not isinstance(dims, dict):
        return None
    raw = dims.get("user_name") or dims.get("username")
    if raw is None:
        return None
    name = str(raw).strip()
    return name or None


def _dataset_item(row: Any) -> dict[str, Any]:
    return {
        "id": getattr(row, "id", None),
        "name": getattr(row, "name", None) or "",
        "display_name": getattr(row, "display_name", None) or "",
        "description": getattr(row, "description", None) or "",
        "status": getattr(row, "status", None),
    }


def _knowledge_item(row: Any) -> dict[str, Any]:
    return {
        "ragflow_dataset_id": getattr(row, "ragflow_dataset_id", None) or "",
        "name": getattr(row, "name", None) or "",
        "description": getattr(row, "description", None) or "",
        "notes": getattr(row, "notes", None) or "",
        "visibility": getattr(row, "visibility", None) or "",
        "owner": getattr(row, "owner", None) or "",
    }


@tool
async def list_accessible_datasets() -> str:
    """列出当前用户有权限且已启用的 ChatBI 数据集轻量目录（id/名称/备注/状态等，不含表字段指标）。

    使用规则：
    - 当用户问「我有哪些数据集」「能查哪些数据」「数据集列表」时调用。
    - 仅返回 status=1（启用）的目录级信息；未启用的数据集不会出现。
    - 不要据此编造表结构或查询结果。
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝列出数据集。"

    try:
        async with AsyncSessionLocal() as db:
            rows = await MetadataService.list_accessible_dataset_options(
                db,
                user_id=ctx.user_id,
                is_admin=bool(ctx.is_admin),
                status=1,
            )
            items = [_dataset_item(row) for row in rows]
            return json.dumps({"items": items, "count": len(items)}, ensure_ascii=False)
    except Exception as e:
        logger.error("[list_accessible_datasets] failed: %s", e, exc_info=True)
        return f"列出可访问数据集失败: {e}"


@tool
async def list_accessible_knowledge_bases() -> str:
    """列出当前用户有权限的知识库轻量目录（id/名称/备注等，不含文档正文）。

    使用规则：
    - 当用户问「我有哪些知识库」「能检索哪些文档库」「知识库列表」时调用。
    - 仅返回目录级信息；具体内容检索请使用 search_knowledge_base。
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝列出知识库。"

    try:
        user_name = _context_user_name(ctx)
        async with AsyncSessionLocal() as db:
            catalog = await fetch_authorized_knowledge_catalog(
                db,
                user_id=int(ctx.user_id),
                user_name=user_name,
                is_admin=bool(ctx.is_admin),
                permission_service=PermissionService(db),
            )
            items = [_knowledge_item(row) for row in catalog.items]
            items.sort(key=lambda x: x.get("ragflow_dataset_id") or "")
            return json.dumps({"items": items, "count": len(items)}, ensure_ascii=False)
    except Exception as e:
        logger.error("[list_accessible_knowledge_bases] failed: %s", e, exc_info=True)
        return f"列出可访问知识库失败: {e}"


def _agent_item(agent: Any, *, current_agent_id: Optional[str] = None) -> dict[str, Any]:
    agent_id = str(getattr(agent, "id", "") or "")
    is_current = bool(current_agent_id and agent_id == str(current_agent_id))
    return {
        "agent_name": getattr(agent, "name", "") or "",
        "display_name": getattr(agent, "display_name", "") or getattr(agent, "name", "") or "",
        "description": getattr(agent, "description", "") or "",
        "capabilities": list(getattr(agent, "capabilities", None) or []),
        "is_current": is_current,
    }


@tool
async def list_available_agents() -> str:
    """列出当前用户有权限访问且可运行的可用智能体/专家目录（包含 agent_name、展示名称、职责描述、核心能力与当前会话标识 is_current）。

    使用规则：
    - 当用户问「我有哪些智能体」「能调用哪些专家」「可用智能体列表」时调用。
    - 在需要通过 sub_agent_call 委派子任务前，若需查询或确认可委派的智能体标识 (agent_name) 与核心能力，可调用此工具。
    - 返回当前用户有权限访问的全部可用智能体，当前正在对话的自身智能体标注为 is_current=true。
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝列出可用智能体。"

    try:
        from app.services.ai.agent_manager import AgentManagerService
        from app.services.ai.tools.agent_delegate_tool import resolve_runnable_delegable_system_agents

        current_agent_id = getattr(ctx, "agent_id", None)
        async with AsyncSessionLocal() as db:
            active_agents = await AgentManagerService.list_agents(db)
            available_agents = await resolve_runnable_delegable_system_agents(
                db,
                active_agents,
                user_id=ctx.user_id,
                is_admin=bool(ctx.is_admin),
                current_agent_id=None,
            )
            items = [_agent_item(a, current_agent_id=current_agent_id) for a in available_agents]
            return json.dumps({"items": items, "count": len(items)}, ensure_ascii=False)
    except Exception as e:
        logger.error("[list_available_agents] failed: %s", e, exc_info=True)
        return f"列出可用智能体失败: {e}"


@tool
async def list_accessible_directories() -> str:
    """列出当前用户与会话可访问的文件目录清单、读写权限（只读/可写）及推荐用途说明。

    使用规则：
    - 当 AI 需要了解自身当前可访问哪些目录、需要确定文件落盘位置（如生成报告、导出 Excel/PDF、写入临时代码）、或查看公共与个人空间区别时调用此工具。
    - 读写或搜索文件前若不确定目标文件路径，或遇到文件找不到/写入被拒时，必须优先调用本工具查看全景映射，严禁盲猜路径。
    - 本工具只负责可访问目录、权限和路径映射；目标目录已经明确且只需要查看目录树时，使用 directory_tree_navigator。
    - 清楚区分「平台公共文档与手册 (data/docs/)，只读」、「系统公共技能库 (skills/)，只读」、「用户专属持久化文档库 (docs/)，可写」、「当前会话临时工作区 (sessions/{cid}/)，可写」和「用户上传附件 (uploads/)，可读写」。
    - 当公共 docs 未命中时，可把 platform_help_files 中列出的服务根目录一级 *.md 作为平台帮助文档兜底来源；仍只能通过宿主 Read/Glob/Grep 只读访问。
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝列出目录清单。"

    try:
        from app.services.config_service import ConfigService, resolve_effective_sandbox_policy
        from app.services.ai.runtime.agentscope.workspace import (
            resolve_workspace_root,
            resolve_user_workspace_root,
            resolve_user_docs_dir,
            resolve_session_workdir,
            resolve_user_sessions_dir,
            extract_workspace_identity,
            resolve_workspace_user_key,
            default_workspace_root,
            USER_SESSIONS_DIR_NAME,
            _resolve_docker_public_docs_source,
            SANDBOX_POLICY_DOCKER,
            SANDBOX_POLICY_LOCAL,
        )
        from app.utils.fs_access import (
            get_platform_skills_root,
            get_public_data_roots,
            get_user_uploads_dir,
            get_user_docs_dir,
            get_user_sessions_dir,
            get_user_sandbox_dir,
            get_user_private_workspace_root,
            get_public_runtime_help_files,
        )

        user_name = _context_user_name(ctx)
        user_info = {
            "user_id": ctx.user_id,
            "id": ctx.user_id,
            "user_name": user_name,
            "username": user_name,
            "role": "admin" if ctx.is_admin else "user",
        }
        conversation_id = str(getattr(ctx, "conversation_id", "") or "").strip() or None

        policy_raw = await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL)
        effective_policy = resolve_effective_sandbox_policy(policy_raw, SANDBOX_POLICY_LOCAL)
        is_docker_sandbox = (effective_policy == SANDBOX_POLICY_DOCKER)

        workspace_root = await resolve_workspace_root(ensure_exists=False)
        resolved_user_id, resolved_user_name = extract_workspace_identity(
            user_id=ctx.user_id,
            user_name=user_name,
            user_info=user_info,
        )
        user_key = resolve_workspace_user_key(user_id=resolved_user_id, user_name=resolved_user_name)
        user_host_workspace = get_user_private_workspace_root(user_info) or os.path.join(workspace_root, user_key)

        from app.utils.fs_paths import get_data_base_dir
        data_base = get_data_base_dir()
        is_container_env = data_base.startswith("/app/data") or os.path.exists("/.dockerenv")

        def _tool_paths(
            *,
            container_path: str | None,
            backend_path: str,
            file_tool_path: str | None = None,
        ) -> dict[str, Any]:
            """Describe the path accepted by each execution backend."""
            return {
                "bash": container_path if is_docker_sandbox else backend_path,
                "file_tools": file_tool_path or backend_path,
            }

        def _path_namespace(container_path: str | None) -> dict[str, str | None]:
            return {
                "bash": (
                    "docker_sandbox"
                    if is_docker_sandbox and container_path
                    else None
                    if is_docker_sandbox
                    else "backend_service"
                ),
                "file_tools": "backend_service",
            }

        # 用户私有目录详细路径
        docs_service_path = os.path.join(user_host_workspace, "docs")
        uploads_service_path = os.path.join(user_host_workspace, "uploads")
        skills_service_path = os.path.join(user_host_workspace, "skills")
        trash_service_path = os.path.join(user_host_workspace, ".trash")

        user_directories: list[dict[str, Any]] = [
            {
                "directory_name": "docs",
                "container_sandbox_path": "/workspace/docs" if is_docker_sandbox else docs_service_path,
                "backend_service_path": docs_service_path,
                "paths": _tool_paths(
                    container_path="/workspace/docs" if is_docker_sandbox else None,
                    backend_path=docs_service_path,
                    file_tool_path="docs",
                ),
                "path_namespace": _path_namespace(
                    "/workspace/docs" if is_docker_sandbox else None,
                ),
                "permission": "read_write",
                "category": "user_persistent_docs",
                "description": "用户专属持久化文档库。跨会话共享，AI 生成的最终分析报告、Markdown、Excel、PDF、图表及长久保存文件请默认保存在此目录。",
                "recommended_for": ["AI 产物落盘", "生成报告", "导出表格与文档", "跨会话复用文件"],
            }
        ]

        if conversation_id:
            session_phys = resolve_session_workdir(
                root=workspace_root,
                user_id=resolved_user_id,
                user_name=resolved_user_name,
                conversation_id=conversation_id,
                user_info=user_info,
            )
            session_container_path = (
                f"/workspace/sessions/{os.path.basename(session_phys)}"
                if is_docker_sandbox
                else None
            )
            session_file_tool_path = os.path.join(
                USER_SESSIONS_DIR_NAME,
                os.path.basename(session_phys),
            )
            user_directories.append({
                "directory_name": f"sessions/{conversation_id}",
                "container_sandbox_path": session_container_path if is_docker_sandbox else session_phys,
                "backend_service_path": session_phys,
                "paths": _tool_paths(
                    container_path=(
                        session_container_path
                    ),
                    backend_path=session_phys,
                    file_tool_path=session_file_tool_path,
                ),
                "path_namespace": _path_namespace(
                    session_container_path,
                ),
                "permission": "read_write",
                "category": "session_scratchpad",
                "description": "当前会话的专属临时工作区。存放仅限本次对话使用的中间过程文件、临时脚本、计算缓存等。",
                "recommended_for": ["当前会话临时脚本", "中间过程缓存", "单次任务草稿"],
            })

        user_directories.extend([
            {
                "directory_name": "uploads",
                "container_sandbox_path": "/workspace/uploads" if is_docker_sandbox else uploads_service_path,
                "backend_service_path": uploads_service_path,
                "paths": _tool_paths(
                    container_path="/workspace/uploads" if is_docker_sandbox else None,
                    backend_path=uploads_service_path,
                    file_tool_path="uploads",
                ),
                "path_namespace": _path_namespace(
                    "/workspace/uploads" if is_docker_sandbox else None,
                ),
                "permission": "read_write",
                "category": "user_uploads",
                "description": "用户上传的会话附件目录。存放用户在聊天界面上传的文件与原始数据资料。",
                "recommended_for": ["读取用户上传的文件", "查找会话原始输入资料"],
            },
            {
                "directory_name": "skills",
                # Docker 的 /workspace/skills 是运行时合并后的公共技能副本，
                # 不是用户私有 skills 源目录；宿主文件工具仍使用用户工作区下的 skills/。
                "container_sandbox_path": None if is_docker_sandbox else skills_service_path,
                "backend_service_path": skills_service_path,
                "paths": _tool_paths(
                    container_path=None,
                    backend_path=skills_service_path,
                    file_tool_path="skills",
                ),
                "path_namespace": _path_namespace(
                    None,
                ),
                "permission": "read_write",
                "category": "user_personal_skills",
                "description": "用户个人专属自定义技能目录。存放当前用户专属创建或定制的 Prompt/技能包。",
                "recommended_for": ["个人自定义技能"],
            },
            {
                "directory_name": ".trash",
                "container_sandbox_path": "/workspace/.trash" if is_docker_sandbox else trash_service_path,
                "backend_service_path": trash_service_path,
                "paths": _tool_paths(
                    container_path="/workspace/.trash" if is_docker_sandbox else None,
                    backend_path=trash_service_path,
                    file_tool_path=".trash",
                ),
                "path_namespace": _path_namespace(
                    "/workspace/.trash" if is_docker_sandbox else None,
                ),
                "permission": "read_write",
                "category": "trash",
                "description": "用户工作区回收站。存放已删除但可恢复的文件。",
                "recommended_for": ["已删除文件归档"],
            },
        ])

        # 构建公共目录（只读）
        global_skills_service_path = get_platform_skills_root() or os.path.join(data_base, "skills")
        branding_service_path = os.path.join(data_base, "branding")
        global_docs_service_path = os.path.join(data_base, "docs")
        public_docs_mounted = bool(
            is_docker_sandbox
            and _resolve_docker_public_docs_source() is not None
        )

        public_directories: list[dict[str, Any]] = [
            {
                "directory_name": "docs",
                "container_sandbox_path": (
                    "/workspace/public/docs"
                    if public_docs_mounted
                    else None
                    if is_docker_sandbox
                    else global_docs_service_path
                ),
                "backend_service_path": global_docs_service_path,
                "paths": _tool_paths(
                    container_path=(
                        "/workspace/public/docs" if public_docs_mounted else None
                    ),
                    backend_path=global_docs_service_path,
                    # When the read-only Docker mount exists, expose the same
                    # logical namespace to Read/Glob/Grep. The workspace
                    # adapter maps it to the backend docs root; leaking the
                    # backend absolute path makes the model confuse it with a
                    # host physical path.
                    file_tool_path=(
                        "/workspace/public/docs"
                        if public_docs_mounted
                        else global_docs_service_path
                    ),
                ),
                "path_namespace": _path_namespace(
                    "/workspace/public/docs" if public_docs_mounted else None,
                ),
                "access_via": (
                    ["Bash", "Read", "Glob", "Grep"]
                    if public_docs_mounted
                    else ["Read", "Glob", "Grep"]
                ),
                "permission": "read_only",
                "category": "platform_global_docs",
                "description": (
                    "平台全局公共文档与模板库（data/docs）。"
                    + (
                        "Docker 模式下以只读方式挂载到 /workspace/public/docs；"
                        if public_docs_mounted
                        else "当前 Docker 沙箱未挂载，通过宿主文件工具只读查阅；"
                        if is_docker_sandbox
                        else "通过宿主文件工具只读查阅。"
                    )
                ),
                "recommended_for": ["查阅公共产品手册", "参考公共标准模板与制度文档"],
            },
            {
                "directory_name": "skills",
                "container_sandbox_path": "/workspace/skills" if is_docker_sandbox else global_skills_service_path,
                "backend_service_path": global_skills_service_path,
                "paths": _tool_paths(
                    container_path="/workspace/skills" if is_docker_sandbox else None,
                    backend_path=global_skills_service_path,
                    file_tool_path=global_skills_service_path,
                ),
                "path_namespace": _path_namespace(
                    "/workspace/skills" if is_docker_sandbox else None,
                ),
                "path_semantics": (
                    "per_user_seeded_copy" if is_docker_sandbox else "platform_directory"
                ),
                "permission": "read_only",
                "category": "platform_global_skills",
                "description": (
                    "平台全局公共技能库。包含所有预置的专业 Agent 技能与工作流模板，"
                    "Docker 模式下通过当前用户工作区内的 /workspace/skills 预置副本访问，"
                    "不是直接挂载 /app/data/skills。"
                ),
                "recommended_for": ["读取系统公共技能指令", "查看内置工作流"],
            },
            {
                "directory_name": "branding",
                "container_sandbox_path": (
                    None if is_docker_sandbox else branding_service_path
                ),
                "backend_service_path": branding_service_path,
                "paths": _tool_paths(
                    container_path=None,
                    backend_path=branding_service_path,
                    file_tool_path=branding_service_path,
                ),
                "path_namespace": _path_namespace(None),
                "access_via": ["Read", "Glob", "Grep"],
                "permission": "read_only",
                "category": "platform_branding_assets",
                "description": (
                    "平台公共品牌与静态资产目录。包含 Logo、图标等公共资源，"
                    "Docker 沙箱 Bash 不直接挂载此目录，请通过宿主文件工具只读查阅。"
                ),
                "recommended_for": ["读取公共静态资源"],
            }
        ]

        platform_help_files = [
            {
                "path": path,
                "container_sandbox_path": None,
                "backend_service_path": path,
                "paths": {
                    "bash": None,
                    "file_tools": path,
                },
                "path_namespace": {
                    "bash": None,
                    "file_tools": "backend_service",
                },
                "path_semantics": "service_root_host_file_tool_path",
                "access_via": ["Read", "Glob", "Grep"],
                "permission": "read_only",
                "category": "platform_root_help",
                "description": (
                    "平台服务根目录帮助文档兜底来源；仅允许服务根目录一级 *.md，"
                    "禁止通过 Docker 沙箱 Bash 访问或递归扫描 /app。"
                ),
                "recommended_for": ["公共 docs 未命中时查阅平台帮助与 FAQ"],
            }
            for path in get_public_runtime_help_files()
        ]

        result = {
            "deployment_environment": "docker_container" if is_container_env else "host_machine",
            "sandbox_execution_mode": "docker_sandbox" if is_docker_sandbox else "host_local",
            "user_identity": {
                "user_id": ctx.user_id,
                "user_name": user_name,
                "user_key": user_key,
                "is_admin": bool(ctx.is_admin),
            },
            "user_workspace": {
                "container_sandbox_root": "/workspace" if is_docker_sandbox else user_host_workspace,
                "backend_service_root": user_host_workspace,
                "paths": {
                    "bash": "/workspace" if is_docker_sandbox else user_host_workspace,
                    "file_tools": ".",
                },
                "path_namespace": {
                    "bash": "docker_sandbox" if is_docker_sandbox else "backend_service",
                    "file_tools": "backend_service",
                },
                "access": "read_write",
                "subdirectories": user_directories,
            },
            "public_directories": {
                "access": "read_only",
                "directories": public_directories,
            },
            "platform_help_files": platform_help_files,
            "usage_guidelines": [
                "1. 在 Docker 沙箱环境下执行 Bash 命令或 Python 脚本时，请使用 paths.bash（通常以 /workspace 开头）；调用 Read/Write/Edit/Glob/Grep 时请使用 paths.file_tools，不能把 Bash 的容器路径直接当作文件工具路径；",
                "2. 生成给用户的分析报告、导出的 Excel/PDF 或需长期保存的文件，请统一写入 docs/ 目录；",
                "3. 当前会话的临时计算脚本、中间缓存请写入 sessions/{conversation_id}/ 目录；",
                "4. 公共技能库 skills/ 和 branding/ 为只读空间，禁止尝试写入；",
                (
                    "5. 公共 docs 在 Docker 中通过 paths.bash=/workspace/public/docs 只读访问；"
                    if public_docs_mounted
                    else "5. 公共 docs 当前未挂载到 Docker Bash；"
                    if is_docker_sandbox
                    else "5. 公共 docs 通过宿主文件工具只读访问；"
                )
                + (
                    "Read/Glob/Grep 也使用 paths.file_tools=/workspace/public/docs，系统会自动映射到后端公共 docs；"
                    if public_docs_mounted
                    else "文件工具使用 paths.file_tools 对应的后端 data/docs 路径；"
                )
                + "未命中时，可按 platform_help_files 使用宿主 Read/Glob/Grep 查阅服务根目录一级 *.md，禁止 Bash 或递归扫描 /app；",
                "6. 严禁尝试访问或臆造其他用户的私有目录路径（系统底层安全沙箱会自动拦截）。",
            ],
        }

        if ctx.is_admin:
            result["admin_notice"] = "当前用户具有管理员权限，可通过系统管理端或全量文件浏览器查看 data/ 下所有用户的目录与系统数据。"

        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("[list_accessible_directories] failed: %s", e, exc_info=True)
        return f"列出可访问目录失败: {e}"


# Grounding 证据声明（双重保障）：使工具不仅在 registry 查表可解析，其自身对象也显式具备元数据
from app.services.ai.grounding.models import EvidenceType

list_accessible_datasets.evidence_types = frozenset({EvidenceType.INTERNAL_DATA})
list_accessible_datasets.evidence_policy = "allow_empty_success"

list_accessible_knowledge_bases.evidence_types = frozenset({EvidenceType.INTERNAL_KNOWLEDGE})
list_accessible_knowledge_bases.evidence_policy = "allow_empty_success"

list_available_agents.evidence_types = frozenset({EvidenceType.RUNTIME_STATE})
list_available_agents.evidence_policy = "allow_empty_success"

list_accessible_directories.evidence_types = frozenset({EvidenceType.RUNTIME_STATE})
list_accessible_directories.evidence_policy = "allow_empty_success"
