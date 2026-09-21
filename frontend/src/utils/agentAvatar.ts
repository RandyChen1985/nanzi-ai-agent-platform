/**
 * AI 头像解析：智能体专属头像 → 全局官方形象 → 内置默认。
 *
 * 背景：全局 AI 头像是「企业官方形象」（仅管理员可配），智能体的 `avatar_url` 是
 * 「这个智能体自己长什么样」（随智能体编辑权限）。两者不是二选一，而是**继承关系**：
 * 智能体没单独配头像时，必须回落到全局形象，而不是显示成破图或内置默认。
 *
 * 解析规则收敛在这里，是因为聊天气泡、专家级联菜单、@提及列表都要用同一套顺序；
 * 各写各的迟早出现「选择列表显示首字母、聊起来却是全局头像」这类不一致。
 */

/** 解析所需的最小智能体字段（结构类型，避免与具体 API 类型强耦合）。 */
export interface AgentAvatarSource {
  id?: string | null;
  name?: string | null;
  avatar_url?: string | null;
}

/**
 * 智能体头像地址长度上限。
 *
 * 与全局 AI 头像的 2048 不同：`ai_agents.avatar_url` 是 `String(255)`，
 * 超长地址在 MySQL 严格模式下会直接 1406 报错，所以输入框必须提前截断。
 */
export const AGENT_AVATAR_URL_MAX_LENGTH = 255;

/** 智能体自身头像；未配置返回空串。 */
export function agentOwnAvatarUrl(agent?: AgentAvatarSource | null): string {
  return String(agent?.avatar_url || "").trim();
}

export interface AgentAvatarIndex {
  byId: Map<string, string>;
  byName: Map<string, string>;
}

/**
 * 建立「有头像的智能体」索引。
 *
 * 未配置头像的智能体**不入索引**，这样查表落空就能干净地回退到全局形象，
 * 不需要调用方再判断一次空值。
 */
export function buildAgentAvatarIndex(
  agents?: AgentAvatarSource[] | null,
): AgentAvatarIndex {
  const byId = new Map<string, string>();
  const byName = new Map<string, string>();
  for (const agent of agents || []) {
    const url = agentOwnAvatarUrl(agent);
    if (!url) continue;
    if (agent?.id) byId.set(String(agent.id), url);
    if (agent?.name) byName.set(String(agent.name), url);
  }
  return { byId, byName };
}

export interface ChatAgentAvatarInput {
  agentId?: string | null;
  agentName?: string | null;
  /** 消息自带头像：由历史接口下发，可覆盖前端索引不到的已停用智能体。 */
  agentAvatarUrl?: string | null;
}

/**
 * 聊天气泡的三层继承解析。
 *
 * 优先级：消息自带头像 → 智能体索引（按 id 再按 name）→ 全局官方形象。
 * 全部落空时返回空串，由调用方决定内置默认资源。
 */
export function resolveChatAgentAvatar(
  message?: ChatAgentAvatarInput | null,
  index?: AgentAvatarIndex | null,
  globalAvatar?: string | null,
): string {
  const direct = String(message?.agentAvatarUrl || "").trim();
  if (direct) return direct;

  const agentId = String(message?.agentId || "");
  const byId = agentId ? index?.byId.get(agentId) : undefined;
  if (byId) return byId;

  const agentName = String(message?.agentName || "");
  const byName = agentName ? index?.byName.get(agentName) : undefined;
  if (byName) return byName;

  return String(globalAvatar || "").trim();
}
