-- V112: 删掉已废弃的槽位唯一索引 `uk_binding_slot`（PostgreSQL）。
--
-- V111 建好的 `uk_binding_slot_strategy` 覆盖了旧索引的约束：前缀完全相同
-- （consumer_type, consumer_id, slot, bound_by_user_id）且多带了 strategy，更严。
-- 留着旧索引只会让「同一槽位再插一条规则绑定」被它挡住——规则绑定与回退用的
-- 固定绑定在旧索引下互相冲突。
--
-- 执行顺序不能反：必须先跑 V111 建好新索引，再跑本文件。反过来是「旧索引已删、
-- 新索引未建」的裸窗口，并发写入可能插进重复的槽位行。
--
-- 幂等：PG 执行器不吞任何错误，而 `uk_binding_slot` 在 PG 里是 V109 用
-- `CONSTRAINT "uk_binding_slot" UNIQUE (...)` 建的——约束拥有的索引不能被
-- DROP INDEX 直接删（会报 2BP01: cannot drop index ... because constraint
-- ... requires it），必须先 DROP CONSTRAINT（删约束会连带删掉它拥有的索引）。
-- 因此两种删法都写上，各自带 IF EXISTS，两种情况都不报错：
--   * 由约束建的（V109 的正常路径）：DROP CONSTRAINT 生效，后面 DROP INDEX 是空操作
--   * 某环境是用 CREATE UNIQUE INDEX 手工建的（没有同名约束）：
--     DROP CONSTRAINT IF EXISTS 只发 NOTICE，由后面 DROP INDEX 收拾
-- 两者都不存在时两句都只发 NOTICE，本文件可以反复执行。

ALTER TABLE "sys_secret_bindings" DROP CONSTRAINT IF EXISTS "uk_binding_slot";

DROP INDEX IF EXISTS "uk_binding_slot";
