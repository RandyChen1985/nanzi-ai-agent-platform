/**
 * 真跑 utils/kbDatasetFilter.ts：esbuild 剥掉类型后直接在 Node 里执行，
 * 断言的是实际过滤结果，不是源码里有没有某段字符串。
 */
const fs = require("fs");
const path = require("path");
const Module = require("module");

const ROOT = path.resolve(__dirname, "../..");
const esbuild = require(require.resolve("esbuild", {
  paths: [path.join(ROOT, "frontend")],
}));

function loadTs(relPath) {
  const abs = path.join(ROOT, relPath);
  const src = fs.readFileSync(abs, "utf8");
  const { code } = esbuild.transformSync(src, {
    loader: "ts",
    format: "cjs",
    target: "node18",
  });
  const mod = new Module(abs, module);
  mod.filename = abs;
  mod.paths = Module._nodeModulePaths(path.dirname(abs));
  mod._compile(code, abs);
  return mod.exports;
}

const f = loadTs("frontend/src/utils/kbDatasetFilter.ts");

const DAY = 86400000;
const now = Date.now();
const daysAgo = (n) => now - n * DAY;

const rows = [
  { id: "kb1", name: "产品规范", description: "PRD 与需求评审", visibility: "public", doc_count: 128, update_time: daysAgo(2), created_by: "张敏", can_write: true },
  { id: "kb2", name: "研发架构", description: "服务拆分", visibility: "team", document_count: 96, update_time: daysAgo(1), created_by: "陈晓龙", can_write: true },
  { id: "kb3", name: "会议纪要", description: "个人记录", visibility: "private", doc_count: 17, update_time: daysAgo(1), created_by: "陈晓龙", can_write: true },
  { id: "kb4", name: "法务条款", description: "合同模板", visibility: "public", doc_count: 14, update_time: daysAgo(44), created_by: "吴楠", can_write: false },
  { id: "kb5", name: "临时验证库", description: "空库", visibility: "private", doc_count: 0, update_time: daysAgo(0), created_by: "陈晓龙", can_write: true },
  { id: "kb6", name: "历史归档", platform_name: "历史项目归档", description: "已下线项目", visibility: "public", doc_count: 340, update_time: daysAgo(180), created_by: "周杰", can_write: false },
];

const empty = { keyword: "", scopes: [], updatedWithin: null, docsSize: null, writeAccess: null };
const ids = (list) => list.map((d) => d.id);

const out = {};

// 关键词：名称 / 描述 / 创建人 三个字段都要能命中
out.keyword_by_name = ids(f.filterAndSortDatasets(rows, { ...empty, keyword: "架构" }, "updated"));
out.keyword_by_desc = ids(f.filterAndSortDatasets(rows, { ...empty, keyword: "PRD" }, "updated"));
out.keyword_by_owner = ids(f.filterAndSortDatasets(rows, { ...empty, keyword: "陈晓龙" }, "updated"));
out.keyword_by_platform_name = ids(f.filterAndSortDatasets(rows, { ...empty, keyword: "历史项目" }, "updated"));
out.keyword_case_insensitive = ids(f.filterAndSortDatasets(rows, { ...empty, keyword: "prd" }, "updated")).length === 1;

// 可见范围
out.scope_team_only = ids(f.filterAndSortDatasets(rows, { ...empty, scopes: ["team"] }, "updated"));
out.scope_multi = ids(f.filterAndSortDatasets(rows, { ...empty, scopes: ["team", "private"] }, "updated")).sort();

// 更新时间
out.within_7 = ids(f.filterAndSortDatasets(rows, { ...empty, updatedWithin: "7" }, "updated")).sort();
out.within_30 = ids(f.filterAndSortDatasets(rows, { ...empty, updatedWithin: "30" }, "updated")).sort();
out.older_than_30 = ids(f.filterAndSortDatasets(rows, { ...empty, updatedWithin: "older" }, "updated")).sort();

// 文档规模
out.docs_empty = ids(f.filterAndSortDatasets(rows, { ...empty, docsSize: "empty" }, "updated"));
out.docs_small = ids(f.filterAndSortDatasets(rows, { ...empty, docsSize: "small" }, "updated")).sort();
out.docs_large = ids(f.filterAndSortDatasets(rows, { ...empty, docsSize: "large" }, "updated")).sort();

// 权限：document_count 也要能被识别成文档数
out.write_only = ids(f.filterAndSortDatasets(rows, { ...empty, writeAccess: "w" }, "updated")).sort();
out.read_only = ids(f.filterAndSortDatasets(rows, { ...empty, writeAccess: "ro" }, "updated")).sort();

// 排序
out.sort_updated = ids(f.filterAndSortDatasets(rows, empty, "updated"));
out.sort_docs = ids(f.filterAndSortDatasets(rows, empty, "docs"));
out.sort_name = ids(f.filterAndSortDatasets(rows, empty, "name"));

// 相同时间戳用 id 兜底，保证顺序稳定
const tie = [
  { id: "b", name: "B", update_time: 1000 },
  { id: "a", name: "A", update_time: 1000 },
];
out.sort_tie_break = ids(f.filterAndSortDatasets(tie, empty, "updated"));

// 范围计数不把当前 scopes 算进去：勾了 team 之后，另两个的数量应保持不变
const countsIfOnlyTeam = f.countByScope(rows, { ...empty, scopes: ["team"] });
const countsNoScope = f.countByScope(rows, empty);
out.counts_ignore_current_scope =
  countsIfOnlyTeam.public === countsNoScope.public &&
  countsIfOnlyTeam.private === countsNoScope.private &&
  countsNoScope.public === 3 &&
  countsNoScope.team === 1 &&
  countsNoScope.private === 2;

// 计数要跟随关键词变化
const countsKw = f.countByScope(rows, { ...empty, keyword: "陈晓龙" });
out.counts_follow_keyword = countsKw.public === 0 && countsKw.team === 1 && countsKw.private === 2;

// 7 天与 30 天的边界必须能分开
const boundary = [
  { id: "d3", name: "3天", update_time: daysAgo(3) },
  { id: "d15", name: "15天", update_time: daysAgo(15) },
  { id: "d60", name: "60天", update_time: daysAgo(60) },
];
out.boundary_7 = ids(f.filterAndSortDatasets(boundary, { ...empty, updatedWithin: "7" }, "updated"));
out.boundary_30 = ids(f.filterAndSortDatasets(boundary, { ...empty, updatedWithin: "30" }, "updated"));
out.boundary_older = ids(f.filterAndSortDatasets(boundary, { ...empty, updatedWithin: "older" }, "updated"));

// 时间戳兼容：秒级 / 毫秒级 / ISO 字符串
out.ts_seconds = f.toMs(1700000000) === 1700000000000;
out.ts_millis = f.toMs(1700000000000) === 1700000000000;
out.ts_iso = f.toMs("2026-11-01T00:00:00Z") === Date.parse("2026-11-01T00:00:00Z");
out.ts_garbage = f.toMs("not-a-date") === 0;

// 有筛选条件但时间戳缺失的库必须被排除，不能「侥幸」命中
out.missing_time_excluded =
  f.filterAndSortDatasets([{ id: "x", name: "无时间" }], { ...empty, updatedWithin: "7" }, "updated").length === 0;

// hasAnyFilter
out.has_filter_false = f.hasAnyFilter(empty) === false;
out.has_filter_keyword = f.hasAnyFilter({ ...empty, keyword: "a" }) === true;
out.has_filter_scope = f.hasAnyFilter({ ...empty, scopes: ["team"] }) === true;

// 未知可见范围按 public 兜底
out.unknown_visibility_is_public = f.scopeOf({ id: "z", name: "Z", visibility: "" }) === "public";

process.stdout.write(JSON.stringify(out));
