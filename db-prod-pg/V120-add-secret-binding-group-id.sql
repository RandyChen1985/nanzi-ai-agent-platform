-- V120: 绑定表加 `binding_group_id`——记录「这条绑定属于哪一次多值配置」。（PostgreSQL）
--
-- 与 MySQL 侧 db-prod/V220-add-secret-binding-group-id.sql 同一结构、同一语义。
--
-- 背景：同一个 MCP / API 工具的参数有两种配法，落库后完全同形——都是「N 个 slot
-- 各一条 sys_secret_bindings 行」，库里分辨不出「一次多值保存」和「分别单值配了
-- N 次」。于是：单值页把多值配的规则显示成 N 条独立规则；单值页改其中一条就把
-- 多值结构拆散而系统无感；注入按 slot 逐个解析，同一条规则重复执行 N 遍
-- （**最后这条本轮未解决**：标识先落库，注入链路暂不消费它，做它只省查询次数、
--   不改结果，前两条已修）。
--
-- 这一列把「组」显式记下来，展示和注入都不必再靠「几个 slot 的 rule_json 是否
-- 相同」去猜（用户单独改过某个 slot 之后那个推断就不成立了）。
--
-- 取值语义：
--   NULL        单值配法（全部存量行的默认，无需回填）
--   uuid 字符串  多值配法：一次 bindings/batch 保存生成一个，这一批 N 条共享

ALTER TABLE "sys_secret_bindings"
    ADD COLUMN IF NOT EXISTS "binding_group_id" VARCHAR(36) NULL;

COMMENT ON COLUMN "sys_secret_bindings"."binding_group_id"
    IS '多值配法的组标识（一次整表保存生成一个，该批各行共享）；单值配法为 NULL';

-- 幂等：PG 执行器不吞错误、整文件一个事务，所以每条语句自己幂等
-- （ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / 可重复的 COMMENT）。

-- 预留给「注入侧按组取同批内的其它 slot」（见上文，当前未实现），给个普通索引。
CREATE INDEX IF NOT EXISTS "idx_binding_group"
    ON "sys_secret_bindings" ("binding_group_id");
