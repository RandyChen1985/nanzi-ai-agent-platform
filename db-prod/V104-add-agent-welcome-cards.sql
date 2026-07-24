-- LOCAL 智能体版本级欢迎页三卡片配置。
ALTER TABLE `ai_agent_versions`
  ADD COLUMN `welcome_config` JSON NULL COMMENT '欢迎页三卡片配置（版本级）' AFTER `skills`;
