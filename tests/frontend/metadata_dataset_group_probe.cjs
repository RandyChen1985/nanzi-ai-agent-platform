/**
 * 真跑 `utils/toolGroupTabs.ts`，用**数据集**的形状验证：元数据管理页的分组页签口径。
 *
 * 数据集与工具的两处差异必须在这里锁住，否则「页签写着 3 个、点进去只有 2 个」：
 * 1. 数据集是 `status` 整型（1 = 启用），页面先映射成 `is_active` 布尔再交给纯函数；
 * 2. 数据集搜索的字段是 name / display_name / description（工具那套默认含 url_template）。
 */
const { loadModule } = require("./ts_module_loader.cjs");

const { computeGroupTabs, matchesToolFilters, toolGroupName } = loadModule(
  "frontend/src/utils/toolGroupTabs.ts",
);

const UNGROUPED = "__ungrouped__";

/** 页面里 `datasetGroupTools` 做的那次映射：status → is_active，group_name 归一为空串 */
const toGroupable = (ds) => ({
  name: ds.name,
  display_name: ds.display_name,
  description: ds.description,
  is_active: ds.status === 1,
  group_name: ds.group_name || "",
});

const DATASETS = [
  { name: "finance_ledger", display_name: "总账与凭证", description: "总账凭证、科目余额", status: 1, group_name: "财务核算" },
  { name: "finance_reconcile", display_name: "对账明细", description: "银行与业务系统对账", status: 1, group_name: "财务核算" },
  { name: "finance_ar_ap", display_name: "应收应付", description: "应收应付账龄", status: 0, group_name: "财务核算" },

  { name: "sales_orders", display_name: "订单交易事实", description: "订单主表与明细", status: 1, group_name: "销售经营" },
  { name: "sales_customer", display_name: "客户主数据", description: "客户档案与分级", status: 1, group_name: "销售经营" },
  { name: "sales_channel", display_name: "渠道与推广", description: "渠道投放与转化", status: 0, group_name: "销售经营" },

  { name: "supply_chain", display_name: "供应链履约", description: "采购入库与时效", status: 1, group_name: "供应链" },
  { name: "inventory_stock", display_name: "库存快照", description: "每日库存快照", status: 1, group_name: "供应链" },

  // 未分组的四种写法都要落进「未分组」，不能各自变成一个页签
  { name: "adhoc_sandbox", display_name: "临时分析沙箱", description: "分析师临时建表", status: 1, group_name: "" },
  { name: "legacy_backup", display_name: "历史归档库", description: "三年前归档", status: 0, group_name: "   " },
  { name: "kb_doc", display_name: "文档素材", description: "知识库结构化素材", status: 1, group_name: null },
  { name: "ops_ticket", display_name: "工单流转", description: "工单创建与关闭", status: 1 },
];

const TOOLS = DATASETS.map(toGroupable);

/** 页面里 `datasetFilters` 的等价物：数据集搜索不看 url_template，但要看 display_name */
const filters = (keyword = "", status = "all") => ({
  keyword,
  status,
  searchFields: ["name", "display_name", "description"],
});

const out = {};

/* 1. 默认：顺序 = 全部 → 各组（命中数降序）→ 未分组（固定末位） */
const base = computeGroupTabs(TOOLS, filters(), UNGROUPED);
out.base_total = base.total;
out.base_ungrouped = base.ungrouped;
out.base_items = base.items.map((item) => [item.label, item.count]);
out.base_first_key = base.items[0].key;
out.base_last_key = base.items[base.items.length - 1].key;

/* 2. 状态筛选参与页签计数：只看已启用时，各组合计数下降 */
const active = computeGroupTabs(TOOLS, filters("", "active"), UNGROUPED);
out.active_total = active.total;
out.active_items = active.items.map((item) => [item.label, item.count]);

/* 3. 关键词命中 display_name（数据集的搜索确实匹配显示名，工具默认字段里没有它）。
   用「交易事实」：它只出现在 `sales_orders` 的显示名里，描述是「订单主表与明细」、
   name 是 `sales_orders`，所以这一条能否命中完全取决于 searchFields 有没有带 display_name。 */
const kw = computeGroupTabs(TOOLS, filters("交易事实"), UNGROUPED);
out.kw_display_name_total = kw.total;
out.kw_display_name_labels = kw.items.map((item) => item.label);

/* 4. 同一个关键词用工具默认字段（不含 display_name）会落空 —— 证明 searchFields 真的生效 */
out.kw_default_fields_total = computeGroupTabs(
  TOOLS,
  { keyword: "交易事实" },
  UNGROUPED,
).total;

/* 5. 关键词命中 description */
out.kw_description_total = computeGroupTabs(TOOLS, filters("归档"), UNGROUPED).total;

/* 6. 关键词把某组搜空 → 该组页签消失（点不到空组） */
const kw2 = computeGroupTabs(TOOLS, filters("库存"), UNGROUPED);
out.kw_narrow_total = kw2.total;
out.kw_narrow_items = kw2.items.map((item) => [item.label, item.count]);

/* 7. 全部有分组时不该出现「未分组」页签 */
const allGrouped = computeGroupTabs(
  TOOLS.filter((tool) => toolGroupName(tool)),
  filters(),
  UNGROUPED,
);
out.all_grouped_has_ungrouped = allGrouped.items.some((item) => item.key === UNGROUPED);

/* 8. 空白分组名一律等于「未分组」，不能自成页签 */
out.blank_group_is_empty = toolGroupName({ group_name: "   " }) === "";
out.null_group_is_empty = toolGroupName({ group_name: null }) === "";
out.trimmed_group = toolGroupName({ group_name: " 财务核算 " });

/* 9. 单一分组（只有「全部」）→ 页签条应整体隐藏（组件按 items.length > 1 判断） */
const single = computeGroupTabs(
  [{ name: "only_one", display_name: "唯一", description: "", is_active: true, group_name: "财务核算" }],
  filters(),
  UNGROUPED,
);
out.single_group_items_length = single.items.length;

/* 10. 过滤口径与页签计数同源：逐条判定命中的条数，必须等于页签「全部」上的数字 */
const inactive = computeGroupTabs(TOOLS, filters("", "inactive"), UNGROUPED);
out.inactive_items = inactive.items.map((item) => [item.label, item.count]);
out.consistency = TOOLS.filter((tool) => matchesToolFilters(tool, filters("", "inactive"))).length;

process.stdout.write(JSON.stringify(out));
