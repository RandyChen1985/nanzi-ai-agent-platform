-- V93: 知识库检索范围门禁（Scope Gate）配置项
--
-- 与 MySQL 侧 db-prod/V193 保持同一组键与默认值。
--
-- 为什么必须落表：代码里三个值都有兜底（true / 5 / 3），不插也能跑；但系统配置页
-- 是读表渲染的，不落表管理员就改不了阈值 —— 等于把 5 写死在代码里。
--
-- 冲突时只更新 description 与 category，不覆盖 value：升级不能冲掉管理员调过的值。

INSERT INTO "system_configs" ("key", "value", "description", "category", "is_secret") VALUES
    ('knowledge_scope_gate_enabled', 'true', '知识库检索范围确认总开关：可检索库较多且用户未指定时先让用户确认范围。填 true/false，默认 true。', 'knowledge', FALSE),
    ('knowledge_scope_gate_threshold', '5', '范围确认触发阈值：合并智能体绑定后实际参与检索的库数超过该值才提示。默认 5。', 'knowledge', FALSE),
    ('knowledge_scope_gate_ranked_limit', '3', '范围确认卡中「最相关库」一项包含的库数量，按问题与库名称、描述的匹配度挑选。默认 3。', 'knowledge', FALSE)
ON CONFLICT ("key") DO UPDATE
SET
    "description" = EXCLUDED."description",
    "category" = EXCLUDED."category";
