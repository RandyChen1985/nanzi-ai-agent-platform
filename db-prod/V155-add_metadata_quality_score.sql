-- V155: 数据集质量治理分（巡检结算时写入，仅新增列，不改动既有数据）
ALTER TABLE `meta_datasets`
  ADD COLUMN `quality_score` INT NULL COMMENT '数据资产质量治理分 0-100',
  ADD COLUMN `quality_breakdown` JSON NULL COMMENT '质量分维度明细',
  ADD COLUMN `quality_scored_at` DATETIME NULL COMMENT '最近一次质量评分时间';
