-- V179 个人技能点对点分享
-- Date: 2026-10-21
-- Purpose:
--   支持把个人技能分享给指定同事（设计见 docs/superpowers/specs/2026-10-21-personal-skill-sharing-design.md）。
--   一行 = 「分享人 → 接收人」的一次邀请；PENDING 期间 snapshot_path 指向一份不可变快照，
--   接收人接受时以快照复制出一份**独立的**个人技能，并回填 accepted_skill_id；
--   接受 / 拒绝 / 撤回后快照立即回收（snapshot_path 置空），记录保留用于血缘与「我的分享」。
--
--   分享内容以快照为准：分享后再改源技能不会影响已发出的邀请，也不存在回写源技能的路径。
--
-- 注意：source_user_id / target_user_id 不建物理外键，由 delete_user 显式清理
-- （先回收快照目录，再删分享记录），否则删除用户会被外键 RESTRICT 挡住。
--
-- 幂等：IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS skill_shares (
    id VARCHAR(36) NOT NULL PRIMARY KEY COMMENT '分享记录 ID，UUID',
    source_user_id BIGINT NOT NULL COMMENT '分享人用户 ID',
    source_skill_id VARCHAR(128) NOT NULL COMMENT '分享人侧个人技能 ID',
    name VARCHAR(255) NOT NULL COMMENT '技能名称快照',
    description TEXT NULL COMMENT '技能描述快照',
    message VARCHAR(500) NULL COMMENT '附言（应用层限制 200 字）',
    target_user_id BIGINT NOT NULL COMMENT '接收人用户 ID',
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING 待接受、ACCEPTED 已接受、REJECTED 已拒绝、WITHDRAWN 已撤回',
    snapshot_path VARCHAR(1024) NULL COMMENT '分享快照目录，终态后置空',
    content_sha256 CHAR(64) NOT NULL COMMENT '快照内容 SHA-256 指纹',
    file_count INT NOT NULL DEFAULT 0 COMMENT '快照文件数量',
    total_size BIGINT NOT NULL DEFAULT 0 COMMENT '快照总大小，单位字节',
    accepted_skill_id VARCHAR(128) NULL COMMENT '接收人侧复制出的个人技能 ID',
    created_at DATETIME NOT NULL COMMENT '分享时间',
    responded_at DATETIME NULL COMMENT '接受 / 拒绝 / 撤回时间',
    -- 刻意不建物理外键：与 ai_agent_user_departments 同一惯例。物理外键会让
    -- 「删除用户」在引用方未清理时直接 500（RESTRICT），而这里的清理还要顺带
    -- 回收 PENDING 分享的快照目录，只能在应用层做（见 management.delete_user）。
    INDEX idx_skill_shares_source (source_user_id, source_skill_id, status),
    INDEX idx_skill_shares_target (target_user_id, status, created_at)
) COMMENT='个人技能点对点分享记录';
