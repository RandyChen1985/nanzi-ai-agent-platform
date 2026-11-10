-- V111: 凭据绑定从「一条 secret_id」升级为「一条选择器」（PostgreSQL）。
-- 与 MySQL 侧 db-prod/V211-add-credential-locator.sql 保持同一结构与同一顺序：
--   strategy = fixed → 就是今天的行为，secret_id 必填（存量行全部落在这一档）
--   strategy = rule  → 按 rule_json 在「调用者可见」的凭据里定位，secret_id 为空
--
-- 幂等：PG 执行器**不吞任何错误**——两个执行器的差别就在这里，MySQL 侧只按语句
-- 粒度忽略 1060/1061/1091，PG 侧是整文件一个事务，任一语句报错全文件回滚。
-- 所以下面每条语句都必须自己幂等，不能指望执行器忽略错误码：
--   * ALTER COLUMN ... DROP NOT NULL 重复执行无副作用（列已可空时是空操作）
--   * ADD COLUMN IF NOT EXISTS
--   * CREATE UNIQUE INDEX IF NOT EXISTS
--   * COMMENT ON COLUMN 可重复设置同一注释
--
-- 旧索引 `uk_binding_slot` **不在这里删**，理由同 MySQL 侧：先建新索引、再在
-- V112 删旧的，不留「旧索引已删、新索引未建」的裸窗口。

ALTER TABLE "sys_secret_bindings" ALTER COLUMN "secret_id" DROP NOT NULL;

ALTER TABLE "sys_secret_bindings"
    ADD COLUMN IF NOT EXISTS "strategy" VARCHAR(16) NOT NULL DEFAULT 'fixed';

ALTER TABLE "sys_secret_bindings"
    ADD COLUMN IF NOT EXISTS "rule_json" TEXT NULL;

COMMENT ON COLUMN "sys_secret_bindings"."secret_id" IS 'strategy=fixed 时必填；strategy=rule 时为空';
COMMENT ON COLUMN "sys_secret_bindings"."strategy" IS 'fixed / rule：绑一条凭据，还是绑一条定位规则';
COMMENT ON COLUMN "sys_secret_bindings"."rule_json" IS 'strategy=rule 时的匹配规则（JSON），见 app/services/secrets/locator.py';

-- 唯一索引加入 strategy：同一槽位允许「规则 + 固定（回退目标）」各一条。
-- 存量行 strategy 恒为 'fixed'，所以新索引的约束与旧索引等价，不会因存量数据冲突。
-- 写成 CREATE UNIQUE INDEX 而不是 ADD CONSTRAINT UNIQUE：PG 的 ADD CONSTRAINT
-- 没有 IF NOT EXISTS，只能靠索引名自查幂等。
CREATE UNIQUE INDEX IF NOT EXISTS "uk_binding_slot_strategy"
    ON "sys_secret_bindings" ("consumer_type", "consumer_id", "slot", "bound_by_user_id", "strategy");
