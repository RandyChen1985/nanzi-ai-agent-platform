-- V208: Docker 沙箱 cgroups 资源上限配置项（MySQL 方言）
--
-- 背景：docker 策略的沙箱容器此前只下发 Binds，完全没有 Memory / NanoCPUs /
-- PidsLimit。DooD 部署下沙箱是宿主机上与平台平级的兄弟容器，一次内存爆破
-- （例如把 100 亿字符的字符串读进内存）或一次 fork bomb 类的进程爆炸，就会
-- 把整台宿主机打进 OOM，由内核随机挑选牺牲品——可能是平台进程、MySQL 或 Redis。
--
-- 语义（与 app/services/ai/runtime/agentscope/workspace.py 的
-- DOCKER_SANDBOX_* 常量、_resolve_docker_sandbox_resource_limits 一一对应）：
--   留空   沿用代码默认值（2048 / 2 / 512），不是「不限」；
--   0      该维度显式不限制（排障/特殊部署时的逃生口）；
--   正数   实际上限，非法值回退默认。
--
-- 为什么默认 2GiB / 2 核 / 512 进程：要能容纳 uv pip install、node 构建这类
-- 正常依赖安装的峰值，又要挡得住打满整机。改动只对新创建的容器生效，已有容器
-- 在空闲回收后重建时才带上新值。
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`, `created_at`, `updated_at`)
VALUES
(
  'sandbox_docker_memory_limit_mb',
  '2048',
  'Docker 沙箱容器内存上限（整数 MB，默认 2048；填 0 表示不限）。同时把 swap 上限压到与内存一致，避免内存打满后继续吃宿主机 swap 把整机拖死。改完只对新容器生效。',
  'sandbox',
  0,
  NOW(6),
  NOW(6)
),
(
  'sandbox_docker_cpu_limit',
  '2',
  'Docker 沙箱容器 CPU 上限（核数，支持小数如 1.5，默认 2；填 0 表示不限）。防止单个死循环把宿主机 CPU 打满。改完只对新容器生效。',
  'sandbox',
  0,
  NOW(6),
  NOW(6)
),
(
  'sandbox_docker_pids_limit',
  '512',
  'Docker 沙箱容器内进程数上限（默认 512；填 0 表示不限）。阻断 fork bomb，同时给 pip/uv 的并发编译留足余量。改完只对新容器生效。',
  'sandbox',
  0,
  NOW(6),
  NOW(6)
);
