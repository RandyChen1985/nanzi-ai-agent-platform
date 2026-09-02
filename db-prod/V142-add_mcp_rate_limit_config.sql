-- MCP 服务台限流配置
ALTER TABLE sys_mcp_platform_config
    ADD COLUMN rate_limit_client_per_minute INT NOT NULL DEFAULT 120 COMMENT '单个 Client 每分钟调用上限，0 表示关闭',
    ADD COLUMN rate_limit_user_per_minute INT NOT NULL DEFAULT 60 COMMENT '单个用户每分钟调用上限，0 表示关闭';
