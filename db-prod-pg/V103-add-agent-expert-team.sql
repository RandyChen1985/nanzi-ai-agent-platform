-- V103: 智能体中心新增「专家团」视图（PostgreSQL）。
--
-- 与 db-prod/V203-add-agent-expert-team.sql 同源，字段语义见该文件注释。
-- 成员不新建表：直接复用版本级 tools 里的 delegable_agent_ids。
ALTER TABLE ai_agents
  ADD COLUMN IF NOT EXISTS is_expert_team_lead BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS team_profile JSONB NULL;

COMMENT ON COLUMN ai_agents.is_expert_team_lead IS
  '是否专家团主专家；为 TRUE 时出现在智能体中心的「专家团」tab';
COMMENT ON COLUMN ai_agents.team_profile IS
  '专家团展示信息 {"name": 团名, "intro": 团介绍}；NULL 表示未配置';

CREATE INDEX IF NOT EXISTS idx_ai_agents_expert_team ON ai_agents (is_expert_team_lead);
