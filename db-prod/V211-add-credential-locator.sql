-- V211: 凭据绑定从「一条 secret_id」升级为「一条选择器」。
--
--   strategy = fixed → 就是今天的行为，secret_id 必填（存量行全部落在这一档）
--   strategy = rule  → 按 rule_json 在「调用者可见」的凭据里定位，secret_id 为空
--
-- 拆成三条 ALTER 而不是一条：多操作的 ALTER 是**整体原子**的，只要其中任何一项
-- 已存在，整条就报错回滚，排在后面的列和索引一起建不上（V210 已经踩过这个坑）。
-- 拆开后每条独立成败，重跑时只有真正没做成的那部分会重新执行。
--
-- 幂等：MySQL 8.0 没有 `ADD COLUMN IF NOT EXISTS`，而 `apply_sql.py` 没有版本
-- 记录表、整文件重放，只按语句粒度忽略错误码（1060 列已存在 / 1061 索引已存在 /
-- 1091 索引不存在）。`MODIFY COLUMN` 本身幂等，重复执行无副作用。
--
-- 旧唯一索引 `uk_binding_slot` **不在这里删**：先建新索引、再在 V212 删旧的。
-- 反过来（先删后建）会出现「旧索引已删、新索引未建」的裸窗口，并发写入可能
-- 插进重复的槽位行。

ALTER TABLE `sys_secret_bindings`
  MODIFY COLUMN `secret_id` VARCHAR(36) NULL
    COMMENT 'strategy=fixed 时必填；strategy=rule 时为空';

ALTER TABLE `sys_secret_bindings`
  ADD COLUMN `strategy` VARCHAR(16) NOT NULL DEFAULT 'fixed'
    COMMENT 'fixed / rule：绑一条凭据，还是绑一条定位规则';

ALTER TABLE `sys_secret_bindings`
  ADD COLUMN `rule_json` TEXT NULL
    COMMENT 'strategy=rule 时的匹配规则（JSON），见 app/services/secrets/locator.py';

-- 唯一索引加入 strategy：同一槽位允许「规则 + 固定（回退目标）」各一条。
-- 存量行 strategy 恒为 'fixed'，所以新索引的约束与旧索引等价，不会因存量数据冲突。
ALTER TABLE `sys_secret_bindings`
  ADD UNIQUE KEY `uk_binding_slot_strategy`
    (`consumer_type`, `consumer_id`, `slot`, `bound_by_user_id`, `strategy`);
