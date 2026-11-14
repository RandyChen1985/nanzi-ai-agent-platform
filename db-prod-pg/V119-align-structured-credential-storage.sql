-- V119：补齐结构化凭据等待及旧授权清理索引。
-- 前置：V118 或同等旧版凭据迁移。PostgreSQL 的 TEXT 和 TIMESTAMP 已支持目标容量与精度。
DO $credential_storage$
DECLARE
    target RECORD;
    existing_index REGCLASS;
BEGIN
    FOR target IN SELECT * FROM (VALUES
        ('sys_task_credential_waits', 'ix_task_credential_waits_task', ARRAY['task_id','status','expires_at']),
        ('sys_mcp_credential_use_grants', 'ix_mcp_credential_use_grants_secret', ARRAY['secret_id'])
    ) AS indexes(table_name, index_name, column_names) LOOP
        existing_index := to_regclass(format('%I.%I', current_schema(), target.index_name));
        IF existing_index IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM pg_index i
            JOIN pg_class idx ON idx.oid=i.indexrelid
            JOIN pg_am am ON am.oid=idx.relam
            WHERE i.indexrelid=existing_index AND i.indrelid=to_regclass(format('%I.%I', current_schema(), target.table_name))
                AND i.indisvalid AND NOT i.indisunique AND am.amname='btree'
                AND i.indpred IS NULL AND i.indexprs IS NULL
                AND ARRAY(SELECT pg_get_indexdef(existing_index, n, TRUE)
                    FROM generate_series(1, i.indnatts) n)=target.column_names
        ) THEN
            RAISE EXCEPTION '索引 % 的定义不兼容，请先核对历史库结构', target.index_name;
        END IF;
    END LOOP;
END
$credential_storage$;

CREATE INDEX IF NOT EXISTS ix_task_credential_waits_task
    ON sys_task_credential_waits (task_id, status, expires_at);
CREATE INDEX IF NOT EXISTS ix_mcp_credential_use_grants_secret
    ON sys_mcp_credential_use_grants (secret_id);
