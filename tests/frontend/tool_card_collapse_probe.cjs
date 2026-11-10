/**
 * 用 esbuild 把真实的 `processTimeline.ts` 剥成 CJS 跑起来，验证「执行完自动折叠」。
 *
 * 只做最少的脚手架：用 esbuild **打包**（bundle）而不是单文件 transform——该模块
 * 还会 import 运行时的 `./subagentTrace`（续聊文案），单文件转译后 node 到 require
 * 那一步就找不到模块，探针会在加载阶段假失败，而被测逻辑其实没坏。
 * 不复制任何业务逻辑。
 *
 * 输出一行 JSON，由 pytest 解析断言。
 */
const path = require("path");
const Module = require("module");

const ROOT = path.resolve(__dirname, "../..");
// esbuild 装在 frontend/node_modules 下，从仓库根直接 require 解析不到。
const esbuild = require(require.resolve("esbuild", { paths: [path.join(ROOT, "frontend")] }));
const SRC = path.join(ROOT, "frontend/src/utils/processTimeline.ts");

function load() {
  const { outputFiles } = esbuild.buildSync({
    entryPoints: [SRC],
    bundle: true,
    write: false,
    format: "cjs",
    platform: "node",
    target: "es2020",
    logLevel: "silent",
  });
  const code = outputFiles[0].text;
  const mod = new Module(SRC);
  mod.filename = SRC;
  mod.paths = Module._nodeModulePaths(path.dirname(SRC));
  mod._compile(code, SRC);
  return mod.exports;
}

const { upsertTimelineLog } = load();

function fresh() {
  return { processTimeline: [] };
}

const results = {};

// 1) pending 中被展开，收敛成 success 后自动收起
{
  const t = fresh();
  upsertTimelineLog(t, { id: "tool:1", title: "Bash", status: "pending", details: "ls -la" });
  const item = t.processTimeline[0];
  item.isExpanded = true; // 模拟执行中展开看输出（不算手动标记）
  upsertTimelineLog(t, { id: "tool:1", title: "Bash", status: "success", details: "total 0" });
  results.collapsed_on_success = item.isExpanded === false;
}

// 2) 收敛成 error 同样收起
{
  const t = fresh();
  upsertTimelineLog(t, { id: "tool:2", title: "Bash", status: "pending" });
  t.processTimeline[0].isExpanded = true;
  upsertTimelineLog(t, { id: "tool:2", status: "error", error_reason: "boom" });
  results.collapsed_on_error = t.processTimeline[0].isExpanded === false;
}

// 3) 用户手动点开过（userToggled）则保持展开，不被抽走
{
  const t = fresh();
  upsertTimelineLog(t, { id: "tool:3", title: "Bash", status: "pending" });
  const item = t.processTimeline[0];
  item.isExpanded = true;
  item.userToggled = true;
  upsertTimelineLog(t, { id: "tool:3", status: "success" });
  results.kept_when_user_toggled = item.isExpanded === true;
}

// 4) 已完成项再收到终态事件，不影响用户此刻的展开
{
  const t = fresh();
  upsertTimelineLog(t, { id: "tool:4", title: "Bash", status: "success" });
  const item = t.processTimeline[0];
  item.isExpanded = true; // 用户去看已完成工具的输出
  upsertTimelineLog(t, { id: "tool:4", status: "success", details: "again" });
  results.untouched_when_already_done = item.isExpanded === true;
}

// 5) 新建项默认收起
{
  const t = fresh();
  upsertTimelineLog(t, { id: "tool:5", title: "Bash", status: "pending" });
  results.new_item_collapsed = t.processTimeline[0].isExpanded === false;
}

// 6) 详情内容仍然保留（只是收起，不是丢弃）
{
  const t = fresh();
  upsertTimelineLog(t, { id: "tool:6", title: "Bash", status: "pending", details: "before" });
  t.processTimeline[0].isExpanded = true;
  upsertTimelineLog(t, { id: "tool:6", status: "success", details: "after" });
  results.details_kept = t.processTimeline[0].details === "after";
}

process.stdout.write(JSON.stringify(results));
