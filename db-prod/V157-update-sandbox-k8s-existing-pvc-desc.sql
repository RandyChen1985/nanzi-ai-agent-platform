-- V157: 更新 sandbox_k8s_existing_pvc 配置说明（自动探测平台数据卷语义，MySQL 方言）
--
-- 背景：沙箱工作区挂载改为「留空即自动探测平台自身数据卷并共享用户工作区」，
-- 原描述「留空则为各用户动态创建独立专属 PVC」已不准确，会误导管理员以为
-- 留空也能看到用户工作区。仅更新说明文案，不改动 value，也不覆盖管理员已填值。
UPDATE `system_configs`
SET `description` = '可选的共享 PVC 名称。留空时自动探测平台自身数据卷并共享用户工作区（推荐，零配置）；填 none 表示强制使用每工作区独立创建的空 PVC（沙箱内看不到用户工作区）；填具体 PVC 名则显式指向该共享卷（须与平台同命名空间）。'
WHERE `key` = 'sandbox_k8s_existing_pvc'
  AND (`description` IS NULL OR `description` NOT LIKE '%自动探测%');
