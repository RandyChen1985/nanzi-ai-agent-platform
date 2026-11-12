-- V215: MCP 服务加「业务分组」（单值归属，仅服务「MCP 工具集」页的分组页签与筛选）。
--
-- 为什么不复用表里现有的字段：
--   remark          = 自由备注（VARCHAR(500)），新建向导里随手写的一句话，前端已拿它
--                     做关键词搜索。自由文本不是「枚举式归属」，用它当分组等于把备注
--                     当分类，改一次文案就换一次分组。
--   scope           = global / personal，是**归属范围与权限维度**（非 admin 访问 global
--                     直接 403）。混用会让「个人服务」和「平台服务」再也没法按同一个
--                     业务域聚合。
--   credential_mode / auth_headers / user_assertion_* = 服务怎么认我们，属于凭据链路，
--                     与导航无关。
--   enabled_status / last_sync_at = 运行状态，已经是现有的筛选与排序维度。
--
-- 所以新增单值 group_name，与 tools.group_name、meta_datasets.group_name（V213）、
-- ai_agents.group_name（V214）同构：
--   group_name = 这条服务属于哪个业务域（单值）→ 左栏页签 / 筛选
-- 它不参与权限判定，也不进 MCP 出站调用链路，改它不影响任何运行期行为。
--
-- 平台服务（global）与个人服务（personal）共用同一列：业务域是同一个概念，
-- 页签计数按 scope 各自统计即可，不把两者混成一个数字。
--
-- 可空、无默认值：存量行落 NULL，前端按「未分组」展示与统计，不需要数据回填。
--
-- 幂等：MySQL 8.0 没有 `ADD COLUMN IF NOT EXISTS`，`apply_sql.py` 整文件重放、
-- 只按语句粒度忽略错误码（1060 列已存在）。这里只有一条 ALTER，重复执行被 1060 忽略。

ALTER TABLE `sys_mcp_servers`
  ADD COLUMN `group_name` VARCHAR(64) NULL
    COMMENT '业务分组：单值归属，服务 MCP 工具集页签与筛选；不参与权限与出站调用';
