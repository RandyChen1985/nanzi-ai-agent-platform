-- V89: 知识库可见范围从「纯展示标记」切换为「真实权限语义」的存量订正（PostgreSQL）
--
-- 与 MySQL V189 对齐：改造前 `visibility` 只是展示标记，实际访问权由
-- `ai_agent_resource_permissions`（resource_type=dataset）决定，即新语义中的
-- **public（公共级）**。直接切语义会让存量 `private` 库的已授权成员失去访问权、
-- 让存量 `team` 库对部门静默扩权，因此存量统一订正为 `public`。
--
-- 三档语义（见 `app/services/permission_service.py`）：
--   public  —— 需要显式授权，创建人自动可用，可继续分配；
--   team    —— 业务归属部门（含下级部门）成员自动只读，也可继续分配；
--   private —— 仅创建人可见，不允许分配授权。
--
-- 幂等与边界：`created_at < TIMESTAMP '2026-10-26 00:00:00'` 表示发布前的存量库，
-- 重复执行不会覆盖发布后新建或手动调整过的库。

UPDATE knowledge_base_metadata
SET visibility = 'public'
WHERE status <> 'deleted'
  AND (visibility IN ('private', 'team') OR visibility IS NULL)
  -- created_at 列可空：历史行若为 NULL，`NULL < 时间点` 求值为 NULL 会被漏掉，
  -- 这些行会保留旧值，切换语义后静默撤权/扩权。因此显式纳入。
  AND (created_at IS NULL OR created_at < TIMESTAMP '2026-10-26 00:00:00');
