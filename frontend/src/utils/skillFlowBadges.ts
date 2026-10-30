export type SkillFlowBadgeKind = "manual" | "enabled" | "candidate";

export interface SkillFlowBadge {
  key: string;
  label: string;
  description?: string;
  kind: SkillFlowBadgeKind;
}

export interface SkillFlowSourceFile {
  type?: string;
  filename: string;
  skillMeta?: {
    id?: string;
    name?: string;
    description?: string;
  };
}

export interface SkillFlowSourceLog {
  id?: string | number;
  title?: string;
  details?: string;
}

// 界面与日志统一称「技能」。旧快照（process_timeline 随消息持久化，不会重写）里仍是
// 「已启用流程」/「已识别候选流程」，两套前缀都要认——否则历史消息的技能徽标会突然消失。
const ENABLED_FLOW_PREFIXES = ["已启用技能:", "已启用技能：", "已启用流程:", "已启用流程："];
const CANDIDATE_FLOW_PREFIXES = [
  "已识别候选技能:",
  "已识别候选技能：",
  "已识别候选流程:",
  "已识别候选流程：",
];
const LEGACY_CANDIDATE_PREFIXES = ["已扫描匹配技能:", "已扫描匹配技能：", "已匹配技能:", "已匹配技能："];

const normalizeSkillLabel = (value: string): string => {
  return String(value || "").replace(" (技能)", "").trim();
};

const normalizedKey = (label: string): string => label.trim().toLowerCase();

const stripKnownPrefix = (title: string, prefixes: string[]): string | null => {
  const text = String(title || "").trim();
  const prefix = prefixes.find((item) => text.startsWith(item));
  if (!prefix) return null;
  return normalizeSkillLabel(text.slice(prefix.length));
};

const extractFlowNameFromDetails = (details?: string): string | null => {
  const match = String(details || "").match(
    /已识别候选(?:技能|流程)「([^」]+)」|已启用(?:技能|流程)「([^」]+)」/,
  );
  return normalizeSkillLabel(match?.[1] || match?.[2] || "");
};

const extractLogBadge = (log: SkillFlowSourceLog): SkillFlowBadge | null => {
  const title = String(log.title || "").trim();
  const enabledName = stripKnownPrefix(title, ENABLED_FLOW_PREFIXES);
  if (enabledName) {
    return {
      key: `enabled:${normalizedKey(enabledName)}`,
      label: enabledName,
      description: log.details,
      kind: "enabled",
    };
  }

  const candidateName = stripKnownPrefix(title, [...CANDIDATE_FLOW_PREFIXES, ...LEGACY_CANDIDATE_PREFIXES])
    || extractFlowNameFromDetails(log.details);
  if (candidateName) {
    return {
      key: `candidate:${normalizedKey(candidateName)}`,
      label: candidateName,
      description: log.details,
      kind: "candidate",
    };
  }

  return null;
};

export function buildSkillFlowBadges(
  files: SkillFlowSourceFile[] = [],
  logs: SkillFlowSourceLog[] = [],
): SkillFlowBadge[] {
  const badges: SkillFlowBadge[] = [];
  const seen = new Set<string>();

  const addBadge = (badge: SkillFlowBadge) => {
    const key = normalizedKey(badge.label);
    if (!key || seen.has(key)) return;
    seen.add(key);
    badges.push(badge);
  };

  files
    .filter((file) => file.type === "skill")
    .forEach((file) => {
      const label = normalizeSkillLabel(file.filename || file.skillMeta?.name || "");
      if (!label) return;
      addBadge({
        key: `manual:${file.skillMeta?.id || normalizedKey(label)}`,
        label,
        description: file.skillMeta?.description,
        kind: "manual",
      });
    });

  logs
    .map(extractLogBadge)
    .filter((badge): badge is SkillFlowBadge => Boolean(badge))
    .filter((badge) => badge.kind === "enabled")
    .forEach(addBadge);

  logs
    .map(extractLogBadge)
    .filter((badge): badge is SkillFlowBadge => Boolean(badge))
    .filter((badge) => badge.kind === "candidate")
    .forEach(addBadge);

  return badges;
}

export function summarizeSkillFlowBadges(badges: SkillFlowBadge[]): string {
  const count = badges.length;
  if (count <= 0) return "";
  const kinds = new Set(badges.map((badge) => badge.kind));
  if (kinds.size === 1 && kinds.has("manual")) return `${count}个技能已加载`;
  if (kinds.size === 1 && kinds.has("enabled")) return `${count}个技能已启用`;
  if (kinds.size === 1 && kinds.has("candidate")) return `${count}个候选技能`;
  return `${count}个技能`;
}

export function skillFlowNoticeLabel(badges: SkillFlowBadge[]): string {
  const kinds = new Set(badges.map((badge) => badge.kind));
  if (kinds.size === 1 && kinds.has("manual")) return "本轮已加载生态技能：";
  if (kinds.size === 1 && kinds.has("enabled")) return "本轮已启用技能：";
  if (kinds.size === 1 && kinds.has("candidate")) return "本轮识别到候选技能：";
  return "本轮关联技能：";
}
