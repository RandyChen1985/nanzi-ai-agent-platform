-- V176: 平台菜单与权限节点表（MySQL 方言）
-- 变更原因：左侧菜单原先写死在 frontend/src/views/Dashboard.vue 的 menuGroups，
--           权限树写死在 frontend/src/constants/permissions.ts 的 MENU_TREE，
--           同一份菜单存在四份清单、靠人工同步，无法在后台改名/排序/加外链。
-- 需求背景：docs/superpowers/specs/2026-10-20-menu-management-design.md
-- 创建时间：2026-10-20
-- 注意事项：本脚本由 scripts/gen_menu_seed_sql.py 从 app/services/menu_seed.py 生成，
--           请勿手工编辑；如需调整菜单请改 menu_seed.py 后重新生成。
--           本脚本仅由 Agent 生成，不得自动执行，请由用户手工导入更新到数据库。
--           apply_sql.py 无 schema_migrations 记录表，幂等必须写在 SQL 自身。

-- 与 V26 等既有迁移保持一致：显式声明连接字符集，否则命令行客户端可能按 latin1 导入，
-- 中文菜单名会被截断或写成问号（表建好了但侧栏是乱码，比报错更难排查）。
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS ai_agent_menus (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    parent_id BIGINT NULL COMMENT '父节点ID：分组为空；菜单项指向分组；功能点指向所属页面',
    node_type VARCHAR(16) NOT NULL COMMENT '节点类型：group/page/external/element',
    code VARCHAR(64) NOT NULL COMMENT '不可变业务键（seed 幂等与外链定位）',
    permission_code VARCHAR(128) NULL COMMENT '授权码（menu:* / element:*），公开菜单为空',
    label VARCHAR(64) NOT NULL COMMENT '显示名',
    icon VARCHAR(32) NULL COMMENT '内置图标 key',
    path VARCHAR(128) NULL COMMENT '内置页面路由路径',
    active_route_names VARCHAR(255) NULL COMMENT '用于高亮的额外路由 name，逗号分隔',
    url VARCHAR(512) NULL COMMENT '外链地址（external 专用）',
    open_mode VARCHAR(16) NULL COMMENT '外链打开方式：new_tab/embed',
    sort_order INT NOT NULL DEFAULT 0 COMMENT '同级排序',
    enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
    desktop_only TINYINT(1) NOT NULL DEFAULT 0 COMMENT '仅桌面端显示',
    is_landing TINYINT(1) NOT NULL DEFAULT 0 COMMENT '默认落地页（全局至多一条）',
    builtin TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否内置项（内置项不可删除）',
    description VARCHAR(255) NULL COMMENT '备注',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY ux_agent_menus_code (code),
    UNIQUE KEY ux_agent_menus_permission_code (permission_code),
    KEY ix_agent_menus_parent_sort (parent_id, sort_order),
    KEY ix_agent_menus_node_type (node_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='平台菜单与权限节点表';

-- 注意：parent_id 刻意不建自引用外键。ON DELETE CASCADE 会连带删掉整棵子树，
--       而菜单删除必须由应用层严格限制（内置项禁止删除，外链删除要清理授权行）。

-- ========== 菜单与权限节点 seed（92 条）==========
-- 顺序即同级排序；分组先于其子项，页面先于其功能点，因此逐条插入不需要额外的拓扑排序。

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT NULL, 'page', 'menu:dashboard', 'menu:dashboard', '概览', 'dashboard', '/dashboard', 'Overview', NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:dashboard');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT NULL, 'group', 'group:assistant', NULL, '智能助手', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'group:assistant');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:assistant'), 'page', 'menu:workbench', NULL, '我的工作台', 'dashboard', '/dashboard/workbench', 'PersonalWorkbench', NULL, NULL, 10, 1, 0, 1, 1, '所有登录用户可见，默认落地页', NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:workbench');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:assistant'), 'page', 'menu:ai_chat', 'menu:ai_chat', '智能助手', 'chat', '/dashboard/chat', 'AIChat', NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:ai_chat');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT NULL, 'group', 'group:agent_platform', NULL, '智能体开发平台', NULL, NULL, NULL, NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'group:agent_platform');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:agent_platform'), 'page', 'menu:agent_management', 'menu:agent_management', '智能体中心', 'agent_mgmt', '/dashboard/agent-management', 'AgentManagement', NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:agent_management');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:agent_platform'), 'page', 'menu:skills_management', 'menu:skills_management', '技能工作台', 'skills', '/dashboard/skills', 'SkillsManagement', NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:skills_management');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:agent_platform'), 'page', 'menu:mcp_management', 'menu:mcp_management', 'MCP 工具集', 'mcp_tools', '/dashboard/mcp', 'McpManagement', NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:mcp_management');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:agent_platform'), 'page', 'menu:mcp_service', 'menu:mcp_service', 'MCP 服务台', 'mcp_service', '/dashboard/mcp-service', 'McpServiceDesk', NULL, NULL, 40, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:mcp_service');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:agent_platform'), 'page', 'menu:memory_management', 'menu:memory_management', '记忆工作台', 'memory', '/dashboard/memory', 'MemoryManagement', NULL, NULL, 50, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:memory_management');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:agent_platform'), 'page', 'menu:prompts', 'menu:prompts', '提示词工坊', 'prompts', '/dashboard/prompts', 'PromptStudio', NULL, NULL, 60, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:prompts');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:agent_platform'), 'page', 'menu:task_center', 'menu:task_center', '任务调度台', 'tasks', '/dashboard/tasks', 'TaskCenter', NULL, NULL, 70, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:task_center');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:agent_platform'), 'page', 'menu:agent_debug', 'menu:agent_debug', '智能体调试', 'agent_debug', '/dashboard/agent-debug', 'AgentDebug', NULL, NULL, 80, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:agent_debug');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:agent_platform'), 'page', 'menu:playground', 'menu:playground', '接口调试台', 'playground', '/dashboard/playground', 'Playground', NULL, NULL, 90, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:playground');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:agent_platform'), 'page', 'menu:widget_debug', 'menu:widget_debug', '组件调试台', 'widget', '/dashboard/widget-debug', 'WidgetDebugger', NULL, NULL, 100, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:widget_debug');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT NULL, 'group', 'group:chatbi', NULL, 'ChatBI 开发平台', NULL, NULL, NULL, NULL, NULL, 40, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'group:chatbi');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:chatbi'), 'page', 'menu:data_sources', 'menu:data_sources', '数据源管理', 'data_source', '/dashboard/data-sources', 'DataSourceManagement', NULL, NULL, 10, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:data_sources');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:chatbi'), 'page', 'menu:metadata', 'menu:metadata', '元数据管理', 'metadata', '/dashboard/metadata', 'Metadata,MetadataTables', NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:metadata');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:chatbi'), 'page', 'menu:chatbi_examples', 'menu:chatbi_examples', '案例集管理', 'chat_bubble_left_right', '/dashboard/examples', 'ExampleManagement', NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:chatbi_examples');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT NULL, 'group', 'group:knowledge', NULL, '知识库开发平台', NULL, NULL, NULL, NULL, NULL, 50, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'group:knowledge');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:knowledge'), 'page', 'menu:knowledge_management', 'menu:knowledge_management', '知识库管理', 'knowledge_base', '/dashboard/knowledge-bases', 'KnowledgeBaseManagement', NULL, NULL, 10, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:knowledge_management');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:knowledge'), 'page', 'menu:knowledge_retrieval_test', 'menu:knowledge_retrieval_test', '检索测试', 'playground', '/dashboard/knowledge-retrieval-test', 'KnowledgeRetrievalTest', NULL, NULL, 20, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:knowledge_retrieval_test');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:knowledge'), 'page', 'menu:knowledge_metrics', 'menu:knowledge_metrics', '运营分析', 'token_stats', '/dashboard/knowledge-metrics', 'KnowledgeMetrics', NULL, NULL, 30, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:knowledge_metrics');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT NULL, 'group', 'group:logs_token', NULL, '日志&TOKEN分析', NULL, NULL, NULL, NULL, NULL, 60, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'group:logs_token');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:logs_token'), 'page', 'menu:system:audit', 'menu:system:audit', '审计日志', 'audit', '/dashboard/audit', 'Audit', NULL, NULL, 10, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:system:audit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:logs_token'), 'page', 'menu:chat_logs', 'menu:chat_logs', '聊天日志', 'chat_logs', '/dashboard/chat-logs', 'ChatLogs', NULL, NULL, 20, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:chat_logs');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:logs_token'), 'page', 'menu:token_stats', 'menu:token_stats', 'Token 统计', 'token_stats', '/dashboard/token-stats', 'TokenStats', NULL, NULL, 30, 1, 1, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:token_stats');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT NULL, 'group', 'group:system', 'menu:system', '系统管理', 'system', NULL, NULL, NULL, NULL, 70, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'group:system');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:system'), 'page', 'menu:system:users', 'menu:system:users', '用户管理', 'users', '/dashboard/users', 'Users', NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:system:users');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:system'), 'page', 'menu:system:roles', 'menu:system:roles', '角色管理', 'roles', '/dashboard/roles', 'Roles', NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:system:roles');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:system'), 'page', 'menu:system:config', 'menu:system:config', '系统配置', 'system', '/dashboard/system', 'System', NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:system:config');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:system'), 'page', 'menu:system:menus', 'menu:system:menus', '菜单管理', 'menu', '/dashboard/menus', 'MenuManagement', NULL, NULL, 40, 1, 0, 0, 1, '菜单与权限树的配置入口', NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:system:menus');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:data_sources'), 'element', 'element:data_sources:edit', NULL, '新增/编辑数据源、测试连接与 SQL 预览', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:data_sources:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:data_sources'), 'element', 'element:data_sources:delete', NULL, '删除数据源', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:data_sources:delete');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:metadata'), 'element', 'element:metadata:sync', NULL, '同步到 RAGFlow', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:metadata:sync');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:metadata'), 'element', 'element:metadata:import', NULL, '智能导入数据', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:metadata:import');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:metadata'), 'element', 'element:metadata:edit', NULL, '编辑数据集', NULL, NULL, NULL, NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:metadata:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:metadata'), 'element', 'element:metadata:delete', NULL, '删除数据集', NULL, NULL, NULL, NULL, NULL, 40, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:metadata:delete');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:metadata'), 'element', 'element:metadata:view_yaml', NULL, '查看 YAML', NULL, NULL, NULL, NULL, NULL, 50, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:metadata:view_yaml');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:metadata'), 'element', 'element:metadata:edit_table', NULL, '配置表结构', NULL, NULL, NULL, NULL, NULL, 60, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:metadata:edit_table');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:metadata'), 'element', 'element:metadata:delete_table', NULL, '删除物理表', NULL, NULL, NULL, NULL, NULL, 70, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:metadata:delete_table');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:agent_management'), 'element', 'element:agent:create', NULL, '创建智能体', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:agent:create');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:agent_management'), 'element', 'element:agent:edit', NULL, '编辑配置', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:agent:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:agent_management'), 'element', 'element:agent:delete', NULL, '删除智能体', NULL, NULL, NULL, NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:agent:delete');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:skills_management'), 'element', 'element:skills:admin', NULL, '管理平台技能与审核', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:skills:admin');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_management'), 'element', 'element:mcp_management:create', NULL, '创建 MCP 服务', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_management:create');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_management'), 'element', 'element:mcp_management:edit', NULL, '编辑服务配置与连接测试', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_management:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_management'), 'element', 'element:mcp_management:delete', NULL, '删除 MCP 服务', NULL, NULL, NULL, NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_management:delete');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_management'), 'element', 'element:mcp_management:sync', NULL, '同步服务工具清单', NULL, NULL, NULL, NULL, NULL, 40, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_management:sync');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_management'), 'element', 'element:mcp_management:execute', NULL, '执行（测试）工具', NULL, NULL, NULL, NULL, NULL, 50, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_management:execute');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_management'), 'element', 'element:mcp_management:publish', NULL, '发布 / 下线工具', NULL, NULL, NULL, NULL, NULL, 60, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_management:publish');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:overview:read', NULL, '查看服务总览', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:overview:read');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:config:read', NULL, '查看服务配置', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:config:read');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:config:edit', NULL, '修改服务开关', NULL, NULL, NULL, NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:config:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:client:read', NULL, '查看外部 Client', NULL, NULL, NULL, NULL, NULL, 40, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:client:read');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:client:manage', NULL, '管理外部 Client', NULL, NULL, NULL, NULL, NULL, 50, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:client:manage');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:client:secret_reset', NULL, '重置 Client Secret', NULL, NULL, NULL, NULL, NULL, 60, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:client:secret_reset');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:client:token_issue', NULL, '生成当前用户 Access Token', NULL, NULL, NULL, NULL, NULL, 70, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:client:token_issue');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:capability:read', NULL, '查看能力与 Scope', NULL, NULL, NULL, NULL, NULL, 80, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:capability:read');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:capability:manage', NULL, '管理能力与 Scope', NULL, NULL, NULL, NULL, NULL, 90, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:capability:manage');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:grant:read', NULL, '查看用户授权', NULL, NULL, NULL, NULL, NULL, 100, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:grant:read');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:grant:revoke', NULL, '撤销用户授权', NULL, NULL, NULL, NULL, NULL, 110, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:grant:revoke');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:mcp_service'), 'element', 'element:mcp_service:audit:read', NULL, '查看调用审计', NULL, NULL, NULL, NULL, NULL, 120, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:mcp_service:audit:read');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:memory_management'), 'element', 'element:memory:config_save', NULL, '保存服务配置', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:memory:config_save');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:memory_management'), 'element', 'element:memory:config_index', NULL, '索引检查与重建', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:memory:config_index');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:memory_management'), 'element', 'element:memory:view_data', NULL, '查看记忆数据', NULL, NULL, NULL, NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:memory:view_data');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:memory_management'), 'element', 'element:memory:delete', NULL, '删除记忆', NULL, NULL, NULL, NULL, NULL, 40, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:memory:delete');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:memory_management'), 'element', 'element:memory:view_all_users', NULL, '按任意用户筛选', NULL, NULL, NULL, NULL, NULL, 50, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:memory:view_all_users');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:memory_management'), 'element', 'element:memory:test_search', NULL, '记忆检索测试', NULL, NULL, NULL, NULL, NULL, 60, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:memory:test_search');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:chatbi_examples'), 'element', 'element:chatbi_example:audit', NULL, '审核反馈', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:chatbi_example:audit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:chatbi_examples'), 'element', 'element:chatbi_example:sync', NULL, '同步至 RAGFlow', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:chatbi_example:sync');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:chatbi_examples'), 'element', 'element:chatbi_example:delete', NULL, '废弃/删除', NULL, NULL, NULL, NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:chatbi_example:delete');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:knowledge_management'), 'element', 'element:knowledge:create', NULL, '创建知识库', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:knowledge:create');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:knowledge_management'), 'element', 'element:knowledge:edit', NULL, '编辑知识库元数据', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:knowledge:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:knowledge_management'), 'element', 'element:knowledge:delete', NULL, '删除知识库', NULL, NULL, NULL, NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:knowledge:delete');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:knowledge_management'), 'element', 'element:knowledge:upload_document', NULL, '上传文档', NULL, NULL, NULL, NULL, NULL, 40, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:knowledge:upload_document');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:knowledge_management'), 'element', 'element:knowledge:delete_document', NULL, '删除文档', NULL, NULL, NULL, NULL, NULL, 50, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:knowledge:delete_document');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:knowledge_management'), 'element', 'element:knowledge:parse_document', NULL, '解析文档', NULL, NULL, NULL, NULL, NULL, 60, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:knowledge:parse_document');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:knowledge_retrieval_test'), 'element', 'element:knowledge:test_retrieval', NULL, '执行检索测试', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:knowledge:test_retrieval');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:chat_logs'), 'element', 'element:chat_logs:export', NULL, '导出日志', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:chat_logs:export');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:users'), 'element', 'element:user:view_key', NULL, '查看用户密钥', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:user:view_key');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:users'), 'element', 'element:user:reset_key', NULL, '重置用户密钥', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:user:reset_key');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:users'), 'element', 'element:user:edit', NULL, '编辑用户信息', NULL, NULL, NULL, NULL, NULL, 30, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:user:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:users'), 'element', 'element:user:delete', NULL, '注销用户', NULL, NULL, NULL, NULL, NULL, 40, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:user:delete');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:roles'), 'element', 'element:role:edit', NULL, '编辑角色', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:role:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:roles'), 'element', 'element:role:delete', NULL, '删除角色', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:role:delete');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:config'), 'element', 'element:system:config_save', NULL, '保存系统配置', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:system:config_save');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:config'), 'element', 'element:system:langfuse_save', NULL, '保存 Langfuse 链路追踪配置', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:system:langfuse_save');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:menus'), 'element', 'element:menu:edit', NULL, '菜单与排序维护', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:menu:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:prompts'), 'element', 'element:prompts:optimize', NULL, 'AI 优化建议', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:prompts:optimize');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:prompts'), 'element', 'element:prompts:edit', NULL, '保存提示词', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:prompts:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:task_center'), 'element', 'element:task:manage', NULL, '任务管理 (增删改执行)', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:task:manage');

-- ========== 权限码拆分：复制既有授权 ==========

-- menu:token_stats：从 menu:system:audit 复制既有授权，升级前后可见性一致
INSERT INTO ai_agent_resource_permissions
    (user_id, role_id, resource_type, resource_id, enabled, created_at, updated_at)
SELECT DISTINCT src.user_id, src.role_id, 'menu', 'menu:token_stats', 1, NOW(), NOW()
FROM ai_agent_resource_permissions src
WHERE src.resource_type = 'menu'
  AND src.resource_id = 'menu:system:audit'
  AND src.enabled = 1
  AND (src.user_id IS NOT NULL OR src.role_id IS NOT NULL)
  AND NOT EXISTS (
      SELECT 1 FROM ai_agent_resource_permissions dst
      WHERE dst.resource_type = 'menu'
        AND dst.resource_id = 'menu:token_stats'
        AND dst.user_id <=> src.user_id
        AND dst.role_id <=> src.role_id
  );

-- menu:knowledge_metrics：从 menu:knowledge_management 复制既有授权，升级前后可见性一致
INSERT INTO ai_agent_resource_permissions
    (user_id, role_id, resource_type, resource_id, enabled, created_at, updated_at)
SELECT DISTINCT src.user_id, src.role_id, 'menu', 'menu:knowledge_metrics', 1, NOW(), NOW()
FROM ai_agent_resource_permissions src
WHERE src.resource_type = 'menu'
  AND src.resource_id = 'menu:knowledge_management'
  AND src.enabled = 1
  AND (src.user_id IS NOT NULL OR src.role_id IS NOT NULL)
  AND NOT EXISTS (
      SELECT 1 FROM ai_agent_resource_permissions dst
      WHERE dst.resource_type = 'menu'
        AND dst.resource_id = 'menu:knowledge_metrics'
        AND dst.user_id <=> src.user_id
        AND dst.role_id <=> src.role_id
  );

-- `menu:system:menus` / `element:menu:edit` 刻意不自动授予任何非 admin 角色：
-- 菜单管理是新增能力，是否需要授予由管理员在角色管理里决定。
