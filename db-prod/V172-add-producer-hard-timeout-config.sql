-- V172: 把 producer 硬超时看门狗放进系统配置（MySQL）
--
-- 背景：此前该值只存在于代码默认值（600s），既没有配置行也没有管理端入口，运维无法
-- 调整；而它与 agent_max_toolcall_timeout 存在一处容易踩的冲突：看门狗约束的是
-- 「整轮」总时长（含每一次工具调用与模型调用），因此工具超时配到 600s 以上并不会生效
-- ——工具有没有超时都轮不到它触发，整轮先被看门狗取消并释放会话锁。
--
-- 值语义：0 表示关闭看门狗；其余必须在 60-3600 秒之间（后端校验强制）。
--
-- 分组与 agent_max_toolcall_timeout 一致（'agent'，即「智能体设置」），便于管理端在
-- 同一组内并排查看两者的关系。
--
-- 采用 ON DUPLICATE KEY UPDATE 以保证可重跑：若该键已存在，重跑只纠正分组与描述，
-- `value` 不动，避免覆盖运维已调整过的值。
INSERT INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
(
    'agent_chat_producer_hard_timeout_seconds',
    '600',
    'producer 硬超时看门狗（秒）：整轮对话的总时长兜底，超时会取消本轮并释放会话锁。默认 600，范围 60-3600，填 0 表示关闭。注意它约束的是整轮（含所有工具调用与模型调用），因此 agent_max_toolcall_timeout 配得比它大不会生效——工具还没到自己的超时，整轮就先被取消。',
    'agent',
    0
)
ON DUPLICATE KEY UPDATE
    `description` = VALUES(`description`),
    `category` = VALUES(`category`);
