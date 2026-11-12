/**
 * 真跑 `utils/toolGroupTabs.ts`，用**智能体**的形状验证智能体中心的分组页签口径。
 *
 * 智能体与工具/数据集/技能的关键差异必须在这里锁住：
 * 1. 这一页有十来个筛选条件（状态 / 类型 / 来源 / 引擎 / 就绪度 / 权属人 / 团队三项），
 *    所以页签计数**必须**建立在「视图 + 关键词 + 全部筛选」之后 —— 页面把这份列表算好
 *    再交给纯函数（filters 传空），于是每个数字恒等于「切到这一组能看到的条数」。
 *    如果反过来只把关键词交给纯函数，勾了「只看已启用」后就会出现「页签说 5、点进去 3」；
 * 2. 智能体的启用状态是布尔 `is_enabled`（不是技能那种 frontmatter 字符串）；
 * 3. 「我的智能体」视图下页签集合只剩自己名下有的分组。
 */
const { loadModule } = require("./ts_module_loader.cjs");

const { computeGroupTabs, toolGroupName, ALL_GROUP_KEY } = loadModule(
  "frontend/src/utils/toolGroupTabs.ts",
);

const UNGROUPED = "__ungrouped__";

/** 页面里 `toGroupableAgent` 做的那次映射 */
const toGroupable = (agent) => ({
  id: agent.id,
  name: agent.name,
  display_name: agent.display_name,
  description: agent.description,
  is_active: agent.is_enabled ?? true,
  group_name: agent.group_name ?? "",
});

const AGENTS = [
  { id: "a1", name: "contract_review", display_name: "合同审查助手", description: "比对合同条款与法务红线", group_name: "法务合规", is_enabled: true, created_by: "zhangwei" },
  { id: "a2", name: "bid_writer", display_name: "投标文件生成", description: "按招标文件组织章节", group_name: "法务合规", is_enabled: true, created_by: "lina" },
  { id: "a3", name: "policy_qa", display_name: "制度问答", description: "回答考勤报销用章流程", group_name: "法务合规", is_enabled: false, created_by: "wangqiang" },
  { id: "a4", name: "finance_report", display_name: "财报分析助手", description: "读三张表生成分析摘要", group_name: "财务分析", is_enabled: true, created_by: "zhangwei" },
  { id: "a5", name: "invoice_audit", display_name: "发票稽核", description: "核对发票要素与报销单", group_name: "财务分析", is_enabled: true, created_by: "chenmin" },
  { id: "a6", name: "sql_copilot", display_name: "SQL 取数助手", description: "自然语言转 SQL", group_name: "数据分析", is_enabled: true, created_by: "liuyang" },
  { id: "a7", name: "metric_explain", display_name: "指标口径解释", description: "解释指标定义与误用", group_name: "数据分析", is_enabled: false, created_by: "liuyang" },
  { id: "a8", name: "ticket_triage", display_name: "工单分派", description: "按内容分派工单到处理组", group_name: "客户服务", is_enabled: true, created_by: "chenmin" },
  // 未分组的四种写法都要落进「未分组」，不能各自变成一个页签
  { id: "a9", name: "main_assistant", display_name: "智能助手", description: "平台默认对话入口", group_name: "", is_enabled: true, created_by: "system" },
  { id: "a10", name: "legacy_bot", display_name: "历史兼容机器人", description: "早期接入的通用问答", group_name: "   ", is_enabled: true, created_by: "wangqiang" },
  { id: "a11", name: "scratch_bot", display_name: "试验机器人", description: "临时试验用", group_name: null, is_enabled: true, created_by: "zhangwei" },
  { id: "a12", name: "quick_qa", display_name: "快速问答", description: "一句话问答", is_enabled: true, created_by: "zhangwei" },
];

/** 页面里的 `agentsMatchingFilters`（视图 + 关键词 + 全部筛选，不含分组） */
const matching = ({ view = "all", keyword = "", enabledOnly = false } = {}) =>
  AGENTS.filter((agent) => {
    if (view === "mine" && agent.created_by !== "zhangwei") return false;
    if (enabledOnly && agent.is_enabled === false) return false;
    const k = keyword.trim().toLowerCase();
    if (!k) return true;
    return [agent.name, agent.display_name, agent.description]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(k);
  }).map(toGroupable);

const out = {};

/* 1. 默认（全部视图、无筛选）：全部 → 各组按命中数降序 → 同数量按中文名 → 未分组末位 */
const base = computeGroupTabs(matching(), {}, UNGROUPED);
out.base_total = base.total;
out.base_ungrouped = base.ungrouped;
out.base_items = base.items.map((item) => [item.label, item.count]);
out.base_first_key = base.items[0].key;
out.base_last_key = base.items[base.items.length - 1].key;

/* 2. 状态筛选参与计数：只看已启用时，各组数字下降且「法务合规 3→2」 */
const enabled = computeGroupTabs(matching({ enabledOnly: true }), {}, UNGROUPED);
out.enabled_total = enabled.total;
out.enabled_items = enabled.items.map((item) => [item.label, item.count]);

/* 3. 页签数字 = 点进去的条数：逐组核对 */
const rows = matching({ enabledOnly: true });
out.per_group_rows = ["法务合规", "财务分析", "数据分析", "客户服务"].map((name) => [
  name,
  rows.filter((row) => toolGroupName(row) === name).length,
]);

/* 4. 「我的智能体」视图：页签集合只剩自己名下有的分组 */
const mine = computeGroupTabs(matching({ view: "mine" }), {}, UNGROUPED);
out.mine_total = mine.total;
out.mine_items = mine.items.map((item) => [item.label, item.count]);
out.mine_only_own_groups = mine.items
  .filter((item) => item.key !== ALL_GROUP_KEY && item.key !== UNGROUPED)
  .every((item) => ["法务合规", "财务分析"].includes(item.label));

/* 5. 关键词把某组筛空 → 该组页签消失（点不到空组） */
const kw = computeGroupTabs(matching({ keyword: "工单" }), {}, UNGROUPED);
out.kw_total = kw.total;
out.kw_items = kw.items.map((item) => [item.label, item.count]);

/* 6. 空白 / null / 缺失都算未分组，不自成页签 */
out.blank_is_empty = toolGroupName({ group_name: "   " }) === "";
out.null_is_empty = toolGroupName({ group_name: null }) === "";
out.missing_is_empty = toolGroupName({}) === "";
out.trimmed = toolGroupName({ group_name: " 法务合规 " });

/* 7. 全部有分组时不出现「未分组」页签 */
out.all_grouped_has_ungrouped = computeGroupTabs(
  matching().filter((row) => toolGroupName(row)),
  {},
  UNGROUPED,
).items.some((item) => item.key === UNGROUPED);

/* 8. 只有「全部」一个页签时，页签条应整体隐藏（页面按 items.length > 1 判断） */
out.single_group_items_length = computeGroupTabs(
  [toGroupable({ id: "only", name: "only", display_name: "唯一", description: "", group_name: "法务合规", is_enabled: true })],
  {},
  UNGROUPED,
).items.length;

/* 9. 与工具页的差别：filters 传空时 is_active 不参与判定（已由页面在上游筛好）——
      同一个列表，一条禁用一条启用，计数都必须算进来，否则「页签数字 = 点进去条数」不成立 */
const two = [
  toGroupable({ id: "x", name: "x", display_name: "X", description: "", group_name: "分组A", is_enabled: true }),
  toGroupable({ id: "y", name: "y", display_name: "Y", description: "", group_name: "分组A", is_enabled: false }),
];
out.empty_filters_ignores_status = computeGroupTabs(two, {}, UNGROUPED).total;

process.stdout.write(JSON.stringify(out));
