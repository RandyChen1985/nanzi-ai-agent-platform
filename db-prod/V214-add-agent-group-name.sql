-- V214: 智能体加「业务分组」（单值归属，仅服务智能体中心的页签与筛选）。
--
-- 为什么不复用表里已有的三个「像分组」的字段：
--   owner_group  = 所属**用户组**（V5 建表时注释「所属组 (预留)」/ PG 注释「所属用户组」），
--                  是组织与权限维度，分享服务、系统级发布服务都在传递它，还有
--                  idx_owner_created(owner_group, created_at)。拿它当业务分组，
--                  以后「按用户组授权智能体」就无路可走。
--   capabilities = JSON **多值**能力标签，注释原文「Tags for routing」——进路由决策。
--                  多值会让一个智能体出现在多个页签、各页签计数相加大于总数；
--                  也会把「导航口径」和「路由口径」绑死。
--   agent_type   = 机制类型（GENERAL 等），已经是列表页的独立筛选维度。
--
-- 所以新增单值 group_name，与 meta_datasets.group_name（V213）、tools.group_name 同构：
--   group_name = 这个智能体属于哪个业务域（单值）→ 页面导航/筛选
--   capabilities = 它能接哪类请求（多值）        → AI 路由
--
-- 可空、无默认值：存量行全部落成 NULL，前端按「未分组」展示与统计，
-- 不需要数据回填，也不影响任何既有查询与运行期判定。
--
-- 幂等：MySQL 8.0 没有 `ADD COLUMN IF NOT EXISTS`，而 `apply_sql.py` 没有版本记录表、
-- 整文件重放，只按语句粒度忽略错误码（1060 列已存在）。这里只有一条 ALTER，
-- 重复执行会被 1060 忽略，无副作用。

ALTER TABLE `ai_agents`
  ADD COLUMN `group_name` VARCHAR(64) NULL
    COMMENT '业务分组：单值归属，服务智能体中心页签与筛选；不参与权限与路由';
