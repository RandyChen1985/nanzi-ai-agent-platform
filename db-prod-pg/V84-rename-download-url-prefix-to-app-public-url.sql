-- V84: 系统配置项 download_url_prefix 更名为 app_public_url（PostgreSQL）
--
-- 口径统一为：系统配置 app_public_url → 环境变量 APP_PUBLIC_URL → 空（相对地址）。
-- 幂等：目标键已存在时保留目标键的值并删除旧行。

UPDATE "system_configs"
SET "key" = 'app_public_url',
    "description" = '平台对外访问地址（协议 + 域名 + 可选的端口或反向代理前缀）。生成文件下载链接、密码重置链接、MCP 公网地址与 OAuth 元数据、报表通知深链、网页预览的 frame-ancestors 判定都使用它。只填协议、域名和必要前缀，不要填写 /api/... 等路径；留空时回退环境变量 APP_PUBLIC_URL，仍未配置则使用相对地址。',
    "updated_at" = CURRENT_TIMESTAMP
WHERE "key" = 'download_url_prefix'
  AND NOT EXISTS (
      SELECT 1 FROM "system_configs" AS target WHERE target."key" = 'app_public_url'
  );

DELETE FROM "system_configs"
WHERE "key" = 'download_url_prefix'
  AND EXISTS (
      SELECT 1 FROM "system_configs" AS target WHERE target."key" = 'app_public_url'
  );
