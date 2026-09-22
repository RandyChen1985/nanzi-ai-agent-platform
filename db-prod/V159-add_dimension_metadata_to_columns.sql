-- V159: 为元数据字段增加维度角色与层级组标记，供 ChatBI 下钻分析识别可分组字段与下钻路径
ALTER TABLE meta_columns
    ADD COLUMN dimension_role VARCHAR(20) NOT NULL DEFAULT 'none' COMMENT '维度角色: none/time/geo/category/identifier',
    ADD COLUMN hierarchy_group VARCHAR(100) NULL COMMENT '层级组标识，同组字段构成下钻链',
    ADD COLUMN hierarchy_order INT NULL COMMENT '组内层级序号，从小到大=从粗到细';
