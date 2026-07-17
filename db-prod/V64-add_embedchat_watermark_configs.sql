-- 需求：系统设置新增水印开关及样式配置，用于防泄露控制。无论选用何种样式，均会自动附带时间戳。
-- 创建人：Antigravity
-- 创建时间：2026-05-28

INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('embedchat_watermark_enabled', 'false', '是否开启嵌入式对话水印 (true/false)', 'other', 0),
('embedchat_watermark_style', 'user_time', '水印样式选项 (user_time: 用户名+时间戳, custom: 自定义文字+时间戳)', 'other', 0),
('embedchat_watermark_text', '南孜系统', '水印自定义文字内容 (选为自定义文字时仍会自动附加当前时间戳)', 'other', 0);
