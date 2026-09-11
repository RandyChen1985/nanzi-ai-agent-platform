-- V154: 创建元数据 Schema 漂移异常告警表（MySQL 方言）
-- 汇聚运行时物理报错反哺与手动/自动巡检发现的 Schema 差异，供管理员人工确认与处置（Human-in-the-loop）
CREATE TABLE IF NOT EXISTS `meta_schema_drift_alerts` (
    `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '告警主键ID',
    `dataset_id` INT NOT NULL COMMENT '关联的数据集ID',
    `table_id` INT NULL COMMENT '关联的元数据表ID（可为空）',
    `table_name` VARCHAR(255) NOT NULL COMMENT '物理表名',
    `column_name` VARCHAR(255) NOT NULL COMMENT '异常字段名',
    `drift_type` VARCHAR(32) NOT NULL DEFAULT 'missing_in_db' COMMENT '漂移类型: missing_in_db-物理库缺失, new_in_db-物理库新增, type_mismatch-类型不符',
    `source` VARCHAR(32) NOT NULL DEFAULT 'runtime' COMMENT '来源: runtime-运行时报错反哺, manual_inspection-手动体检, cron_inspection-定时巡检',
    `error_sample` TEXT NULL COMMENT '物理报错或变更样例',
    `hit_count` INT NOT NULL DEFAULT 1 COMMENT '累计触发/检测频次',
    `status` TINYINT NOT NULL DEFAULT 0 COMMENT '处置状态: 0-待处理 pending, 1-已处理 resolved, 2-已忽略 ignored',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次检出时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (`id`),
    KEY `idx_drift_alerts_dataset_status` (`dataset_id`, `status`),
    KEY `idx_drift_alerts_target` (`dataset_id`, `table_name`, `column_name`, `status`),
    KEY `idx_drift_alerts_status_created` (`status`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='元数据 Schema 漂移异常告警与人机协同处置表';
