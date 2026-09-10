-- V54: 添加沙箱通用配置（docker/k8s 均生效）：自动预热开关 + 空闲回收时长（PostgreSQL 方言）
INSERT INTO system_configs (key, value, description, category, is_secret) VALUES
(
  'sandbox_auto_warm',
  'true',
  '沙箱自动预热开关（docker/k8s 均生效）。开启时，在 embed 页面打开会话/新建会话后会自动静默拉起沙箱（每个会话一次）；关闭后仅手动「启动」或发消息时拉沙箱。',
  'sandbox',
  FALSE
),
(
  'sandbox_idle_time',
  '30',
  '沙箱空闲回收时长（分钟，docker/k8s 均生效）。沙箱超过该时长未被使用时，将自动关闭容器/Pod 以释放资源；默认 30 分钟，改动约 1 分钟内生效。',
  'sandbox',
  FALSE
)
ON CONFLICT (key) DO UPDATE SET
  description = EXCLUDED.description,
  category = EXCLUDED.category;