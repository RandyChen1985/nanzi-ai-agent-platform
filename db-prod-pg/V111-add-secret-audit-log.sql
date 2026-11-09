-- V111: 凭据变更审计表（PostgreSQL 方言）
--
-- 与 MySQL 的 V211-add-secret-audit-log.sql 对应，两套迁移不可混用。
--
-- 背景：sys_secret_usage_logs 记的是「谁用了凭据」，不是「谁改了配置」。
-- 出事时最常问的两个问题——「这条 token 谁轮换过」「这个绑定谁加的」——现在
-- 一条都答不上。
--
-- 两条设计约束：
--   1. **不设外键**：删凭据不删审计；secret_key_snapshot 让记录在凭据消失后仍可读。
--   2. **只增不改**：没有 UPDATE / DELETE 路径。actor_name 是快照，不 join 用户表。
--
-- 代码可以先于本迁移上线：写审计失败只记日志，不阻断主流程。

CREATE TABLE IF NOT EXISTS sys_secret_audit_logs (
  id VARCHAR(36) PRIMARY KEY,
  secret_id VARCHAR(36) NULL,
  secret_key_snapshot VARCHAR(128) NULL,
  owner_scope VARCHAR(16) NULL,
  actor_id VARCHAR(64) NULL,
  actor_name VARCHAR(128) NULL,
  action VARCHAR(24) NOT NULL,
  detail TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_secret_audit_secret_created
  ON sys_secret_audit_logs (secret_id, created_at);
CREATE INDEX IF NOT EXISTS ix_secret_audit_actor_created
  ON sys_secret_audit_logs (actor_id, created_at);
CREATE INDEX IF NOT EXISTS ix_secret_audit_action
  ON sys_secret_audit_logs (action);

COMMENT ON TABLE sys_secret_audit_logs IS '凭据变更审计：谁在什么时候对哪条凭据做了什么';
