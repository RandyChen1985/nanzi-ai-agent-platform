-- V185: 智能体系统级发布机制（申请审核 + 操作留痕）
--
-- 背景：原来「是否系统级」是编辑表单里的一个开关，管理员随时勾选，既不留痕也没有流程；
--   普通用户还能通过复制系统智能体把提示词、工具/MCP 绑定整份带走。改成：
--   1) 系统级只能通过「发布为系统级」动作设置，新建/编辑表单不再出现该开关；
--   2) 普通用户走「提交申请 → 管理员审核 → 上架」，上架后对所有者只读，只能复制；
--   3) 发布、下架、元数据变更、启用/禁用全部落事件，可查发布历史与内容快照。
--
-- 新增列（ai_agents）：
--   system_status             流程状态：NONE / PENDING_REVIEW。
--                             注意 is_system 仍是运行时的唯一事实源，本列只表达「有没有在审核中」。
--   system_pending_request_id 待审申请单 ID（system_status=PENDING_REVIEW 时有值，其余为 NULL）。
--
-- 新增表：
--   ai_agent_system_requests 申请单：提交时打快照，状态 PENDING/APPROVED/REJECTED/CANCELLED。
--   ai_agent_system_events   操作事件：PUBLISH / UNPUBLISH / META_UPDATE / TOGGLE_ENABLED，
--                            PUBLISH 存内容快照，UNPUBLISH 存操作时受影响人数。
--
-- 刻意不建物理外键：与 skill_publications / mcp_server_shares 同一惯例，
--   避免删除用户或智能体时撞 RESTRICT，清理统一由应用层负责。
--
-- 幂等：加列先查 information_schema；建表 IF NOT EXISTS；存量事件按 agent + actor 去重。

SET @column_exists := (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'ai_agents'
      AND column_name = 'system_status'
);
SET @sql := IF(
    @column_exists = 0,
    'ALTER TABLE `ai_agents` ADD COLUMN `system_status` VARCHAR(20) NOT NULL DEFAULT ''NONE'' COMMENT ''系统级流程状态：NONE / PENDING_REVIEW（is_system 仍是运行时事实源）'' AFTER `is_system`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @column_exists := (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'ai_agents'
      AND column_name = 'system_pending_request_id'
);
SET @sql := IF(
    @column_exists = 0,
    'ALTER TABLE `ai_agents` ADD COLUMN `system_pending_request_id` VARCHAR(36) NULL COMMENT ''待审申请单 ID（system_status=PENDING_REVIEW 时有值）'' AFTER `system_status`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 已建过该表的环境（本脚本早版本执行过）补 request_type：
-- CREATE TABLE IF NOT EXISTS 不会给已存在的表加列。
SET @column_exists := (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'ai_agent_system_requests'
      AND column_name = 'request_type'
);
SET @sql := IF(
    @column_exists = 0,
    'ALTER TABLE `ai_agent_system_requests` ADD COLUMN `request_type` VARCHAR(24) NOT NULL DEFAULT ''SYSTEM_PUBLISH'' COMMENT ''SYSTEM_PUBLISH=申请发布为系统级；VERSION_RELEASE=申请把某版本发布上线'' AFTER `agent_id`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

CREATE TABLE IF NOT EXISTS `ai_agent_system_requests` (
    `id` VARCHAR(36) NOT NULL COMMENT '申请单 ID（uuid4）',
    `agent_id` VARCHAR(36) NOT NULL COMMENT '智能体 ID',
    `snapshot_version_id` VARCHAR(36) NULL COMMENT '提交时使用的版本 ID（取最新 PUBLISHED 版本）',
    `snapshot` LONGTEXT NULL COMMENT '提交时的完整内容快照（JSON：元数据 + 版本配置）',
    `request_type` VARCHAR(24) NOT NULL DEFAULT 'SYSTEM_PUBLISH' COMMENT 'SYSTEM_PUBLISH=申请发布为系统级；VERSION_RELEASE=申请把某版本发布上线',
    `status` VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING / APPROVED / REJECTED / CANCELLED',
    `request_comment` TEXT NULL COMMENT '申请人填写的申请理由（可选，最多 500 字）',
    `requested_by` INT NULL COMMENT '申请人用户 ID',
    `requested_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '提交时间',
    `reviewed_by` INT NULL COMMENT '审核人用户 ID',
    `reviewed_at` DATETIME NULL COMMENT '审核时间',
    `review_comment` TEXT NULL COMMENT '审核意见（驳回时必填）',
    PRIMARY KEY (`id`),
    KEY `idx_agent_system_requests_status_time` (`status`, `requested_at`),
    KEY `idx_agent_system_requests_agent` (`agent_id`, `requested_at`)
-- 必须显式指定 collation：ai_agents 是老表（utf8mb4_unicode_ci），库默认是
-- utf8mb4_0900_ai_ci，两张表混用会在 agent_id 关联时抛 1267 Illegal mix of collations。
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体系统级发布申请单';

CREATE TABLE IF NOT EXISTS `ai_agent_system_events` (
    `id` VARCHAR(36) NOT NULL COMMENT '事件 ID（uuid4）',
    `agent_id` VARCHAR(36) NOT NULL COMMENT '智能体 ID',
    `action` VARCHAR(20) NOT NULL COMMENT 'PUBLISH / UNPUBLISH / META_UPDATE / TOGGLE_ENABLED',
    `version_id` VARCHAR(36) NULL COMMENT '关联版本 ID',
    `version_number` INT NULL COMMENT '关联版本号（便于展示，避免回查）',
    `snapshot` LONGTEXT NULL COMMENT '发布时的内容快照（JSON）；下架与元数据事件为空',
    `reason` TEXT NULL COMMENT '操作说明 / 下架原因 / 停用原因',
    `affected_user_count` INT NULL COMMENT '操作时受影响人数（下架时记录，与是否通知无关）',
    `notified` TINYINT(1) NULL COMMENT '是否通知了受影响用户（仅 UNPUBLISH 有值；NULL=不适用）',
    `actor_id` INT NULL COMMENT '操作人用户 ID（迁移补录为 NULL）',
    `actor_name` VARCHAR(64) NULL COMMENT '操作人用户名快照（迁移补录为 system）',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发生时间',
    PRIMARY KEY (`id`),
    KEY `idx_agent_system_events_agent_time` (`agent_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体系统级操作事件';

-- 存量系统智能体补一条「发布」事件，否则发布历史里看不到它们的来历。
-- 只补 PUBLISH 且 actor_name='system' 的，重复执行不会叠加。
INSERT INTO `ai_agent_system_events`
    (`id`, `agent_id`, `action`, `reason`, `actor_name`, `created_at`)
SELECT
    UUID(),
    a.`id`,
    'PUBLISH',
    '存量迁移：历史系统智能体',
    'system',
    COALESCE(a.`updated_at`, CURRENT_TIMESTAMP)
FROM `ai_agents` a
WHERE a.`is_system` = 1
  AND NOT EXISTS (
      SELECT 1 FROM `ai_agent_system_events` e
      WHERE e.`agent_id` = a.`id`
        AND e.`action` = 'PUBLISH'
        AND e.`actor_name` = 'system'
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

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:agent_management'), 'element', 'element:agent:system_publish', NULL, '发布审核与系统级上下架', NULL, NULL, NULL, NULL, NULL, 40, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:agent:system_publish');
-- <<< gen_menu_seed_sql:agent-system-publish END <<<
