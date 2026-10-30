-- V201: 会话历史固化智能体身份快照（MySQL）
--
-- 背景：ai_agent_execution_history 原本只落 agent_id，展示名靠 /chat/history 实时查
-- ai_agents 回填。而那次查询走的是 AgentManagerService.list_agents，带着可见性过滤：
-- 非管理员只能看到自己创建的智能体。于是普通用户打开「主助手」的历史会话时查不到
-- 名字，界面上「谁回答的」那一条直接变空白；智能体被删除或降权时同理。
--
-- 修复分两层：写入时在这里固化快照（本迁移），读取按
--   行内快照 → Redis 快照 → ai_agents 直查（不套可见性）
-- 依次回落。
--
-- 存量行保持 NULL，不回填：历史数据的 agent_id 仍然可用，读取时会走兜底链路补齐。

ALTER TABLE `ai_agent_execution_history`
    ADD COLUMN `agent_name` VARCHAR(255) NULL COMMENT '智能体 slug 标识名快照（写入时刻固化）',
    ADD COLUMN `agent_display_name` VARCHAR(255) NULL COMMENT '智能体显示名快照（写入时刻固化）';
