/**
 * 真跑 constants/userModeMenu.ts：esbuild 打包成 CJS 后在 Node 里执行，
 * 断言权限过滤的真实结果（而不是只看源码里有没有某个字符串）。
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

const { resolveUserModeMenu } = loadBundledTs("frontend/src/constants/userModeMenu.ts");

const out = {};

const keys = (items) => items.map((i) => i.key);
const childKeys = (item) => (item.children || []).map((c) => c.key);

// 普通用户：只有聊天 + 知识库权限
const limited = resolveUserModeMenu({
  menus: ["menu:ai_chat", "menu:knowledge_management"],
  isAdmin: false,
});
out.limited = keys(limited);
out.limitedMore = childKeys(limited.find((i) => i.key === "more") || {});
out.libraryLocked = (limited.find((i) => i.key === "library") || {}).locked;

// 没有知识库权限：资料库保留但置灰（唯一的 lock）
const noKb = resolveUserModeMenu({ menus: ["menu:ai_chat"], isAdmin: false });
out.noKb = keys(noKb);
out.noKbLibraryLocked = (noKb.find((i) => i.key === "library") || {}).locked;

// 连聊天权限都没有：新建任务直接消失（hide），不能留一个点进去被弹回的入口
const nothing = resolveUserModeMenu({ menus: [], isAdmin: false });
out.nothing = keys(nothing);

// 定时任务不挂权限：任何账号（甚至零权限）都能看到
out.tasksAlwaysVisible = keys(nothing).includes("tasks");

// 管理员：系统管理出现，且不受菜单码限制
const admin = resolveUserModeMenu({ menus: [], isAdmin: true });
out.admin = keys(admin);
out.adminMore = childKeys(admin.find((i) => i.key === "more") || {});
out.adminHasSystem = out.adminMore.includes("overview");

// 普通用户即使拿到全部菜单码，也看不到 adminOnly 的系统管理
const userAllMenus = resolveUserModeMenu({
  menus: [
    "menu:ai_chat",
    "menu:agent_management",
    "menu:skills_management",
    "menu:mcp_service",
    "menu:knowledge_management",
    "menu:memory_management",
    "menu:token_stats",
  ],
  isAdmin: false,
});
out.userAllMenusMore = childKeys(userAllMenus.find((i) => i.key === "more") || {});
out.userHasSystem = out.userAllMenusMore.includes("overview");

// 专家组：三个子项都没有权限时整组丢弃（不留一个点了没反应的父项）
const noExperts = resolveUserModeMenu({ menus: ["menu:ai_chat"], isAdmin: false });
out.hasExpertsGroup = keys(noExperts).includes("experts");

// 只有部分子项有权限：组保留，子项按权限裁剪
const partial = resolveUserModeMenu({
  menus: ["menu:ai_chat", "menu:skills_management"],
  isAdmin: false,
});
out.partialExperts = childKeys(partial.find((i) => i.key === "experts") || {});

const expect = {
  limited: ["new-chat", "experts", "tasks", "library", "workspace", "more"],
  limitedMore: ["workbench"],
  libraryLocked: false,
  noKb: ["new-chat", "experts", "tasks", "library", "workspace", "more"],
  noKbLibraryLocked: true,
  nothing: ["experts", "tasks", "library", "workspace", "more"],
  tasksAlwaysVisible: true,
  admin: ["new-chat", "experts", "tasks", "library", "workspace", "more"],
  adminMore: ["workbench", "token", "overview"],
  adminHasSystem: true,
  userAllMenusMore: ["workbench", "token"],
  userHasSystem: false,
  hasExpertsGroup: true,
  partialExperts: ["expert", "skill", "connector"],
};

const problems = [];
for (const key of Object.keys(expect)) {
  if (JSON.stringify(out[key]) !== JSON.stringify(expect[key])) {
    problems.push(`${key}: 期望 ${JSON.stringify(expect[key])}，实际 ${JSON.stringify(out[key])}`);
  }
}

if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}

console.log(JSON.stringify(out));
