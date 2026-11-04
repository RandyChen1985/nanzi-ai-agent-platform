-- V204: 委派上限放进系统配置（MySQL）
--
-- 背景：`sub_agent_batch_call` 一次最多 4 个任务、同一子智能体每轮最多 2 次，
-- 这两个值此前是 `app/services/ai/tools/agent_delegate_tool.py` 里的硬编码常量，
-- 既没有配置行也没有管理端入口，运维无法按部署规模调整；而 4 路并发委派等于
-- 4 个完整子智能体会话同时跑，模型 QPS、token 消耗与上游限流压力是同步放大的，
-- 不同规模的环境需要的值并不相同。
--
-- 值语义：
-- - delegation_max_batch_calls：1-20，默认 4（一次 batch 的任务数）；
-- - delegation_max_calls_per_agent：1-10，默认 2（同一子智能体每轮调用次数）。
-- 后端在保存时校验范围，并在每次委派前从配置同步（改完无需重启）。
--
-- 采用 ON DUPLICATE KEY UPDATE 保证可重跑：若键已存在，重跑只纠正分组与描述，
-- `value` 不动，避免覆盖运维已调整过的值。
INSERT INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
(
    'delegation_max_batch_calls',
    '4',
    '一次并行委派（sub_agent_batch_call）最多包含的任务数。默认 4，范围 1-20。调大后同时运行的子智能体会话变多，模型并发、token 消耗与上游限流压力会同步放大，请按机器与模型配额谨慎调整。',
    'agent',
    0
),
(
    'delegation_max_calls_per_agent',
    '2',
    '同一轮对话里对同一个子智能体的调用次数上限。默认 2，范围 1-10。它同时约束串行与批量委派：达到上限后该子任务直接返回错误，避免模型反复委派同一个专家。',
    'agent',
    0
)
ON DUPLICATE KEY UPDATE
    `description` = VALUES(`description`),
    `category` = VALUES(`category`);
