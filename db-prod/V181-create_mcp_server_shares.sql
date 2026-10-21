-- V181 个人 MCP 分享
-- Date: 2026-10-21
-- Purpose:
--   支持把「我的 MCP」分享给指定同事，对方接受后复制一份到他的个人 MCP。
--   分享即打快照（payload 存完整配置 + 工具清单），源服务之后被改/删都不影响已发出的分享。
--
--   刻意不建物理外键：与 skill_shares / ai_agent_user_departments 同一惯例。
--   物理外键会在管理员删除用户时直接撞 RESTRICT 报 500，而清理还要连带处理 share 的两端，
--   统一由 management.delete_user 在应用层完成。
--
--   幂等：IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS mcp_server_shares (
    id VARCHAR(36) NOT NULL COMMENT '分享记录 ID（uuid4）',
    source_user_id BIGINT NOT NULL COMMENT '分享人用户 ID',
    source_server_id VARCHAR(36) NOT NULL COMMENT '源个人 MCP 服务 ID',
    server_name VARCHAR(100) NOT NULL COMMENT '分享瞬间的服务显示名快照',
    remark VARCHAR(500) NULL COMMENT '分享瞬间的备注快照',
    sse_url TEXT NOT NULL COMMENT '分享瞬间的服务地址快照',
    payload LONGTEXT NULL COMMENT '配置与工具清单的 JSON 快照（敏感字段已是密文）；终态清空',
    include_credentials TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否携带认证凭证（Token/断言私钥）',
    target_user_id BIGINT NOT NULL COMMENT '接收人用户 ID',
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/ACCEPTED/REJECTED/WITHDRAWN',
    message VARCHAR(500) NULL COMMENT '附言',
    accepted_server_id VARCHAR(36) NULL COMMENT '接收人安装后的 MCP 服务 ID（可能带 -2 后缀）',
    created_at DATETIME NOT NULL COMMENT '创建/最近一次刷新时间',
    responded_at DATETIME NULL COMMENT '接受/拒绝/撤回时间',
    PRIMARY KEY (id),
    KEY idx_mcp_shares_source (source_user_id, source_server_id, status),
    KEY idx_mcp_shares_target (target_user_id, status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个人 MCP 分享记录';
