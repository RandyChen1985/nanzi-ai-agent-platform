-- V84: 品牌个性化配置
-- Date: 2026-07-01

INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('branding.enabled', 'false', '是否启用品牌个性化', 'branding', 0),
('branding.product_name', '南孜 · 智能体平台', '产品名称（浏览器标题、侧栏、登录页）', 'branding', 0),
('branding.login_subtitle', 'NanZi Intelligent Agent Platform', '登录页副标题', 'branding', 0),
('branding.icon_url', '/favicon.png', 'Logo / Favicon 地址（相对或绝对 URL）', 'branding', 0),
('branding.hide_login_sso', 'false', '登录页隐藏 SSO 登录', 'branding', 0),
('branding.hide_version_link', 'false', '侧栏版本号取消 GitHub 外链', 'branding', 0),
('branding.contact_markdown', '', '联系信息 Markdown（个人中心 → 关于）', 'branding', 0),
('branding.copyright_text', '', '登录页底部版权文案（启用品牌个性化后展示）', 'branding', 0);
