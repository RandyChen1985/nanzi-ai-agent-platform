from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, distinct
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from app.models.permission import ResourcePermission, Role, UserRoleRelation
from app.models.user import User
from app.models.agent import AIAgent
from app.models.metadata import MetaDataset
from app.models.knowledge import KnowledgeBaseMetadata
from app.models.tool import SysApiTool
from app.schemas.permission import PermissionUpdate, UserPermissionsResponse, PermissionSet, PermissionSetDetail, ResourceDetail
from app.core.redis import get_redis
from collections.abc import Iterable
from typing import List, Optional
import json
import logging

logger = logging.getLogger(__name__)

class PermissionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._redis = None

    async def _get_redis(self):
        if not self._redis:
            self._redis = await get_redis()
        return self._redis

    async def _get_cache_key(self, user_id: int) -> str:
        # v3: 知识库 details 关联 knowledge_base_metadata.name
        return f"sys:auth:permissions:v3:user:{user_id}"

    async def get_user_permissions(self, user_id: int) -> UserPermissionsResponse:
        try:
            parsed_uid = int(user_id)
        except (TypeError, ValueError):
            parsed_uid = user_id
        # 1. Try Cache
        redis = await self._get_redis()
        cache_key = await self._get_cache_key(parsed_uid)
        if redis:
            cached_data = await redis.get(cache_key)
            if cached_data:
                try:
                    data = json.loads(cached_data)
                    cached = UserPermissionsResponse(**data)
                    # 资源 ID 列表走缓存；展示名（尤其知识库）随元数据表更新，每次命中后刷新 details
                    cached.details = await self._fetch_permission_details(cached.permissions)
                    return cached
                except Exception as e:
                    logger.warning(f"Failed to parse permission cache for user {user_id}: {e}")

        # 2. DB Query
        perm_set = PermissionSet()
        user_roles_list = []

        # A. Fetch User & System Role
        user_stmt = select(User).where(User.id == parsed_uid)
        user_result = await self.db.execute(user_stmt)
        user = user_result.scalar_one_or_none()

        if user and user.role:
            user_roles_list.append(user.role) # Add 'admin' or 'user'

        # B. Fetch Business Roles
        role_stmt = select(Role.code).join(UserRoleRelation, Role.id == UserRoleRelation.role_id).where(UserRoleRelation.user_id == parsed_uid)
        role_result = await self.db.execute(role_stmt)
        business_roles = role_result.scalars().all()
        user_roles_list.extend(business_roles)

        # C. Fetch Direct Permissions
        direct_stmt = select(ResourcePermission).where(
            ResourcePermission.user_id == parsed_uid,
            ResourcePermission.enabled == True
        )
        direct_perms = (await self.db.execute(direct_stmt)).scalars().all()
        self._aggregate_permissions(perm_set, direct_perms)

        # D. Fetch Role Permissions
        # Join: UserRoleRelation -> ResourcePermission (via role_id)
        role_perm_stmt = select(ResourcePermission).join(
            UserRoleRelation, ResourcePermission.role_id == UserRoleRelation.role_id
        ).where(
            UserRoleRelation.user_id == parsed_uid,
            ResourcePermission.enabled == True
        )
        role_perms = (await self.db.execute(role_perm_stmt)).scalars().all()
        self._aggregate_permissions(perm_set, role_perms)

        # E. Fetch Details
        details = await self._fetch_permission_details(perm_set)

        response = UserPermissionsResponse(
            roles=list(set(user_roles_list)), # Dedup
            permissions=perm_set,
            details=details
        )

        # 3. Set Cache (TTL 1 hour)
        if redis:
            await redis.set(
                cache_key,
                response.model_dump_json(),
                ex=3600
            )

        return response

    async def get_accessible_dataset_ids_csv(self, user_id: int) -> str:
        """
        RagFlow 知识库 ID 列表（逗号分隔），供 OpenClaw AUTH_CONTEXT 等使用。

        口径说明：
        - 数据集权限在系统里通常以 ``resource_type == "metadata"`` 存储（resource_id 对应 MetaDataset.id）
          需要再映射为 MetaDataset.rag_dataset_id（RagFlow 侧 knowledge base id）
        - 为兼容历史数据，同时并集 ``resource_type == "dataset"`` 直接给出的 resource_id（若其本身即为 rag_dataset_id）

        ``get_user_permissions`` 已聚合 **用户直接授权** 与 **用户所属角色授权** 的并集。
        """
        perms = await self.get_user_permissions(user_id)
        ids: set[str] = set()
        # 1) 兼容：resource_type == "dataset" 时的直接写入（假设其已是 RagFlow KB id）
        for d in perms.permissions.datasets:
            s = (d or "").strip()
            if s:
                ids.add(s)

        # 2) 主口径：resource_type == "metadata" -> MetaDataset.id -> MetaDataset.rag_dataset_id
        meta_ids: list[int] = []
        for mid in perms.permissions.metadata:
            s = (mid or "").strip()
            if s.isdigit():
                meta_ids.append(int(s))

        if meta_ids:
            stmt = select(MetaDataset).where(MetaDataset.id.in_(meta_ids))
            metas = (await self.db.execute(stmt)).scalars().all()
            for m in metas:
                rag_id = (getattr(m, "rag_dataset_id", None) or "").strip()
                if rag_id:
                    ids.add(rag_id)

        return ",".join(sorted(ids))

    @staticmethod
    def is_admin_user(roles: Iterable[str]) -> bool:
        return "admin" in set(roles or [])

    async def get_knowledge_base_access(
        self,
        user_id: int,
        user_name: Optional[str] = None,
    ) -> dict:
        """
        知识库（RAGFlow Dataset）访问口径：
        - 管理员：全部可访问、可写
        - 其他用户：角色+用户直连权限并集（permissions.datasets）+ 自己创建的知识库
        - 仅被分配、非创建人：只读（不可改元数据/文档，不可查看 chunk 明细）
        """
        perms = await self.get_user_permissions(user_id)
        if self.is_admin_user(perms.roles):
            return {
                "is_admin": True,
                "accessible_ids": None,
                "writable_ids": None,
            }

        accessible: set[str] = set()
        for dataset_id in perms.permissions.datasets:
            token = (dataset_id or "").strip()
            if token:
                accessible.add(token)

        created_ids: set[str] = set()
        if user_name:
            stmt = select(KnowledgeBaseMetadata.ragflow_dataset_id).where(
                KnowledgeBaseMetadata.status != "deleted",
                KnowledgeBaseMetadata.created_by == user_name,
            )
            rows = (await self.db.execute(stmt)).scalars().all()
            created_ids = {row for row in rows if row}

        # 合并系统级配置的默认公开知识库
        from app.services.config_service import ConfigService
        default_ids_str = await ConfigService.get("knowledge_ragflow_dataset_ids")
        if default_ids_str:
            for item in default_ids_str.split(","):
                token = item.strip()
                if token:
                    accessible.add(token)

        accessible |= created_ids
        return {
            "is_admin": False,
            "accessible_ids": accessible,
            "writable_ids": created_ids,
        }

    async def filter_knowledge_dataset_ids(
        self,
        user_id: int,
        user_name: Optional[str],
        dataset_ids: List[str],
    ) -> List[str]:
        access = await self.get_knowledge_base_access(user_id, user_name)
        if access["is_admin"]:
            return list(dataset_ids)
        allowed = access["accessible_ids"] or set()
        return [d for d in dataset_ids if d in allowed]

    async def get_accessible_ragflow_meta_datasets(self, user_id: int) -> list[dict[str, str]]:
        """
        返回用户可访问的、已同步到 RagFlow 的元数据数据集信息（供 OpenClaw AUTH_CONTEXT.datasets 使用）。

        输出元素结构：
        - ragflow_meta_id: MetaDataset.rag_dataset_id
        - ragflow_meta_name: MetaDataset.name
        - ragflow_meta_desc: MetaDataset.display_name

        权限来源复用 ``get_user_permissions``（个人中心/权限服务口径，聚合用户直连 + 角色）。
        """
        perms = await self.get_user_permissions(user_id)
        meta_ids: list[int] = []
        for mid in perms.permissions.metadata:
            s = (mid or "").strip()
            if s.isdigit():
                meta_ids.append(int(s))

        if not meta_ids:
            return []

        stmt = (
            select(MetaDataset)
            .where(MetaDataset.id.in_(meta_ids))
            .where(MetaDataset.status == 1)
        )
        metas = (await self.db.execute(stmt)).scalars().all()

        datasets: list[dict[str, str]] = []
        for m in metas:
            rag_id = (getattr(m, "rag_dataset_id", None) or "").strip()
            if not rag_id:
                continue
            datasets.append(
                {
                    "ragflow_meta_id": rag_id,
                    "ragflow_meta_name": (m.name or "").strip(),
                    "ragflow_meta_desc": (m.display_name or "").strip(),
                }
            )
        datasets.sort(key=lambda x: x.get("ragflow_meta_id") or "")
        return datasets

    async def _fetch_permission_details(self, perm_set: PermissionSet) -> PermissionSetDetail:
        details = PermissionSetDetail()

        # 完整权限 ID 精确中文映射表（最高优先级匹配）
        exact_permission_mapping = {
            # 菜单类
            "menu:dashboard": "系统概览",
            "menu:ai_chat": "智能助手",
            "menu:data_sources": "数据源管理",
            "menu:metadata": "元数据管理",
            "menu:agent_management": "智能体中心",
            "menu:skills_management": "技能工作台",
            "menu:mcp_management": "MCP 工具集",
            "menu:mcp_service": "MCP 服务台",
            "menu:memory_management": "记忆工作台",
            "menu:chatbi_examples": "案例集管理",
            "menu:knowledge_management": "知识库管理",
            "menu:knowledge_retrieval_test": "检索测试",
            "menu:agent_debug": "智能体调试",
            "menu:playground": "接口调试台",
            "menu:chat_logs": "聊天日志",
            "menu:system": "系统管理",
            "menu:system:users": "用户管理",
            "menu:system:roles": "角色管理",
            "menu:system:config": "系统配置",
            "menu:system:audit": "审计日志",
            "menu:prompts": "提示词工坊",
            "menu:task_center": "任务调度台",
            "menu:widget_debug": "组件调试台",

            # MCP 服务台四段式功能点
            "element:mcp_service:overview:read": "MCP服务台-查看服务总览",
            "element:mcp_service:config:read": "MCP服务台-查看服务配置",
            "element:mcp_service:config:edit": "MCP服务台-修改服务开关",
            "element:mcp_service:client:read": "MCP服务台-查看外部Client",
            "element:mcp_service:client:manage": "MCP服务台-管理外部Client",
            "element:mcp_service:client:secret_reset": "MCP服务台-重置Client Secret",
            "element:mcp_service:client:token_issue": "MCP服务台-生成用户AccessToken",
            "element:mcp_service:capability:read": "MCP服务台-查看能力与Scope",
            "element:mcp_service:capability:manage": "MCP服务台-管理能力与Scope",
            "element:mcp_service:grant:read": "MCP服务台-查看用户授权",
            "element:mcp_service:grant:revoke": "MCP服务台-撤销用户授权",
            "element:mcp_service:audit:read": "MCP服务台-查看调用审计",

            # 技能工作台
            "element:skills:admin": "技能工作台-平台管理与审核",

            # 任务调度台
            "element:task:manage": "任务调度台-任务管理",

            # 记忆工作台
            "element:memory:config_save": "记忆工作台-保存服务配置",
            "element:memory:config_index": "记忆工作台-索引检查与重建",
            "element:memory:view_data": "记忆工作台-查看记忆数据",
            "element:memory:delete": "记忆工作台-删除记忆",
            "element:memory:view_all_users": "记忆工作台-按任意用户筛选",
            "element:memory:test_search": "记忆工作台-记忆检索测试",

            # 提示词工坊与聊天日志
            "element:prompts:optimize": "提示词工坊-AI优化建议",
            "element:chat_logs:export": "聊天日志-导出日志",

            # 数据源与元数据
            "element:metadata:import": "元数据管理-智能导入",
            "element:metadata:sync": "元数据管理-同步至RAGFlow",
            "element:metadata:edit": "元数据管理-编辑数据集",
            "element:metadata:delete": "元数据管理-删除数据集",
            "element:metadata:view_yaml": "元数据管理-查看YAML",
            "element:metadata:edit_table": "元数据管理-配置表结构",
            "element:metadata:delete_table": "元数据管理-删除物理表",

            # 案例集
            "element:chatbi_example:audit": "案例集管理-审核反馈",
            "element:chatbi_example:sync": "案例集管理-同步至RAGFlow",
            "element:chatbi_example:delete": "案例集管理-废弃删除",
        }

        # 中文映射字典 (支持多种可能的键名格式)
        menu_name_mapping = {
            'dashboard': '仪表盘',
            'Dashboard': '仪表盘',
            'ai_chat': '智能助手',
            'metadata': '元数据管理',
            'Metadata': '元数据管理',
            'data_sources': '数据源管理',
            'agent_management': '智能体中心',
            'Agent_management': '智能体中心',
            'skills_management': '技能工作台',
            'mcp_management': 'MCP 工具集',
            'mcp_service': 'MCP 服务台',
            'agent_debug': '智能体调试',
            'playground': '接口调试台',
            'chat_logs': '聊天日志',
            'chatbi_examples': '案例集管理',
            'users': '用户管理',
            'roles': '角色管理',
            'config': '系统配置',
            'audit': '审计日志',
            'prompts': '提示词工坊',
            'widget_debug': '组件调试台',
            'task_center': '任务调度台',
            'knowledge_management': '知识库管理',
            'knowledge_retrieval_test': '检索测试',
            'memory_management': '记忆工作台'
        }

        element_module_mapping = {
            'metadata': '元数据管理',
            'agent': '智能体中心',
            'chat_logs': '聊天日志',
            'prompts': '提示词工坊',
            'user': '用户管理',
            'role': '角色管理',
            'system': '系统管理',
            'chatbi_example': '案例集管理',
            'knowledge': '知识库开发平台',
            'memory': '记忆工作台',
            'skills': '技能工作台',
            'mcp_service': 'MCP服务台',
            'task': '任务调度台'
        }

        element_action_mapping = {
            'import': '导入',
            'edit': '编辑',
            'delete': '删除',
            'create': '创建',
            'view_yaml': '查看YAML',
            'edit_table': '编辑表',
            'delete_table': '删除表',
            'export': '导出',
            'optimize': '优化',
            'view_key': '查看密钥',
            'reset_key': '重置密钥',
            'config_save': '保存配置',
            'audit': '审核',
            'sync': '同步',
            'upload_document': '上传文档',
            'delete_document': '删除文档',
            'parse_document': '解析文档',
            'test_retrieval': '检索测试',
            'admin': '管理与审核',
            'manage': '管理',
            'overview': '服务总览',
            'config': '配置',
            'client': '外部Client',
            'capability': '能力与Scope',
            'grant': '用户授权',
            'read': '查看'
        }

        # 1. Agents
        if perm_set.agents:
            stmt = select(AIAgent).where(AIAgent.id.in_(perm_set.agents))
            agents = (await self.db.execute(stmt)).scalars().all()
            details.agents = [
                ResourceDetail(
                    id=a.id,
                    name=a.name,
                    display_name=a.display_name,
                    description=a.description
                ) for a in agents
            ]

        # 2. Metadata (Datasets)
        if perm_set.metadata:
            # Metadata IDs are Strings in permission set but Ints in DB
            meta_ids = []
            for mid in perm_set.metadata:
                if mid.isdigit():
                    meta_ids.append(int(mid))

            if meta_ids:
                stmt = select(MetaDataset).where(MetaDataset.id.in_(meta_ids))
                metas = (await self.db.execute(stmt)).scalars().all()
                details.metadata = [
                    ResourceDetail(
                        id=str(m.id),
                        name=m.name,
                        display_name=m.display_name,
                        description=m.description or ""
                    ) for m in metas
                ]

        # 3. APIs
        if perm_set.apis:
            stmt = select(SysApiTool).where(SysApiTool.id.in_(perm_set.apis))
            apis = (await self.db.execute(stmt)).scalars().all()
            details.apis = [
                ResourceDetail(
                    id=a.id,
                    name=a.name,
                    display_name=a.name, # API tools might not have separate display_name
                    description=a.description
                ) for a in apis
            ]

        # 4. 知识库（RAGFlow dataset id → knowledge_base_metadata 名称）
        if perm_set.datasets:
            ds_ids = [(d or "").strip() for d in perm_set.datasets if (d or "").strip()]
            meta_by_rag_id: dict[str, KnowledgeBaseMetadata] = {}
            if ds_ids:
                stmt = select(KnowledgeBaseMetadata).where(
                    KnowledgeBaseMetadata.ragflow_dataset_id.in_(ds_ids),
                    KnowledgeBaseMetadata.status != "deleted",
                )
                rows = (await self.db.execute(stmt)).scalars().all()
                meta_by_rag_id = {
                    m.ragflow_dataset_id: m for m in rows if m.ragflow_dataset_id
                }

            details.datasets = []
            for ds_id in perm_set.datasets:
                token = (ds_id or "").strip()
                if not token:
                    continue
                local = meta_by_rag_id.get(token)
                if local:
                    display_name = (local.name or "").strip() or token
                    description = (local.description or "").strip() or None
                else:
                    display_name = token
                    description = "未同步平台元数据"
                details.datasets.append(
                    ResourceDetail(
                        id=token,
                        name=token,
                        display_name=display_name,
                        description=description,
                    )
                )

        # 5. Menus (Static or logical IDs)
        if perm_set.menus:
            details.menus = []
            for m_id in perm_set.menus:
                # 优先查全量精确映射表
                if m_id in exact_permission_mapping:
                    display_name = exact_permission_mapping[m_id]
                else:
                    # 提取菜单键名
                    menu_key = m_id.split(':')[-1] if ':' in m_id else m_id
                    display_name = menu_name_mapping.get(menu_key, menu_name_mapping.get(m_id, menu_key.replace('_', ' ').capitalize()))
                details.menus.append(
                    ResourceDetail(
                        id=m_id,
                        name=m_id,
                        display_name=display_name
                    )
                )

        # 6. Elements
        if perm_set.elements:
            details.elements = []
            for e_id in perm_set.elements:
                # 1. 优先查全量精确映射表
                if e_id in exact_permission_mapping:
                    display_name = exact_permission_mapping[e_id]
                else:
                    # 2. 动态多段式规则拆分: element:模块:操作[:子操作...]
                    parts = e_id.split(':')
                    if len(parts) >= 3:
                        module = parts[1]  # 模块
                        action_tokens = parts[2:]  # 操作与子操作
                        module_name = element_module_mapping.get(module, module)

                        # 对每个操作词进行翻译并拼接
                        translated_actions = [element_action_mapping.get(tok, tok) for tok in action_tokens]
                        action_name = "".join(translated_actions) if len(translated_actions) > 1 else translated_actions[0]
                        display_name = f"{module_name}-{action_name}"
                    else:
                        display_name = e_id.split(':')[-1]

                details.elements.append(
                    ResourceDetail(
                        id=e_id,
                        name=e_id,
                        display_name=display_name
                    )
                )

        # 7. Forbidden Tools
        if perm_set.forbidden_tools:
            details.forbidden_tools = [
                ResourceDetail(id=t_id, name=t_id, display_name=t_id)
                for t_id in perm_set.forbidden_tools
            ]

        # 8. Forbidden Commands
        if perm_set.forbidden_commands:
            details.forbidden_commands = [
                ResourceDetail(id=cmd, name=cmd, display_name=cmd)
                for cmd in perm_set.forbidden_commands
            ]

        return details

    def _aggregate_permissions(self, perm_set: PermissionSet, perms: list[ResourcePermission]):
        for p in perms:
            if p.resource_type == "agent":
                if p.resource_id not in perm_set.agents:
                    perm_set.agents.append(p.resource_id)
            elif p.resource_type == "dataset":
                if p.resource_id not in perm_set.datasets:
                    perm_set.datasets.append(p.resource_id)
            elif p.resource_type == "api":
                if p.resource_id not in perm_set.apis:
                    perm_set.apis.append(p.resource_id)
            elif p.resource_type == "metadata":
                if p.resource_id not in perm_set.metadata:
                    perm_set.metadata.append(p.resource_id)
            elif p.resource_type == "menu":
                if p.resource_id not in perm_set.menus:
                    perm_set.menus.append(p.resource_id)
            elif p.resource_type == "element":
                if p.resource_id not in perm_set.elements:
                    perm_set.elements.append(p.resource_id)
            elif p.resource_type == "forbidden_tool":
                if p.resource_id not in perm_set.forbidden_tools:
                    perm_set.forbidden_tools.append(p.resource_id)
            elif p.resource_type == "forbidden_command":
                if p.resource_id not in perm_set.forbidden_commands:
                    perm_set.forbidden_commands.append(p.resource_id)

    async def update_user_permissions(self, user_id: int, updates: PermissionUpdate):
        """Update DIRECT user permissions"""
        try:
            types_to_update = ["agent", "dataset", "api", "metadata", "menu", "element", "forbidden_tool", "forbidden_command"]
            await self.db.execute(
                delete(ResourcePermission).where(
                    ResourcePermission.user_id == user_id,
                    ResourcePermission.resource_type.in_(types_to_update)
                )
            )

            new_perms = []
            for agent_id in updates.agents:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="agent", resource_id=agent_id))
            for dataset_id in updates.datasets:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="dataset", resource_id=dataset_id))
            for api_id in updates.apis:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="api", resource_id=api_id))
            for meta_id in updates.metadata:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="metadata", resource_id=str(meta_id)))
            for menu_id in updates.menus:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="menu", resource_id=menu_id))
            for element_id in updates.elements:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="element", resource_id=element_id))
            for t_id in updates.forbidden_tools:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="forbidden_tool", resource_id=t_id))
            for cmd in updates.forbidden_commands:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="forbidden_command", resource_id=cmd))

            if new_perms:
                self.db.add_all(new_perms)

            await self.db.commit()
            await self._invalidate_user_cache(user_id)

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update permissions for user {user_id}: {e}")
            raise e

    async def update_user_roles(self, user_id: int, role_ids: list[int]):
        """Update User Business Roles"""
        try:
            # Delete old relations
            await self.db.execute(
                delete(UserRoleRelation).where(UserRoleRelation.user_id == user_id)
            )

            # Insert new
            relations = [UserRoleRelation(user_id=user_id, role_id=rid) for rid in role_ids]
            if relations:
                self.db.add_all(relations)

            await self.db.commit()
            await self._invalidate_user_cache(user_id)
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update roles for user {user_id}: {e}")
            raise e

    async def get_role_permissions(self, role_id: int) -> UserPermissionsResponse:
        """Get permissions assigned to a Role"""
        stmt = select(ResourcePermission).where(
            ResourcePermission.role_id == role_id,
            ResourcePermission.enabled == True
        )
        perms = (await self.db.execute(stmt)).scalars().all()

        perm_set = PermissionSet()
        self._aggregate_permissions(perm_set, perms)

        # Get role code/name
        role = await self.db.get(Role, role_id)
        role_name = [role.code] if role else []

        return UserPermissionsResponse(
            roles=role_name,
            permissions=perm_set
        )

    async def update_role_permissions(self, role_id: int, updates: PermissionUpdate):
        """Update Role Permissions"""
        try:
            types_to_update = ["agent", "dataset", "api", "metadata", "menu", "element", "forbidden_tool", "forbidden_command"]
            await self.db.execute(
                delete(ResourcePermission).where(
                    ResourcePermission.role_id == role_id,
                    ResourcePermission.resource_type.in_(types_to_update)
                )
            )

            new_perms = []
            for agent_id in updates.agents:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="agent", resource_id=agent_id))
            for dataset_id in updates.datasets:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="dataset", resource_id=dataset_id))
            for api_id in updates.apis:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="api", resource_id=api_id))
            for meta_id in updates.metadata:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="metadata", resource_id=str(meta_id)))
            for menu_id in updates.menus:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="menu", resource_id=menu_id))
            for element_id in updates.elements:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="element", resource_id=element_id))
            for t_id in updates.forbidden_tools:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="forbidden_tool", resource_id=t_id))
            for cmd in updates.forbidden_commands:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="forbidden_command", resource_id=cmd))

            if new_perms:
                self.db.add_all(new_perms)

            await self.db.commit()

            # Heavy operation: Invalidate cache for ALL users who have this role
            # For now, we might just accept 1 hour delay or try to find them.
            # Efficient way: Select user_ids from UserRoleRelation where role_id = role_id
            user_ids_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == role_id)
            user_ids = (await self.db.execute(user_ids_stmt)).scalars().all()

            for uid in user_ids:
                await self._invalidate_user_cache(uid)

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update permissions for role {role_id}: {e}")
            raise e

    async def _invalidate_user_cache(self, user_id: int):
        redis = await self._get_redis()
        if redis:
            await redis.delete(await self._get_cache_key(user_id))
            # 兼容历史 v2 key，避免旧缓存继续命中
            await redis.delete(f"sys:auth:permissions:v2:user:{user_id}")
            try:
                from app.services.ai.config import AgentConfigProvider
                await AgentConfigProvider.invalidate_dataset_menu_cache(user_id=user_id)
            except Exception as ex:
                logger.warning(f"Failed to invalidate dataset menu cache for user {user_id}: {ex}")
        try:
            from app.services.auth_service import AuthService
            await AuthService.invalidate_user_auth_cache(user_id, db=self.db)
        except Exception as ex:
            logger.warning(f"Failed to invalidate auth api_key cache for user {user_id}: {ex}")

    async def invalidate_cached_permissions_for_users(self, user_ids: Iterable[int]) -> None:
        """Clear permission cache for users (e.g. after role membership changes outside update_user_roles)."""
        for uid in set(user_ids):
            await self._invalidate_user_cache(uid)

    async def check_permission(self, user_id: int, resource_type: str, resource_id: str) -> bool:
        """
        Check if user has access to a specific resource.
        Admins bypass this check.
        """
        perms = await self.get_user_permissions(user_id)

        # Admin bypass
        if "admin" in perms.roles:
            return True

        # Check specific permission
        if resource_type == "agent":
            # 1. Explicit Permission Check (for System Agents)
            if resource_id in perms.permissions.agents:
                return True

            # 2. Ownership Check (for User Created Agents)
            agent = await self.db.get(AIAgent, resource_id)
            user = await self.db.get(User, user_id)

            if agent and user:
                # If System Agent: Must have explicit permission (already failed above)
                if agent.is_system:
                    return False

                # If User Agent: Must be Owner AND Enabled
                if agent.created_by == user.user_name and agent.is_enabled:
                    return True

            return False

        elif resource_type == "dataset":
            return resource_id in perms.permissions.datasets
        elif resource_type == "api":
            from app.core.v1_api_access import expand_api_permission_candidates

            granted = set(perms.permissions.apis or [])
            if not granted:
                return False
            return bool(granted & expand_api_permission_candidates(resource_id))
        elif resource_type == "metadata":
            return resource_id in perms.permissions.metadata
        elif resource_type == "element":
            return resource_id in perms.permissions.elements
        elif resource_type == "menu":
            return resource_id in perms.permissions.menus

        return False

    async def check_permission_by_api_key(self, api_key: str, resource_type: str, resource_id: str) -> bool:
        """
        Check if user has access to a specific resource using API Key.
        Admins bypass this check.
        """
        from app.services.auth_service import AuthService

        user = await AuthService.verify_api_key(api_key, self.db)
        if not user:
            return False

        return await self.check_permission(
            user_id=int(user["user_id"]),
            resource_type=resource_type,
            resource_id=resource_id
        )
