-- V212: 删掉已废弃的槽位唯一索引 `uk_binding_slot`。
--
-- V211 建好的 `uk_binding_slot_strategy` 覆盖了旧索引的约束：前缀完全相同
-- （consumer_type, consumer_id, slot, bound_by_user_id）且多带了 strategy，更严。
-- 留着旧索引只会让「同一槽位再插一条规则绑定」被它挡住——规则绑定与回退用的
-- 固定绑定在旧索引下互相冲突。
--
-- 执行顺序不能反：必须先跑 V211 建好新索引，再跑本文件。反过来是「旧索引已删、
-- 新索引未建」的裸窗口，并发写入可能插进重复的槽位行。
--
-- 幂等：索引不存在时 MySQL 报 1091（Can't DROP ...），`apply_sql.py` 已把
-- 1091 列入可忽略错误码，重复执行本文件无副作用。

ALTER TABLE `sys_secret_bindings` DROP INDEX `uk_binding_slot`;
