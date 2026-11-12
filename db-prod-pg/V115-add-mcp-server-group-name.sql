-- V115: MCP 服务加「业务分组」（PostgreSQL）。
-- 与 MySQL 侧 db-prod/V215-add-mcp-server-group-name.sql 保持同一结构：
--   group_name      = 这条服务属于哪个业务域（单值）→ 左栏页签 / 筛选
--   remark          = 自由备注，前端已用它做关键词搜索，不能当分类
--   scope           = global / personal，归属范围与权限维度，不能当业务域
--   credential_*    = 服务怎么认我们，凭据链路，与导航无关
--
-- 平台服务与个人服务共用同一列；页签计数按 scope 各自统计，不混成一个数字。
--
-- 幂等：PG 执行器不吞任何错误，整文件一个事务，所以语句必须自己幂等 ——
-- `ADD COLUMN IF NOT EXISTS` + 可重复设置的 `COMMENT ON COLUMN`。
--
-- 可空无默认：存量行落 NULL，前端按「未分组」统计，无需回填。

ALTER TABLE "sys_mcp_servers"
    ADD COLUMN IF NOT EXISTS "group_name" VARCHAR(64) NULL;

COMMENT ON COLUMN "sys_mcp_servers"."group_name"
    IS '业务分组：单值归属，服务 MCP 工具集页签与筛选；不参与权限与出站调用';
