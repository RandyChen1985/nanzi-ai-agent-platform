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
  // 默认收起：一开始就收，不摊开也不闪
  pending_collapses: collapse({ isPreparation: true, status: 'pending' }),
  finished_collapses: collapse({ isPreparation: true, status: 'success' }),
  // 历史数据可能没有 status：同样收起
  finished_without_status_collapses: collapse({ isPreparation: true }),
  // 同一个节点在 pending -> success 的整个生命周期里判定不变，才不会闪
  stable_across_pending_to_success:
    collapse({ isPreparation: true, status: 'pending' }) === collapse({ isPreparation: true, status: 'success' }),
  // 出问题要留着让人看见，收起等于藏故障
  error_keeps_open: collapse({ isPreparation: true, status: 'error' }),
  interrupted_keeps_open: collapse({ isPreparation: true, status: 'interrupted' }),
  // 用户手动点过 -> 听用户的
  user_toggled_keeps_open: collapse({ isPreparation: true, userToggled: true, status: 'success' }),
  user_toggled_while_pending_keeps_open: collapse({ isPreparation: true, userToggled: true, status: 'pending' }),
  // 非准备父级（路由组、工具组）完全不受影响
  other_parent_untouched: collapse({ isPreparation: false, status: 'success' }),
  other_parent_untouched_when_pending: collapse({ isPreparation: false, status: 'pending' }),
};

// 派生判定的关键性质：同样的输入，无论调用多少次、对象重建多少次，结果一致
const first = collapse({ isPreparation: true, status: 'success' });
const again = collapse({ isPreparation: true, status: 'success' });
out.deterministic = first === again && first === true;

process.stdout.write(JSON.stringify(out));
