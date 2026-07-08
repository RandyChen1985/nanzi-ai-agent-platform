-- V93-create_knowledge_base_metrics.sql
-- 创建知识库与文档的每日运营统计指标表

CREATE TABLE IF NOT EXISTS `knowledge_base_metrics` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `metric_date` DATE NOT NULL COMMENT '统计日期',
  `target_type` VARCHAR(32) NOT NULL COMMENT '统计类型: dataset/document',
  `target_id` VARCHAR(64) NOT NULL COMMENT '目标ID (dataset_id 或 doc_id)',
  `target_name` VARCHAR(255) NULL COMMENT '名称快照',
  `citation_count` INT NOT NULL DEFAULT 0 COMMENT '被引用次数',
  `search_count` INT NOT NULL DEFAULT 0 COMMENT '被搜索/召回次数',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_kb_metric_date_target` (`metric_date`, `target_type`, `target_id`),
  KEY `idx_kb_metrics_date` (`metric_date`),
  KEY `idx_kb_metrics_target` (`target_type`, `target_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库与文档的每日运营统计指标表';
