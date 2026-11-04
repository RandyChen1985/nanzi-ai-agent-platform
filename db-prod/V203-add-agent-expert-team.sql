-- V203: 智能体中心新增「专家团」视图（MySQL）。
--
-- 一个专家团 = 一个「专家团主专家」智能体 + 它 sub_agent_call 调度范围里的子专家。
-- 成员不新建表：直接复用版本级 tools 里的 delegable_agent_ids，避免「团里有 5 个、
-- 调度实际只有 3 个」的两套真相。这里只补展示所需的两个字段。
--
-- 为什么团名/介绍不复用 display_name / description：
--   description 就是 build_sub_agents_context() 发给委派模型看的「职责描述」，
--   把「从取数到口径一条龙」这类团队文案塞进去会直接污染子智能体路由判断。
ALTER TABLE `ai_agents`
  ADD COLUMN `is_expert_team_lead` BOOLEAN NOT NULL DEFAULT 0
    COMMENT '是否专家团主专家；为 1 时出现在智能体中心的「专家团」tab' AFTER `is_enabled`,
  ADD COLUMN `team_profile` JSON NULL
    COMMENT '专家团展示信息 {"name": 团名, "intro": 团介绍}；NULL 表示未配置' AFTER `is_expert_team_lead`;

-- 列表页要按它过滤并计数，单独成列 + 索引，而不是塞进 JSON 里做函数查询。
CREATE INDEX `idx_ai_agents_expert_team` ON `ai_agents` (`is_expert_team_lead`);
