-- V85 智能体系统级发布机制（申请审核 + 操作留痕）
-- Date: 2026-10-24
-- Purpose:
--   原来「是否系统级」是编辑表单里的一个开关，管理员随时勾选，既不留痕也没有流程；
--   普通用户还能通过复制系统智能体把提示词、工具/MCP 绑定整份带走。改成：
--   1) 系统级只能通过「发布为系统级」动作设置，新建/编辑表单不再出现该开关；
--   2) 普通用户走「提交申请 → 管理员审核 → 上架」，上架后对所有者只读，只能复制；
--   3) 发布、下架、元数据变更、启用/禁用全部落事件，可查发布历史与内容快照。
--
--   新增列 ai_agents.system_status / ai_agents.system_pending_request_id：
--   注意 is_system 仍是运行时的唯一事实源，system_status 只表达「有没有在审核中」。
--
--   新增表 ai_agent_system_requests（申请单，提交时打快照）
--          ai_agent_system_events（操作事件 PUBLISH/UNPUBLISH/META_UPDATE/TOGGLE_ENABLED）
--
--   刻意不建物理外键：与 skill_publications / mcp_server_shares 同一惯例，
--   避免删除用户或智能体时撞 RESTRICT，清理统一由应用层负责。
--
--   幂等：列变更用 PostgreSQL 原生的 IF NOT EXISTS，建表/建索引同样 IF NOT EXISTS，
--   存量事件按 agent + actor 去重，可重复执行。

-- PG 9.6+ 原生支持 IF NOT EXISTS，不需要 information_schema 判断
ALTER TABLE ai_agents
    ADD COLUMN IF NOT EXISTS system_status VARCHAR(20) NOT NULL DEFAULT 'NONE';

ALTER TABLE ai_agents
    ADD COLUMN IF NOT EXISTS system_pending_request_id VARCHAR(36);

COMMENT ON COLUMN ai_agents.system_status
    IS '系统级流程状态：NONE / PENDING_REVIEW（is_system 仍是运行时事实源）';

COMMENT ON COLUMN ai_agents.system_pending_request_id
    IS '待审申请单 ID（system_status=PENDING_REVIEW 时有值）';

CREATE TABLE IF NOT EXISTS ai_agent_system_requests (
    id VARCHAR(36) PRIMARY KEY,
    agent_id VARCHAR(36) NOT NULL,
    snapshot_version_id VARCHAR(36),
    snapshot TEXT,
    request_type VARCHAR(24) NOT NULL DEFAULT 'SYSTEM_PUBLISH',
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    request_comment TEXT,
    requested_by INTEGER,
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_by INTEGER,
    reviewed_at TIMESTAMP,
    review_comment TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_system_requests_status_time
    ON ai_agent_system_requests (status, requested_at);

CREATE INDEX IF NOT EXISTS idx_agent_system_requests_agent
    ON ai_agent_system_requests (agent_id, requested_at);

COMMENT ON TABLE "ai_agent_system_requests" IS '智能体系统级发布申请单';
COMMENT ON COLUMN "ai_agent_system_requests"."id" IS '申请单 ID（uuid4）';
COMMENT ON COLUMN "ai_agent_system_requests"."agent_id" IS '智能体 ID';
COMMENT ON COLUMN "ai_agent_system_requests"."snapshot_version_id" IS '提交时使用的版本 ID（取最新 PUBLISHED 版本）';
COMMENT ON COLUMN "ai_agent_system_requests"."snapshot" IS '提交时的完整内容快照（JSON：元数据 + 版本配置）';
COMMENT ON COLUMN "ai_agent_system_requests"."status" IS 'PENDING / APPROVED / REJECTED / CANCELLED';
ALTER TABLE ai_agent_system_requests
    ADD COLUMN IF NOT EXISTS request_type VARCHAR(24) NOT NULL DEFAULT 'SYSTEM_PUBLISH';

COMMENT ON COLUMN "ai_agent_system_requests"."request_type" IS 'SYSTEM_PUBLISH=申请发布为系统级；VERSION_RELEASE=申请把某版本发布上线';
COMMENT ON COLUMN "ai_agent_system_requests"."request_comment" IS '申请人填写的申请理由（可选，最多 500 字）';
COMMENT ON COLUMN "ai_agent_system_requests"."requested_by" IS '申请人用户 ID';
COMMENT ON COLUMN "ai_agent_system_requests"."requested_at" IS '提交时间';
COMMENT ON COLUMN "ai_agent_system_requests"."reviewed_by" IS '审核人用户 ID';
COMMENT ON COLUMN "ai_agent_system_requests"."reviewed_at" IS '审核时间';
COMMENT ON COLUMN "ai_agent_system_requests"."review_comment" IS '审核意见（驳回时必填）';

CREATE TABLE IF NOT EXISTS ai_agent_system_events (
    id VARCHAR(36) PRIMARY KEY,
    agent_id VARCHAR(36) NOT NULL,
    action VARCHAR(20) NOT NULL,
    version_id VARCHAR(36),
    version_number INTEGER,
    snapshot TEXT,
    reason TEXT,
    affected_user_count INTEGER,
    notified BOOLEAN,
    actor_id INTEGER,
    actor_name VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_system_events_agent_time
    ON ai_agent_system_events (agent_id, created_at);

COMMENT ON TABLE "ai_agent_system_events" IS '智能体系统级操作事件';
COMMENT ON COLUMN "ai_agent_system_events"."id" IS '事件 ID（uuid4）';
COMMENT ON COLUMN "ai_agent_system_events"."agent_id" IS '智能体 ID';
COMMENT ON COLUMN "ai_agent_system_events"."action" IS 'PUBLISH / UNPUBLISH / META_UPDATE / TOGGLE_ENABLED';
COMMENT ON COLUMN "ai_agent_system_events"."version_id" IS '关联版本 ID';
COMMENT ON COLUMN "ai_agent_system_events"."version_number" IS '关联版本号（便于展示，避免回查）';
COMMENT ON COLUMN "ai_agent_system_events"."snapshot" IS '发布时的内容快照（JSON）；下架与元数据事件为空';
COMMENT ON COLUMN "ai_agent_system_events"."reason" IS '操作说明 / 下架原因 / 停用原因';
COMMENT ON COLUMN "ai_agent_system_events"."affected_user_count" IS '操作时受影响人数（下架时记录，与是否通知无关）';
COMMENT ON COLUMN "ai_agent_system_events"."notified" IS '是否通知了受影响用户（仅 UNPUBLISH 有值；NULL=不适用）';
COMMENT ON COLUMN "ai_agent_system_events"."actor_id" IS '操作人用户 ID（迁移补录为 NULL）';
COMMENT ON COLUMN "ai_agent_system_events"."actor_name" IS '操作人用户名快照（迁移补录为 system）';
COMMENT ON COLUMN "ai_agent_system_events"."created_at" IS '发生时间';

-- 存量系统智能体补一条「发布」事件，否则发布历史里看不到它们的来历。
INSERT INTO ai_agent_system_events (id, agent_id, action, reason, actor_name, created_at)
SELECT
    md5(random()::text || a.id),
    a.id,
    'PUBLISH',
    '存量迁移：历史系统智能体',
    'system',
    COALESCE(a.updated_at, CURRENT_TIMESTAMP)
FROM ai_agents a
WHERE a.is_system = TRUE
  AND NOT EXISTS (
      SELECT 1 FROM ai_agent_system_events e
      WHERE e.agent_id = a.id
        AND e.action = 'PUBLISH'
        AND e.actor_name = 'system'
  );

-- =====================================================================
-- 发布审核权限点（前置依赖：V176-create_agent_menus.sql，本文件末尾的 seed 需要 ai_agent_menus 表）
-- 建表与权限种子属于同一个功能，因此写在同一份迁移里（与 V178/V79 同构）。
-- 下面 START/END 之间的内容由 scripts/gen_menu_seed_sql.py 从 menu_seed.py 生成，
-- 请勿手改；要调整权限点请改 menu_seed.py 后重新运行脚本。
-- =====================================================================
-- >>> gen_menu_seed_sql:agent-system-publish START（本段由脚本生成，勿手改）>>>
-- ========== 菜单与权限节点 seed（1 条）==========
-- 顺序即同级排序；分组先于其子项，页面先于其功能点，因此逐条插入不需要额外的拓扑排序。

INSERT INTO "ai_agent_menus"
    ("parent_id", "node_type", "code", "permission_code", "label", "icon", "path", "active_route_names", "url", "open_mode", "sort_order", "enabled", "desktop_only", "is_landing", "builtin", "description", "created_at", "updated_at")
SELECT (SELECT p.id FROM "ai_agent_menus" p WHERE p.code = 'menu:agent_management'), 'element', 'element:agent:system_publish', NULL, '发布审核与系统级上下架', NULL, NULL, NULL, NULL, NULL, 40, TRUE, FALSE, FALSE, TRUE, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM "ai_agent_menus" m WHERE m.code = 'element:agent:system_publish');
-- <<< gen_menu_seed_sql:agent-system-publish END <<<
