-- V199: 增加 Agent 渐进式收尾开关（MySQL）
-- 开启后，步数将尽时中间件会注入收尾提示并禁用工具调用，
-- 让模型基于已有信息作答，而不是直接输出「达到最大执行步骤」模板文案。
INSERT IGNORE INTO system_configs
    (`key`, `value`, `description`, `category`, `is_secret`, `created_at`, `updated_at`)
VALUES
    (
        'agent_graceful_finish_enabled',
        'true',
        '步数将尽时是否让模型基于已有信息收尾（替代直接中断）。true=开启，false=恢复输出「达到最大执行步骤」。',
        'agent',
        0,
        NOW(6),
        NOW(6)
    );
