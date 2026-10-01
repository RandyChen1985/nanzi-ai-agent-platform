-- V164: 系统配置新增「邮件服务」分组
-- 用途：全平台统一的 SMTP 发送服务，供审核结果通知与用户通知渠道的「使用全局」选项使用
-- 说明：总开关默认关闭；email_smtp_password 是平台首个 is_secret=1 配置，
--       仅在 API 出参脱敏，数据库内仍为明文（与既有用户级 SMTP 配置一致）。

INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('email_service_enabled', 'false', '是否启用平台邮件发送服务（关闭时不发送任何邮件，也不会阻塞审核等主流程）', 'email', 0),
('email_smtp_host', '', 'SMTP 服务器地址，例如 smtp.example.com 或内网中继 smtp.internal', 'email', 0),
('email_smtp_port', '465', 'SMTP 端口。SSL 常用 465，STARTTLS 常用 587，免认证内网中继常用 25', 'email', 0),
('email_smtp_security', 'ssl', '加密方式：ssl（SSL/TLS 直连）/ starttls（先明文连接再升级）/ none（不加密，仅适用于内网中继，不安全）', 'email', 0),
('email_smtp_user', '', 'SMTP 登录账号。免认证的内网中继请留空（此时密码也必须留空）', 'email', 0),
('email_smtp_password', '', 'SMTP 登录密码或授权码。免认证的内网中继请留空', 'email', 1),
('email_from_address', '', '发件人地址，留空则使用 SMTP 登录账号', 'email', 0),
('email_sender_name', 'NanZi AI Agent', '发件人显示名称，同时作为邮件主题前缀', 'email', 0);
