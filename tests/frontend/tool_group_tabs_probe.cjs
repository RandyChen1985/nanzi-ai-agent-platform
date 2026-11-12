/**
 * 真跑 `utils/toolGroupTabs.ts`：分组页签的过滤口径、每组命中数、页签顺序。
 *
 * 用 ts_module_loader 加载真实模块（不是把逻辑复制一份到测试里），所以这里断言的就是
 * 线上页面渲染页签时用的那份计算。
 */
const { loadModule } = require("./ts_module_loader.cjs");

const { computeGroupTabs, matchesToolFilters, toolGroupName } = loadModule(
  "frontend/src/utils/toolGroupTabs.ts",
);

const UNGROUPED = "__ungrouped__";

/** 12 个工具：3 个业务分组（4/3/2）+ 3 个未分组，其中 2 个停用 */
const TOOLS = [
  { name: "query_penalty_records", description: "查询履约罚款明细", url_template: "/api/penalty/records", method: "GET", is_active: true, group_name: "履约罚款" },
  { name: "create_penalty_notice", description: "生成罚款通知单", url_template: "/api/penalty/notice", method: "POST", is_active: true, group_name: "履约罚款" },
  { name: "update_penalty_rule", description: "更新罚款规则", url_template: "/api/penalty/rule/{id}", method: "PUT", is_active: true, group_name: "履约罚款" },
  { name: "void_penalty_notice", description: "作废罚款通知单", url_template: "/api/penalty/notice/{id}", method: "DELETE", is_active: false, group_name: "履约罚款" },

  { name: "query_device_status", description: "查询设备在线状态", url_template: "/api/device/status", method: "GET", is_active: true, group_name: "设备巡检" },
  { name: "list_inspection_plans", description: "列出巡检计划", url_template: "/api/inspection/plans", method: "GET", is_active: true, group_name: "设备巡检" },
  { name: "create_inspection_task", description: "创建巡检任务", url_template: "/api/inspection/tasks", method: "POST", is_active: true, group_name: "设备巡检" },

  { name: "search_tickets", description: "搜索工单", url_template: "/api/ticket/search", method: "GET", is_active: true, group_name: "工单处理" },
  { name: "create_ticket", description: "创建工单", url_template: "/api/ticket", method: "POST", is_active: true, group_name: "工单处理" },

  { name: "weather_query", description: "查询城市实时天气", url_template: "/api/weather/now", method: "GET", is_active: true, group_name: "" },
  { name: "exchange_rate", description: "查询汇率", url_template: "/api/fx/rate", method: "GET", is_active: true, group_name: "   " },
  { name: "holiday_calendar", description: "查询法定节假日", url_template: "/api/calendar/holiday", method: "GET", is_active: false, group_name: null },
];

/** 全部有分组的场景：用来验证「没有未分组时不该出现该页签」 */
const ALL_GROUPED = TOOLS.filter((tool) => toolGroupName(tool));

const out = {};

/* 1. 默认（无筛选）：页签顺序与计数 */
const base = computeGroupTabs(TOOLS, {}, UNGROUPED);
out.base_total = base.total;
out.base_ungrouped = base.ungrouped;
out.base_items = base.items.map((item) => [item.label, item.count]);
out.base_first_key = base.items[0].key;
out.base_last_key = base.items[base.items.length - 1].key;
out.base_group_names = base.groups.map(([name]) => name);
out.base_group_counts = base.groups.map(([, count]) => count);

/* 2. 按 HTTP 方法过滤：计数跟着方法走，且「组」只保留有命中的 */
const post = computeGroupTabs(TOOLS, { method: "POST" }, UNGROUPED);
out.post_total = post.total;
out.post_items = post.items.map((item) => [item.label, item.count]);
out.post_has_empty_group = post.items.some((item) => item.count === 0);

/* 3. 状态过滤：停用 */
const inactive = computeGroupTabs(TOOLS, { status: "inactive" }, UNGROUPED);
out.inactive_total = inactive.total;
out.inactive_items = inactive.items.map((item) => [item.label, item.count]);

/* 4. 关键词：命中名称 / 描述 */
const keyword = computeGroupTabs(TOOLS, { keyword: "罚款" }, UNGROUPED);
out.kw_total = keyword.total;
out.kw_items = keyword.items.map((item) => [item.label, item.count]);

/* 5. 组合：方法 + 状态 + 关键词一个都不命中 → 只剩「全部 0」 */
const none = computeGroupTabs(TOOLS, { method: "DELETE", status: "active", keyword: "天气" }, UNGROUPED);
out.none_total = none.total;
out.none_items = none.items.map((item) => [item.label, item.count]);

/* 6. 全部有分组：不出现未分组页签 */
const grouped = computeGroupTabs(ALL_GROUPED, {}, UNGROUPED);
out.grouped_labels = grouped.items.map((item) => item.label);
out.grouped_has_ungrouped = grouped.items.some((item) => item.key === UNGROUPED);

/* 7. 空白 group_name（空格 / null）都算未分组 */
out.blank_is_ungrouped = TOOLS.filter((tool) => toolGroupName(tool) === "").length;

/* 8. matchesToolFilters 单点：空筛选全放行、未知状态不放行停用工具 */
out.match_default = matchesToolFilters(TOOLS[0], {});
out.match_unknown_status_keeps_active = matchesToolFilters(TOOLS[0], { status: "whatever" });
out.match_inactive_only = matchesToolFilters(TOOLS[3], { status: "inactive" });

/* 9. 脚本工具场景：多一个「来源」维度，关键词要能搜到 display_name */
const SCRIPT_TOOLS = [
  { name: "parse_invoice", display_name: "发票解析", description: "把 PDF 发票转成结构化字段", source: "custom", is_active: true, group_name: "财务对账" },
  { name: "sync_erp", display_name: "同步 ERP", description: "定时同步 ERP 订单", source: "builtin", is_active: true, group_name: "财务对账" },
  { name: "gen_report", display_name: "生成日报", description: "按模板生成日报", source: "builtin", is_active: false, group_name: "工单处理" },
  { name: "hello_tool", display_name: "示例脚本", description: "最小示例", source: "builtin", is_active: true, group_name: "" },
];
const SCRIPT_FIELDS = ["name", "display_name", "description"];

const scriptBase = computeGroupTabs(SCRIPT_TOOLS, { searchFields: SCRIPT_FIELDS }, UNGROUPED);
out.script_base_items = scriptBase.items.map((item) => [item.label, item.count]);

const scriptCustom = computeGroupTabs(SCRIPT_TOOLS, { source: "custom", searchFields: SCRIPT_FIELDS }, UNGROUPED);
out.script_custom_items = scriptCustom.items.map((item) => [item.label, item.count]);

const scriptBuiltin = computeGroupTabs(SCRIPT_TOOLS, { source: "builtin", searchFields: SCRIPT_FIELDS }, UNGROUPED);
out.script_builtin_total = scriptBuiltin.total;

/* display_name 只有传入 searchFields 才参与匹配：证明这个参数真的被用上了 */
const scriptKw = computeGroupTabs(SCRIPT_TOOLS, { keyword: "发票解析", searchFields: SCRIPT_FIELDS }, UNGROUPED);
out.script_kw_with_display_name = scriptKw.total;
const scriptKwDefault = computeGroupTabs(SCRIPT_TOOLS, { keyword: "发票解析" }, UNGROUPED);
out.script_kw_without_display_name = scriptKwDefault.total;

console.log(JSON.stringify(out, null, 2));
