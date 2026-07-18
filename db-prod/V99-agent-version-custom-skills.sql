-- V99: 智能体版本支持自定义 Skills 白名单
ALTER TABLE `ai_agent_versions`
  ADD COLUMN `skills_custom` BOOLEAN NOT NULL DEFAULT 0 COMMENT '是否自定义 Skills（仅勾选公共技能）' AFTER `tools`,
  ADD COLUMN `skills` JSON NULL COMMENT '自定义公共技能 ID 列表' AFTER `skills_custom`;
