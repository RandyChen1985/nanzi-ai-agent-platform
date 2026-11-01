/**
 * 真跑 utils/metadataDatasetFilter.ts：esbuild 连同它依赖的 kbDatasetFilter
 * 一起打包成 CJS 后在 Node 里执行，断言实际过滤结果。
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

const f = loadBundledTs("frontend/src/utils/metadataDatasetFilter.ts");

const rows = [
  { id: 1, name: "mysql_aiagent", display_name: "智能体数据集", description: "覆盖智能体管理、元数据治理与系统配置", data_source: "mysql_aiagent", tags: ["核心", "生产"], table_count: 42, updated_at: "2026-10-30T10:00:00Z" },
  { id: 53, name: "postgresql_demo", display_name: "pgdemo主数据表数据集", description: "客户主数据、订单交易事实与产品基础信息", data_source: "postgresql_demo", tags: ["销售"], table_count: 18, updated_at: "2026-10-20T10:00:00Z" },
  { id: 7, name: "agent2", display_name: "智能体2", description: undefined, data_source: "mysql_aiagent", tags: [], table_count: 5, updated_at: "2026-11-01T10:00:00Z" },
  { id: 99, name: "warehouse", display_name: "数仓宽表", description: "离线数仓", data_source: "clickhouse", tags: ["生产"], table_count: 120, updated_at: undefined, created_at: "2026-09-01T00:00:00Z" },
];

const empty = { keyword: "", dataSource: "" };
const ids = (list) => list.map((d) => d.id);

const out = {};

// 关键词：显示名 / 内部名 / 描述 / 数据源 / 标签
out.kw_display_name = ids(f.filterAndSortMetadataDatasets(rows, { ...empty, keyword: "智能体" }, "name"));
out.kw_internal_name = ids(f.filterAndSortMetadataDatasets(rows, { ...empty, keyword: "agent2" }, "name"));
out.kw_description = ids(f.filterAndSortMetadataDatasets(rows, { ...empty, keyword: "订单" }, "name"));
out.kw_data_source = ids(f.filterAndSortMetadataDatasets(rows, { ...empty, keyword: "clickhouse" }, "name"));
out.kw_tag = ids(f.filterAndSortMetadataDatasets(rows, { ...empty, keyword: "生产" }, "name")).sort();
out.kw_case_insensitive = ids(f.filterAndSortMetadataDatasets(rows, { ...empty, keyword: "MYSQL" }, "name")).length === 2;

// 数据源筛选
out.ds_mysql = ids(f.filterAndSortMetadataDatasets(rows, { ...empty, dataSource: "mysql_aiagent" }, "name")).sort();
out.ds_none_matches = f.filterAndSortMetadataDatasets(rows, { ...empty, dataSource: "不存在" }, "name").length === 0;

// 排序
out.sort_name = ids(f.filterAndSortMetadataDatasets(rows, empty, "name"));
out.sort_tables = ids(f.filterAndSortMetadataDatasets(rows, empty, "tables"));
out.sort_updated = ids(f.filterAndSortMetadataDatasets(rows, empty, "updated"));

// id 兜底：同样表数时按 id 升序
const tie = [
  { id: 9, name: "b", table_count: 3 },
  { id: 2, name: "a", table_count: 3 },
];
out.sort_tie = ids(f.filterAndSortMetadataDatasets(tie, empty, "tables"));

// updated 排序要能回落到 created_at
out.sort_updated_fallback_created_at =
  ids(f.filterAndSortMetadataDatasets(
    [
      { id: 1, name: "old", updated_at: "2026-01-01T00:00:00Z" },
      { id: 2, name: "newer", created_at: "2026-06-01T00:00:00Z" },
    ],
    empty,
    "updated",
  ))[0] === 2;

// 数据源选项去重并排序
out.options = f.listDataSourceOptions(rows);
out.counts = f.countByDataSource(rows);

// 空筛选判定
out.no_filter = f.hasAnyMetadataFilter(empty) === false;
out.kw_filter = f.hasAnyMetadataFilter({ maybe: 1, keyword: "x", dataSource: "" }) === true;
out.ds_filter = f.hasAnyMetadataFilter({ keyword: "", dataSource: "clickhouse" }) === true;

// 缺显示名时回落到内部名，再回落到 #id
out.label_fallback_name = f.labelOf({ id: 5, name: "raw_name" }) === "raw_name";
out.label_fallback_id = f.labelOf({ id: 5 }) === "#5";

process.stdout.write(JSON.stringify(out));
