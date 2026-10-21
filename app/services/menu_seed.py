"""菜单与权限节点的内置定义（唯一代码源）。

背景：左侧菜单原先写死在 `frontend/src/views/Dashboard.vue` 的 `menuGroups`，
权限树另写死在 `frontend/src/constants/permissions.ts` 的 `MENU_TREE`。本文件把两者
合并成一份结构化定义，供三个消费方使用：

1. `scripts/gen_menu_seed_sql.py` —— 生成 `db-prod/V176-*.sql` 与 `db-prod-pg/V77-*.sql`；
2. `MenuService` —— **表尚未建立时**兜底返回导航树，保证"代码先上线、迁移后执行"期间
   侧栏与改造前完全一致；
3. 契约测试 —— 对账前端图标白名单、页面路径白名单与后端权限码。

禁止在别处再维护第二份菜单常量：一旦双写，"内置菜单只有一份"这个前提就失效了，
改造要解决的漂移问题会原样复发。

节点类型：
- `group`：侧栏分组标题，本身不可点击，也不参与授权（「系统管理」保留 `menu:system` 码）；
- `page`：内置页面，`path` 必须出自 `ALLOWED_PAGE_PATHS`；
- `external`：外链菜单，只由管理员在菜单管理页创建，不在此 seed 中；
- `element`：页面内功能点，由代码里的 `require_permission("element", ...)` 决定，入库后只读。
"""

from dataclasses import dataclass, replace
from typing import Dict, List, Optional

NODE_GROUP = "group"
NODE_PAGE = "page"
NODE_EXTERNAL = "external"
NODE_ELEMENT = "element"

# 内置图标白名单。前端 MenuIcon.vue 必须与之一一对应（`test_menu_seed_ownership` 对账）。
ALLOWED_ICON_KEYS = (
    "dashboard",
    "menu",
    "org",
    "widget",
    "ellipsis",
    "rectangle_stack",
    "squares_plus",
    "globe",
    "chat",
    "chat_bubble_left_right",
    "chat_logs",
    "prompts",
    "agent_mgmt",
    "agent_debug",
    "playground",
    "skills",
    "bolt",
    "rocket",
    "data_source",
    "metadata",
    "chart_bar",
    "chart_line",
    "chart_pie",
    "document_chart",
    "token_stats",
    "funnel",
    "arrows_right_left",
    "knowledge_base",
    "document_text",
    "clipboard_list",
    "folder",
    "memory",
    "bookmark",
    "tag",
    "users",
    "user",
    "user_circle",
    "roles",
    "key",
    "lock",
    "shield",
    "audit",
    "identification",
    "system",
    "mcp_tools",
    "mcp_service",
    "cloud",
    "code_bracket",
    "command_line",
    "bug",
    "language",
    "link",
    "external_link",
    "signal",
    "tasks",
    "clock",
    "calendar",
    "play",
    "pause",
    "star",
    "flag",
    "info",
    "lifebuoy",
    "bell",
    "megaphone",
)


# 可以挂到侧栏的页面路径白名单。只收 `/dashboard` 下的已注册路由：
# 菜单表里的 `path` 一旦指向不存在/已改名的路由，管理员点开就是白屏，
# 而这类错误在服务端无法自我发现，只能靠白名单挡在保存阶段。
ALLOWED_PAGE_PATHS = (
    "/dashboard",
    "/dashboard/chat",
    "/dashboard/workbench",
    "/dashboard/personal",
    "/dashboard/agent-management",
    "/dashboard/scenario-templates",
    "/dashboard/examples",
    "/dashboard/skills",
    "/dashboard/mcp",
    "/dashboard/mcp-service",
    "/dashboard/memory",
    "/dashboard/prompts",
    "/dashboard/tasks",
    "/dashboard/agent-debug",
    "/dashboard/playground",
    "/dashboard/widget-debug",
    "/dashboard/data-sources",
    "/dashboard/metadata",
    "/dashboard/knowledge-bases",
    "/dashboard/knowledge-retrieval-test",
    "/dashboard/knowledge-metrics",
    "/dashboard/audit",
    "/dashboard/chat-logs",
    "/dashboard/token-stats",
    "/dashboard/users",
    "/dashboard/roles",
    "/dashboard/system",
    "/dashboard/menus",
    "/dashboard/departments",
)

# 外链菜单允许的协议。`javascript:` / `data:` 一律拒绝，避免管理员误填变成脚本执行入口。
ALLOWED_EXTERNAL_SCHEMES = ("http://", "https://")

# 外链打开方式。
OPEN_MODE_NEW_TAB = "new_tab"
OPEN_MODE_EMBED = "embed"
ALLOWED_OPEN_MODES = (OPEN_MODE_NEW_TAB, OPEN_MODE_EMBED)


@dataclass(frozen=True)
class BuiltinMenuNode:
    """一条内置节点定义。`code` 是不可变业务键，`permission_code` 是授权口径。"""

    code: str
    node_type: str
    label: str
    parent_code: Optional[str] = None
    permission_code: Optional[str] = None
    icon: Optional[str] = None
    path: Optional[str] = None
    active_route_names: Optional[str] = None
    desktop_only: bool = False
    is_landing: bool = False
    description: Optional[str] = None
    sort_order: int = 0


# 顺序即同级排序（`_assign_sort_orders` 会按出现顺序分配 10/20/30…）。
_RAW_SEED: List[BuiltinMenuNode] = [
    # ---------- 顶层：概览独立在分组之外，与改造前"空标题分组"的视觉一致 ----------
    BuiltinMenuNode(
        code="menu:dashboard",
        node_type=NODE_PAGE,
        label="概览",
        permission_code="menu:dashboard",
        icon="dashboard",
        path="/dashboard",
        active_route_names="Overview",
    ),
    # ---------- 智能助手 ----------
    BuiltinMenuNode(code="group:assistant", node_type=NODE_GROUP, label="智能助手"),
    BuiltinMenuNode(
        code="menu:workbench",
        node_type=NODE_PAGE,
        label="我的工作台",
        parent_code="group:assistant",
        # 刻意不带权限码：所有登录用户可见，且非 admin 登录后默认落到这里。
        # 权限树里不出现，与改造前的行为一致（见 test_dashboard_permission_guard_contract）。
        permission_code=None,
        icon="dashboard",
        path="/dashboard/workbench",
        active_route_names="PersonalWorkbench",
        is_landing=True,
        description="所有登录用户可见，默认落地页",
    ),
    BuiltinMenuNode(
        code="menu:ai_chat",
        node_type=NODE_PAGE,
        label="智能助手",
        parent_code="group:assistant",
        permission_code="menu:ai_chat",
        icon="chat",
        path="/dashboard/chat",
        active_route_names="AIChat",
    ),
    # ---------- 智能体开发平台 ----------
    BuiltinMenuNode(code="group:agent_platform", node_type=NODE_GROUP, label="智能体开发平台"),
    BuiltinMenuNode(
        code="menu:agent_management",
        node_type=NODE_PAGE,
        label="智能体中心",
        parent_code="group:agent_platform",
        permission_code="menu:agent_management",
        icon="agent_mgmt",
        path="/dashboard/agent-management",
        active_route_names="AgentManagement",
    ),
    BuiltinMenuNode(
        code="menu:skills_management",
        node_type=NODE_PAGE,
        label="技能工作台",
        parent_code="group:agent_platform",
        permission_code="menu:skills_management",
        icon="skills",
        path="/dashboard/skills",
        active_route_names="SkillsManagement",
    ),
    BuiltinMenuNode(
        code="menu:mcp_management",
        node_type=NODE_PAGE,
        label="MCP 工具集",
        parent_code="group:agent_platform",
        permission_code="menu:mcp_management",
        icon="mcp_tools",
        path="/dashboard/mcp",
        active_route_names="McpManagement",
    ),
    BuiltinMenuNode(
        code="menu:mcp_service",
        node_type=NODE_PAGE,
        label="MCP 服务台",
        parent_code="group:agent_platform",
        permission_code="menu:mcp_service",
        icon="mcp_service",
        path="/dashboard/mcp-service",
        active_route_names="McpServiceDesk",
    ),
    BuiltinMenuNode(
        code="menu:memory_management",
        node_type=NODE_PAGE,
        label="记忆工作台",
        parent_code="group:agent_platform",
        permission_code="menu:memory_management",
        icon="memory",
        path="/dashboard/memory",
        active_route_names="MemoryManagement",
    ),
    BuiltinMenuNode(
        code="menu:prompts",
        node_type=NODE_PAGE,
        label="提示词工坊",
        parent_code="group:agent_platform",
        permission_code="menu:prompts",
        icon="prompts",
        path="/dashboard/prompts",
        active_route_names="PromptStudio",
        desktop_only=True,
    ),
    BuiltinMenuNode(
        code="menu:task_center",
        node_type=NODE_PAGE,
        label="任务调度台",
        parent_code="group:agent_platform",
        permission_code="menu:task_center",
        icon="tasks",
        path="/dashboard/tasks",
        active_route_names="TaskCenter",
    ),
    BuiltinMenuNode(
        code="menu:agent_debug",
        node_type=NODE_PAGE,
        label="智能体调试",
        parent_code="group:agent_platform",
        permission_code="menu:agent_debug",
        icon="agent_debug",
        path="/dashboard/agent-debug",
        active_route_names="AgentDebug",
        desktop_only=True,
    ),
    BuiltinMenuNode(
        code="menu:playground",
        node_type=NODE_PAGE,
        label="接口调试台",
        parent_code="group:agent_platform",
        permission_code="menu:playground",
        icon="playground",
        path="/dashboard/playground",
        active_route_names="Playground",
        desktop_only=True,
    ),
    BuiltinMenuNode(
        code="menu:widget_debug",
        node_type=NODE_PAGE,
        label="组件调试台",
        parent_code="group:agent_platform",
        permission_code="menu:widget_debug",
        icon="widget",
        path="/dashboard/widget-debug",
        active_route_names="WidgetDebugger",
        desktop_only=True,
    ),
    # ---------- ChatBI 开发平台 ----------
    BuiltinMenuNode(code="group:chatbi", node_type=NODE_GROUP, label="ChatBI 开发平台"),
    BuiltinMenuNode(
        code="menu:data_sources",
        node_type=NODE_PAGE,
        label="数据源管理",
        parent_code="group:chatbi",
        permission_code="menu:data_sources",
        icon="data_source",
        path="/dashboard/data-sources",
        active_route_names="DataSourceManagement",
        desktop_only=True,
    ),
    BuiltinMenuNode(
        code="menu:metadata",
        node_type=NODE_PAGE,
        label="元数据管理",
        parent_code="group:chatbi",
        permission_code="menu:metadata",
        icon="metadata",
        path="/dashboard/metadata",
        active_route_names="Metadata,MetadataTables",
    ),
    BuiltinMenuNode(
        code="menu:chatbi_examples",
        node_type=NODE_PAGE,
        label="案例集管理",
        parent_code="group:chatbi",
        permission_code="menu:chatbi_examples",
        icon="chat_bubble_left_right",
        path="/dashboard/examples",
        active_route_names="ExampleManagement",
    ),
    # ---------- 知识库开发平台 ----------
    BuiltinMenuNode(code="group:knowledge", node_type=NODE_GROUP, label="知识库开发平台"),
    BuiltinMenuNode(
        code="menu:knowledge_management",
        node_type=NODE_PAGE,
        label="知识库管理",
        parent_code="group:knowledge",
        permission_code="menu:knowledge_management",
        icon="knowledge_base",
        path="/dashboard/knowledge-bases",
        active_route_names="KnowledgeBaseManagement",
        desktop_only=True,
    ),
    BuiltinMenuNode(
        code="menu:knowledge_retrieval_test",
        node_type=NODE_PAGE,
        label="检索测试",
        parent_code="group:knowledge",
        permission_code="menu:knowledge_retrieval_test",
        icon="playground",
        path="/dashboard/knowledge-retrieval-test",
        active_route_names="KnowledgeRetrievalTest",
        desktop_only=True,
    ),
    BuiltinMenuNode(
        code="menu:knowledge_metrics",
        node_type=NODE_PAGE,
        label="运营分析",
        parent_code="group:knowledge",
        # 新拆出的独立码：原先复用 menu:knowledge_management，导致无法单独授权。
        # 迁移会把旧码上已生效的授权复制到新码，升级前后可见性一致。
        permission_code="menu:knowledge_metrics",
        icon="token_stats",
        path="/dashboard/knowledge-metrics",
        active_route_names="KnowledgeMetrics",
        desktop_only=True,
    ),
    # ---------- 日志 & TOKEN 分析 ----------
    BuiltinMenuNode(code="group:logs_token", node_type=NODE_GROUP, label="日志&TOKEN分析"),
    BuiltinMenuNode(
        code="menu:system:audit",
        node_type=NODE_PAGE,
        label="审计日志",
        parent_code="group:logs_token",
        permission_code="menu:system:audit",
        icon="audit",
        path="/dashboard/audit",
        active_route_names="Audit",
        desktop_only=True,
    ),
    BuiltinMenuNode(
        code="menu:chat_logs",
        node_type=NODE_PAGE,
        label="聊天日志",
        parent_code="group:logs_token",
        permission_code="menu:chat_logs",
        icon="chat_logs",
        path="/dashboard/chat-logs",
        active_route_names="ChatLogs",
        desktop_only=True,
    ),
    BuiltinMenuNode(
        code="menu:token_stats",
        node_type=NODE_PAGE,
        label="Token 统计",
        parent_code="group:logs_token",
        # 新拆出的独立码：原先与「审计日志」共用 menu:system:audit。
        permission_code="menu:token_stats",
        icon="token_stats",
        path="/dashboard/token-stats",
        active_route_names="TokenStats",
        desktop_only=True,
    ),
    # ---------- 系统管理 ----------
    # 该分组保留 menu:system 码：历史授权数据里存在，且它在权限树里一直是父节点。
    # 后端没有任何接口单独校验它，因此保留不影响行为，删除反而会丢历史授权。
    BuiltinMenuNode(
        code="group:system",
        node_type=NODE_GROUP,
        label="系统管理",
        permission_code="menu:system",
        icon="system",
    ),
    BuiltinMenuNode(
        code="menu:system:users",
        node_type=NODE_PAGE,
        label="用户管理",
        parent_code="group:system",
        permission_code="menu:system:users",
        icon="users",
        path="/dashboard/users",
        active_route_names="Users",
    ),
    BuiltinMenuNode(
        code="menu:system:roles",
        node_type=NODE_PAGE,
        label="角色管理",
        parent_code="group:system",
        permission_code="menu:system:roles",
        icon="roles",
        path="/dashboard/roles",
        active_route_names="Roles",
    ),
    BuiltinMenuNode(
        code="menu:system:departments",
        node_type=NODE_PAGE,
        label="部门管理",
        parent_code="group:system",
        permission_code="menu:system:departments",
        icon="org",
        path="/dashboard/departments",
        active_route_names="DepartmentManagement",
        description="组织架构（树形部门）与部门成员维护",
    ),
    BuiltinMenuNode(
        code="menu:system:config",
        node_type=NODE_PAGE,
        label="系统配置",
        parent_code="group:system",
        permission_code="menu:system:config",
        icon="system",
        path="/dashboard/system",
        active_route_names="System",
    ),
    BuiltinMenuNode(
        code="menu:system:menus",
        node_type=NODE_PAGE,
        label="菜单管理",
        parent_code="group:system",
        permission_code="menu:system:menus",
        icon="menu",
        path="/dashboard/menus",
        active_route_names="MenuManagement",
        description="菜单与权限树的配置入口",
    ),
    # ==================== 功能点（element，只读）====================
    # 归属与中文名原先写在 MENU_TREE，入库后由权限树接口返回。
    # 这些码由后端 require_permission("element", ...) 校验，因此不可在后台增删或禁用。
    BuiltinMenuNode(
        code="element:data_sources:edit",
        node_type=NODE_ELEMENT,
        label="新增/编辑数据源、测试连接与 SQL 预览",
        parent_code="menu:data_sources",
    ),
    BuiltinMenuNode(
        code="element:data_sources:delete",
        node_type=NODE_ELEMENT,
        label="删除数据源",
        parent_code="menu:data_sources",
    ),
    BuiltinMenuNode(
        code="element:metadata:sync",
        node_type=NODE_ELEMENT,
        label="同步到 RAGFlow",
        parent_code="menu:metadata",
    ),
    BuiltinMenuNode(
        code="element:metadata:import",
        node_type=NODE_ELEMENT,
        label="智能导入数据",
        parent_code="menu:metadata",
    ),
    BuiltinMenuNode(
        code="element:metadata:edit",
        node_type=NODE_ELEMENT,
        label="编辑数据集",
        parent_code="menu:metadata",
    ),
    BuiltinMenuNode(
        code="element:metadata:delete",
        node_type=NODE_ELEMENT,
        label="删除数据集",
        parent_code="menu:metadata",
    ),
    BuiltinMenuNode(
        code="element:metadata:view_yaml",
        node_type=NODE_ELEMENT,
        label="查看 YAML",
        parent_code="menu:metadata",
    ),
    BuiltinMenuNode(
        code="element:metadata:edit_table",
        node_type=NODE_ELEMENT,
        label="配置表结构",
        parent_code="menu:metadata",
    ),
    BuiltinMenuNode(
        code="element:metadata:delete_table",
        node_type=NODE_ELEMENT,
        label="删除物理表",
        parent_code="menu:metadata",
    ),
    BuiltinMenuNode(
        code="element:agent:create",
        node_type=NODE_ELEMENT,
        label="创建智能体",
        parent_code="menu:agent_management",
    ),
    BuiltinMenuNode(
        code="element:agent:edit",
        node_type=NODE_ELEMENT,
        label="编辑配置",
        parent_code="menu:agent_management",
    ),
    BuiltinMenuNode(
        code="element:agent:delete",
        node_type=NODE_ELEMENT,
        label="删除智能体",
        parent_code="menu:agent_management",
    ),
    BuiltinMenuNode(
        code="element:skills:admin",
        node_type=NODE_ELEMENT,
        label="管理平台技能与审核",
        parent_code="menu:skills_management",
    ),
    BuiltinMenuNode(
        code="element:mcp_management:create",
        node_type=NODE_ELEMENT,
        label="创建 MCP 服务",
        parent_code="menu:mcp_management",
    ),
    BuiltinMenuNode(
        code="element:mcp_management:edit",
        node_type=NODE_ELEMENT,
        label="编辑服务配置与连接测试",
        parent_code="menu:mcp_management",
    ),
    BuiltinMenuNode(
        code="element:mcp_management:delete",
        node_type=NODE_ELEMENT,
        label="删除 MCP 服务",
        parent_code="menu:mcp_management",
    ),
    BuiltinMenuNode(
        code="element:mcp_management:sync",
        node_type=NODE_ELEMENT,
        label="同步服务工具清单",
        parent_code="menu:mcp_management",
    ),
    BuiltinMenuNode(
        code="element:mcp_management:execute",
        node_type=NODE_ELEMENT,
        label="执行（测试）工具",
        parent_code="menu:mcp_management",
    ),
    BuiltinMenuNode(
        code="element:mcp_management:publish",
        node_type=NODE_ELEMENT,
        label="发布 / 下线工具",
        parent_code="menu:mcp_management",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:overview:read",
        node_type=NODE_ELEMENT,
        label="查看服务总览",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:config:read",
        node_type=NODE_ELEMENT,
        label="查看服务配置",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:config:edit",
        node_type=NODE_ELEMENT,
        label="修改服务开关",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:client:read",
        node_type=NODE_ELEMENT,
        label="查看外部 Client",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:client:manage",
        node_type=NODE_ELEMENT,
        label="管理外部 Client",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:client:secret_reset",
        node_type=NODE_ELEMENT,
        label="重置 Client Secret",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:client:token_issue",
        node_type=NODE_ELEMENT,
        label="生成当前用户 Access Token",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:capability:read",
        node_type=NODE_ELEMENT,
        label="查看能力与 Scope",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:capability:manage",
        node_type=NODE_ELEMENT,
        label="管理能力与 Scope",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:grant:read",
        node_type=NODE_ELEMENT,
        label="查看用户授权",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:grant:revoke",
        node_type=NODE_ELEMENT,
        label="撤销用户授权",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:mcp_service:audit:read",
        node_type=NODE_ELEMENT,
        label="查看调用审计",
        parent_code="menu:mcp_service",
    ),
    BuiltinMenuNode(
        code="element:memory:config_save",
        node_type=NODE_ELEMENT,
        label="保存服务配置",
        parent_code="menu:memory_management",
    ),
    BuiltinMenuNode(
        code="element:memory:config_index",
        node_type=NODE_ELEMENT,
        label="索引检查与重建",
        parent_code="menu:memory_management",
    ),
    BuiltinMenuNode(
        code="element:memory:view_data",
        node_type=NODE_ELEMENT,
        label="查看记忆数据",
        parent_code="menu:memory_management",
    ),
    BuiltinMenuNode(
        code="element:memory:delete",
        node_type=NODE_ELEMENT,
        label="删除记忆",
        parent_code="menu:memory_management",
    ),
    BuiltinMenuNode(
        code="element:memory:view_all_users",
        node_type=NODE_ELEMENT,
        label="按任意用户筛选",
        parent_code="menu:memory_management",
    ),
    BuiltinMenuNode(
        code="element:memory:test_search",
        node_type=NODE_ELEMENT,
        label="记忆检索测试",
        parent_code="menu:memory_management",
    ),
    BuiltinMenuNode(
        code="element:chatbi_example:audit",
        node_type=NODE_ELEMENT,
        label="审核反馈",
        parent_code="menu:chatbi_examples",
    ),
    BuiltinMenuNode(
        code="element:chatbi_example:sync",
        node_type=NODE_ELEMENT,
        label="同步至 RAGFlow",
        parent_code="menu:chatbi_examples",
    ),
    BuiltinMenuNode(
        code="element:chatbi_example:delete",
        node_type=NODE_ELEMENT,
        label="废弃/删除",
        parent_code="menu:chatbi_examples",
    ),
    BuiltinMenuNode(
        code="element:knowledge:create",
        node_type=NODE_ELEMENT,
        label="创建知识库",
        parent_code="menu:knowledge_management",
    ),
    BuiltinMenuNode(
        code="element:knowledge:edit",
        node_type=NODE_ELEMENT,
        label="编辑知识库元数据",
        parent_code="menu:knowledge_management",
    ),
    BuiltinMenuNode(
        code="element:knowledge:delete",
        node_type=NODE_ELEMENT,
        label="删除知识库",
        parent_code="menu:knowledge_management",
    ),
    BuiltinMenuNode(
        code="element:knowledge:upload_document",
        node_type=NODE_ELEMENT,
        label="上传文档",
        parent_code="menu:knowledge_management",
    ),
    BuiltinMenuNode(
        code="element:knowledge:delete_document",
        node_type=NODE_ELEMENT,
        label="删除文档",
        parent_code="menu:knowledge_management",
    ),
    BuiltinMenuNode(
        code="element:knowledge:parse_document",
        node_type=NODE_ELEMENT,
        label="解析文档",
        parent_code="menu:knowledge_management",
    ),
    BuiltinMenuNode(
        code="element:knowledge:test_retrieval",
        node_type=NODE_ELEMENT,
        label="执行检索测试",
        parent_code="menu:knowledge_retrieval_test",
    ),
    BuiltinMenuNode(
        code="element:chat_logs:export",
        node_type=NODE_ELEMENT,
        label="导出日志",
        parent_code="menu:chat_logs",
    ),
    BuiltinMenuNode(
        code="element:user:view_key",
        node_type=NODE_ELEMENT,
        label="查看用户密钥",
        parent_code="menu:system:users",
    ),
    BuiltinMenuNode(
        code="element:user:reset_key",
        node_type=NODE_ELEMENT,
        label="重置用户密钥",
        parent_code="menu:system:users",
    ),
    BuiltinMenuNode(
        code="element:user:edit",
        node_type=NODE_ELEMENT,
        label="编辑用户信息",
        parent_code="menu:system:users",
    ),
    BuiltinMenuNode(
        code="element:user:delete",
        node_type=NODE_ELEMENT,
        label="注销用户",
        parent_code="menu:system:users",
    ),
    BuiltinMenuNode(
        code="element:role:edit",
        node_type=NODE_ELEMENT,
        label="编辑角色",
        parent_code="menu:system:roles",
    ),
    BuiltinMenuNode(
        code="element:role:delete",
        node_type=NODE_ELEMENT,
        label="删除角色",
        parent_code="menu:system:roles",
    ),
    BuiltinMenuNode(
        code="element:dept:edit",
        node_type=NODE_ELEMENT,
        label="维护部门结构与部门成员",
        parent_code="menu:system:departments",
    ),
    BuiltinMenuNode(
        code="element:dept:delete",
        node_type=NODE_ELEMENT,
        label="删除部门",
        parent_code="menu:system:departments",
    ),
    BuiltinMenuNode(
        code="element:system:config_save",
        node_type=NODE_ELEMENT,
        label="保存系统配置",
        parent_code="menu:system:config",
    ),
    BuiltinMenuNode(
        code="element:system:langfuse_save",
        node_type=NODE_ELEMENT,
        label="保存 Langfuse 链路追踪配置",
        parent_code="menu:system:config",
    ),
    BuiltinMenuNode(
        code="element:menu:edit",
        node_type=NODE_ELEMENT,
        label="菜单与排序维护",
        parent_code="menu:system:menus",
    ),
    BuiltinMenuNode(
        code="element:prompts:optimize",
        node_type=NODE_ELEMENT,
        label="AI 优化建议",
        parent_code="menu:prompts",
    ),
    BuiltinMenuNode(
        code="element:prompts:edit",
        node_type=NODE_ELEMENT,
        label="保存提示词",
        parent_code="menu:prompts",
    ),
    BuiltinMenuNode(
        code="element:task:manage",
        node_type=NODE_ELEMENT,
        label="任务管理 (增删改执行)",
        parent_code="menu:task_center",
    ),
]


def _assign_sort_orders(nodes: List[BuiltinMenuNode]) -> List[BuiltinMenuNode]:
    """按出现顺序给"同一父节点"下的节点分配 10/20/30… 的排序值。"""
    counters: Dict[str, int] = {}
    ordered: List[BuiltinMenuNode] = []
    for node in nodes:
        parent_key = node.parent_code or ""
        index = counters.get(parent_key, 0)
        counters[parent_key] = index + 1
        ordered.append(replace(node, sort_order=(index + 1) * 10))
    return ordered


BUILTIN_MENU_SEED: List[BuiltinMenuNode] = _assign_sort_orders(_RAW_SEED)

BUILTIN_BY_CODE: Dict[str, BuiltinMenuNode] = {node.code: node for node in BUILTIN_MENU_SEED}

# 内置页面路径 → 节点，供"表未建立"兜底与路径白名单校验使用。
BUILTIN_PAGE_BY_PATH: Dict[str, BuiltinMenuNode] = {
    node.path: node
    for node in BUILTIN_MENU_SEED
    if node.node_type == NODE_PAGE and node.path
}

# 内置菜单节点在权限树中的根：分组节点（有 permission_code 的也包含，如「系统管理」）。
BUILTIN_GROUP_CODES = [
    node.code for node in BUILTIN_MENU_SEED if node.node_type == NODE_GROUP
]


def builtin_menu_permission_codes() -> List[str]:
    """内置菜单类权限码（`menu:*`），不含 element。"""
    return [
        node.permission_code
        for node in BUILTIN_MENU_SEED
        if node.permission_code and node.permission_code.startswith("menu:")
    ]


def builtin_element_permission_codes() -> List[str]:
    """内置功能点权限码（`element:*`）。"""
    return [
        node.code
        for node in BUILTIN_MENU_SEED
        if node.node_type == NODE_ELEMENT
    ]
