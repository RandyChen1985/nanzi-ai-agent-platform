-- V120: 绑定表加 `binding_group_id`——记录「这条绑定属于哪一次多值配置」。（PostgreSQL）
--
-- 与 MySQL 侧 db-prod/V220-add-secret-binding-group-id.sql 同一结构、同一语义。
--
-- 背景：同一个 MCP / API 工具的参数有两种配法，落库后完全同形——都是「N 个 slot
-- 各一条 sys_secret_bindings 行」，库里分辨不出「一次多值保存」和「分别单值配了
-- N 次」。于是：单值页把多值配的规则显示成 N 条独立规则；单值页改其中一条就把
-- 多值结构拆散而系统无感；注入按 slot 逐个解析，同一条规则重复执行 N 遍。
-- 前两条已修（展示与改规则都按组处理）。第三条里真正重复的只有「可见集合」查询
-- ——它只取决于当前用户、与槽位无关，却每个槽位重查一次（还带权限与场景过滤）。
-- 已用单次调用级的 visible_cache 消掉（resolver.resolve 的入参，注入层每个入口
-- 传同一份；作用域是一次工具调用，不缓存明文）。规则本身仍按 slot 各算一遍：
-- 不同槽位的 value_path 不同，且每个槽位都要各自走场景/域名/授权灌门并记流水，
-- 合并反而说不清「哪个槽位被谁拦下」。
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

-- 注入侧与展示侧都要按组取同批内的其它 slot，给个普通索引。
CREATE INDEX IF NOT EXISTS "idx_binding_group"
    ON "sys_secret_bindings" ("binding_group_id");
