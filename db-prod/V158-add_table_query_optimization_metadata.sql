-- V158: 记录物理表分区字段和索引字段，供 ChatBI 生成可裁剪、可命中索引的 SQL
ALTER TABLE meta_tables
    ADD COLUMN partition_fields JSON NULL COMMENT '分区字段物理名列表',
    ADD COLUMN index_fields JSON NULL COMMENT '索引字段物理名列表';