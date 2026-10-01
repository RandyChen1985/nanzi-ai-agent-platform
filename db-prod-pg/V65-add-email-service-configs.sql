-- V65: 系统配置新增「邮件服务」分组（对应 MySQL V164）
-- 说明：总开关默认关闭；email_smtp_password 是平台首个 is_secret=TRUE 配置，
--       仅在 API 出参脱敏，数据库内仍为明文（与既有用户级 SMTP 配置一致）。

INSERT INTO "system_configs" ("key", "value", "description", "category", "is_secret") VALUES
('email_service_enabled', 'false', '是否启用平台邮件发送服务（关闭时不发送任何邮件，也不会阻塞审核等主流程）', 'email', FALSE),
('email_smtp_host', '', 'SMTP 服务器地址，例如 smtp.example.com 或内网中继 smtp.internal', 'email', FALSE),
('email_smtp_port', '465', 'SMTP 端口。SSL 常用 465，STARTTLS 常用 587，免认证内网中继常用 25', 'email', FALSE),
('email_smtp_security', 'ssl', '加密方式：ssl / starttls / none（none 不加密，仅适用于内网中继，不安全）', 'email', FALSE),
('email_smtp_user', '', 'SMTP 登录账号。免认证的内网中继请留空（此时密码也必须留空）', 'email', FALSE),
('email_smtp_password', '', 'SMTP 登录密码或授权码。免认证的内网中继请留空', 'email', TRUE),
('email_from_address', '', '发件人地址，留空则使用 SMTP 登录账号', 'email', FALSE),
('email_sender_name', 'NanZi AI Agent', '发件人显示名称，同时作为邮件主题前缀', 'email', FALSE)
ON CONFLICT ("key") DO NOTHING;
