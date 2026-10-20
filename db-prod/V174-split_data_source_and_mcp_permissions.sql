-- V174: 数据源权限拆分（edit / delete） + MCP 工具集写权限登记
--
-- 一次迁移完成两件事：
--   一、数据源管理拆出独立的写权限码。只读能力跟随菜单权限（有 menu:data_sources
--       即可查看列表、表画像与同步任务），功能点只管写：
--         element:data_sources:edit   —— 新增/编辑、测试连接、SQL 预览、画像任务
--         element:data_sources:delete —— 删除数据源
--       其中「列表 / 测试连接 / SQL 预览」被元数据导入弹窗复用，后端已改为
--       「数据源码 或 元数据导入码」，因此老账号不会因为拆分而用不了导入流程；
--       「删除」则把既有能力（来自 element:metadata:import）复制给新码。
--   二、MCP 工具集的写端点补上权限校验。这些接口原先只校验登录，任何账号都能
--       增删改自己名下的服务；现按动作分码，并给现有全部角色补齐授权，
--       使升级前后的可用性一致，之后可由管理员在角色管理里收紧。
--
-- 注意：element:metadata:import 仍然保留，继续控制「元数据管理 → 智能导入数据」。

-- ========== 一、数据源管理 ==========
--
-- 背景：数据源连接管理的 15 个端点原先与「元数据导入」共用 element:metadata:import，
-- 做不到「能看数据源但不能导入元数据」。现拆为写操作两个码；**只读能力跟随菜单权限**
-- （有 menu:data_sources 即可查看列表、表画像与同步任务），功能点只管写。
--
-- 兼容策略：
--   1. 写端点中「测试连接 / SQL 预览」被元数据导入弹窗复用，改为「数据源码 或 元数据导入码」，
--      只勾「智能导入数据」的角色不该被挡住；
--   2. 删除端点只认 element:data_sources:delete，这里把既有删除能力（来自 metadata:import）
--      复制给新码，避免升级后老账号突然删不了。
-- 注意：element:metadata:import 仍然保留，继续控制「元数据管理 → 智能导入数据」。

-- 1) 登记两个资源本身
INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT 'element', 'element:data_sources:edit', 1, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions
    WHERE resource_type = 'element' AND resource_id = 'element:data_sources:edit'
);

INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT 'element', 'element:data_sources:delete', 1, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions
    WHERE resource_type = 'element' AND resource_id = 'element:data_sources:delete'
);

-- 2) 把「删除数据源」的既有授权复制到新码
INSERT INTO ai_agent_resource_permissions (user_id, role_id, resource_type, resource_id, enabled, created_at, updated_at)
SELECT src.user_id, src.role_id, 'element', 'element:data_sources:delete', 1, NOW(), NOW()
FROM ai_agent_resource_permissions src
WHERE src.resource_type = 'element'
  AND src.resource_id = 'element:metadata:import'
  AND src.enabled = 1
  AND (src.user_id IS NOT NULL OR src.role_id IS NOT NULL)
  AND NOT EXISTS (
      SELECT 1 FROM ai_agent_resource_permissions dst
      WHERE dst.resource_type = 'element'
        AND dst.resource_id = 'element:data_sources:delete'
        AND dst.user_id <=> src.user_id
        AND dst.role_id <=> src.role_id
  );

-- ========== 二、MCP 工具集 ==========
--
-- 背景：app/api/portal/endpoints/mcp.py 的写端点此前只校验登录，任何账号都能增删改
-- 自己名下的 MCP 服务、执行工具、发布工具。现改为按动作校验 element 权限。
--
-- 兼容：给现有全部角色补这 6 个码（等价于升级前的「登录即可用」），
-- 之后由管理员在角色管理里按需收紧；新建角色默认不带这些权限。

-- 1) 登记六个资源本身
INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT 'element', 'element:mcp_management:create', 1, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions
    WHERE resource_type = 'element' AND resource_id = 'element:mcp_management:create'
);

INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT 'element', 'element:mcp_management:edit', 1, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions
    WHERE resource_type = 'element' AND resource_id = 'element:mcp_management:edit'
);

INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT 'element', 'element:mcp_management:delete', 1, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions
    WHERE resource_type = 'element' AND resource_id = 'element:mcp_management:delete'
);

INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT 'element', 'element:mcp_management:sync', 1, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions
    WHERE resource_type = 'element' AND resource_id = 'element:mcp_management:sync'
);

INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT 'element', 'element:mcp_management:execute', 1, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions
    WHERE resource_type = 'element' AND resource_id = 'element:mcp_management:execute'
);

INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT 'element', 'element:mcp_management:publish', 1, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions
    WHERE resource_type = 'element' AND resource_id = 'element:mcp_management:publish'
);

-- 2) 给现有全部角色授权，保持升级前后行为一致
INSERT INTO ai_agent_resource_permissions (user_id, role_id, resource_type, resource_id, enabled, created_at, updated_at)
SELECT NULL, r.id, 'element', 'element:mcp_management:create', 1, NOW(), NOW()
FROM ai_agent_roles r
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions dst
    WHERE dst.resource_type = 'element'
      AND dst.resource_id = 'element:mcp_management:create'
      AND dst.role_id = r.id
);

INSERT INTO ai_agent_resource_permissions (user_id, role_id, resource_type, resource_id, enabled, created_at, updated_at)
SELECT NULL, r.id, 'element', 'element:mcp_management:edit', 1, NOW(), NOW()
FROM ai_agent_roles r
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions dst
    WHERE dst.resource_type = 'element'
      AND dst.resource_id = 'element:mcp_management:edit'
      AND dst.role_id = r.id
);

INSERT INTO ai_agent_resource_permissions (user_id, role_id, resource_type, resource_id, enabled, created_at, updated_at)
SELECT NULL, r.id, 'element', 'element:mcp_management:delete', 1, NOW(), NOW()
FROM ai_agent_roles r
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions dst
    WHERE dst.resource_type = 'element'
      AND dst.resource_id = 'element:mcp_management:delete'
      AND dst.role_id = r.id
);

INSERT INTO ai_agent_resource_permissions (user_id, role_id, resource_type, resource_id, enabled, created_at, updated_at)
SELECT NULL, r.id, 'element', 'element:mcp_management:sync', 1, NOW(), NOW()
FROM ai_agent_roles r
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions dst
    WHERE dst.resource_type = 'element'
      AND dst.resource_id = 'element:mcp_management:sync'
      AND dst.role_id = r.id
);

INSERT INTO ai_agent_resource_permissions (user_id, role_id, resource_type, resource_id, enabled, created_at, updated_at)
SELECT NULL, r.id, 'element', 'element:mcp_management:execute', 1, NOW(), NOW()
FROM ai_agent_roles r
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions dst
    WHERE dst.resource_type = 'element'
      AND dst.resource_id = 'element:mcp_management:execute'
      AND dst.role_id = r.id
);

INSERT INTO ai_agent_resource_permissions (user_id, role_id, resource_type, resource_id, enabled, created_at, updated_at)
SELECT NULL, r.id, 'element', 'element:mcp_management:publish', 1, NOW(), NOW()
FROM ai_agent_roles r
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_resource_permissions dst
    WHERE dst.resource_type = 'element'
      AND dst.resource_id = 'element:mcp_management:publish'
      AND dst.role_id = r.id
);
