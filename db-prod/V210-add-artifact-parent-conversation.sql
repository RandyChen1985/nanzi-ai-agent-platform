-- V210: 产物支持「父会话归属」。
--
-- 委派子代理发布产物时，artifact 的 conversation_id 仍是实际产生该文件的子会话
-- （保留审计口径），同时写入 parent_conversation_id / parent_trace_id，
-- 让主会话的产物列表与消息角标可以直接聚合到这次委派，主智能体无需二次发布。
--
-- 幂等：MySQL 8.0 没有 `ADD COLUMN IF NOT EXISTS`，而 `apply_sql.py` 没有版本
-- 记录表、整文件重放，只按语句粒度忽略错误码（1060 列已存在 / 1061 索引已存在）。
--
-- 四条 ALTER 原本写成一条语句，而多操作的 ALTER 是**整体原子**的：只要其中有
-- 任何一项已存在，整条就报错回滚，排在后面的列和索引一起建不上——比如有人在
-- 别的分支手工加过一列，这个文件就再也跑不完整了。拆成四条，每条独立成败。

ALTER TABLE `ai_artifacts`
  ADD COLUMN `parent_conversation_id` VARCHAR(128) DEFAULT NULL
    COMMENT '产物可见的父会话 ID（子代理委派产物）' AFTER `conversation_id`;

ALTER TABLE `ai_artifacts`
  ADD COLUMN `parent_trace_id` VARCHAR(64) DEFAULT NULL
    COMMENT '父会话执行 trace_id（子代理委派产物）' AFTER `trace_id`;

ALTER TABLE `ai_artifacts`
  ADD KEY `idx_ai_artifacts_parent_conversation_id` (`parent_conversation_id`);

ALTER TABLE `ai_artifacts`
  ADD KEY `idx_ai_artifacts_parent_trace_id` (`parent_trace_id`);
