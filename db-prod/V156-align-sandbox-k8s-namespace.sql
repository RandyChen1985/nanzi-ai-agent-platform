-- V156: K8s 沙箱命名空间默认值对齐平台命名空间（共享用户工作区 PVC 的前置条件，MySQL 方言）
--
-- 背景：Kubernetes 的 PersistentVolumeClaim 是命名空间级资源，Pod 只能引用自身
-- 命名空间内的 PVC。历史默认值 `agent-sandboxes` 与平台主 PVC（位于 `nanzi-ai-agent`）
-- 分属不同命名空间，导致 sandbox_k8s_existing_pvc 配置的共享工作区挂载无法生效：
-- 沙箱 Pod 找不到该 PVC 而 Pending，或退化为独立空 PVC（沙箱内 /workspace 看不到
-- 用户工作区，与 Docker 沙箱行为不一致）。
--
-- 处理：仅当 sandbox_k8s_namespace 仍是历史默认值 `agent-sandboxes` 或为空时，
-- 对齐为平台自身部署的默认命名空间 `nanzi-ai-agent`，并同步更新配置说明。
-- 绝不覆盖管理员显式设置的其它命名空间。
--
-- 注意：
--   1. 自定义命名空间部署（非 nanzi-ai-agent）：请在本迁移后于
--      【系统设置 → 安全沙箱】把 sandbox_k8s_namespace 改为平台实际命名空间，
--      或留空以自动跟随平台自身命名空间。
--   2. 沙箱 Pod 迁入平台命名空间后，需在该命名空间重新应用
--      k8s_deploy/sandbox-rbac.example.yaml，授予 Pod/PVC 管理权限。
--   3. 若希望保持强隔离（沙箱不共享用户工作区），可显式指定独立命名空间，
--      并留空 sandbox_k8s_existing_pvc。
UPDATE `system_configs`
SET `value` = 'nanzi-ai-agent',
    `description` = 'Kubernetes 沙箱 Pod 运行的命名空间。默认与平台同命名空间（nanzi-ai-agent），这是共享平台 PVC 用户工作区的前置条件（PVC 为命名空间级资源）。留空表示自动跟随平台命名空间。'
WHERE `key` = 'sandbox_k8s_namespace'
  AND (`value` = 'agent-sandboxes' OR `value` IS NULL OR `value` = '');
