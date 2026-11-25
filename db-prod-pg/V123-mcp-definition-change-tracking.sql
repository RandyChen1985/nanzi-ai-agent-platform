-- V123: MCP 工具定义变更——自动探测与变更留痕（PostgreSQL）
--
-- 与 MySQL 侧 db-prod/V223-mcp-definition-change-tracking.sql 同一结构、同一语义。
-- 设计理由与分流规则见该文件顶部注释，这里只记 PG 方言差异。
--
-- 方言差异：
--   · 列注释用独立 COMMENT ON 语句（PG 不支持列内联 COMMENT）；
--   · DATETIME → TIMESTAMP；
--   · ALTER TABLE ADD COLUMN IF NOT EXISTS 可重复执行，与 PG 侧既有迁移风格一致。

-- ---------------------------------------------------------------- 探测元数据
ALTER TABLE sys_mcp_servers
    ADD COLUMN IF NOT EXISTS last_probe_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS last_probe_status VARCHAR(24),
    ADD COLUMN IF NOT EXISTS probe_failure_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_probe_error VARCHAR(500);

COMMENT ON COLUMN sys_mcp_servers.last_probe_at IS
    '最近一次定义探测时间（只读探测，与 last_sync_at 不是一回事）';
COMMENT ON COLUMN sys_mcp_servers.last_probe_status IS
    'ok / changed / unreachable / needs_user / skipped_disabled';
COMMENT ON COLUMN sys_mcp_servers.probe_failure_count IS
    '连续探测失败次数，成功一次即归零；用于区分「偶发超时」与「服务已下线」';
COMMENT ON COLUMN sys_mcp_servers.last_probe_error IS
    '最近一次探测失败原因，截断 500 字，禁止存远端返回的完整报文';

-- ---------------------------------------------------------------- 定义指纹
ALTER TABLE sys_mcp_tool_cache
    ADD COLUMN IF NOT EXISTS definition_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS definition_changed_at TIMESTAMP;

COMMENT ON COLUMN sys_mcp_tool_cache.definition_hash IS
    'description+schema+annotations 规范化后的 sha256，判断远端是否变化的唯一依据';
COMMENT ON COLUMN sys_mcp_tool_cache.definition_changed_at IS
    '最近一次发现「远端定义与本地生效值不一致」的时间（仅标记，不代表已应用）';

-- ---------------------------------------------------------------- 变更记录
CREATE TABLE IF NOT EXISTS sys_mcp_tool_definition_changes (
    id VARCHAR(36) NOT NULL,
    batch_id VARCHAR(36) NOT NULL,
    server_id VARCHAR(36) NOT NULL,
    tool_name VARCHAR(255) NOT NULL,
    change_kind VARCHAR(32) NOT NULL,
    severity VARCHAR(16) NOT NULL DEFAULT 'notice',
    before_value TEXT,
    after_value TEXT,
    detected_at TIMESTAMP NOT NULL,
    detected_by VARCHAR(24) NOT NULL DEFAULT 'manual',
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    handled_at TIMESTAMP,
    handled_by INTEGER,
    CONSTRAINT pk_sys_mcp_tool_definition_changes PRIMARY KEY (id),
    CONSTRAINT fk_mcp_chg_server FOREIGN KEY (server_id)
        REFERENCES sys_mcp_servers (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mcp_chg_server_status
    ON sys_mcp_tool_definition_changes (server_id, status);
CREATE INDEX IF NOT EXISTS idx_mcp_chg_detected
    ON sys_mcp_tool_definition_changes (detected_at);
CREATE INDEX IF NOT EXISTS idx_mcp_chg_tool
    ON sys_mcp_tool_definition_changes (tool_name);

COMMENT ON TABLE sys_mcp_tool_definition_changes IS
    'MCP 工具定义变更记录：探测发现远端与本地不一致时留痕，人工确认后才应用';
COMMENT ON COLUMN sys_mcp_tool_definition_changes.batch_id IS
    '一次探测一批，前端按批次分组展示';
COMMENT ON COLUMN sys_mcp_tool_definition_changes.tool_name IS
    '全名 server:tool，与 sys_mcp_tool_cache.tool_name 同口径';
COMMENT ON COLUMN sys_mcp_tool_definition_changes.change_kind IS
    'description_changed / param_added_optional / param_removed / param_required / param_type / param_enum / tool_added / tool_removed / annotations_changed';
COMMENT ON COLUMN sys_mcp_tool_definition_changes.severity IS
    'safe / notice / breaking，决定前端徽标颜色与是否点名影响面';
COMMENT ON COLUMN sys_mcp_tool_definition_changes.before_value IS
    '变化部分的 JSON 快照（只存变的那几项，不存全量定义）';
COMMENT ON COLUMN sys_mcp_tool_definition_changes.detected_by IS
    'nightly / page / hot_call / manual，用于回答「这条是谁发现的」';
COMMENT ON COLUMN sys_mcp_tool_definition_changes.status IS
    'pending / applied / dismissed';
COMMENT ON COLUMN sys_mcp_tool_definition_changes.handled_by IS
    '处理人 user_id；NULL 表示系统自动应用';
