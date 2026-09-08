-- V150: Register Feishu Notification Tool into Database for Agent UI selection
INSERT IGNORE INTO sys_api_tools (id, name, description, method, url_template, is_active, created_at, updated_at) VALUES
('tool-feishu-msg', 'send_feishu_message', '发送飞书群机器人 Markdown 卡片消息。自动读取当前用户在个人中心 -> 消息通知里的飞书 Webhook/加签配置，无需在本轮对话或工具配置中提供 webhook、access_token 或群聊目标。', 'POST', 'https://open.feishu.cn/open-apis/bot/v2/hook', 1, NOW(), NOW());
