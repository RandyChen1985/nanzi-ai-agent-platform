-- V92: 资源广场（PostgreSQL）—— 申请表区分资源类型 + 数据集业务审计
--
-- 与 MySQL V192 对齐；V91 已被知识治理表占用，本迁移顺排 V92。
-- 两项变更的原因见 MySQL 版本注释，这里只保留必要说明：
--   1) resource_type 让知识库与数据集共用一条「申请 → 审批 → 落授权」链路，
--      统一审批页才能按时间混排分页；存量行默认 dataset（都是知识库）。
--   2) 数据集业务审计独立成表：访问日志只查 90 天且 resource_id 埋在 JSON 里，
--      ResourcePermission 又只有当前状态没有历史。
--   3) 数据集目录总开关要在 system_configs 里有种子行，否则系统配置页看不到它。

ALTER TABLE ai_agent_knowledge_access_requests
    ADD COLUMN IF NOT EXISTS resource_type VARCHAR(20) NOT NULL DEFAULT 'dataset';

COMMENT ON COLUMN ai_agent_knowledge_access_requests.resource_type IS
    '资源类型: dataset=知识库 / metadata=数据集';

CREATE INDEX IF NOT EXISTS idx_kbar_type_status
    ON ai_agent_knowledge_access_requests (resource_type, status);

CREATE TABLE IF NOT EXISTS ai_agent_metadata_audit_logs (
    id BIGSERIAL PRIMARY KEY,
    metadata_dataset_id BIGINT NOT NULL,
    action VARCHAR(64) NOT NULL,
    user_id BIGINT NULL,
    user_name VARCHAR(64) NULL,
    status_code INTEGER NOT NULL DEFAULT 200,
    detail JSONB NULL,
    client_ip VARCHAR(50) NULL,
    error_message TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mal_dataset_created
    ON ai_agent_metadata_audit_logs (metadata_dataset_id, created_at);
CREATE INDEX IF NOT EXISTS idx_mal_action_created
    ON ai_agent_metadata_audit_logs (action, created_at);
CREATE INDEX IF NOT EXISTS idx_mal_user
    ON ai_agent_metadata_audit_logs (user_id);

COMMENT ON TABLE ai_agent_metadata_audit_logs IS '数据集业务审计日志';
COMMENT ON COLUMN ai_agent_metadata_audit_logs.metadata_dataset_id IS 'meta_datasets.id';
COMMENT ON COLUMN ai_agent_metadata_audit_logs.action IS '操作码: metadata:request_access / metadata:grant / metadata:reject ...';
COMMENT ON COLUMN ai_agent_metadata_audit_logs.user_id IS '操作人用户 ID';
COMMENT ON COLUMN ai_agent_metadata_audit_logs.user_name IS '操作人账号快照';
COMMENT ON COLUMN ai_agent_metadata_audit_logs.status_code IS 'HTTP 状态码（>=400 即失败）';
COMMENT ON COLUMN ai_agent_metadata_audit_logs.detail IS '操作细节（申请ID/目标用户/审批意见/变更前后）';

-- 资源广场数据集目录开关（默认开启）
INSERT INTO "system_configs" ("key", "value", "description", "category", "is_secret")
VALUES (
  'resource_square_show_datasets',
  'true',
  '「我的工作台 › 资源广场」是否展示数据集目录（默认开启）。关闭后普通用户看不到数据集卡片，需要数据只能由管理员在用户管理中手工授权；已提交的申请不受影响。',
  'general',
  FALSE
)
ON CONFLICT ("key") DO NOTHING;
