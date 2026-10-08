// 用 Node 真跑纯函数：静态字符串断言看不出「.json 含子串 js 会不会被判成 JavaScript」，
// 这里直接调用真实实现，用一批真实文件名做行为验证。
import assert from 'node:assert/strict'

import { resolveHighlightLanguage } from '../../frontend/src/utils/documentPreviewFormats.ts'

const CASES = [
  // --- 数据与配置 ---
  ['config.json', 'json'],
  ['api.json', 'json'],
  ['settings.json5', 'json'],
  ['data.jsonc', 'json'],
  ['nb.ipynb', 'json'],
  ['app.yaml', 'yaml'],
  ['app.yml', 'yaml'],
  ['Cargo.toml', 'ini'],
  ['setup.ini', 'ini'],
  ['CONFIG.JSON', 'json'], // 大写扩展名
  // --- 语言 ---
  ['main.py', 'python'],
  ['app.js', 'javascript'],
  ['m.mjs', 'javascript'],
  ['c.cjs', 'javascript'],
  ['x.jsx', 'javascript'],
  ['a.ts', 'typescript'],
  ['b.tsx', 'typescript'],
  ['Comp.vue', 'xml'],
  ['deploy.sh', 'bash'],
  ['run.bash', 'bash'],
  ['schema.sql', 'sql'],
  ['Main.java', 'java'],
  ['main.go', 'go'],
  ['lib.rs', 'rust'],
  ['x.rb', 'ruby'],
  ['i.php', 'php'],
  ['a.c', 'c'],
  ['a.h', 'c'],
  ['a.cpp', 'cpp'],
  ['a.hpp', 'cpp'],
  ['Program.cs', 'csharp'],
  ['a.swift', 'swift'],
  ['a.kt', 'kotlin'],
  // --- 文本与标记 ---
  ['README.md', 'markdown'],
  ['notes.txt', 'plaintext'],
  ['crash.log', 'plaintext'],
  ['index.html', 'xml'],
  ['page.htm', 'xml'],
  ['style.css', 'css'],
  ['fix.diff', 'diff'],
  ['change.patch', 'diff'],
]

// --- 曾经被文件名子串判定误伤的真实文件名（本次修复的回归护栏）---
const SUBSTRING_TRAP_CASES = [
  // `.json` 含子串 `js`：曾被判成 javascript，导致 json 分支成为不可达代码
  ['config.json', 'json'],
  ['package.json', 'json'],
  ['tsconfig.json', 'json'],
  // `.txt` 含子串 `ts`（asse-ts）：曾被判成 typescript
  ['assets.txt', 'plaintext'],
  ['results.txt', 'plaintext'],
  // 含子串 `sh`（fi-sh / fini-sh）：曾被判成 bash
  ['fish.txt', 'plaintext'],
  ['finish.md', 'markdown'],
  // 文件名不含 "python" 三字就丢高亮：曾被判成 txt
  ['main.py', 'python'],
  ['app.py', 'python'],
  // 含子串 `js` 的未知扩展名文件名（.bak 未知，此时才走文本兜底）
  ['notes.jsx.bak', 'plaintext'],
]

for (const [name, expected] of CASES) {
  const actual = resolveHighlightLanguage(name)
  assert.equal(actual, expected, `${name} 应判为 ${expected}，实际为 ${actual}`)
}

for (const [name, expected] of SUBSTRING_TRAP_CASES) {
  const actual = resolveHighlightLanguage(name)
  assert.equal(actual, expected, `子串陷阱：${name} 应判为 ${expected}，实际为 ${actual}`)
}

// --- 无扩展名（AI 生成内容的标题往往是描述而非文件名）：保留文本兜底 ---
const NO_EXTENSION_CASES = [
  ['Python 脚本', 'python'],
  ['TypeScript 工具函数', 'typescript'],
  ['Shell 部署脚本', 'bash'],
  ['JSON 配置片段', 'json'],
  ['Markdown 文档', 'markdown'],
  ['未知内容', 'plaintext'],
  ['generated-file', 'plaintext'],
]

for (const [name, expected] of NO_EXTENSION_CASES) {
  const actual = resolveHighlightLanguage(name)
  assert.equal(actual, expected, `无扩展名：${name} 应判为 ${expected}，实际为 ${actual}`)
}

// --- 未知扩展名不应被当作已知语言，且不得因文件名里的零散字母而误判 ---
for (const name of ['archive.xyz', 'data.bin', 'thing.unknown']) {
  assert.equal(
    resolveHighlightLanguage(name),
    'plaintext',
    `${name} 是未知扩展名，应回落纯文本`,
  )
}

// --- 空值与非字符串输入不得抛错 ---
for (const value of ['', null, undefined]) {
  assert.equal(resolveHighlightLanguage(value), 'plaintext', `${String(value)} 应回落纯文本`)
}

// --- 返回值必须是 highlight.js 能识别的语言 id，否则会静默退化成纯文本转义 ---
// 用 createRequire 锚定到 frontend/：highlight.js 装在 frontend/node_modules，
// 而本脚本位于 tests/frontend/，裸说明符按 Node 的向上查找规则解析不到它。
import { createRequire } from 'node:module'

const requireFromFrontend = createRequire(new URL('../../frontend/package.json', import.meta.url))
const hljs = requireFromFrontend('highlight.js')

const returned = new Set(
  [...CASES, ...SUBSTRING_TRAP_CASES, ...NO_EXTENSION_CASES].map(([, lang]) => lang),
)
for (const lang of returned) {
  assert.ok(
    hljs.getLanguage(lang),
    `highlight.js 未注册语言 id「${lang}」，会导致高亮静默失效`,
  )
}

console.log('高亮语言判定测试全部通过')
