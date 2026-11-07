/**
 * 真跑 utils/paginationWindow.ts：页码窗口是纯函数，可以直接断言行为。
 *
 * 为什么不用字符串断言：页码算法最容易在边界上错（总页数刚好 7/8、
 * 当前页贴着首尾、总数为 0），这些只有真算一遍才看得出来。
 */
const fs = require("fs");
const path = require("path");
const Module = require("module");
const assert = require("assert");

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

const p = loadTs("frontend/src/utils/paginationWindow.ts");

// ---- 总页数 ----
assert.strictEqual(p.totalPagesOf(0, 20), 1, "0 条也算 1 页，不能出现「第 1/0 页」");
assert.strictEqual(p.totalPagesOf(20, 20), 1);
assert.strictEqual(p.totalPagesOf(21, 20), 2);
assert.strictEqual(p.totalPagesOf(250, 100), 3);
assert.strictEqual(p.totalPagesOf(250, 0), 250, "pageSize 非法时按 1 兜底，不能除以 0");
assert.strictEqual(p.totalPagesOf(-5, 20), 1, "负数总数当 0");

// ---- 越界兜底 ----
assert.strictEqual(p.clampPage(0, 5), 1);
assert.strictEqual(p.clampPage(-3, 5), 1);
assert.strictEqual(p.clampPage(9, 5), 5);
assert.strictEqual(p.clampPage(Number.NaN, 5), 1);
assert.strictEqual(p.clampPage(3, 0), 1, "总页数为 0 时按 1 页处理");

// ---- 页码窗口：≤7 全显示 ----
assert.deepStrictEqual(p.buildPageWindow(1, 1), [1]);
assert.deepStrictEqual(p.buildPageWindow(1, 5), [1, 2, 3, 4, 5]);
assert.deepStrictEqual(p.buildPageWindow(7, 7), [1, 2, 3, 4, 5, 6, 7]);

// ---- 页码窗口：>7 时首尾 + 当前±1 ----
assert.deepStrictEqual(p.buildPageWindow(1, 20), [1, 2, "…", 20], "首屏不显示左侧省略号");
assert.deepStrictEqual(
  p.buildPageWindow(2, 20),
  [1, 2, 3, "…", 20],
  "当前页贴着头，前一段不该出现省略号",
);
assert.deepStrictEqual(p.buildPageWindow(10, 20), [1, "…", 9, 10, 11, "…", 20]);
assert.deepStrictEqual(
  p.buildPageWindow(19, 20),
  [1, "…", 18, 19, 20],
  "当前页贴着尾，后一段不该出现省略号",
);
assert.deepStrictEqual(p.buildPageWindow(20, 20), [1, "…", 19, 20]);
// 越界输入不能把窗口算崩
assert.deepStrictEqual(p.buildPageWindow(999, 20), [1, "…", 19, 20]);

// ---- 区间文案 ----
assert.deepStrictEqual(p.pageRangeOf(0, 1, 20), { from: 0, to: 0 });
assert.deepStrictEqual(p.pageRangeOf(250, 1, 100), { from: 1, to: 100 });
assert.deepStrictEqual(p.pageRangeOf(250, 3, 100), { from: 201, to: 250 });
assert.deepStrictEqual(p.pageRangeOf(250, 99, 100), { from: 201, to: 250 }, "越界页码按末页算");

process.stdout.write("pagination window ok\n");
