-- V108: Docker 沙箱 cgroups 资源上限配置项（PostgreSQL 方言）
--
-- 与 MySQL db-prod/V208-add-sandbox-docker-resource-limits.sql 保持同一口径：
--   留空   沿用代码默认值（2048 MB / 2 核 / 512 进程），不是「不限」；
--   0      该维度显式不限制；
--   正数   实际上限，非法值回退默认。
--
-- 冲突时只更新配置元数据（category/description/is_secret），不覆盖已有部署的
-- value，避免升级时丢失用户环境配置。改动只对新创建的沙箱容器生效。
INSERT INTO "system_configs" ("key", "value", "description", "category", "is_secret") VALUES
    (
        'sandbox_docker_memory_limit_mb',
        '2048',
        'Docker 沙箱容器内存上限（整数 MB，默认 2048；填 0 表示不限）。同时把 swap 上限压到与内存一致，避免内存打满后继续吃宿主机 swap 把整机拖死。改完只对新容器生效。',
        'sandbox',
        FALSE
    ),
    (
        'sandbox_docker_cpu_limit',
        '2',
        'Docker 沙箱容器 CPU 上限（核数，支持小数如 1.5，默认 2；填 0 表示不限）。防止单个死循环把宿主机 CPU 打满。改完只对新容器生效。',
        'sandbox',
        FALSE
    ),
    (
        'sandbox_docker_pids_limit',
        '512',
        'Docker 沙箱容器内进程数上限（默认 512；填 0 表示不限）。阻断 fork bomb，同时给 pip/uv 的并发编译留足余量。改完只对新容器生效。',
        'sandbox',
        FALSE
    )
ON CONFLICT ("key") DO UPDATE
SET
    "description" = EXCLUDED."description",
    "category" = EXCLUDED."category",
    "is_secret" = EXCLUDED."is_secret",
    "updated_at" = CURRENT_TIMESTAMP;
