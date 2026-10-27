"""System tools: list current user's accessible datasets and knowledge bases."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.ai.tools.document_paths import resolve_context_user_name
from app.services.ai.tools.tool_compat import tool
from app.services.ai.knowledge_catalog import fetch_authorized_knowledge_catalog
from app.services.metadata_service import MetadataService
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)


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
    - 仅返回目录级信息，不含文档正文；本工具不承担正文检索。
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝列出知识库。"

    try:
        user_name = resolve_context_user_name(ctx)
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
    """列出当前用户有权委派且运行就绪的系统级智能体/专家目录（包含 agent_name、展示名称、职责描述、核心能力与当前会话标识 is_current）。

    使用规则：
    - 当用户问「我有哪些智能体」「能调用哪些专家」「可用智能体列表」时调用。
    - 在需要委派子任务前，若需查询或确认可委派的智能体标识 (agent_name) 与核心能力，可调用此工具。
    - 目录只包含**平台系统级智能体**：用户自建智能体即使由本人自动授权，也不能被委派，故不在此列。
    - 返回体含 scope/items/count；当前对话的智能体若在列则标注 is_current=true。
    - 空目录时返回体附带 note 说明原因，必须据此向用户解释（例如「你没被分配可委派的系统级智能体，自建的不可委派」），不要简化成「你没有任何智能体」。
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝列出可用智能体。"

    try:
        from app.services.ai.tools.agent_delegate_tool import (
            load_delegable_system_agent_candidates,
            resolve_runnable_delegable_system_agents,
        )

        current_agent_id = getattr(ctx, "agent_id", None)
        async with AsyncSessionLocal() as db:
            # 候选口径与委派链其余环节共用同一来源（目录 / 专家清单 / 子代理候选 /
            # sub_agent_call 全部走 load_delegable_system_agent_candidates）。
            # 不能复用 AgentManagerService.list_agents：那是 UI 可见性口径，按 created_by
            # 归属过滤，而工具侧没有 user 可传，会把候选整批剃成空目录。
            active_agents = await load_delegable_system_agent_candidates(db)
            available_agents = await resolve_runnable_delegable_system_agents(
                db,
                active_agents,
                user_id=ctx.user_id,
                is_admin=bool(ctx.is_admin),
                # 传 None 才能把自身也纳入目录（由 is_current 标识）；传当前 id 会把自己剔除。
                current_agent_id=None,
            )
            items = [_agent_item(a, current_agent_id=current_agent_id) for a in available_agents]
            payload: dict[str, Any] = {
                "scope": "delegable_system_agents",
                "items": items,
                "count": len(items),
            }
            if not items:
                # 空目录必须自带原因。只给 {"items": [], "count": 0} 时，模型很容易把它
                # 转述成「你没有任何智能体」，与智能体中心里用户自建的条目直接冲突——
                # 页面上有 3 个、对话里说 0 个。note 让模型能照着解释「为什么是 0」。
                payload["note"] = (
                    "当前平台没有可委派的系统级智能体（可能均未启用、未发布或运行不就绪）。"
                    if bool(ctx.is_admin)
                    else (
                        "你没有被分配可委派的系统级智能体；自己创建的智能体不可委派，"
                        "故不在本目录中（仍可在智能体中心直接使用）。"
                    )
                )
            return json.dumps(payload, ensure_ascii=False)
    except Exception as e:
        logger.error("[list_available_agents] failed: %s", e, exc_info=True)
        return f"列出可用智能体失败: {e}"


@tool
async def list_accessible_directories() -> str:
    """列出当前用户与会话可访问的文件目录清单、读写权限（只读/可写）及推荐用途说明。

    使用规则：
    - 当 AI 需要了解自身当前可访问哪些目录、需要确定文件落盘位置（如生成报告、导出 Excel/PDF、写入临时代码）、或查看公共与个人空间区别时调用此工具。
    - 读写或搜索文件前若不确定目标文件路径，或遇到文件找不到/写入被拒时，必须优先调用本工具查看全景映射，严禁盲猜路径。
    - 本工具只负责可访问目录、权限和路径映射；目标目录已经明确时无需重复调用本工具。
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
            resolve_sandbox_logical_root,
            sandbox_policy_is_sandbox,
            sandbox_skill_layout,
            sandbox_personal_skills_path,
            SANDBOX_POLICY_DOCKER,
            SANDBOX_POLICY_K8S,
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

        user_name = resolve_context_user_name(ctx)
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
        # 是否「Bash 跑在隔离环境」统一走 workspace 侧口径（含 e2b / ssh）：只认 docker/k8s
        # 会让 e2b/ssh 报成 backend_service，模型据此去访问后端技能路径，方向反了。
        is_sandbox = sandbox_policy_is_sandbox(effective_policy)
        # Bash 侧逻辑工作区根：docker/k8s=/workspace、e2b=/home/user/workspace、
        # ssh=系统配置的 remote_workdir。local 时为空（Bash 直接用后端真实路径）。
        sandbox_root = (
            await resolve_sandbox_logical_root(effective_policy) if is_sandbox else None
        )
        if is_sandbox and not sandbox_root:  # 配置读失败时退回历史默认，避免拼出 None/
            sandbox_root = "/workspace"
        # 宿主用户工作区（docs/sessions/uploads）只挂进 docker/k8s；e2b/ssh 只同步技能副本。
        workspace_mounted = is_sandbox and effective_policy in (
            SANDBOX_POLICY_DOCKER,
            SANDBOX_POLICY_K8S,
        )
        personal_skills_container_path = sandbox_personal_skills_path(effective_policy)
        skill_layout = sandbox_skill_layout(effective_policy)
        # 机读命名空间：几种沙箱的值必须可区分，否则模型在 K8s 下读到的执行环境仍是
        # "docker_sandbox"，容易做出错误的路径/工具推断。
        sandbox_namespace = (
            f"{effective_policy}_sandbox" if is_sandbox else "backend_service"
        )

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
                "bash": container_path if is_sandbox else backend_path,
                "file_tools": file_tool_path or backend_path,
            }

        def _path_namespace(container_path: str | None) -> dict[str, str | None]:
            return {
                "bash": (
                    sandbox_namespace
                    if is_sandbox and container_path
                    else None
                    if is_sandbox
                    else "backend_service"
                ),
                "file_tools": "backend_service",
            }

        def _workspace_container_path(*parts: str) -> str | None:
            """宿主用户工作区子目录在沙箱内的路径。

            只有 docker/k8s 把宿主用户工作区（docs/sessions/uploads/.trash）挂进沙箱；
            e2b/ssh 只同步技能副本，这些子目录在沙箱内不存在，必须返回 None，
            否则模型会照着清单去 Bash 里访问一个不存在的目录。
            """
            if not workspace_mounted:
                return None
            suffix = "/".join(str(part).strip("/") for part in parts if str(part or "").strip("/"))
            return f"{sandbox_root.rstrip('/')}/{suffix}" if suffix else sandbox_root

        # 用户私有目录详细路径
        docs_service_path = os.path.join(user_host_workspace, "docs")
        uploads_service_path = os.path.join(user_host_workspace, "uploads")
        skills_service_path = os.path.join(user_host_workspace, "skills")
        trash_service_path = os.path.join(user_host_workspace, ".trash")

        user_directories: list[dict[str, Any]] = [
            {
                "directory_name": "docs",
                "container_sandbox_path": (
                    _workspace_container_path("docs") if is_sandbox else docs_service_path
                ),
                "backend_service_path": docs_service_path,
                "paths": _tool_paths(
                    container_path=_workspace_container_path("docs"),
                    backend_path=docs_service_path,
                    file_tool_path="docs",
                ),
                "path_namespace": _path_namespace(
                    _workspace_container_path("docs"),
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
                _workspace_container_path("sessions", os.path.basename(session_phys))
                if is_sandbox
                else None
            )
            session_file_tool_path = os.path.join(
                USER_SESSIONS_DIR_NAME,
                os.path.basename(session_phys),
            )
            user_directories.append({
                "directory_name": f"sessions/{conversation_id}",
                "container_sandbox_path": session_container_path if is_sandbox else session_phys,
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
                "container_sandbox_path": (
                    _workspace_container_path("uploads") if is_sandbox else uploads_service_path
                ),
                "backend_service_path": uploads_service_path,
                "paths": _tool_paths(
                    container_path=_workspace_container_path("uploads"),
                    backend_path=uploads_service_path,
                    file_tool_path="uploads",
                ),
                "path_namespace": _path_namespace(
                    _workspace_container_path("uploads"),
                ),
                "permission": "read_write",
                "category": "user_uploads",
                "description": "用户上传的会话附件目录。存放用户在聊天界面上传的文件与原始数据资料。",
                "recommended_for": ["读取用户上传的文件", "查找会话原始输入资料"],
            },
            {
                "directory_name": "skills",
                # 两个 skills 路径必须分清：
                #   - <逻辑根>/skills 是运行时合并后的**公共技能副本**（AgentScope 的 .seed
                #     模板 + 各智能体分区，平台按公共技能库指纹在下次容器启动时自动重建），
                #     归 platform_global_skills 条目；
                #   - <逻辑根>/personal-skills 是本用户**个人技能源目录**的只读挂载（仅 docker/k8s），实时
                #     包含用户当前所有个人技能，技能自带脚本可以直接用 Bash 执行，
                #     不需要先复制到会话目录。
                # 宿主文件工具仍然使用用户工作区下的 skills/（清单相对路径 skills）。
                "container_sandbox_path": (
                    personal_skills_container_path if is_sandbox else skills_service_path
                ),
                "backend_service_path": skills_service_path,
                "paths": _tool_paths(
                    container_path=personal_skills_container_path,
                    backend_path=skills_service_path,
                    file_tool_path="skills",
                ),
                "path_namespace": _path_namespace(
                    personal_skills_container_path,
                ),
                "path_semantics": "user_personal_skills_source",
                "access_via": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
                # 用户自己的技能可以在宿主侧改（Write/Edit），只有沙箱内是只读挂载。
                "permission": "read_write",
                "category": "user_personal_skills",
                "description": (
                    "用户个人专属自定义技能目录（技能源目录，含 SKILL.md 与自带脚本）。"
                    + (
                        (
                            f"沙箱内以**只读**方式挂载到 {personal_skills_container_path}"
                            "（实时、不是副本），可用 Bash 直接执行技能自带脚本；写操作请用宿主文件工具。"
                            if personal_skills_container_path
                            else "当前沙箱策略只把技能复制进 skills/ 副本，没有个人技能源挂载；"
                            "技能文本请用 read_skill_instruction(skill_id) 读取，"
                            "需要改技能请用宿主文件工具。"
                        )
                        if is_sandbox
                        else "通过宿主文件工具读写。"
                    )
                ),
                "recommended_for": ["个人自定义技能", "执行个人技能自带脚本"],
            },
            {
                "directory_name": ".trash",
                "container_sandbox_path": (
                    _workspace_container_path(".trash") if is_sandbox else trash_service_path
                ),
                "backend_service_path": trash_service_path,
                "paths": _tool_paths(
                    container_path=_workspace_container_path(".trash"),
                    backend_path=trash_service_path,
                    file_tool_path=".trash",
                ),
                "path_namespace": _path_namespace(
                    _workspace_container_path(".trash"),
                ),
                "permission": "read_write",
                "category": "trash",
                "description": "用户工作区回收站。存放已删除但可恢复的文件。",
                "recommended_for": ["已删除文件归档"],
            },
        ])

        # 构建公共目录（只读）
        global_skills_service_path = get_platform_skills_root() or os.path.join(data_base, "skills")
        # 沙箱内技能根随逻辑根走：docker/k8s -> /workspace/skills、e2b -> /home/user/workspace/skills、
        # ssh -> <remote_workdir>/skills。local 不用它（Bash 直接用上面那个后端路径）。
        sandbox_skills_root = (
            f"{sandbox_root.rstrip('/')}/skills" if is_sandbox else None
        )
        branding_service_path = os.path.join(data_base, "branding")
        global_docs_service_path = os.path.join(data_base, "docs")
        public_docs_mounted = bool(
            workspace_mounted
            and (
                # K8s 适配器固定挂载 subPath docs（k8s_workspace.py 传
                # public_docs_mounted=True），无需探测宿主 docs 目录。
                effective_policy == SANDBOX_POLICY_K8S
                or _resolve_docker_public_docs_source() is not None
            )
        )

        # 被禁技能不再作为可读目录列出：技能根整体仍是可读的（脚本等附件是正常用法），
        # 但清单必须点名哪些技能子目录读不到，否则模型会照着 /app/data/skills/<id>/SKILL.md
        # 的固定形状去猜、去试。
        from app.services.ai.skills.policy import get_forbidden_skill_ids

        forbidden_skill_ids = await get_forbidden_skill_ids()
        available_skill_ids: list[str] = []
        try:
            from app.services.ai.skill_resolver import list_global_skill_metas

            available_skill_ids = sorted(
                {
                    str(meta.get("id") or "").strip()
                    for meta in list_global_skill_metas()
                    if str(meta.get("id") or "").strip()
                    and str(meta.get("id") or "").strip() not in forbidden_skill_ids
                }
            )
        except Exception as exc:
            # 清单是提示性信息：列举失败不能把整个目录工具打成不可用
            logger.warning("[list_accessible_directories] skill ids unavailable: %s", exc)

        public_directories: list[dict[str, Any]] = [
            {
                "directory_name": "docs",
                "container_sandbox_path": (
                    f"{sandbox_root.rstrip('/')}/public/docs"
                    if public_docs_mounted
                    else None
                    if is_sandbox
                    else global_docs_service_path
                ),
                "backend_service_path": global_docs_service_path,
                "paths": _tool_paths(
                    container_path=(
                        f"{sandbox_root.rstrip('/')}/public/docs"
                        if public_docs_mounted
                        else None
                    ),
                    backend_path=global_docs_service_path,
                    # When the read-only Docker mount exists, expose the same
                    # logical namespace to Read/Glob/Grep. The workspace
                    # adapter maps it to the backend docs root; leaking the
                    # backend absolute path makes the model confuse it with a
                    # host physical path.
                    file_tool_path=(
                        f"{sandbox_root.rstrip('/')}/public/docs"
                        if public_docs_mounted
                        else global_docs_service_path
                    ),
                ),
                "path_namespace": _path_namespace(
                    (
                        f"{sandbox_root.rstrip('/')}/public/docs"
                        if public_docs_mounted
                        else None
                    ),
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
                        f"沙箱内以只读方式挂载到 {sandbox_root.rstrip('/')}/public/docs；"
                        if public_docs_mounted
                        else "当前 Docker 沙箱未挂载，通过宿主文件工具只读查阅；"
                        if is_sandbox
                        else "通过宿主文件工具只读查阅。"
                    )
                ),
                "recommended_for": ["查阅公共产品手册", "参考公共标准模板与制度文档"],
            },
            {
                "directory_name": "skills",
                "container_sandbox_path": (
                    sandbox_skills_root if is_sandbox else global_skills_service_path
                ),
                "backend_service_path": global_skills_service_path,
                "paths": _tool_paths(
                    container_path=sandbox_skills_root if is_sandbox else None,
                    backend_path=global_skills_service_path,
                    file_tool_path=global_skills_service_path,
                ),
                "path_namespace": _path_namespace(
                    sandbox_skills_root if is_sandbox else None,
                ),
                "path_semantics": (
                    "per_user_seeded_copy" if is_sandbox else "platform_directory"
                ),
                "permission": "read_only",
                "category": "platform_global_skills",
                # 按「当前用户可用技能」收口：被禁技能不得出现在任何一份可读清单里
                # （它们的 SKILL.md 在 fs 读判定上也会被直接拒绝）。
                "available_skill_ids": available_skill_ids,
                "forbidden_skill_ids": sorted(forbidden_skill_ids),
                "description": (
                    "平台全局公共技能库。包含所有预置的专业 Agent 技能与工作流模板，"
                    + (
                        (
                            f"沙箱内通过 {sandbox_skills_root} 下的副本访问，"
                            "不是直接挂载 /app/data/skills；副本沿用 AgentScope 的 seed 布局，"
                            "技能目录在其点开头的隐藏子目录 .seed/ 下，"
                            f"仅在需要执行技能自带脚本时才用 Bash `ls -a {sandbox_skills_root}` "
                            "定位实际布局，不要假设固定子路径；该副本按当前可用技能打包，"
                            "技能新增/修改/删除/禁用后会在下次沙箱启动时自动重建"
                            "（正在运行的沙箱不会立即刷新）。"
                            if skill_layout != "flat"
                            else
                            f"沙箱内通过 {sandbox_skills_root} 下的平铺副本访问"
                            f"（形如 {sandbox_skills_root}/<skill_id>/SKILL.md，平铺副本，没有隐藏的种子模板子目录）；"
                            "技能变更后会在下次沙箱启动时重新复制。"
                        )
                        if is_sandbox
                        else (
                            f"local 执行策略下 Bash 与宿主文件工具（Read/Glob/Grep）使用同一套后端真实路径 "
                            f"{global_skills_service_path}，每个技能一个平铺子目录"
                            "（<skill_id>/SKILL.md），没有容器路径命名空间、也不存在沙箱专属的隐藏技能目录布局；"
                            "技能文本一律用 read_skill_instruction(skill_id) 读取，"
                            "只有需要执行技能自带脚本时才用 Bash 进入技能目录。"
                        )
                    )
                    + (
                        "其中 forbidden_skill_ids 里的技能已被管理员禁用，"
                        "其目录不可读取，禁止尝试读取或检索。"
                        if forbidden_skill_ids
                        else ""
                    )
                ),
                "recommended_for": ["读取系统公共技能指令", "查看内置工作流"],
            },
            {
                "directory_name": "branding",
                "container_sandbox_path": (
                    None if is_sandbox else branding_service_path
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
                    "禁止通过沙箱 Bash 访问或递归扫描 /app。"
                ),
                "recommended_for": ["公共 docs 未命中时查阅平台帮助与 FAQ"],
            }
            for path in get_public_runtime_help_files()
        ]

        result = {
            "deployment_environment": "docker_container" if is_container_env else "host_machine",
            "sandbox_execution_mode": sandbox_namespace if is_sandbox else "host_local",
            "user_identity": {
                "user_id": ctx.user_id,
                "user_name": user_name,
                "user_key": user_key,
                "is_admin": bool(ctx.is_admin),
            },
            "user_workspace": {
                "container_sandbox_root": (
                    sandbox_root if is_sandbox else user_host_workspace
                ),
                "backend_service_root": user_host_workspace,
                "paths": {
                    "bash": sandbox_root if is_sandbox else user_host_workspace,
                    "file_tools": ".",
                },
                "path_namespace": {
                    "bash": sandbox_namespace if is_sandbox else "backend_service",
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
                (
                    f"1. 在沙箱环境（{effective_policy}）下执行 Bash 命令或 Python 脚本时，请使用 paths.bash"
                    f"（沙箱内以 {sandbox_root} 开头）；调用 Read/Write/Edit/Glob/Grep 时请使用 paths.file_tools，"
                    "不能把 Bash 的容器路径直接当作文件工具路径；"
                    if is_sandbox
                    else "1. 当前为 local 执行策略：Bash 与 Read/Write/Edit/Glob/Grep 使用同一套后端真实路径"
                    "（见各目录的 paths.bash / paths.file_tools），不存在 /workspace 容器命名空间；"
                ),
                "2. 生成给用户的分析报告、导出的 Excel/PDF 或需长期保存的文件，请统一写入 docs/ 目录；",
                "3. 当前会话的临时计算脚本、中间缓存请写入 sessions/{conversation_id}/ 目录；",
                "4. 公共技能库 skills/ 和 branding/ 为只读空间，禁止尝试写入；",
                (
                    f"5. 公共 docs 在沙箱中通过 paths.bash={sandbox_root.rstrip('/')}/public/docs 只读访问；"
                    if public_docs_mounted
                    else "5. 公共 docs 当前未挂载到沙箱 Bash；"
                    if is_sandbox
                    else "5. 公共 docs 通过宿主文件工具只读访问；"
                )
                + (
                    "Read/Glob/Grep 也使用 paths.file_tools 指向的公共 docs 路径，系统会自动映射到后端公共 docs；"
                    if public_docs_mounted
                    else "文件工具使用 paths.file_tools 对应的后端 data/docs 路径；"
                )
                + "未命中时，可按 platform_help_files 使用宿主 Read/Glob/Grep 查阅服务根目录一级 *.md，禁止 Bash 或递归扫描 /app；",
                "6. 严禁尝试访问或臆造其他用户的私有目录路径（系统底层安全沙箱会自动拦截）。",
                (
                    (
                        f"7. 注意两份 skills 的区别：公共技能库的 {sandbox_skills_root} 是按当前可用技能打包的副本"
                        + (
                            "（沿用 AgentScope 的 seed 布局，技能目录在其点开头的隐藏子目录 .seed/ 下，"
                            f"仅在需要执行技能自带脚本时才用 Bash `ls -a {sandbox_skills_root}` 定位实际目录、"
                            "不要假设固定子路径；技能变更后会在下次沙箱启动时自动重建，运行中的沙箱不会立即刷新）；"
                            if skill_layout != "flat"
                            else (
                                f"（平铺复制，形如 {sandbox_skills_root}/<skill_id>/SKILL.md，没有隐藏的种子模板子目录；"
                                "技能变更后会在下次沙箱启动时重新复制）；"
                            )
                        )
                        + (
                            f"个人技能（category=user_personal_skills）源目录实时只读挂载在 "
                            f"{personal_skills_container_path}，技能自带脚本可直接用 Bash 执行，无需先复制到会话目录；"
                            if personal_skills_container_path
                            else "个人技能（category=user_personal_skills）没有源目录挂载，已随技能副本一起打包；"
                        )
                        + "技能文本请用 read_skill_instruction(skill_id) 读取；写入技能文件请用宿主文件工具（paths.file_tools）。"
                    )
                )
                if is_sandbox
                else f"7. 注意两份 skills 的区别：public_directories.skills 就是平台公共技能库本身（paths.bash 与 paths.file_tools 同值，均为后端真实路径 {global_skills_service_path}），每个技能一个平铺子目录（<skill_id>/SKILL.md），没有容器路径命名空间也没有沙箱专属的隐藏技能目录布局；个人技能（category=user_personal_skills）源目录在本人工作区的 skills/ 下；技能文本一律用 read_skill_instruction(skill_id) 读取，只有需要执行技能自带脚本时才用 Bash 进入技能目录；写入技能文件请用宿主文件工具（paths.file_tools）。",
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
