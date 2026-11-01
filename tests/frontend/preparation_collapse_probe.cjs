/**
 * 真跑 processTimeline.shouldCollapsePreparationChildren：
 * 准备父级是否收起必须是派生判定，这里把真值表固定下来。
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

const t = loadBundledTs("frontend/src/utils/processTimeline.ts");
const collapse = t.shouldCollapsePreparationChildren;

const out = {
  // 这一步跑完就收，不必等整轮回答结束
  finished_collapses: collapse({ isPreparation: true, status: 'success', childrenPending: false }),
  // 历史数据可能没有 status：视为已完成
  finished_without_status_collapses: collapse({ isPreparation: true, childrenPending: false }),
  // 自身还在跑 -> 展开（准备阶段是此刻唯一能看到的进展）
  self_pending_keeps_open: collapse({ isPreparation: true, status: 'pending', childrenPending: false }),
  // 子步骤还有 pending -> 展开
  child_pending_keeps_open: collapse({ isPreparation: true, status: 'success', childrenPending: true }),
  // 出问题要留着让人看见，收起等于藏故障
  error_keeps_open: collapse({ isPreparation: true, status: 'error', childrenPending: false }),
  interrupted_keeps_open: collapse({ isPreparation: true, status: 'interrupted', childrenPending: false }),
  error_with_pending_child_keeps_open: collapse({ isPreparation: true, status: 'error', childrenPending: true }),
  // 用户手动点过 -> 听用户的
  user_toggled_keeps_open: collapse({ isPreparation: true, userToggled: true, status: 'success', childrenPending: false }),
  // 非准备父级（路由组、工具组）完全不受影响
  other_parent_untouched: collapse({ isPreparation: false, status: 'success', childrenPending: false }),
  other_parent_untouched_when_pending: collapse({ isPreparation: false, status: 'pending', childrenPending: true }),
  // 缺参按「已完成且无 pending」处理，不能崩
  undefined_tolerated: collapse({ isPreparation: true }),
};

// 派生判定的关键性质：同样的输入，无论调用多少次、对象重建多少次，结果一致
const first = collapse({ isPreparation: true, status: 'success', childrenPending: false });
const again = collapse({ isPreparation: true, status: 'success', childrenPending: false });
out.deterministic = first === again && first === true;

process.stdout.write(JSON.stringify(out));
