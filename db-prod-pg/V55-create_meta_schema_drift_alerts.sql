-- V55: 创建元数据 Schema 漂移异常告警表（PostgreSQL 方言）
-- 汇聚运行时物理报错反哺与手动/自动巡检发现的 Schema 差异，供管理员人工确认与处置（Human-in-the-loop）
CREATE TABLE IF NOT EXISTS "meta_schema_drift_alerts" (
    "id" BIGSERIAL PRIMARY KEY,
    "dataset_id" INT NOT NULL,
    "table_id" INT NULL,
    "table_name" VARCHAR(255) NOT NULL,
    "column_name" VARCHAR(255) NOT NULL,
    "drift_type" VARCHAR(32) NOT NULL DEFAULT 'missing_in_db',
    "source" VARCHAR(32) NOT NULL DEFAULT 'runtime',
    "error_sample" TEXT NULL,
    "hit_count" INT NOT NULL DEFAULT 1,
    "status" SMALLINT NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE "meta_schema_drift_alerts" IS '元数据 Schema 漂移异常告警与人机协同处置表';
COMMENT ON COLUMN "meta_schema_drift_alerts"."id" IS '告警主键ID';
COMMENT ON COLUMN "meta_schema_drift_alerts"."dataset_id" IS '关联的数据集ID';
COMMENT ON COLUMN "meta_schema_drift_alerts"."table_id" IS '关联的元数据表ID（可为空）';
COMMENT ON COLUMN "meta_schema_drift_alerts"."table_name" IS '物理表名';
COMMENT ON COLUMN "meta_schema_drift_alerts"."column_name" IS '异常字段名';
COMMENT ON COLUMN "meta_schema_drift_alerts"."drift_type" IS '漂移类型: missing_in_db-物理库缺失, new_in_db-物理库新增, type_mismatch-类型不符';
COMMENT ON COLUMN "meta_schema_drift_alerts"."source" IS '来源: runtime-运行时报错反哺, manual_inspection-手动体检, cron_inspection-定时巡检';
COMMENT ON COLUMN "meta_schema_drift_alerts"."error_sample" IS '物理报错或变更样例';
COMMENT ON COLUMN "meta_schema_drift_alerts"."hit_count" IS '累计触发/检测频次';
COMMENT ON COLUMN "meta_schema_drift_alerts"."status" IS '处置状态: 0-待处理 pending, 1-已处理 resolved, 2-已忽略 ignored';
COMMENT ON COLUMN "meta_schema_drift_alerts"."created_at" IS '首次检出时间';
COMMENT ON COLUMN "meta_schema_drift_alerts"."updated_at" IS '最后更新时间';

CREATE INDEX IF NOT EXISTS "idx_drift_alerts_dataset_status"
    ON "meta_schema_drift_alerts" ("dataset_id", "status");
CREATE INDEX IF NOT EXISTS "idx_drift_alerts_target"
    ON "meta_schema_drift_alerts" ("dataset_id", "table_name", "column_name", "status");
CREATE INDEX IF NOT EXISTS "idx_drift_alerts_status_created"
    ON "meta_schema_drift_alerts" ("status", "created_at");
