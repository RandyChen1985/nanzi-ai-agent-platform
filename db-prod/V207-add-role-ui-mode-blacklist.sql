-- V207: 角色级「界面模式黑名单」（MySQL）
--
-- 背景：平台有「用户模式 / 专业模式」两种界面形态，默认两种都能用。业务上需要
-- 把某些角色限定成只能用其中一种（例如一线客服只需要简化侧栏）。
--
-- 语义（与 app/services/ui_mode_policy.py 一一对应）：
--   ''        两种模式都可用（默认，存量角色全部落到这里）
--   'pro'     禁用专业模式 → 只能用用户模式
--   'user'    禁用用户模式 → 只能用专业模式
--   'user,pro' 非法：必须二选一，写入层直接拒绝（见 validate_blocked）
--
-- 为什么单列而不是策略表：只有两种取值、按角色一读一写，单列少一次 join、
-- 也避免角色删除后留下孤儿策略行。将来若要按部门/租户控制再升级成
-- scope_type/scope_id 表，读取接口都走 effective_policy()，不需要改调用方。
--
-- 注意：该列**不在 ORM 映射里声明**，读写走原生 SQL + 列探测。原因是代码可能
-- 先于本迁移上线，映射里带上它会让所有 SELECT role 报 Unknown column，把角色
-- 管理和登录链路一起打挂（ai_agent_resource_permissions.agent_id 有过同样教训）。
ALTER TABLE `ai_agent_roles`
    ADD COLUMN `blocked_ui_modes` VARCHAR(32) NOT NULL DEFAULT ''
    COMMENT '禁用的界面模式（黑名单，逗号分隔：user/pro；空串=都不禁）';
