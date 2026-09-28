-- V160: 增加大模型 HTTP 请求的读取超时（chunk 间隔）配置（MySQL）
--
-- 背景：此前未覆盖 OpenAI SDK 默认超时（Timeout(timeout=600, connect=5.0)），
-- 单个请求在服务端静默时最长等 600s，恰好等于 producer 看门狗上限，导致业务层
-- 重试来不及执行。默认值 180s 可保证「2 次尝试共 360s」落在看门狗预算内。
--
-- 分组与 llm_model_name / llm_temperature 一致（'agent'，即「智能体设置」）：
-- V68 已废弃独立的 llm 分组。
--
-- 采用 ON DUPLICATE KEY UPDATE 以保证可重跑：若该键已存在（例如早期版本写入了
-- 错误的分组），重跑本迁移会把分组与描述纠正回来；`value` 不动，
-- 避免覆盖运维已调整过的值。
INSERT INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
(
    'llm_request_read_timeout',
    '180',
    '大模型 HTTP 请求的读取超时（秒）：相邻两次数据到达的最大间隔，默认 180 秒，范围 30-300。设为过大会导致模型重试来不及执行。',
    'agent',
    0
)
ON DUPLICATE KEY UPDATE
    `description` = VALUES(`description`),
    `category` = VALUES(`category`);
