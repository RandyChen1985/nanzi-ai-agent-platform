-- V80 个人技能点对点分享
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
    id VARCHAR(36) PRIMARY KEY,
    source_user_id BIGINT NOT NULL,
    source_skill_id VARCHAR(128) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    message VARCHAR(500),
    target_user_id BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    snapshot_path VARCHAR(1024),
    content_sha256 CHAR(64) NOT NULL,
    file_count INTEGER NOT NULL DEFAULT 0,
    total_size BIGINT NOT NULL DEFAULT 0,
    accepted_skill_id VARCHAR(128),
    created_at TIMESTAMP NOT NULL,
    responded_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_skill_shares_source
    ON skill_shares (source_user_id, source_skill_id, status);
CREATE INDEX IF NOT EXISTS idx_skill_shares_target
    ON skill_shares (target_user_id, status, created_at);

COMMENT ON TABLE "skill_shares" IS '个人技能点对点分享记录';
COMMENT ON COLUMN "skill_shares"."id" IS '分享记录 ID，UUID';
COMMENT ON COLUMN "skill_shares"."source_user_id" IS '分享人用户 ID';
COMMENT ON COLUMN "skill_shares"."source_skill_id" IS '分享人侧个人技能 ID';
COMMENT ON COLUMN "skill_shares"."name" IS '技能名称快照';
COMMENT ON COLUMN "skill_shares"."description" IS '技能描述快照';
COMMENT ON COLUMN "skill_shares"."message" IS '附言（应用层限制 200 字）';
COMMENT ON COLUMN "skill_shares"."target_user_id" IS '接收人用户 ID';
COMMENT ON COLUMN "skill_shares"."status" IS 'PENDING 待接受、ACCEPTED 已接受、REJECTED 已拒绝、WITHDRAWN 已撤回';
COMMENT ON COLUMN "skill_shares"."snapshot_path" IS '分享快照目录，终态后置空';
COMMENT ON COLUMN "skill_shares"."content_sha256" IS '快照内容 SHA-256 指纹';
COMMENT ON COLUMN "skill_shares"."file_count" IS '快照文件数量';
COMMENT ON COLUMN "skill_shares"."total_size" IS '快照总大小，单位字节';
COMMENT ON COLUMN "skill_shares"."accepted_skill_id" IS '接收人侧复制出的个人技能 ID';
COMMENT ON COLUMN "skill_shares"."created_at" IS '分享时间';
COMMENT ON COLUMN "skill_shares"."responded_at" IS '接受 / 拒绝 / 撤回时间';
