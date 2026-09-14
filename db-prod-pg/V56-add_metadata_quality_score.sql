-- V56: 数据集质量治理分（巡检结算时写入，仅新增列，不改动既有数据）
ALTER TABLE meta_datasets
  ADD COLUMN IF NOT EXISTS quality_score INTEGER,
  ADD COLUMN IF NOT EXISTS quality_breakdown JSONB,
  ADD COLUMN IF NOT EXISTS quality_scored_at TIMESTAMP;

COMMENT ON COLUMN meta_datasets.quality_score IS '数据资产质量治理分 0-100';
COMMENT ON COLUMN meta_datasets.quality_breakdown IS '质量分维度明细';
COMMENT ON COLUMN meta_datasets.quality_scored_at IS '最近一次质量评分时间';
