-- Add a user-facing primary type while retaining capabilities for runtime compatibility.
ALTER TABLE `ai_agents`
  ADD COLUMN `agent_type` VARCHAR(32) NOT NULL DEFAULT 'GENERAL' AFTER `capabilities`;

-- Keep each ALTER independent: apply_sql.py can skip an existing column/index
-- while continuing to add the remaining fields on partially upgraded databases.
ALTER TABLE `ai_agents`
  ADD COLUMN `onboarding_key` VARCHAR(64) NULL AFTER `agent_type`;

ALTER TABLE `ai_agents`
  ADD COLUMN `onboarding_step` VARCHAR(20) NOT NULL DEFAULT 'COMPLETE' AFTER `onboarding_key`;

ALTER TABLE `ai_agents`
  ADD UNIQUE KEY `uk_ai_agents_owner_onboarding` (`created_by`, `onboarding_key`);

UPDATE `ai_agents`
SET `agent_type` = CASE
  WHEN JSON_CONTAINS(COALESCE(`capabilities`, JSON_ARRAY()), JSON_QUOTE('data_query'))
       OR `name` = 'chat-bi' THEN 'CHATBI'
  WHEN JSON_CONTAINS(COALESCE(`capabilities`, JSON_ARRAY()), JSON_QUOTE('knowledge_base'))
       OR `name` = 'knowledge-base' THEN 'KNOWLEDGE_BASE'
  ELSE 'GENERAL'
END;

UPDATE `ai_agents`
SET `capabilities` = JSON_ARRAY_APPEND(COALESCE(`capabilities`, JSON_ARRAY()), '$', 'data_query')
WHERE `agent_type` = 'CHATBI'
  AND NOT JSON_CONTAINS(COALESCE(`capabilities`, JSON_ARRAY()), JSON_QUOTE('data_query'));

UPDATE `ai_agents`
SET `capabilities` = JSON_ARRAY_APPEND(COALESCE(`capabilities`, JSON_ARRAY()), '$', 'knowledge_base')
WHERE `agent_type` = 'KNOWLEDGE_BASE'
  AND NOT JSON_CONTAINS(COALESCE(`capabilities`, JSON_ARRAY()), JSON_QUOTE('knowledge_base'));

UPDATE `ai_agents`
SET `capabilities` = JSON_ARRAY_APPEND(COALESCE(`capabilities`, JSON_ARRAY()), '$', 'general_chat')
WHERE `agent_type` = 'GENERAL'
  AND NOT JSON_CONTAINS(COALESCE(`capabilities`, JSON_ARRAY()), JSON_QUOTE('general_chat'));
