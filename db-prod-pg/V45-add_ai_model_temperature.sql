-- V45: add an optional per-model temperature for model tests and new agent versions.
-- NULL keeps the global llm_temperature fallback for legacy model records.

ALTER TABLE "ai_models"
    ADD COLUMN IF NOT EXISTS "temperature" DOUBLE PRECISION NULL;

COMMENT ON COLUMN "ai_models"."temperature" IS '模型测试及新建智能体版本的默认温度（0 至 2）';
