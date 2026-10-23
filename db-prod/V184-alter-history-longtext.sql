-- ----------------------------------------------------------------
-- V184: 对话历史长文本扩容 TEXT -> MEDIUMTEXT
--
-- 问题：ai_agent_execution_history 的 query / summary / reasoning_content 原为
--   TEXT（上限 65,535 字节）。开启思考模式后单轮推理可达数万字节（utf8mb4 下
--   中文 3 字节/字，约 2.1 万汉字即触顶），严格模式（MySQL 5.7+/8.0 默认）下
--   INSERT 直接命中 1406 Data too long，整轮历史从 /chat/history 消失；
--   非严格模式下则被静默截断，推理尾部丢失。
--
-- 处理：三列统一升到 MEDIUMTEXT（16,777,215 字节）。应用侧同步为
--   Text().with_variant(MEDIUMTEXT, "mysql")，见 app/models/audit.py；
--   写入前另有 1MiB 保底截断与一次降级重试，见 app/services/ai/audit.py。
--
-- 说明：PostgreSQL 的 TEXT 本身无长度上限，db-prod-pg 无需对应变更。
--
-- 注意：MODIFY COLUMN 改变列类型会走 COPY 算法重建该表，请在业务低峰执行；
--   表很大时可先 `SELECT COUNT(*)` 评估，并预留等量磁盘空间。
-- ----------------------------------------------------------------

ALTER TABLE `ai_agent_execution_history`
  MODIFY COLUMN `query` MEDIUMTEXT NULL COMMENT '用户提问',
  MODIFY COLUMN `summary` MEDIUMTEXT NULL COMMENT 'AI 响应/总结',
  MODIFY COLUMN `reasoning_content` MEDIUMTEXT NULL COMMENT '模型推理过程';
