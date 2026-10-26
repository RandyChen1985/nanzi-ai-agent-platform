-- V91: 知识库治理三张表（访问申请 / 检索日志 / 健康快照）
--
-- 与 MySQL V191 对齐，放同一个文件的原因也相同：三张表是同一批治理能力
-- 一起上线的，彼此配套；分开只是多跑两次，回滚还得记顺序。
-- 三张表都是新增，不改动任何既有表。

-- 1) 访问申请
CREATE TABLE IF NOT EXISTS ai_agent_knowledge_access_requests (
    id BIGSERIAL PRIMARY KEY,
    dataset_id VARCHAR(64) NOT NULL,
    dataset_name VARCHAR(255) NULL,
    applicant_user_id BIGINT NOT NULL,
    applicant_user_name VARCHAR(64) NULL,
    applicant_dept_id BIGINT NULL,
    reason VARCHAR(1000) NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    reviewer_user_id BIGINT NULL,
    reviewer_user_name VARCHAR(64) NULL,
    review_comment VARCHAR(1000) NULL,
    reviewed_at TIMESTAMP NULL,
    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_kbar_dataset_status ON ai_agent_knowledge_access_requests (dataset_id, status);
CREATE INDEX IF NOT EXISTS idx_kbar_applicant_status ON ai_agent_knowledge_access_requests (applicant_user_id, status);
CREATE INDEX IF NOT EXISTS idx_kbar_status_created ON ai_agent_knowledge_access_requests (status, created_at);

COMMENT ON TABLE ai_agent_knowledge_access_requests IS '知识库访问申请';
COMMENT ON COLUMN ai_agent_knowledge_access_requests.status IS 'pending/approved/rejected/cancelled';
COMMENT ON COLUMN ai_agent_knowledge_access_requests.review_comment IS '审批意见（驳回原因给对方看）';

-- 2) 检索日志：记录「检索了什么、有没有命中」，与审计日志分表
CREATE TABLE IF NOT EXISTS ai_agent_knowledge_retrieval_logs (
    id BIGSERIAL PRIMARY KEY,
    query VARCHAR(512) NOT NULL,
    dataset_ids JSONB NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    top_score DOUBLE PRECISION NULL,
    latency_ms INTEGER NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'test',
    user_id BIGINT NULL,
    user_name VARCHAR(64) NULL,
    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_krl_created ON ai_agent_knowledge_retrieval_logs (created_at);
CREATE INDEX IF NOT EXISTS idx_krl_source_created ON ai_agent_knowledge_retrieval_logs (source, created_at);
CREATE INDEX IF NOT EXISTS idx_krl_hit_created ON ai_agent_knowledge_retrieval_logs (hit_count, created_at);

COMMENT ON TABLE ai_agent_knowledge_retrieval_logs IS '知识库检索日志';
COMMENT ON COLUMN ai_agent_knowledge_retrieval_logs.hit_count IS '命中切片数，0 即零结果';
COMMENT ON COLUMN ai_agent_knowledge_retrieval_logs.source IS 'test=检索测试 / agent=智能体调用';

-- 3) 健康快照：按天落表，同一天重复巡检是覆盖而不是堆重复行
CREATE TABLE IF NOT EXISTS ai_agent_knowledge_health_snapshots (
    id BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    dataset_id VARCHAR(64) NOT NULL,
    name VARCHAR(255) NULL,
    documents INTEGER NOT NULL DEFAULT 0,
    parsed INTEGER NOT NULL DEFAULT 0,
    parsing INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    unparsed INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0,
    is_missing BOOLEAN NOT NULL DEFAULT FALSE,
    visibility VARCHAR(32) NULL,
    owner_user_id BIGINT NULL,
    stale_days INTEGER NULL,
    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uix_khs_date_dataset UNIQUE (snapshot_date, dataset_id)
);

CREATE INDEX IF NOT EXISTS idx_khs_date ON ai_agent_knowledge_health_snapshots (snapshot_date);
CREATE INDEX IF NOT EXISTS idx_khs_dataset ON ai_agent_knowledge_health_snapshots (dataset_id);

COMMENT ON TABLE ai_agent_knowledge_health_snapshots IS '知识库健康巡检快照';
COMMENT ON COLUMN ai_agent_knowledge_health_snapshots.is_missing IS '引擎侧已不存在（本地仍有配置）';
COMMENT ON COLUMN ai_agent_knowledge_health_snapshots.failed IS '解析失败数';

-- 4) 配额配置项：默认值写在代码里（50 库 / 2000 文档），这里落成可编辑的配置行。
--    没有这行的话系统配置页「知识库」分组里根本看不到它们 —— 默认值不落表，等于写死。
--    冲突时只更新描述与分类，不覆盖 value：升级不能冲掉管理员改过的值。
INSERT INTO "system_configs" ("key", "value", "description", "category", "is_secret") VALUES
    ('knowledge_quota_max_datasets_per_user', '50', '每个用户最多可创建的知识库数量（只统计自己拥有的库，被授权的库不占额度）。填 0 或负数表示不限。默认 50。', 'knowledge', FALSE),
    ('knowledge_quota_max_documents_per_dataset', '2000', '单个知识库内最多可上传的文档数量。填 0 或负数表示不限。默认 2000。', 'knowledge', FALSE)
ON CONFLICT ("key") DO UPDATE
SET
    "description" = EXCLUDED."description",
    "category" = EXCLUDED."category";
