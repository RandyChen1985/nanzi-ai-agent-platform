-- V212: 产物支持「父会话归属」。
-- 委派子代理发布产物时，artifact 的 conversation_id 仍是实际产生该文件的子会话
-- （保留审计口径），同时写入 parent_conversation_id / parent_trace_id，
-- 让主会话的产物列表与消息角标可以直接聚合到这次委派，主智能体无需二次发布。
ALTER TABLE `ai_artifacts`
  ADD COLUMN `parent_conversation_id` VARCHAR(128) DEFAULT NULL COMMENT '产物可见的父会话 ID（子代理委派产物）' AFTER `conversation_id`,
  ADD COLUMN `parent_trace_id` VARCHAR(64) DEFAULT NULL COMMENT '父会话执行 trace_id（子代理委派产物）' AFTER `trace_id`,
  ADD KEY `idx_ai_artifacts_parent_conversation_id` (`parent_conversation_id`),
  ADD KEY `idx_ai_artifacts_parent_trace_id` (`parent_trace_id`);
