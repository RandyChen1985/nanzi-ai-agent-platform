-- V3: Create Metadata Management Tables for AI Agent
-- Description: Stores semantic metadata (business terms, synonyms, enums) for Text-to-SQL enhancement.

CREATE TABLE IF NOT EXISTS meta_datasets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL COMMENT '数据集名称，如 NanZi_Resources',
    display_name VARCHAR(100) COMMENT '显示名称',
    description TEXT COMMENT '详细描述：用于帮助 Agent 理解该数据集包含什么业务内容',
    tags JSON COMMENT '标签列表：辅助检索和分类，如 ["资产", "基础信息"]',
    data_source VARCHAR(50) NOT NULL DEFAULT 'clickhouse' COMMENT '数据源类型: clickhouse, mysql, api',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_name (name)
) COMMENT='元数据集(业务域)表' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS meta_tables (
    id INT AUTO_INCREMENT PRIMARY KEY,
    dataset_id INT NOT NULL COMMENT '归属数据集',
    physical_name VARCHAR(255) NOT NULL COMMENT '数据库物理表名',
    term VARCHAR(255) NOT NULL COMMENT '业务术语名称',
    description TEXT COMMENT '详细描述',
    synonyms JSON COMMENT '同义词列表 (JSON Array)',
    status TINYINT DEFAULT 1 COMMENT '1:启用, 0:禁用',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (dataset_id) REFERENCES meta_datasets(id) ON DELETE CASCADE,
    UNIQUE KEY uk_physical_name (physical_name)
) COMMENT='元数据-表定义' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS meta_columns (
    id INT AUTO_INCREMENT PRIMARY KEY,
    table_id INT NOT NULL COMMENT '归属表ID',
    physical_name VARCHAR(255) NOT NULL COMMENT '物理字段名',
    term VARCHAR(255) NOT NULL COMMENT '业务术语名称',
    type VARCHAR(50) COMMENT '字段类型',
    description TEXT COMMENT '字段描述',
    enums JSON COMMENT '枚举值定义 (JSON Array of Objects: {value, label})',
    synonyms JSON COMMENT '同义词列表 (JSON Array)',
    examples JSON COMMENT '示例值 (JSON Array)',
    foreign_key VARCHAR(255) COMMENT '外键关联 (Table.Column)',
    is_primary TINYINT DEFAULT 0 COMMENT '是否主键',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (table_id) REFERENCES meta_tables(id) ON DELETE CASCADE,
    UNIQUE KEY uk_table_col (table_id, physical_name)
) COMMENT='元数据-字段定义' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS `meta_metrics` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `dataset_id` int(11) NOT NULL COMMENT '所属数据集',
  `name` varchar(100) NOT NULL COMMENT '指标名称 (如 pue)',
  `display_name` varchar(100) NOT NULL COMMENT '显示名称',
  `description` text COMMENT '业务口径描述',
  `calculation_logic` text COMMENT '计算逻辑 (SQL 片段或公式)',
  `unit` varchar(20) DEFAULT NULL COMMENT '单位',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_dataset_name` (`dataset_id`,`name`),
  CONSTRAINT `fk_metric_dataset` FOREIGN KEY (`dataset_id`) REFERENCES `meta_datasets` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='业务指标定义表';

CREATE TABLE IF NOT EXISTS `meta_relationships` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `source_table_id` int(11) NOT NULL,
  `target_table_id` int(11) NOT NULL,
  `join_condition` varchar(255) NOT NULL COMMENT '关联条件 (如 source.id = target.source_id)',
  `join_type` varchar(20) DEFAULT 'LEFT' COMMENT 'LEFT, INNER, RIGHT',
  `description` text COMMENT '关系描述',
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_rel_source` FOREIGN KEY (`source_table_id`) REFERENCES `meta_tables` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_rel_target` FOREIGN KEY (`target_table_id`) REFERENCES `meta_tables` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='表间关系定义表';
