import assert from "node:assert/strict";
import type { AIAgentVersion } from "../src/api/agent.ts";
import {
  buildAgentVersionDiff,
  filterAgentVersionDiffGroups,
  getAgentVersionDiffPair,
} from "../src/utils/agentVersionDiff.ts";

const makeVersion = (overrides: Partial<AIAgentVersion> = {}): AIAgentVersion => ({
  id: "v1",
  agent_id: "agent-1",
  version_number: 1,
  model_name: "model-a",
  temperature: 0.2,
  synthesis_model_name: "synth-a",
  synthesis_temperature: 0.7,
  system_prompt: "回答问题",
  tools: ["search", { name: "query", enabled: true, temperature: 0.1 }],
  skills_custom: true,
  skills: ["skill-b", "skill-a"],
  welcome_config: {
    enabled: true,
    mode: "manual",
    generation_requirement: "保持简洁",
    cards: [{ icon: "chat", title: "问候", subtitle: "你好", prompt: "打个招呼" }],
  },
  status: "ARCHIVED",
  comment: "历史版本",
  created_at: "2026-08-23T12:00:00Z",
  ...overrides,
});

const same = buildAgentVersionDiff(
  makeVersion(),
  makeVersion({ id: "v2", version_number: 2, status: "PUBLISHED" }),
);
assert.equal(same.identical, true);
assert.equal(same.changedCount, 0);

const changed = buildAgentVersionDiff(
  makeVersion({
    temperature: 0.3,
    tools: [
      { name: "query", enabled: false, temperature: 0.1 },
      "history",
    ],
    system_prompt: "回答问题并引用依据",
    skills: ["skill-a", "skill-b"],
    welcome_config: {
      enabled: true,
      mode: "manual",
      generation_requirement: "保持简洁",
      cards: [{ icon: "chat", title: "问候用户", subtitle: "你好", prompt: "打个招呼" }],
    },
  }),
  makeVersion({ status: "PUBLISHED", version_number: 3 }),
);
assert.equal(changed.identical, false);
assert.ok(changed.groups.find((group) => group.id === "model")?.items.some((item) => item.key === "temperature" && item.changed));
assert.ok(changed.groups.find((group) => group.id === "prompt")?.items.some((item) => item.changed));
assert.ok(changed.groups.find((group) => group.id === "tools")?.items.some((item) => item.change === "added" && item.label.includes("search")));
assert.ok(changed.groups.find((group) => group.id === "tools")?.items.some((item) => item.change === "removed" && item.label.includes("history")));
assert.ok(changed.groups.find((group) => group.id === "tools")?.items.some((item) => item.change === "modified" && item.label.includes("query")));
assert.equal(changed.groups.find((group) => group.id === "skills")?.changedCount, 0);
assert.ok(changed.groups.find((group) => group.id === "welcome")?.items.some((item) => item.key.includes("cards.0.title")));

const changedGroupsOnly = filterAgentVersionDiffGroups(changed, true);
assert.ok(changedGroupsOnly.length < changed.groups.length);
assert.ok(changedGroupsOnly.every((group) => group.items.every((item) => item.changed)));
assert.equal(filterAgentVersionDiffGroups(same, true).length, 0);
assert.equal(filterAgentVersionDiffGroups(changed, false).length, changed.groups.length);

const legacyDefaults = buildAgentVersionDiff(
  makeVersion({
    tools: ["search"],
    skills_custom: undefined,
    skills: "skill-a" as any,
    welcome_config: null,
  }),
  makeVersion({
    status: "PUBLISHED",
    tools: [{ name: "search", enabled: true }],
    skills_custom: false,
    skills: ["skill-a"],
    welcome_config: {
      enabled: false,
      mode: "manual",
      generation_requirement: "",
      cards: [],
    },
  }),
);
assert.equal(legacyDefaults.groups.find((group) => group.id === "tools")?.changedCount, 0);
assert.equal(legacyDefaults.groups.find((group) => group.id === "skills")?.changedCount, 0);
assert.equal(legacyDefaults.groups.find((group) => group.id === "welcome")?.changedCount, 0);

const nullableToolDefaults = buildAgentVersionDiff(
  makeVersion({
    tools: [
      {
        name: "search",
        enabled: true,
        model_name: null,
        temperature: null,
        description_override: null,
        engine_config_override: null,
        metadata_dataset_ids: null,
      },
    ] as any,
  }),
  makeVersion({ status: "PUBLISHED", tools: ["search"] }),
);
assert.equal(nullableToolDefaults.groups.find((group) => group.id === "tools")?.changedCount, 0);

const typedScalarDifference = buildAgentVersionDiff(
  makeVersion({ temperature: 0 as any }),
  makeVersion({ status: "PUBLISHED", temperature: "0" as any }),
);
assert.ok(typedScalarDifference.groups.find((group) => group.id === "model")?.items.some((item) => item.key === "temperature" && item.changed));

// 测试工具调用超时时间 (toolcall_timeout_seconds) 变更
const toolTimeoutDifference = buildAgentVersionDiff(
  makeVersion({ toolcall_timeout_seconds: 120 }),
  makeVersion({ status: "PUBLISHED", toolcall_timeout_seconds: null }),
);
const toolTimeoutItem = toolTimeoutDifference.groups.find((group) => group.id === "tools")?.items.find((item) => item.key === "toolcall_timeout_seconds");
assert.ok(toolTimeoutItem?.changed);
assert.equal(toolTimeoutItem?.sourceText, "120 秒");
assert.equal(toolTimeoutItem?.publishedText, "跟随全局配置");

// 测试欢迎卡片图标 (icon) 变更
const cardIconDifference = buildAgentVersionDiff(
  makeVersion({
    welcome_config: {
      enabled: true,
      mode: "manual",
      generation_requirement: "",
      cards: [{ icon: "chart", title: "问候", subtitle: "你好", prompt: "打个招呼" }],
    },
  }),
  makeVersion({
    status: "PUBLISHED",
    welcome_config: {
      enabled: true,
      mode: "manual",
      generation_requirement: "",
      cards: [{ icon: "knowledge", title: "问候", subtitle: "你好", prompt: "打个招呼" }],
    },
  }),
);
const cardIconItem = cardIconDifference.groups.find((group) => group.id === "welcome")?.items.find((item) => item.key === "welcome.cards.0.icon");
assert.ok(cardIconItem?.changed);
assert.equal(cardIconItem?.sourceText, "chart");
assert.equal(cardIconItem?.publishedText, "knowledge");

const published = makeVersion({ id: "online", status: "PUBLISHED", version_number: 3 });
const archived = makeVersion({ id: "history", status: "ARCHIVED", version_number: 2 });
const diffPair = getAgentVersionDiffPair([published, archived], archived.id);
assert.equal(diffPair?.sourceVersion.id, archived.id);
assert.equal(diffPair?.publishedVersion.id, published.id);
assert.equal(getAgentVersionDiffPair([published, archived], published.id), null);
assert.equal(getAgentVersionDiffPair([archived], archived.id), null);

console.log("agentVersionDiff.test.ts passed");
