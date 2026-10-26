-- V90: 新增知识库业务审计表（PostgreSQL）
--
-- 与 MySQL V190 对齐。V89 已被「归属人」迁移占用，本表顺排 V90。
--
-- 独立于访问日志 `ai_agent_access_logs` 的原因见 MySQL 版本注释：
-- 业务审计需要长期保留，且 dataset_id 必须可索引 —— 埋在 JSON 文本里查不动。

CREATE TABLE IF NOT EXISTS ai_agent_knowledge_audit_logs (
    id BIGSERIAL PRIMARY KEY,
    dataset_id VARCHAR(64) NOT NULL,
    action VARCHAR(64) NOT NULL,
    user_id BIGINT NULL,
    user_name VARCHAR(64) NULL,
    status_code INTEGER NOT NULL DEFAULT 200,
    detail JSONB NULL,
    client_ip VARCHAR(50) NULL,
    error_message TEXT NULL,
    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_kal_dataset_id ON ai_agent_knowledge_audit_logs (dataset_id);
CREATE INDEX IF NOT EXISTS idx_kal_dataset_created ON ai_agent_knowledge_audit_logs (dataset_id, created_at);
CREATE INDEX IF NOT EXISTS idx_kal_action ON ai_agent_knowledge_audit_logs (action, created_at);
CREATE INDEX IF NOT EXISTS idx_kal_user ON ai_agent_knowledge_audit_logs (user_name);
CREATE INDEX IF NOT EXISTS idx_kal_created ON ai_agent_knowledge_audit_logs (created_at);

COMMENT ON TABLE ai_agent_knowledge_audit_logs IS '知识库业务审计日志';
COMMENT ON COLUMN ai_agent_knowledge_audit_logs.dataset_id IS '知识库（RAGFlow Dataset）ID';
COMMENT ON COLUMN ai_agent_knowledge_audit_logs.action IS '操作码：datasets:create / documents:upload / datasets:grant ...';
COMMENT ON COLUMN ai_agent_knowledge_audit_logs.user_id IS '操作人用户 ID';
COMMENT ON COLUMN ai_agent_knowledge_audit_logs.user_name IS '操作人账号快照';
COMMENT ON COLUMN ai_agent_knowledge_audit_logs.status_code IS 'HTTP 状态码（>=400 即失败）';
COMMENT ON COLUMN ai_agent_knowledge_audit_logs.detail IS '操作细节（文件名/文档数/授权对象/变更前后）';
