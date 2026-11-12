-- V113: 数据集加「业务分组」（PostgreSQL）。
-- 与 MySQL 侧 db-prod/V213-add-dataset-group-name.sql 保持同一结构：
--   group_name = 这个数据集归谁管（单值）→ 页面导航/筛选
--   tags       = 它有什么属性（多值）    → AI 选库（已授权数据集目录提示词）
--
-- 幂等：PG 执行器不吞任何错误，整文件一个事务，所以语句必须自己幂等 ——
-- `ADD COLUMN IF NOT EXISTS` + 可重复设置的 `COMMENT ON COLUMN`。
--
-- 可空无默认：存量行落 NULL，前端按「未分组」统计，无需回填。

ALTER TABLE "meta_datasets"
    ADD COLUMN IF NOT EXISTS "group_name" VARCHAR(64) NULL;

COMMENT ON COLUMN "meta_datasets"."group_name"
    IS '业务分组：单值归属，服务列表页页签与筛选；不进 AI 提示词';
