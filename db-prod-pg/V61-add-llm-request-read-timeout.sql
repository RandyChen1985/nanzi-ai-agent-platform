-- V61: 增加大模型 HTTP 请求的读取超时（chunk 间隔）配置（PostgreSQL）
--
-- 背景：此前未覆盖 OpenAI SDK 默认超时（Timeout(timeout=600, connect=5.0)），
-- 单个请求在服务端静默时最长等 600s，恰好等于 producer 看门狗上限，导致业务层
-- 重试来不及执行。默认值 180s 可保证「2 次尝试共 360s」落在看门狗预算内。
--
-- 分组与 llm_model_name / llm_temperature 一致（'agent'，即「智能体设置」）：
-- V68 已废弃独立的 llm 分组。
INSERT INTO "system_configs"
    ("key", "value", "description", "category", "is_secret")
VALUES
    (
        'llm_request_read_timeout',
        '180',
        '大模型 HTTP 请求的读取超时（秒）：相邻两次数据到达的最大间隔，默认 180 秒，范围 30-300。设为过大会导致模型重试来不及执行。',
        'agent',
        FALSE
    )
ON CONFLICT ("key") DO UPDATE
SET
    "description" = EXCLUDED."description",
    "category" = EXCLUDED."category",
    "is_secret" = EXCLUDED."is_secret",
    "updated_at" = CURRENT_TIMESTAMP;
