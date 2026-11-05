-- V104: 委派上限放进系统配置（PostgreSQL）
--
-- 与 db-prod/V204-add-delegation-limit-configs.sql 对应：`sub_agent_batch_call`
-- 一次最多 4 个任务、同一子智能体每轮最多 2 次，此前是硬编码常量，运维无法调整。
--
-- 值语义：
-- - delegation_max_batch_calls：1-20，默认 4；
-- - delegation_max_calls_per_agent：1-10，默认 2。
-- 后端保存时校验范围，并在每次委派前从配置同步（改完无需重启）。
--
-- ON CONFLICT 保证可重跑：值已存在时只纠正描述/分组，不覆盖已调整的 value。
INSERT INTO "system_configs"
    ("key", "value", "description", "category", "is_secret")
VALUES
    (
        'delegation_max_batch_calls',
        '4',
        '一次并行委派（sub_agent_batch_call）最多包含的任务数。默认 4，范围 1-20。调大后同时运行的子智能体会话变多，模型并发、token 消耗与上游限流压力会同步放大，请按机器与模型配额谨慎调整。',
        'agent',
        FALSE
    ),
    (
        'delegation_max_calls_per_agent',
        '2',
        '同一轮对话里对同一个子智能体的调用次数上限。默认 2，范围 1-10。它同时约束串行与批量委派：达到上限后该子任务直接返回错误，避免模型反复委派同一个专家。',
        'agent',
        FALSE
    )
ON CONFLICT ("key") DO UPDATE
SET
    "description" = EXCLUDED."description",
    "category" = EXCLUDED."category",
    "is_secret" = EXCLUDED."is_secret",
    "updated_at" = CURRENT_TIMESTAMP;
