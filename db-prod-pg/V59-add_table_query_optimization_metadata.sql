-- V59: 记录物理表分区字段和索引字段，供 ChatBI 生成可裁剪、可命中索引的 SQL
ALTER TABLE meta_tables
    ADD COLUMN IF NOT EXISTS partition_fields JSONB,
    ADD COLUMN IF NOT EXISTS index_fields JSONB;