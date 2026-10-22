-- V182: send_email 支持附件后同步工具市场的能力说明
-- 说明：模型侧的工具描述由代码里的工具对象提供（notification_tools.py），
--       本迁移只让「工具管理 / 智能体编辑器」里展示的面板文案与实际能力一致，
--       避免运营看到旧文案以为只能发纯文本。

UPDATE sys_api_tools
SET description = '发送邮件通知，可携带附件（docx/xlsx/pptx/pdf/csv/txt/md/json/图片/zip，最多 5 个、单个与合计不超过 20MB）。自动读取当前用户在个人中心 -> 消息通知里的 SMTP 配置；附件路径必须来自当前会话附件或用户工作区，其他路径会被拒绝。'
WHERE name = 'send_email';
