-- V60: 为元数据字段增加维度角色与层级组标记，供 ChatBI 下钻分析识别可分组字段与下钻路径（PostgreSQL 方言）
--
-- 背景：MySQL 侧由 V159 加这三列，但 Doris/维度角色那次交付只补了 meta_tables 的
-- partition_fields / index_fields（V59），漏掉了 meta_columns 的维度列。PostgreSQL 主库
-- 部署后 MetaColumn ORM 会带上这三列查询，缺列将直接 UndefinedColumn 报错。
--
-- 处理：仅新增列，默认 'none'，不改动既有数据；与 db-prod/V159 保持一一对应。
ALTER TABLE meta_columns
  ADD COLUMN IF NOT EXISTS dimension_role VARCHAR(20) NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS hierarchy_group VARCHAR(100),
  ADD COLUMN IF NOT EXISTS hierarchy_order INTEGER;

COMMENT ON COLUMN meta_columns.dimension_role IS '维度角色: none/time/geo/category/identifier';
COMMENT ON COLUMN meta_columns.hierarchy_group IS '层级组标识，同组字段构成下钻链';
COMMENT ON COLUMN meta_columns.hierarchy_order IS '组内层级序号，从小到大=从粗到细';
