-- V218: 管理员站内信群发的发送记录
--
-- 背景：群发是「一次写入成百上千条、发出去收不回」的广播动作。原先只依赖请求日志
-- （ai_agent_access_logs）留痕，问题有两个：正文超过 10KB 会被截断；日志按 endpoint
-- 聚合，没法回答「哪一次群发、发给了哪个范围、命中多少人」。本表记录每次群发的输入
-- （范围 + 标题 + 正文快照）与结果（实际条数），支撑管理页的历史列表与回填重发。
--
-- 设计取舍：
--   • 只存「范围」，不存最终收件人名单。名单可以由范围重新解析；存一份快照会在人员
--     变动后与事实不符。重发时重新预览，把「历史 128 人 / 现在 130 人」的差异摆给
--     操作者看，比悄悄按旧名单再发一次安全。
--   • 不建外键：发送者账号可能被删除/停用，历史记录仍须可读（与 ai_agent_departments
--     等表的既有做法一致）。sender_* 三列都是写入时刻的快照。
--   • 不做保留期清理：每次群发只有一行，体量远小于 portal_notifications（后者 30 天
--     清理）。保留全量便于回溯「上个月那条公告是谁发的」。
--
-- 幂等性：CREATE TABLE IF NOT EXISTS + CREATE INDEX 由 db-prod/apply_sql.py 执行时
-- 重复跑会被当作「对象已存在」跳过；手工执行时该语句同样可重复执行。

CREATE TABLE IF NOT EXISTS portal_broadcast_records (
    id BIGINT NOT NULL AUTO_INCREMENT,
    sender_user_id BIGINT NOT NULL COMMENT '发送者用户ID（写入时刻快照，不建外键）',
    sender_user_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '发送者登录名快照',
    sender_real_name VARCHAR(64) NULL COMMENT '发送者姓名快照',
    title VARCHAR(200) NOT NULL COMMENT '消息标题（与 portal_notifications.title 同上限）',
    content TEXT NOT NULL COMMENT '消息正文快照（Markdown 原文）',
    level VARCHAR(16) NOT NULL DEFAULT 'info' COMMENT '消息级别：info/success/warning/error',
    audience_user_ids JSON NULL COMMENT '指定用户范围（ID 数组）',
    audience_dept_ids JSON NULL COMMENT '部门范围（ID 数组，含下级）',
    audience_role_ids JSON NULL COMMENT '角色范围（ID 数组）',
    recipient_count INT NOT NULL DEFAULT 0 COMMENT '当次实际写入的站内信条数（去重、仅启用账号）',
    created_at DATETIME NOT NULL COMMENT '发送时间',
    PRIMARY KEY (id),
    -- 历史列表按时间倒序分页；created_at 单独建索引即可覆盖
    INDEX idx_portal_broadcast_records_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理员站内信群发记录';
