-- V223: MCP 工具定义变更——自动探测与变更留痕（MySQL）
--
-- 与 PostgreSQL 侧 db-prod-pg/V123-mcp-definition-change-tracking.sql 同一结构、同一语义。
--
-- 背景：「同步工具」是手动按钮，而 sync_tools() 是整批覆盖——远端改了描述、加了参数、
-- 删了参数，点一次同步就被静默吃掉，库里只剩一个 last_sync_at。人不知道改了什么、
-- 什么时候改的、会不会影响已绑定的智能体。
--
-- 本迁移只加「探测结果」的载体，不改任何同步/发布语义：
--
--   1. sys_mcp_servers 加 last_probe_*：探测是独立于同步的一条链路。它的时间与失败次数
--      不能和 last_sync_at 混用——last_sync_at 只在真正写库（应用差异）时更新，而探测
--      每天都跑且从不写业务字段。混用会让「7 天未同步」这类提醒彻底失真。
--
--   2. sys_mcp_tool_cache 加 definition_hash：判断「远端变没变」不能用 updated_at，它是
--      ON UPDATE CURRENT_TIMESTAMP，只反映上次同步时间。指纹取 description + schema +
--      annotations 的规范化 sha256（json sort_keys），并剥掉平台自己的 x-nanzi-* 扩展
--      字段——否则 key 顺序、空白差异会让每天巡检都报假变更。
--
--   3. 新表 sys_mcp_tool_definition_changes 只存差异项（before/after 片段），不存全量
--      工具快照：一次探测动辄几十个工具，存全量会让这张表比工具缓存本身还大。
--
-- 分流规则（表结构跟着它走，所以写在这里）：
--   · 新增/删除工具不阻塞——那边已有「待发布」计数与 stale_tool_count 两套现成机制，
--     变更记录只作历史留痕，severity=notice。
--   · 描述变更、参数增删/必填/类型/枚举变化才是要人看一眼的，进 pending。
--
-- 去重口径：同一 (server_id, tool_name, change_kind) 只保留一条 pending，重复探测覆盖
-- after_value 而不是叠加。否则每天巡检一次，一周后同一个改动会堆出 7 条噪音。
--
-- 不建触发式清理：历史记录与出站审计同一个保留口径，由既有日志清理任务按天裁剪。

-- ---------------------------------------------------------------- 探测元数据
ALTER TABLE sys_mcp_servers
    ADD COLUMN last_probe_at DATETIME NULL
        COMMENT '最近一次定义探测时间（只读探测，与 last_sync_at 不是一回事）',
    ADD COLUMN last_probe_status VARCHAR(24) NULL
        COMMENT 'ok / changed / unreachable / needs_user / skipped_disabled',
    ADD COLUMN probe_failure_count INT NOT NULL DEFAULT 0
        COMMENT '连续探测失败次数，成功一次即归零；用于区分「偶发超时」与「服务已下线」',
    ADD COLUMN last_probe_error VARCHAR(500) NULL
        COMMENT '最近一次探测失败原因，截断 500 字，禁止存远端返回的完整报文';

-- ---------------------------------------------------------------- 定义指纹
ALTER TABLE sys_mcp_tool_cache
    ADD COLUMN definition_hash VARCHAR(64) NULL
        COMMENT 'description+schema+annotations 规范化后的 sha256，判断远端是否变化的唯一依据',
    ADD COLUMN definition_changed_at DATETIME NULL
        COMMENT '最近一次发现「远端定义与本地生效值不一致」的时间（仅标记，不代表已应用）';

-- ---------------------------------------------------------------- 变更记录
CREATE TABLE IF NOT EXISTS sys_mcp_tool_definition_changes (
    id CHAR(36) NOT NULL,
    batch_id CHAR(36) NOT NULL
        COMMENT '一次探测一批，前端按批次分组展示',
    server_id CHAR(36) NOT NULL
        COMMENT '关联 sys_mcp_servers.id，删服务时级联清理',
    tool_name VARCHAR(255) NOT NULL
        COMMENT '全名 server:tool，与 sys_mcp_tool_cache.tool_name 同口径',
    change_kind VARCHAR(32) NOT NULL
        COMMENT 'description_changed / param_added_optional / param_removed / param_required / param_type / param_enum / tool_added / tool_removed / annotations_changed',
    severity VARCHAR(16) NOT NULL DEFAULT 'notice'
        COMMENT 'safe / notice / breaking，决定前端徽标颜色与是否点名影响面',
    before_value TEXT NULL
        COMMENT '变化部分的 JSON 快照（只存变的那几项，不存全量定义）',
    after_value TEXT NULL
        COMMENT '变化部分的 JSON 快照',
    detected_at DATETIME NOT NULL
        COMMENT '探测时间',
    detected_by VARCHAR(24) NOT NULL DEFAULT 'manual'
        COMMENT 'nightly / page / hot_call / manual，用于回答「这条是谁发现的」',
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        COMMENT 'pending / applied / dismissed',
    handled_at DATETIME NULL
        COMMENT '应用或忽略的时间',
    handled_by INT NULL
        COMMENT '处理人 user_id；NULL 表示系统自动应用（如巡检后按策略清理）',
    PRIMARY KEY (id),
    KEY idx_mcp_chg_server_status (server_id, status),
    KEY idx_mcp_chg_detected (detected_at),
    KEY idx_mcp_chg_tool (tool_name),
    CONSTRAINT fk_mcp_chg_server FOREIGN KEY (server_id)
        REFERENCES sys_mcp_servers (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='MCP 工具定义变更记录：探测发现远端与本地不一致时留痕，人工确认后才应用';
