-- V144: add an optional per-model temperature for model tests and new agent versions.
-- NULL keeps the global llm_temperature fallback for legacy model records.

SET @temperature_exists := (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'ai_models'
      AND column_name = 'temperature'
);
SET @sql := IF(
    @temperature_exists = 0,
    'ALTER TABLE ai_models ADD COLUMN temperature FLOAT NULL COMMENT ''Model test/default temperature, 0 to 2'' AFTER max_output_tokens',
    'SELECT 1'
);
PREPARE add_temperature FROM @sql;
EXECUTE add_temperature;
DEALLOCATE PREPARE add_temperature;
