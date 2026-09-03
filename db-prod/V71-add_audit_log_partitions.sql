-- ----------------------------
-- V71: 对审计日志与执行 Trace 表进行 MySQL Range 分区改造
--
-- 结构变更先读取 information_schema，再通过同一会话中的 PREPARE 条件执行：
-- 首次执行只做缺失变更，已完成库可重复执行，客户端断线后可从中间状态继续。
-- ----------------------------

-- 1. 改造 ai_agent_access_logs 表

SET @v71_sql = IF(EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
      AND column_name = 'created_at' AND LOWER(data_type) = 'datetime'
), 'SELECT 1',
'ALTER TABLE `ai_agent_access_logs` MODIFY COLUMN `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

SET @v71_sql = IF(
    (SELECT COUNT(*) FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
       AND index_name = 'PRIMARY') = 1
    AND EXISTS (SELECT 1 FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
       AND index_name = 'PRIMARY' AND column_name = 'id' AND seq_in_index = 1)
    AND EXISTS (SELECT 1 FROM information_schema.columns
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
       AND column_name = 'id' AND FIND_IN_SET('auto_increment', LOWER(extra)) > 0),
    'ALTER TABLE `ai_agent_access_logs` MODIFY COLUMN `id` BIGINT NOT NULL', 'SELECT 1');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

SET @v71_sql = IF(
    (SELECT COUNT(*) FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
       AND index_name = 'PRIMARY') = 1
    AND EXISTS (SELECT 1 FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
       AND index_name = 'PRIMARY' AND column_name = 'id' AND seq_in_index = 1),
    'ALTER TABLE `ai_agent_access_logs` DROP PRIMARY KEY', 'SELECT 1');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

SET @v71_sql = IF(
    (SELECT COUNT(*) FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
       AND index_name = 'PRIMARY') = 2
    AND EXISTS (SELECT 1 FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
       AND index_name = 'PRIMARY' AND column_name = 'id' AND seq_in_index = 1)
    AND EXISTS (SELECT 1 FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
       AND index_name = 'PRIMARY' AND column_name = 'created_at' AND seq_in_index = 2),
    'SELECT 1', 'ALTER TABLE `ai_agent_access_logs` ADD PRIMARY KEY (`id`, `created_at`)');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

SET @v71_sql = IF(EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
      AND column_name = 'id' AND FIND_IN_SET('auto_increment', LOWER(extra)) > 0
), 'SELECT 1',
'ALTER TABLE `ai_agent_access_logs` MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

SET @v71_sql = IF(NOT EXISTS (
    SELECT 1 FROM information_schema.partitions
    WHERE table_schema = DATABASE() AND table_name = 'ai_agent_access_logs'
      AND partition_name IS NOT NULL
), 'ALTER TABLE `ai_agent_access_logs` PARTITION BY RANGE COLUMNS(`created_at`) (
    PARTITION p202605 VALUES LESS THAN (''2026-06-01 00:00:00''),
    PARTITION p202606 VALUES LESS THAN (''2026-07-01 00:00:00''),
    PARTITION p202607 VALUES LESS THAN (''2026-08-01 00:00:00''),
    PARTITION pmax VALUES LESS THAN MAXVALUE
)', 'SELECT 1');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

-- 2. 改造 ai_agent_execution_traces 表

SET @v71_sql = IF(EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
      AND column_name = 'created_at' AND LOWER(data_type) = 'datetime'
), 'SELECT 1',
'ALTER TABLE `ai_agent_execution_traces` MODIFY COLUMN `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

SET @v71_sql = IF(
    (SELECT COUNT(*) FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
       AND index_name = 'PRIMARY') = 1
    AND EXISTS (SELECT 1 FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
       AND index_name = 'PRIMARY' AND column_name = 'id' AND seq_in_index = 1)
    AND EXISTS (SELECT 1 FROM information_schema.columns
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
       AND column_name = 'id' AND FIND_IN_SET('auto_increment', LOWER(extra)) > 0),
    'ALTER TABLE `ai_agent_execution_traces` MODIFY COLUMN `id` BIGINT NOT NULL', 'SELECT 1');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

SET @v71_sql = IF(
    (SELECT COUNT(*) FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
       AND index_name = 'PRIMARY') = 1
    AND EXISTS (SELECT 1 FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
       AND index_name = 'PRIMARY' AND column_name = 'id' AND seq_in_index = 1),
    'ALTER TABLE `ai_agent_execution_traces` DROP PRIMARY KEY', 'SELECT 1');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

SET @v71_sql = IF(
    (SELECT COUNT(*) FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
       AND index_name = 'PRIMARY') = 2
    AND EXISTS (SELECT 1 FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
       AND index_name = 'PRIMARY' AND column_name = 'id' AND seq_in_index = 1)
    AND EXISTS (SELECT 1 FROM information_schema.statistics
     WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
       AND index_name = 'PRIMARY' AND column_name = 'created_at' AND seq_in_index = 2),
    'SELECT 1', 'ALTER TABLE `ai_agent_execution_traces` ADD PRIMARY KEY (`id`, `created_at`)');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

SET @v71_sql = IF(EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
      AND column_name = 'id' AND FIND_IN_SET('auto_increment', LOWER(extra)) > 0
), 'SELECT 1',
'ALTER TABLE `ai_agent_execution_traces` MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

SET @v71_sql = IF(NOT EXISTS (
    SELECT 1 FROM information_schema.partitions
    WHERE table_schema = DATABASE() AND table_name = 'ai_agent_execution_traces'
      AND partition_name IS NOT NULL
), 'ALTER TABLE `ai_agent_execution_traces` PARTITION BY RANGE COLUMNS(`created_at`) (
    PARTITION p202605 VALUES LESS THAN (''2026-06-01 00:00:00''),
    PARTITION p202606 VALUES LESS THAN (''2026-07-01 00:00:00''),
    PARTITION p202607 VALUES LESS THAN (''2026-08-01 00:00:00''),
    PARTITION pmax VALUES LESS THAN MAXVALUE
)', 'SELECT 1');
PREPARE v71_stmt FROM @v71_sql;
EXECUTE v71_stmt;
DEALLOCATE PREPARE v71_stmt;

-- 3. 注册系统配置参数：日志保留天数
INSERT IGNORE INTO `system_configs`
    (`key`, `value`, `description`, `category`, `is_secret`)
VALUES
    ('audit_log_retention_days', '90', '系统日志和智能体步骤 Trace 保留期限天数', 'other', 0);
