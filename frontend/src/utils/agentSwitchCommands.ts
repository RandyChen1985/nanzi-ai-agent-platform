export interface SwitchableAgent {
  id?: string | number;
  name?: string;
  display_name?: string;
  displayName?: string;
  capabilities?: string[];
}

const CAPABILITY_ALIASES: Record<string, string[]> = {
  data_query: ["ChatBI", "数据智能助手", "数据分析专家", "数据查询", "查数", "报表"],
  knowledge_base: ["知识库智能体", "知识库助手", "知识库专家", "知识库"],
  knowledge_retrieval: ["知识库智能体", "知识库助手", "知识库专家", "知识库"],
  document_search: ["知识库智能体", "知识库助手", "知识库专家", "知识库"],
  human_resources: ["HR", "人力资源", "人事"],
  hr: ["HR", "人力资源", "人事"],
};

export function buildAgentSwitchCommand(agentId: string | number): string {
  return `/switch_agent_expert?agent_id=${String(agentId)}`;
}

function normalizeForMatch(value: string): string {
  return value.toLowerCase().replace(/\s+/g, "");
}

function asksToSwitchAgent(raw: string): boolean {
  const compact = raw.replace(/\s+/g, "");
  return (
    /^(请|麻烦)?(帮我)?(切换到|切换至|切到|转到|进入|使用)/.test(compact) ||
    /^(请|麻烦)?(帮我)?切换/.test(compact)
  );
}

function agentDisplayName(agent: SwitchableAgent): string {
  return String(agent.display_name || agent.displayName || agent.name || agent.id || "");
}

function agentAliases(agent: SwitchableAgent): string[] {
  const aliases = [
    agentDisplayName(agent),
    String(agent.name || ""),
    String(agent.id || ""),
  ];
  for (const capability of agent.capabilities || []) {
    aliases.push(...(CAPABILITY_ALIASES[capability] || []));
  }
  return aliases.filter(Boolean);
}

export function normalizeAgentSwitchCommand(input: string, agents: SwitchableAgent[] = []): string {
  const raw = String(input || "").trim();
  if (!raw) return "";
  if (raw.startsWith("/switch_")) return raw;
  if (!asksToSwitchAgent(raw)) return raw;

  const normalizedRaw = normalizeForMatch(raw);
  const target = agents.find((agent) =>
    agent.id && agentAliases(agent).some((alias) => normalizedRaw.includes(normalizeForMatch(alias)))
  );
  return target?.id ? buildAgentSwitchCommand(target.id) : raw;
}
