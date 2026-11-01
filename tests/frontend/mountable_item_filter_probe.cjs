/**
 * 真跑 utils/mountableItemFilter.ts：esbuild 打包依赖后在 Node 里执行，
 * 断言实际过滤结果，而不是源码里有没有某段字符串。
 */
const fs = require("fs");
const path = require("path");
const Module = require("module");

const ROOT = path.resolve(__dirname, "../..");
const esbuild = require(require.resolve("esbuild", {
  paths: [path.join(ROOT, "frontend")],
}));

function loadBundledTs(relPath) {
  const abs = path.join(ROOT, relPath);
  const { outputFiles } = esbuild.buildSync({
    entryPoints: [abs],
    bundle: true,
    format: "cjs",
    platform: "node",
    target: "node18",
    write: false,
  });
  const mod = new Module(abs, module);
  mod.filename = abs;
  mod.paths = Module._nodeModulePaths(path.dirname(abs));
  mod._compile(outputFiles[0].text, abs);
  return mod.exports;
}

const f = loadBundledTs("frontend/src/utils/mountableItemFilter.ts");

/* 数据集形态：带 dataSource / tags / tableCount */
const datasets = [
  { id: "1", name: "智能体数据集", description: "覆盖智能体管理", dataSource: "mysql_aiagent", tags: ["核心"], tableCount: 42, updatedAt: "2026-10-30T10:00:00Z" },
  { id: "53", name: "pgdemo主数据表数据集", description: "客户主数据与订单", dataSource: "postgresql_demo", tags: ["销售"], tableCount: 18, updatedAt: "2026-10-20T10:00:00Z" },
  { id: "7", name: "智能体2", description: "另一套", dataSource: "mysql_aiagent", tags: [], tableCount: 5, updatedAt: "2026-11-01T10:00:00Z" },
  { id: "99", name: "数仓宽表", description: "离线数仓", dataSource: "clickhouse", tags: ["生产"], tableCount: 120, updatedAt: "" },
];

/* 知识库形态：带 docCount / visibility */
const knowledge = [
  { id: "k1", name: "产品规范", description: "PRD", docCount: 128, visibility: "public", updatedAt: 1700000000000 },
  { id: "k2", name: "研发架构", description: "服务拆分", docCount: 96, visibility: "team", updatedAt: 1700100000000 },
  { id: "k3", name: "会议纪要", description: "个人", docCount: 17, visibility: "private", updatedAt: 1700200000000 },
  { id: "k4", name: "法务条款", description: "合同", docCount: 14, visibility: "", updatedAt: 1699000000000 },
];

const empty = { keyword: "", dataSource: "", visibility: "" };
const ids = (list) => list.map((i) => i.id);

const out = {};

// 关键词：名称 / ID / 描述 / 数据源 / 标签
out.kw_name = ids(f.filterAndSortMountableItems(datasets, { ...empty, keyword: "智能体" }, "name"));
out.kw_id = ids(f.filterAndSortMountableItems(datasets, { ...empty, keyword: "53" }, "name"));
out.kw_desc = ids(f.filterAndSortMountableItems(datasets, { ...empty, keyword: "订单" }, "name"));
out.kw_data_source = ids(f.filterAndSortMountableItems(datasets, { ...empty, keyword: "clickhouse" }, "name"));
out.kw_tag = ids(f.filterAndSortMountableItems(datasets, { ...empty, keyword: "生产" }, "name"));
out.kw_case_insensitive = ids(f.filterAndSortMountableItems(datasets, { ...empty, keyword: "MYSQL" }, "name")).length === 2;

// 原有的「名称/ID/描述」匹配不能被削弱
out.kw_keeps_original_scope = ids(f.filterAndSortMountableItems(knowledge, { ...empty, keyword: "prd" }, "name"));

// 数据源筛选（只对数据集生效）
out.ds_filter = ids(f.filterAndSortMountableItems(datasets, { ...empty, dataSource: "mysql_aiagent" }, "name")).sort();
/*
 * 条件一旦传入就作用于所有行：知识库没有 dataSource 字段，会被整片排除。
 * 也就是说按资源类型隔离是**调用方**的责任（组件里只在数据集页签才传 dataSource），
 * 这里把这个约定固定下来，避免以后有人把两个条件一起传下去。
 */
out.data_source_not_isolated_by_kind =
  f.filterAndSortMountableItems(knowledge, { ...empty, dataSource: "mysql_aiagent" }, "name").length === 0;

// 可见范围筛选（只对知识库生效；空值按 public 兜底）
out.vis_team = ids(f.filterAndSortMountableItems(knowledge, { ...empty, visibility: "team" }, "name"));
out.vis_public = ids(f.filterAndSortMountableItems(knowledge, { ...empty, visibility: "public" }, "name")).sort();
/* 数据集没有 visibility，按 public 兜底，因此选「TEAM 级」会整片排除——同样由调用方隔离 */
out.visibility_not_isolated_by_kind =
  f.filterAndSortMountableItems(datasets, { ...empty, visibility: "team" }, "name").length === 0;

// 规模排序：数据集看表数、知识库看文档数
out.sort_size_datasets = ids(f.filterAndSortMountableItems(datasets, empty, "size"));
out.sort_size_knowledge = ids(f.filterAndSortMountableItems(knowledge, empty, "size"));

// 更新时间排序：空值排最后
out.sort_updated = ids(f.filterAndSortMountableItems(datasets, empty, "updated"));

// 名称排序只锁中文相对顺序（拉丁/汉字混排位置由 ICU 决定）
out.sort_name = ids(f.filterAndSortMountableItems(datasets, empty, "name"));

// id 兜底
out.sort_tie = ids(f.filterAndSortMountableItems(
  [{ id: "b", name: "b", tableCount: 3 }, { id: "a", name: "a", tableCount: 3 }],
  empty,
  "size",
));

// 选项与计数
out.data_sources = f.listMountableDataSources(datasets);
out.visibility_counts = f.countByVisibility(knowledge);

// 筛选条件判定
out.no_filter = f.hasAnyMountableFilter(empty) === false;
out.kw_filter = f.hasAnyMountableFilter({ keyword: "x", dataSource: "", visibility: "" }) === true;
out.ds_only = f.hasAnyMountableFilter({ keyword: "", dataSource: "clickhouse", visibility: "" }) === true;
out.vis_only = f.hasAnyMountableFilter({ keyword: "", dataSource: "", visibility: "team" }) === true;

// 组合：关键词 + 数据源同时生效
out.combined = ids(f.filterAndSortMountableItems(datasets, { keyword: "智能体", dataSource: "mysql_aiagent", visibility: "" }, "name"));

process.stdout.write(JSON.stringify(out));
