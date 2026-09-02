-- V138: 增加 MCP 服务台“生成当前用户 Access Token”功能权限
-- 说明：Token 的用户身份始终来自当前登录会话，不支持选择或代发其他用户身份。

INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT 'element', 'element:mcp_service:client:token_issue', 1, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1
    FROM ai_agent_resource_permissions
    WHERE resource_type = 'element'
      AND resource_id = 'element:mcp_service:client:token_issue'
);
