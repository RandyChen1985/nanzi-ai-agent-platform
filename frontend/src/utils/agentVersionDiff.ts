import type { AIAgentVersion } from "../api/agent";

export type VersionDiffChange = "added" | "removed" | "modified" | "unchanged";

export interface VersionDiffItem {
  key: string;
  label: string;
  change: VersionDiffChange;
  changed: boolean;
  sourceValue: unknown;
  publishedValue: unknown;
  sourceText: string;
  publishedText: string;
}

export interface VersionDiffGroup {
  id: "model" | "tools" | "skills" | "prompt" | "welcome";
  label: string;
  items: VersionDiffItem[];
  changedCount: number;
}

export interface AgentVersionDiff {
  groups: VersionDiffGroup[];
  changedCount: number;
  identical: boolean;
}

export const filterAgentVersionDiffGroups = (
  diff: AgentVersionDiff,
  onlyChanges: boolean,
): VersionDiffGroup[] => {
  if (!onlyChanges) return diff.groups;

  return diff.groups
    .map((group) => {
      const items = group.items.filter((item) => item.changed);
      return { ...group, items, changedCount: items.length };
    })
    .filter((group) => group.items.length > 0);
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const stableValue = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(stableValue);
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, stableValue(value[key])]),
    );
  }
  return value;
};

const textValue = (value: unknown): string => {
  if (value === null || value === undefined || value === "") return "未配置";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(stableValue(value), null, 2) || "未配置";
  } catch {
    return String(value);
  }
};

const comparableValue = (value: unknown): unknown =>
  value === null || value === undefined || value === "" ? null : stableValue(value);

const equalValue = (left: unknown, right: unknown): boolean =>
  JSON.stringify(comparableValue(left)) === JSON.stringify(comparableValue(right));

const createItem = (
  key: string,
  label: string,
  sourceValue: unknown,
  publishedValue: unknown,
  explicitChange?: VersionDiffChange,
  formatText?: (value: unknown) => string,
): VersionDiffItem => {
  const change = explicitChange || (equalValue(sourceValue, publishedValue) ? "unchanged" : "modified");
  return {
    key,
    label,
    change,
    changed: change !== "unchanged",
    sourceValue,
    publishedValue,
    sourceText: formatText ? formatText(sourceValue) : textValue(sourceValue),
    publishedText: formatText ? formatText(publishedValue) : textValue(publishedValue),
  };
};

const compareFields = (
  fields: Array<[string, string]>,
  source: AIAgentVersion,
  published: AIAgentVersion,
): VersionDiffItem[] =>
  fields.map(([key, label]) =>
    createItem(key, label, source[key as keyof AIAgentVersion], published[key as keyof AIAgentVersion]),
  );

const normalizedNamedCollection = (value: unknown): Map<string, unknown> => {
  const result = new Map<string, unknown>();
  for (const entry of Array.isArray(value) ? value : []) {
    const name =
      typeof entry === "string"
        ? entry.trim()
        : isRecord(entry)
          ? String(entry.name || "").trim()
          : "";
    if (!name) continue;
    if (typeof entry === "string") {
      result.set(name, { name, enabled: true });
      continue;
    }
    const normalizedEntry = Object.fromEntries(
      Object.entries(entry).filter(
        ([key, value]) => value !== null && value !== undefined || key === "enabled",
      ),
    );
    if (normalizedEntry.enabled === null || normalizedEntry.enabled === undefined) {
      normalizedEntry.enabled = true;
    }
    result.set(name, stableValue(normalizedEntry));
  }
  return result;
};

const compareNamedCollections = (
  key: string,
  label: string,
  sourceValue: unknown,
  publishedValue: unknown,
): VersionDiffItem[] => {
  const source = normalizedNamedCollection(sourceValue);
  const published = normalizedNamedCollection(publishedValue);
  return [...new Set([...source.keys(), ...published.keys()])].sort().map((name) => {
    const sourceEntry = source.get(name);
    const publishedEntry = published.get(name);
    const change =
      sourceEntry === undefined
        ? "added"
        : publishedEntry === undefined
          ? "removed"
          : undefined;
    return createItem(`${key}.${name}`, `${label}：${name}`, sourceEntry, publishedEntry, change);
  });
};

const normalizedSkills = (value: unknown): string[] => {
  const entries =
    typeof value === "string" ? [value] : Array.isArray(value) ? value : [];
  return [
    ...new Set(
      entries
        .map((entry) => {
          if (typeof entry === "string") return entry.trim();
          if (isRecord(entry)) return String(entry.id || entry.name || "").trim();
          return "";
        })
        .filter(Boolean),
    ),
  ].sort();
};

const normalizedBoolean = (value: unknown): boolean => {
  if (typeof value === "string") return value.trim().toLowerCase() === "true";
  return Boolean(value);
};

const compareSkills = (source: AIAgentVersion, published: AIAgentVersion): VersionDiffItem => {
  const sourceSkills = normalizedSkills(source.skills);
  const publishedSkills = normalizedSkills(published.skills);
  return createItem("skills", "已选择 Skills", sourceSkills, publishedSkills);
};

type NormalizedWelcomeConfig = {
  enabled: boolean;
  mode: "manual" | "ai";
  generation_requirement: string;
  cards: unknown[];
};

const normalizedWelcomeConfig = (value: unknown): NormalizedWelcomeConfig => {
  const raw = isRecord(value) ? value : {};
  const mode = String(raw.mode || "manual").trim().toLowerCase();
  const requirement = raw.generation_requirement;
  return {
    enabled: normalizedBoolean(raw.enabled),
    mode: mode === "ai" ? "ai" : "manual",
    generation_requirement: requirement === null || requirement === undefined
      ? ""
      : String(requirement).trim(),
    cards: Array.isArray(raw.cards) ? raw.cards : [],
  };
};

const compareWelcome = (source: AIAgentVersion, published: AIAgentVersion): VersionDiffItem[] => {
  const sourceConfig = normalizedWelcomeConfig(source.welcome_config);
  const publishedConfig = normalizedWelcomeConfig(published.welcome_config);
  const fields: VersionDiffItem[] = [
    createItem("welcome.enabled", "启用欢迎语", sourceConfig.enabled, publishedConfig.enabled),
    createItem("welcome.mode", "欢迎语模式", sourceConfig.mode, publishedConfig.mode),
    createItem(
      "welcome.generation_requirement",
      "生成要求",
      sourceConfig.generation_requirement,
      publishedConfig.generation_requirement,
    ),
  ];
  const sourceCards = Array.isArray(sourceConfig.cards) ? sourceConfig.cards : [];
  const publishedCards = Array.isArray(publishedConfig.cards) ? publishedConfig.cards : [];
  for (let index = 0; index < Math.max(sourceCards.length, publishedCards.length); index += 1) {
    const sourceCardValue = sourceCards[index];
    const publishedCardValue = publishedCards[index];
    const sourceCard: Record<string, unknown> = isRecord(sourceCardValue) ? sourceCardValue : {};
    const publishedCard: Record<string, unknown> = isRecord(publishedCardValue)
      ? publishedCardValue
      : {};
    for (const field of ["icon", "title", "subtitle", "prompt"] as const) {
      const label =
        field === "icon"
          ? "图标"
          : field === "title"
            ? "标题"
            : field === "subtitle"
              ? "副标题"
              : "Prompt";
      fields.push(
        createItem(
          `welcome.cards.${index}.${field}`,
          `第 ${index + 1} 张卡片${label}`,
          sourceCard[field],
          publishedCard[field],
        ),
      );
    }
  }
  return fields;
};

export const findPublishedAgentVersion = (
  versions: AIAgentVersion[],
): AIAgentVersion | null =>
  versions.find((version) => version.status === "PUBLISHED") || null;

export const getAgentVersionDiffPair = (
  versions: AIAgentVersion[],
  sourceVersionId: string,
): { sourceVersion: AIAgentVersion; publishedVersion: AIAgentVersion } | null => {
  const publishedVersion = findPublishedAgentVersion(versions);
  const sourceVersion = versions.find((version) => version.id === sourceVersionId);
  if (!publishedVersion || !sourceVersion || sourceVersion.status === "PUBLISHED") return null;
  return { sourceVersion, publishedVersion };
};

export const buildAgentVersionDiff = (
  source: AIAgentVersion,
  published: AIAgentVersion,
): AgentVersionDiff => {
  const groups: VersionDiffGroup[] = [
    {
      id: "model",
      label: "模型策略",
      items: [
        createItem("model_name", "主模型 (编排)", source.model_name, published.model_name),
        createItem("temperature", "主模型采样温度", source.temperature, published.temperature),
        createItem(
          "synthesis_model_name",
          "合成模型 (裁判)",
          source.synthesis_model_name,
          published.synthesis_model_name,
          undefined,
          (val) => (val ? String(val) : "跟随编排模型"),
        ),
        createItem(
          "synthesis_temperature",
          "合成模型采样温度",
          source.synthesis_temperature,
          published.synthesis_temperature,
          undefined,
          (val) => (val != null && val !== "" ? String(val) : "跟随编排模型"),
        ),
      ],
      changedCount: 0,
    },
    {
      id: "tools",
      label: "工具",
      items: [
        createItem(
          "toolcall_timeout_seconds",
          "单次工具调用超时",
          source.toolcall_timeout_seconds,
          published.toolcall_timeout_seconds,
          undefined,
          (val) => (val != null && val !== "" ? `${val} 秒` : "跟随全局配置"),
        ),
        ...compareNamedCollections("tools", "工具", source.tools, published.tools),
      ],
      changedCount: 0,
    },
    {
      id: "skills",
      label: "Skills",
      items: [
        createItem(
          "skills_custom",
          "自定义 Skills",
          normalizedBoolean(source.skills_custom),
          normalizedBoolean(published.skills_custom),
        ),
        compareSkills(source, published),
      ],
      changedCount: 0,
    },
    {
      id: "prompt",
      label: "系统提示词",
      items: [createItem("system_prompt", "系统提示词", source.system_prompt, published.system_prompt)],
      changedCount: 0,
    },
    {
      id: "welcome",
      label: "欢迎语配置",
      items: compareWelcome(source, published),
      changedCount: 0,
    },
  ];

  for (const group of groups) {
    group.changedCount = group.items.filter((entry) => entry.changed).length;
  }
  const changedCount = groups.reduce((total, group) => total + group.changedCount, 0);
  return { groups, changedCount, identical: changedCount === 0 };
};
