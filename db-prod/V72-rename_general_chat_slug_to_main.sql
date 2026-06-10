-- V72: 将兜底通用助手的路由 slug 从 general-chat 重命名为 main
-- Date: 2026-06-10
-- Purpose:
--   对外产品名与代码层 Assistant 命名对齐；Router 兜底已支持 assistant / main / general-chat 多 slug。
--   本迁移将系统内置通用助手 (sys-agent-chat) 的 ai_agents.name 统一为 main。
-- 影响: 仅更新 ai_agents.name；agent_id (sys-agent-chat) 与版本配置不变。
-- 幂等: 若已是 main 或不存在 general-chat 记录则跳过。

UPDATE `ai_agents`
SET `name` = 'main',
    `updated_at` = CURRENT_TIMESTAMP
WHERE `name` = 'general-chat'
  AND `id` = 'sys-agent-chat';
