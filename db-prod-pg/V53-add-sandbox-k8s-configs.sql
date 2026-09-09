-- V53: 添加 Kubernetes 原生安全沙箱（sandbox_policy = k8s）相关系统配置项（PostgreSQL 方言）
INSERT INTO system_configs (key, value, description, category, is_secret) VALUES
(
  'sandbox_k8s_namespace',
  'agent-sandboxes',
  'Kubernetes 沙箱 Pod 运行的命名空间。默认为 agent-sandboxes，建议与平台业务命名空间隔离。',
  'sandbox',
  FALSE
),
(
  'sandbox_k8s_image',
  'python:3.11-slim',
  'Kubernetes 沙箱容器运行的基础镜像。默认为 python:3.11-slim，必须包含 Python 3.10+ 环境。',
  'sandbox',
  FALSE
),
(
  'sandbox_k8s_existing_pvc',
  '',
  '可选已存在的共享 PVC 存储卷名称（如平台后端的 app-data）。若指定，沙箱 Pod 将通过 subPath 挂载用户工作区与公共文档，与平台数据无缝打通；留空则为各用户动态创建独立专属 PVC。',
  'sandbox',
  FALSE
),
(
  'sandbox_k8s_storage_class',
  '',
  '动态创建独立 PVC 时使用的 Kubernetes 存储类名称（StorageClass）。留空表示使用集群默认存储类。',
  'sandbox',
  FALSE
),
(
  'sandbox_k8s_storage_size',
  '1Gi',
  '动态创建独立 PVC 时的申请容量。例如 1Gi、5Gi。默认为 1Gi。',
  'sandbox',
  FALSE
),
(
  'sandbox_k8s_cpu_request',
  '100m',
  'Kubernetes 沙箱 Pod 的 CPU 请求保障（requests.cpu）。默认 100m，避免调度器过度分配节点算力。留空表示不设 requests。',
  'sandbox',
  FALSE
),
(
  'sandbox_k8s_memory_request',
  '128Mi',
  'Kubernetes 沙箱 Pod 的内存请求保障（requests.memory）。默认 128Mi。留空表示不设 requests。',
  'sandbox',
  FALSE
),
(
  'sandbox_k8s_cpu_limit',
  '1000m',
  'Kubernetes 沙箱 Pod 的 CPU 限制上限（limits.cpu）。例如 500m、1000m、2。留空表示不设上限。',
  'sandbox',
  FALSE
),
(
  'sandbox_k8s_memory_limit',
  '1Gi',
  'Kubernetes 沙箱 Pod 的内存限制上限（limits.memory）。例如 512Mi、1Gi、2Gi。留空表示不设上限。',
  'sandbox',
  FALSE
),
(
  'sandbox_k8s_delete_pvc_on_close',
  'true',
  '沙箱会话到期或关闭时，是否同步删除动态创建的独立专属 PVC。默认为 true（随用随清释放存储，防存储泄漏）。若使用已有共享 PVC 则受保护绝对不删除。',
  'sandbox',
  FALSE
)
ON CONFLICT (key) DO UPDATE SET
  description = EXCLUDED.description,
  category = EXCLUDED.category;
