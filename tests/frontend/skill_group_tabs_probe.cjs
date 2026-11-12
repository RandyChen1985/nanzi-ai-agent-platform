/**
 * 真跑 `utils/toolGroupTabs.ts`，用**技能**的形状验证技能工作台的分组页签口径。
 *
 * 技能与工具/数据集的关键差异必须在这里锁住：
 * 1. 技能 ID 是字符串且用户习惯直接搜 ID（`weather_travel_advice`），所以 searchFields
 *    必须带 `id`，否则「搜 ID 搜不到」——而这是技能页最常见的用法；
 * 2. 技能的启用状态是 frontmatter 里的**字符串** `enabled`，页面用 `isSkillEnabled`
 *    映射成 `is_active` 布尔再交给纯函数（口径见 test_skill_enabled_parity_contract）；
 * 3. 平台技能与「我的技能」是两份列表，各自算页签，绝不混在一起计数。
 */
const { loadModule } = require("./ts_module_loader.cjs");

const { computeGroupTabs, matchesToolFilters, toolGroupName } = loadModule(
  "frontend/src/utils/toolGroupTabs.ts",
);

const UNGROUPED = "__ungrouped__";

/** 与页面 `isSkillEnabled` 同口径：只认 {1,true,yes,on} */
const isEnabled = (raw) =>
  raw === undefined || raw === null
    ? true
    : ["1", "true", "yes", "on"].includes(String(raw).trim().toLowerCase());

/** 页面里 `toGroupableSkill` 做的那次映射 */
const toGroupable = (skill) => ({
  id: skill.id,
  name: skill.name || skill.id,
  display_name: skill.name || skill.id,
  description: skill.description || "",
  is_active: isEnabled(skill.enabled),
  group_name: skill.group ?? "",
});

const SKILLS = [
  { id: "weather_travel_advice", name: "天气查询与出行建议", description: "查询城市实时天气并给出出行建议", enabled: "true", group: "天气出行" },
  { id: "brainstorming", name: "brainstorming", description: "Explore intent before implementation", enabled: "true", group: "开发工具" },
  { id: "test_driven_development", name: "test-driven-development", description: "Write tests before implementation", enabled: "true", group: "开发工具" },
  { id: "sql_query_builder", name: "SQL 取数助手", description: "根据自然语言生成跨库 SQL", enabled: "true", group: "数据检索" },
  { id: "rag_retrieval", name: "知识库检索", description: "多轮检索知识库并给带引用的答案", enabled: "false", group: "数据检索" },
  { id: "pdf_text_extract", name: "PDF 文本抽取", description: "把 PDF 转成结构化文本", enabled: "true", group: "文档写作" },
  { id: "doc_polish", name: "公文润色", description: "按公文格式规范润色", enabled: "False", group: "文档写作" },
  { id: "metric_explain", name: "指标口径解释", description: "解释指标计算口径", enabled: "true", group: "数据检索" },
  // 未分组的四种写法都要落进「未分组」，不能各自变成一个页签
  { id: "meeting_notes", name: "会议纪要", description: "整理会议决议与待办", enabled: "true", group: "" },
  { id: "quick_translate", name: "快速翻译", description: "中英日互译", enabled: "true", group: "   " },
  { id: "legacy_helper", name: "历史兼容助手", description: "早期遗留的辅助技能", enabled: "true", group: null },
  { id: "scratch_pad", name: "临时草稿", description: "试验用草稿", enabled: "true" },
];

const SKILL_SEARCH_FIELDS = ["id", "name", "description"];

const TOOLS = SKILLS.map(toGroupable);

const filters = (keyword = "") => ({ keyword, searchFields: SKILL_SEARCH_FIELDS });

const out = {};

/* 1. 默认顺序：全部 → 各组（命中数降序，同数量按中文名）→ 未分组固定末位 */
const base = computeGroupTabs(TOOLS, filters(), UNGROUPED);
out.base_total = base.total;
out.base_ungrouped = base.ungrouped;
out.base_items = base.items.map((item) => [item.label, item.count]);
out.base_first_key = base.items[0].key;
out.base_last_key = base.items[base.items.length - 1].key;

/* 2. 按技能 ID 搜索必须命中（这是技能页最高频的搜索方式） */
const byId = computeGroupTabs(TOOLS, filters("weather_travel"), UNGROUPED);
out.kw_id_total = byId.total;
out.kw_id_labels = byId.items.map((item) => item.label);

/* 3. 同样的关键词，去掉 id 字段就落空 —— 证明 searchFields 里的 id 真的生效 */
out.kw_id_without_id_field_total = computeGroupTabs(
  TOOLS,
  { keyword: "weather_travel", searchFields: ["name", "description"] },
  UNGROUPED,
).total;

/* 4. 中文名搜索命中，并归到正确分组 */
const byName = computeGroupTabs(TOOLS, filters("知识库"), UNGROUPED);
out.kw_name_total = byName.total;
out.kw_name_items = byName.items.map((item) => [item.label, item.count]);

/* 5. 空白 / null / 缺失三种写法都是「未分组」，不自成页签 */
out.blank_is_empty = toolGroupName({ group_name: "   " }) === "";
out.null_is_empty = toolGroupName({ group_name: null }) === "";
out.missing_is_empty = toolGroupName({}) === "";
out.trimmed_group = toolGroupName({ group_name: " 开发工具 " });

/* 6. 启用状态映射：字符串 "false" / "False" 都必须是禁用（技能页据此置灰并排序靠后） */
out.enabled_true = TOOLS.find((t) => t.id === "weather_travel_advice").is_active;
out.enabled_false = TOOLS.find((t) => t.id === "rag_retrieval").is_active;
out.enabled_False_capital = TOOLS.find((t) => t.id === "doc_polish").is_active;
out.enabled_missing = TOOLS.find((t) => t.id === "scratch_pad").is_active;

/* 7. 逐条过滤与页签计数同源：命中条数必须等于「全部」页签上的数字 */
out.consistency = TOOLS.filter((tool) => matchesToolFilters(tool, filters("指标"))).length;
out.consistency_tab_total = computeGroupTabs(TOOLS, filters("指标"), UNGROUPED).total;

/* 8. 全部有分组时不出现「未分组」页签 */
out.all_grouped_has_ungrouped = computeGroupTabs(
  TOOLS.filter((tool) => toolGroupName(tool)),
  filters(),
  UNGROUPED,
).items.some((item) => item.key === UNGROUPED);

/* 9. 只有「全部」一个页签时，页签条应整体隐藏（组件按 items.length > 1 判断） */
out.single_group_items_length = computeGroupTabs(
  [toGroupable({ id: "only_one", name: "唯一技能", description: "", enabled: "true", group: "天气出行" })],
  filters(),
  UNGROUPED,
).items.length;

/* 10. 平台技能与我的技能各自计数，不混算 */
const PERSONAL = [
  { id: "my_weekly_report", name: "周报生成", description: "生成周报草稿", enabled: "true", group: "文档写作" },
  { id: "my_scratch", name: "临时草稿", description: "个人试验", enabled: "true", group: "" },
];
const personal = computeGroupTabs(PERSONAL.map(toGroupable), filters(), UNGROUPED);
out.personal_total = personal.total;
out.personal_items = personal.items.map((item) => [item.label, item.count]);
out.personal_only_own_groups = personal.items
  .filter((item) => item.key !== "all" && item.key !== UNGROUPED)
  .every((item) => item.label === "文档写作");

process.stdout.write(JSON.stringify(out));
