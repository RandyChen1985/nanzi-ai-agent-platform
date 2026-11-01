/**
 * 真跑 processTimeline.resolveTimelineExpanded：整张思考卡片的展开决策。
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
const decide = t.resolveTimelineExpanded;

const out = {
  // 生成完就折叠：开关开着也照样折（这是这次修的核心）
  done_collapses_even_when_setting_on: decide({ hasAnswer: true, hasPending: false, expandThoughts: true }),
  done_collapses_when_setting_off: decide({ hasAnswer: true, hasPending: false, expandThoughts: false }),
  done_collapses_when_setting_unset: decide({ hasAnswer: true, hasPending: false }),
  // 生成中听设置
  running_opens_when_setting_on: decide({ hasAnswer: false, hasPending: true, expandThoughts: true }),
  running_stays_closed_when_setting_off: decide({ hasAnswer: false, hasPending: true, expandThoughts: false }),
  running_opens_by_default_when_unset: decide({ hasAnswer: false, hasPending: true }),
  // 已有正文但工具还在跑：仍属于生成中，听设置
  body_but_tools_running_honors_setting_off: decide({ hasAnswer: true, hasPending: true, expandThoughts: false }),
  body_but_tools_running_opens_when_setting_on: decide({ hasAnswer: true, hasPending: true, expandThoughts: true }),
  // 用户点过标题：不要改
  user_toggled_is_noop_when_done: decide({ hasAnswer: true, hasPending: false, userToggled: true }),
  user_toggled_is_noop_when_running: decide({ hasAnswer: false, hasPending: true, expandThoughts: false, userToggled: true }),
  // 空输入：按「生成中、没设置」处理，默认展开
  empty_input_opens: decide({}),
};

process.stdout.write(JSON.stringify(out));
