-- V189: 知识库可见范围从「纯展示标记」切换为「真实权限语义」的存量订正
--
-- 背景：
--   改造前 `knowledge_base_metadata.visibility` 只是详情页上的一个标记，
--   实际访问权完全由 `ai_agent_resource_permissions`（resource_type=dataset）决定，
--   即"需要授权才能访问"——这正是新语义里的 **public（公共级）**。
--
--   本次起三档语义真正参与判定（见 `app/services/permission_service.py`）：
--     public  —— 需要显式授权，创建人自动可用，可继续分配；
--     team    —— 业务归属部门（含下级部门）成员自动只读，也可继续分配；
--     private —— 仅创建人可见，不允许分配授权。
--
-- 为什么要订正：
--   存量库的 `visibility` 多为建库时的默认值 `private`（创建弹窗从不传该字段），
--   历史上却按 public 行为在运行。若直接切语义，会出现两类事故：
--     1. `private` 库 → 已授权的成员立刻失去访问权；
--     2. `team` 库 → 部门同事突然获得访问权（静默扩权）。
--   因此把存量库统一订正为 `public`：改造前后行为完全一致，之后由业务按需
--   逐个改成 team / private。
--
-- 幂等与边界：
--   `created_at < '2026-10-26 00:00:00'` 表达"本次发布之前的存量库"。
--   重复执行不会覆盖发布后新建或手动调整过的库。
--   若某些环境晚于该时间点才上线、且希望把当天更晚创建的库一并纳入，
--   请按实际发布时间调整这个时间常量（本项目默认时区为 Asia/Shanghai）。

SET NAMES utf8mb4;

UPDATE `knowledge_base_metadata`
SET `visibility` = 'public'
WHERE `status` != 'deleted'
  AND (`visibility` IN ('private', 'team') OR `visibility` IS NULL)
  -- created_at 列可空：历史行若为 NULL，`NULL < 时间点` 求值为 NULL 会被漏掉，
  -- 这些行会保留旧值，切换语义后静默撤权/扩权。因此显式纳入。
  AND (`created_at` IS NULL OR `created_at` < '2026-10-26 00:00:00');
