/**
 * 加载真实 `.ts` 模块给 node 探针用（支持相对依赖递归）。
 *
 * 为什么需要这层：多个契约测试要跑「真正的 `processTimeline.ts`」而不是复制逻辑，
 * 原先各自 `ts.transpileModule` 转译单文件再把 node 的 `require` 塞进去——这成立
 * 的前提是被测模块只有 `import type`（编译期擦除）。一旦它 import 了运行时模块
 * （例如 `./subagentTrace` 的续聊文案），单文件加载就会在 require 处报
 * `Cannot find module './subagentTrace'`，一批探针集体失效，而失败信息看起来像是
 * 被测逻辑坏了。
 *
 * 这里提供一个能解析相对 TS 依赖的 require，探针不必再关心被测模块有没有依赖。
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "../..");
const ts = require(path.join(ROOT, "frontend/node_modules/typescript"));
const cache = new Map();

/** 相对路径按 node 的解析顺序找：原样 → .ts → .js → 目录 index.ts。 */
function resolveSpecifier(specifier, fromFile) {
  if (!specifier.startsWith(".")) return null;
  const base = path.resolve(path.dirname(fromFile), specifier);
  const candidates = [base, `${base}.ts`, `${base}.js`, path.join(base, "index.ts")];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  return null;
}

function loadModule(filePath) {
  const absolute = path.resolve(ROOT, filePath);
  const cached = cache.get(absolute);
  if (cached) return cached.exports;

  const source = fs.readFileSync(absolute, "utf8");
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;

  // 先登记再执行：模块间循环依赖时不会无限递归。
  const moduleRef = { exports: {} };
  cache.set(absolute, moduleRef);

  const localRequire = (specifier) => {
    const resolved = resolveSpecifier(specifier, absolute);
    // 非相对路径（node 内置模块、已安装依赖）交回给 node 自己解析。
    return resolved ? loadModule(resolved) : require(specifier);
  };

  new Function("module", "exports", "require", code)(
    moduleRef,
    moduleRef.exports,
    localRequire,
  );
  return moduleRef.exports;
}

module.exports = { loadModule, ROOT };
