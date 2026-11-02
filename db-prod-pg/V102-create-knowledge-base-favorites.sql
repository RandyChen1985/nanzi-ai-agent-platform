-- V102: 知识库收藏（我的收藏）
-- 与 MySQL 侧 V200 对齐：按人记录收藏，dataset_id 为 RAGFlow Dataset ID。
CREATE TABLE IF NOT EXISTS knowledge_base_favorites (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    dataset_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE knowledge_base_favorites IS '知识库收藏（我的收藏）';
COMMENT ON COLUMN knowledge_base_favorites.user_id IS '收藏人用户ID';
COMMENT ON COLUMN knowledge_base_favorites.dataset_id IS 'RAGFlow Dataset ID';

CREATE UNIQUE INDEX IF NOT EXISTS uix_kbf_user_dataset ON knowledge_base_favorites (user_id, dataset_id);
CREATE INDEX IF NOT EXISTS ix_kbf_dataset ON knowledge_base_favorites (dataset_id);
