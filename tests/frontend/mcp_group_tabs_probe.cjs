/**
 * 真跑 `utils/toolGroupTabs.ts`，用**MCP 服务**的形状验证「MCP 工具集」的分组页签口径。
 *
 * MCP 与工具/数据集/技能/智能体的关键差异，必须在这里锁住：
 * 1. 这一页的分类不是「启用/停用」，而是 `enabled_status`（1/0 整数）。页面先按
 *    「仅运行中 + 关键词 + 待办筛选」筛一遍，再把结果交给纯函数（filters 传空），
 *    于是每个数字恒等于「切到这一组能看到的条数」；
 * 2. 搜索要同时覆盖 服务名 / 备注 / 地址（接口字段是 server_name / remark / sse_url），
 *    早期版本写成 description / url，两个字段都不存在，按备注搜永远是 0 结果；
 * 3. 待办筛选（未启用 / 一个工具都没发布 / 远端已失效 / 超 7 天未同步）是这一页特有的
 *    第二层筛选，它必须**参与**页签计数——否则勾上待办再看页签，数字会虚高。
 */
const { loadModule } = require("./ts_module_loader.cjs");

const { computeGroupTabs, toolGroupName, ALL_GROUP_KEY } = loadModule(
  "frontend/src/utils/toolGroupTabs.ts",
);

const UNGROUPED = "__ungrouped__";
const STALE_MS = 7 * 24 * 60 * 60 * 1000;
const NOW = new Date("2026-11-12T10:00:00+08:00").getTime();
const daysAgo = (days) => new Date(NOW - days * 24 * 3600 * 1000).toISOString();

/** 页面里 `toGroupableServer` 做的那次映射 */
const toGroupable = (server) => ({
  id: server.id,
  name: server.server_name,
  display_name: server.server_name,
  description: server.remark,
  url_template: server.sse_url,
  is_active: server.enabled_status === 1,
  group_name: server.group_name ?? "",
});

const SERVERS = [
  { id: "s1", server_name: "github-mcp", remark: "仓库与 PR 查询", sse_url: "https://mcp.internal/github", group_name: "研发效能", enabled_status: 1, tool_count: 14, published_tool_count: 14, stale_tool_count: 0, last_sync_at: daysAgo(0.1) },
  { id: "s2", server_name: "jira-mcp", remark: "需求与缺陷", sse_url: "https://mcp.internal/jira", group_name: "研发效能", enabled_status: 1, tool_count: 11, published_tool_count: 11, stale_tool_count: 0, last_sync_at: daysAgo(0.1) },
  { id: "s3", server_name: "gitlab-mcp", remark: "CI 流水线", sse_url: "https://mcp.internal/gitlab", group_name: "研发效能", enabled_status: 1, tool_count: 12, published_tool_count: 0, stale_tool_count: 0, last_sync_at: daysAgo(1) },
  { id: "s4", server_name: "postgres-mcp-readonly", remark: "只读数仓", sse_url: "https://mcp.internal/pg", group_name: "数据平台", enabled_status: 1, tool_count: 6, published_tool_count: 6, stale_tool_count: 0, last_sync_at: daysAgo(1) },
  { id: "s5", server_name: "clickhouse-mcp", remark: "明细查询", sse_url: "https://mcp.internal/ch", group_name: "数据平台", enabled_status: 1, tool_count: 5, published_tool_count: 5, stale_tool_count: 2, last_sync_at: daysAgo(2) },
  { id: "s6", server_name: "slack-mcp", remark: "频道与消息", sse_url: "https://mcp.internal/slack", group_name: "协作办公", enabled_status: 0, tool_count: 7, published_tool_count: 0, stale_tool_count: 0, last_sync_at: daysAgo(5) },
  { id: "s7", server_name: "dingtalk-mcp", remark: "群机器人与审批", sse_url: "https://mcp.internal/dingtalk", group_name: "协作办公", enabled_status: 1, tool_count: 9, published_tool_count: 9, stale_tool_count: 0, last_sync_at: daysAgo(2) },
  { id: "s8", server_name: "prometheus-mcp", remark: "指标查询", sse_url: "https://mcp.internal/prom", group_name: "运维监控", enabled_status: 1, tool_count: 6, published_tool_count: 6, stale_tool_count: 0, last_sync_at: daysAgo(2) },
  { id: "s9", server_name: "k8s-mcp", remark: "集群操作", sse_url: "https://mcp.internal/k8s", group_name: "运维监控", enabled_status: 1, tool_count: 13, published_tool_count: 4, stale_tool_count: 0, last_sync_at: daysAgo(9) },
  { id: "s10", server_name: "legacy-crm-mcp", remark: "老 CRM 桥接", sse_url: "https://mcp.legacy/crm", group_name: "测试验证", enabled_status: 1, tool_count: 4, published_tool_count: 0, stale_tool_count: 3, last_sync_at: daysAgo(12) },
  // 未分组的四种写法都要落进「未分组」，不能各自变成一个页签
  { id: "s11", server_name: "Renamed Server c1c8e5c3", remark: "", sse_url: "https://mcp.internal/a", group_name: "", enabled_status: 1, tool_count: 1, published_tool_count: 1, stale_tool_count: 0, last_sync_at: daysAgo(2) },
  { id: "s12", server_name: "Renamed Server 82634021", remark: "", sse_url: "https://mcp.internal/b", group_name: "   ", enabled_status: 1, tool_count: 1, published_tool_count: 1, stale_tool_count: 0, last_sync_at: daysAgo(2) },
  { id: "s13", server_name: "Test Server 4fff", remark: "", sse_url: "https://mcp.internal/c", group_name: null, enabled_status: 0, tool_count: 2, published_tool_count: 0, stale_tool_count: 0, last_sync_at: null },
  { id: "s14", server_name: "scratch-echo", remark: "", sse_url: "https://mcp.internal/d", group_name: undefined, enabled_status: 1, tool_count: 1, published_tool_count: 1, stale_tool_count: 0, last_sync_at: daysAgo(0.05) },
];

/** 页面里的 `serversMatchingFilters`（仅运行中 + 关键词 + 待办，不含分组） */
const matchesTodo = (server, key) => {
  if (key === "offline") return server.enabled_status !== 1;
  if (key === "unpublished") {
    return server.enabled_status === 1 && Number(server.published_tool_count || 0) === 0;
  }
  if (key === "stale-tools") return Number(server.stale_tool_count || 0) > 0;
  const ts = server.last_sync_at ? new Date(server.last_sync_at).getTime() : 0;
  return !ts || NOW - ts > STALE_MS;
};

const matching = ({ onlyRunning = false, keyword = "", todo = "" } = {}) =>
  SERVERS.filter((server) => {
    if (onlyRunning && server.enabled_status !== 1) return false;
    if (todo && !matchesTodo(server, todo)) return false;
    const k = keyword.trim().toLowerCase();
    if (!k) return true;
    // 与页面一致：服务名 / 备注 / 地址，外加已加载的工具名
    return [server.server_name, server.remark, server.sse_url]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(k);
  }).map(toGroupable);

const out = {};

/* 1. 默认（无筛选）：全部 → 各组按命中数降序 → 同数量按中文名 → 未分组末位 */
const base = computeGroupTabs(matching(), {}, UNGROUPED);
out.base_total = base.total;
out.base_ungrouped = base.ungrouped;
out.base_items = base.items.map((item) => [item.label, item.count]);
out.base_first_key = base.items[0].key;
out.base_last_key = base.items[base.items.length - 1].key;

/* 2. 「仅运行中」参与计数：slack / Test Server 被排除，总数与各组数字一起降 */
const running = computeGroupTabs(matching({ onlyRunning: true }), {}, UNGROUPED);
out.running_total = running.total;
out.running_items = running.items.map((item) => [item.label, item.count]);

/* 3. 页签数字 = 点进去的条数（逐组核对，用同一份上游列表） */
const rows = matching({ onlyRunning: true });
out.per_group_rows = ["研发效能", "数据平台", "协作办公", "运维监控", "测试验证"].map((name) => [
  name,
  rows.filter((row) => toolGroupName(row) === name).length,
]);

/* 4. 关键词命中备注与地址（不是只命中服务名） */
const kwRemark = computeGroupTabs(matching({ keyword: "只读数仓" }), {}, UNGROUPED);
out.kw_remark_total = kwRemark.total;
out.kw_remark_items = kwRemark.items.map((item) => [item.label, item.count]);
const kwUrl = computeGroupTabs(matching({ keyword: "legacy" }), {}, UNGROUPED);
out.kw_url_items = kwUrl.items.map((item) => [item.label, item.count]);

/* 5. 待办筛选参与计数：只看「一个工具都没发布」时，只剩 gitlab / legacy */
const todoUnpub = computeGroupTabs(matching({ todo: "unpublished" }), {}, UNGROUPED);
out.todo_unpublished_total = todoUnpub.total;
out.todo_unpublished_items = todoUnpub.items.map((item) => [item.label, item.count]);
const todoOffline = computeGroupTabs(matching({ todo: "offline" }), {}, UNGROUPED);
out.todo_offline_items = todoOffline.items.map((item) => [item.label, item.count]);
const todoStaleTools = computeGroupTabs(matching({ todo: "stale-tools" }), {}, UNGROUPED);
out.todo_stale_tools_items = todoStaleTools.items.map((item) => [item.label, item.count]);

/* 6. 空白 / null / 缺失都算未分组，不自成页签 */
out.blank_is_empty = toolGroupName({ group_name: "   " }) === "";
out.null_is_empty = toolGroupName({ group_name: null }) === "";
out.missing_is_empty = toolGroupName({}) === "";
out.trimmed = toolGroupName({ group_name: " 研发效能 " });

/* 7. 全部有分组时不出现「未分组」页签 */
out.all_grouped_has_ungrouped = computeGroupTabs(
  matching().filter((row) => toolGroupName(row)),
  {},
  UNGROUPED,
).items.some((item) => item.key === UNGROUPED);

/* 8. 只有一个真实分组、没有未分组时：items 只有「全部 + 该组」= 2 条，
      页面按 `items.length > 2` 判断，此时整行页签连开关都不渲染 */
out.single_group_items_length = computeGroupTabs(
  [toGroupable({ id: "only", server_name: "only-mcp", remark: "", sse_url: "", group_name: "研发效能", enabled_status: 1 })],
  {},
  UNGROUPED,
).items.length;

/* 9. filters 传空时 is_active 不参与判定（已由页面在上游筛好）：
      同一分组里一条启用一条停用，计数都必须算进来 */
out.empty_filters_ignores_status = computeGroupTabs(
  [
    toGroupable({ id: "x", server_name: "x", remark: "", sse_url: "", group_name: "分组A", enabled_status: 1 }),
    toGroupable({ id: "y", server_name: "y", remark: "", sse_url: "", group_name: "分组A", enabled_status: 0 }),
  ],
  {},
  UNGROUPED,
).total;

process.stdout.write(JSON.stringify(out));
