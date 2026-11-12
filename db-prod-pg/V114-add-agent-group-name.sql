-- V114: 智能体加「业务分组」（PostgreSQL）。
-- 与 MySQL 侧 db-prod/V214-add-agent-group-name.sql 保持同一结构：
--   group_name   = 这个智能体属于哪个业务域（单值）→ 页面导航/筛选
--   capabilities = 它能接哪类请求（多值 JSON）    → AI 路由（注释原文 "Tags for routing"）
--   owner_group  = 所属用户组（组织/权限维度）    → 分享、发布、将来的按组授权
--
-- 幂等：PG 执行器不吞任何错误，整文件一个事务，所以语句必须自己幂等 ——
-- `ADD COLUMN IF NOT EXISTS` + 可重复设置的 `COMMENT ON COLUMN`。
--
-- 可空无默认：存量行落 NULL，前端按「未分组」统计，无需回填。

ALTER TABLE "ai_agents"
    ADD COLUMN IF NOT EXISTS "group_name" VARCHAR(64) NULL;

COMMENT ON COLUMN "ai_agents"."group_name"
    IS '业务分组：单值归属，服务智能体中心页签与筛选；不参与权限与路由';
