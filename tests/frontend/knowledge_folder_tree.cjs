/**
 * 真跑 utils/knowledgeFolders.ts：esbuild 剥掉类型后直接在 Node 里执行。
 * 断言的是解析出来的层级结构，不是源码里有没有某段字符串。
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

const f = loadTs("frontend/src/utils/knowledgeFolders.ts");

// ---- 路径工具 ----
assert.deepStrictEqual(f.splitFolderPath(" 制度文件 / 2025 年度 "), ["制度文件", "2025 年度"], "首尾空格与空段要归一");
assert.deepStrictEqual(f.splitFolderPath("//a///b//"), ["a", "b"], "连续斜杠不能产生空段");
assert.deepStrictEqual(f.splitFolderPath(""), [], "空路径没有段");
assert.strictEqual(f.joinFolderPath(["a", "b"]), "a/b");
assert.strictEqual(f.parentFolderPath("a/b/c"), "a/b");
assert.strictEqual(f.parentFolderPath("a"), "", "根级的父级是空串");
assert.strictEqual(f.folderNameOf("a/b/c"), "c");
assert.strictEqual(f.folderNameOf(""), "");

// ---- 老数据：纯单层，行为必须与改造前一致 ----
const legacy = f.buildFolderIndex({
  "制度文件": ["d1", "d2"],
  "培训资料": ["d3"],
});
assert.strictEqual(legacy.topLevel.length, 2, "单层结构的顶层就是两个 key");
assert.ok(legacy.topLevel.includes("制度文件") && legacy.topLevel.includes("培训资料"));
assert.deepStrictEqual(legacy.byPath.get("制度文件").docIds, ["d1", "d2"]);
assert.strictEqual(legacy.byPath.get("制度文件").totalDocCount, 2);
assert.strictEqual(legacy.byPath.get("制度文件").depth, 0);
assert.deepStrictEqual([...legacy.assigned].sort(), ["d1", "d2", "d3"], "assigned 覆盖所有被归类的文档");

// ---- 路径式 key：自动补出中间层 ----
const nested = f.buildFolderIndex({
  "制度文件/2025 年度": ["d1"],
  "制度文件/2024 年度": ["d2", "d3"],
});
assert.deepStrictEqual(nested.topLevel, ["制度文件"], "只有一级节点出现在顶层");
assert.ok(nested.byPath.has("制度文件"), "缺 key 的中间层要补出来");
assert.deepStrictEqual(nested.byPath.get("制度文件").docIds, [], "父级本身不直接持有文档");
assert.deepStrictEqual(
  [...nested.byPath.get("制度文件").children].sort((a, b) => (a < b ? -1 : 1)),
  ["制度文件/2024 年度", "制度文件/2025 年度"],
  "子节点要挂到父级下",
);
assert.strictEqual(nested.byPath.get("制度文件").totalDocCount, 3, "父级文档数含子孙");
assert.strictEqual(nested.byPath.get("制度文件/2025 年度").depth, 1);
assert.strictEqual(nested.byPath.get("制度文件/2025 年度").parentPath, "制度文件");

// ---- 同名不同层互不冲突（老结构最大的毛病） ----
const sameName = f.buildFolderIndex({
  "制度文件/年度": ["d1"],
  "模板中心/年度": ["d2"],
});
assert.strictEqual(sameName.byPath.get("制度文件/年度").name, "年度");
assert.strictEqual(sameName.byPath.get("模板中心/年度").name, "年度");
assert.strictEqual(sameName.byPath.size, 4, "两个同名文件夹 + 两个父级 = 4 个节点");
assert.strictEqual(sameName.byPath.get("制度文件/年度").docIds[0], "d1");
assert.strictEqual(sameName.byPath.get("模板中心/年度").docIds[0], "d2");

// ---- 脏数据兜底 ----
const dirty = f.buildFolderIndex({
  " 制度文件 ": ["d1", "", null, 123],
});
assert.deepStrictEqual(dirty.byPath.get("制度文件").docIds, ["d1"], "非字符串与空 ID 一律丢弃");
assert.deepStrictEqual(f.buildFolderIndex(undefined).topLevel, [], "结构缺失时返回空索引");
assert.deepStrictEqual(f.buildFolderIndex(null).topLevel, [], "null 同样安全");
assert.deepStrictEqual(f.buildFolderIndex({}).topLevel, []);

// 同一路径重复出现时合并文档而不是覆盖
const duplicated = f.buildFolderIndex({ "A": ["d1"], "A/": ["d2"] });
assert.deepStrictEqual(duplicated.byPath.get("A").docIds, ["d1", "d2"], "`A` 与 `A/` 归一成同一节点并合并");

// ---- 移动合法性：不能把文件夹拖进自己的子树 ----
assert.strictEqual(f.canMoveFolderInto("A", "B"), true);
assert.strictEqual(f.canMoveFolderInto("A", "A"), false, "不能移到自己里面");
assert.strictEqual(f.canMoveFolderInto("A", "A/B"), false, "不能移到自己的子目录");
assert.strictEqual(f.canMoveFolderInto("A/B", "A"), true, "子目录可以上移到父级");

console.log("knowledge_folder_tree: all assertions passed");
