-- V183: 系统配置项 download_url_prefix 更名为 app_public_url（MySQL）
--
-- 口径统一为：系统配置 app_public_url → 环境变量 APP_PUBLIC_URL → 空（相对地址）。
-- 下载链接、密码重置链接、MCP、报表通知、网页预览共用同一来源，
-- 不再各自直读 APP_PUBLIC_URL。
--
-- 幂等：若目标键已存在（手工建过或重跑），保留目标键的值并删除旧行，
-- 避免唯一键冲突导致迁移中断。

UPDATE `system_configs` AS legacy
LEFT JOIN `system_configs` AS target ON target.`key` = 'app_public_url'
SET legacy.`key` = 'app_public_url',
    legacy.`description` = '平台对外访问地址（协议 + 域名 + 可选的端口或反向代理前缀）。生成文件下载链接、密码重置链接、MCP 公网地址与 OAuth 元数据、报表通知深链、网页预览的 frame-ancestors 判定都使用它。只填协议、域名和必要前缀，不要填写 /api/... 等路径；留空时回退环境变量 APP_PUBLIC_URL，仍未配置则使用相对地址。',
    legacy.`updated_at` = NOW(6)
WHERE legacy.`key` = 'download_url_prefix'
  AND target.`key` IS NULL;

DELETE legacy
FROM `system_configs` AS legacy
JOIN `system_configs` AS target ON target.`key` = 'app_public_url'
WHERE legacy.`key` = 'download_url_prefix';
