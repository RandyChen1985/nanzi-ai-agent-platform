-- V219：对齐结构化凭据的关联列、密文容量与等待状态索引。
-- 前置：V218 或已完成同等结构的旧版凭据迁移。不得修改已执行的 V218 代替本文件。
-- 只对齐 profile 两个 MCP ID 列；保留凭据账号、类型键、密文表的二进制比较语义。

SET @mcp_tool_id_collation = (
    SELECT COLLATION_NAME FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sys_mcp_tool_cache' AND COLUMN_NAME='id'
        AND CHARACTER_SET_NAME='utf8mb4' AND CHARACTER_MAXIMUM_LENGTH=36
);
SET @mcp_server_id_collation = (
    SELECT COLLATION_NAME FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sys_mcp_tool_cache' AND COLUMN_NAME='server_id'
        AND CHARACTER_SET_NAME='utf8mb4' AND CHARACTER_MAXIMUM_LENGTH=36
);
SET @task_wait_index = (
    SELECT GROUP_CONCAT(
        IF(NON_UNIQUE=1 AND SUB_PART IS NULL AND IS_VISIBLE='YES', COALESCE(COLUMN_NAME,'?'), '?')
        ORDER BY SEQ_IN_INDEX
    ) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sys_task_credential_waits'
        AND INDEX_NAME='ix_task_credential_waits_task'
);
SET @use_grant_index = (
    SELECT GROUP_CONCAT(
        IF(NON_UNIQUE=1 AND SUB_PART IS NULL AND IS_VISIBLE='YES', COALESCE(COLUMN_NAME,'?'), '?')
        ORDER BY SEQ_IN_INDEX
    ) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sys_mcp_credential_use_grants'
        AND INDEX_NAME='ix_mcp_credential_use_grants_secret'
);

-- 预检使用连接级临时表。CHECK 失败会在任何业务表 ALTER 前报出不兼容对象。
CREATE TEMPORARY TABLE nanzi_v219_credential_preflight (
    mcp_ids_ok BOOLEAN NOT NULL,
    mcp_tool_id_unique BOOLEAN NOT NULL,
    wait_index_ok BOOLEAN NOT NULL,
    grant_index_ok BOOLEAN NOT NULL,
    CONSTRAINT v219_requires_utf8mb4_mcp_id_columns CHECK (mcp_ids_ok=TRUE),
    CONSTRAINT v219_mcp_tool_id_collisions CHECK (mcp_tool_id_unique=TRUE),
    CONSTRAINT v219_ix_task_credential_waits_task_incompatible CHECK (wait_index_ok=TRUE),
    CONSTRAINT v219_ix_mcp_credential_use_grants_secret_incompatible CHECK (grant_index_ok=TRUE)
);
INSERT INTO nanzi_v219_credential_preflight VALUES (
    @mcp_tool_id_collation IS NOT NULL AND @mcp_server_id_collation IS NOT NULL AND
    (SELECT COUNT(*)=2 FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sys_mcp_credential_profiles'
        AND COLUMN_NAME IN ('tool_id','server_id') AND DATA_TYPE='varchar'
        AND CHARACTER_MAXIMUM_LENGTH=36 AND IS_NULLABLE='NO' AND COLUMN_DEFAULT IS NULL),
    TRUE,
    @task_wait_index IS NULL OR @task_wait_index='task_id,status,expires_at',
    @use_grant_index IS NULL OR @use_grant_index='secret_id'
);

-- 旧二进制主键可能包含按目标排序规则等价的 ID。提前报错，避免迁移器把 ALTER 的
-- 1062 当成可跳过的重复种子记录。排序规则已一致时不扫描。
SET @credential_ddl = IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sys_mcp_credential_profiles'
            AND COLUMN_NAME='tool_id' AND COLLATION_NAME=@mcp_tool_id_collation),
    'SET @mcp_tool_id_unique = TRUE',
    CONCAT('SELECT NOT EXISTS (SELECT 1 FROM sys_mcp_credential_profiles ',
        'GROUP BY CONVERT(tool_id USING utf8mb4) COLLATE ', @mcp_tool_id_collation,
        ' HAVING COUNT(*) > 1) INTO @mcp_tool_id_unique')
);
PREPARE credential_stmt FROM @credential_ddl;
EXECUTE credential_stmt;
DEALLOCATE PREPARE credential_stmt;
UPDATE nanzi_v219_credential_preflight SET mcp_tool_id_unique=@mcp_tool_id_unique;
DROP TEMPORARY TABLE nanzi_v219_credential_preflight;

SET @credential_ddl = IF(
    (SELECT COUNT(*)=2 FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sys_mcp_credential_profiles' AND
        ((COLUMN_NAME='tool_id' AND COLLATION_NAME=@mcp_tool_id_collation) OR
         (COLUMN_NAME='server_id' AND COLLATION_NAME=@mcp_server_id_collation))),
    'SELECT 1',
    CONCAT('ALTER TABLE sys_mcp_credential_profiles ',
        'MODIFY tool_id VARCHAR(36) CHARACTER SET utf8mb4 COLLATE ', @mcp_tool_id_collation,
        ' NOT NULL, MODIFY server_id VARCHAR(36) CHARACTER SET utf8mb4 COLLATE ', @mcp_server_id_collation, ' NOT NULL')
);
PREPARE credential_stmt FROM @credential_ddl;
EXECUTE credential_stmt;
DEALLOCATE PREPARE credential_stmt;

SET @credential_ddl = IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
        AND TABLE_NAME='sys_secret_structured_payloads' AND COLUMN_NAME='payload_encrypted'
        AND DATA_TYPE='longtext'),
    'SELECT 1', 'ALTER TABLE sys_secret_structured_payloads MODIFY payload_encrypted LONGTEXT NOT NULL'
);
PREPARE credential_stmt FROM @credential_ddl;
EXECUTE credential_stmt;
DEALLOCATE PREPARE credential_stmt;

SET @credential_ddl = IF(
    (SELECT COUNT(*)=4 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
        AND TABLE_NAME='sys_task_credential_waits' AND DATA_TYPE='datetime' AND DATETIME_PRECISION=6
        AND COLUMN_NAME IN ('expires_at','created_at','resume_lease_until','resolved_at')),
    'SELECT 1',
    'ALTER TABLE sys_task_credential_waits MODIFY expires_at DATETIME(6) NOT NULL, MODIFY created_at DATETIME(6) NOT NULL, MODIFY resume_lease_until DATETIME(6) NULL, MODIFY resolved_at DATETIME(6) NULL'
);
PREPARE credential_stmt FROM @credential_ddl;
EXECUTE credential_stmt;
DEALLOCATE PREPARE credential_stmt;

SET @credential_ddl = IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
        AND TABLE_NAME='sys_mcp_credential_use_grants' AND COLUMN_NAME='expires_at' AND DATETIME_PRECISION=6),
    'SELECT 1', 'ALTER TABLE sys_mcp_credential_use_grants MODIFY expires_at DATETIME(6) NULL'
);
PREPARE credential_stmt FROM @credential_ddl;
EXECUTE credential_stmt;
DEALLOCATE PREPARE credential_stmt;

SET @credential_ddl = IF(@task_wait_index IS NULL,
    'CREATE INDEX ix_task_credential_waits_task ON sys_task_credential_waits (task_id, status, expires_at)',
    'SELECT 1'
);
PREPARE credential_stmt FROM @credential_ddl;
EXECUTE credential_stmt;
DEALLOCATE PREPARE credential_stmt;

SET @credential_ddl = IF(@use_grant_index IS NULL,
    'CREATE INDEX ix_mcp_credential_use_grants_secret ON sys_mcp_credential_use_grants (secret_id)',
    'SELECT 1'
);
PREPARE credential_stmt FROM @credential_ddl;
EXECUTE credential_stmt;
DEALLOCATE PREPARE credential_stmt;
