// 用 Node 真跑纯函数：判定必须只看扩展名，且 .md/.html/.py/.sh 这三类
// 「画布专属渲染」的格式绝不能被误判为可交给 file-viewer。
import assert from 'node:assert/strict'

import { shouldPreviewWithFileViewerInCanvas } from '../../frontend/src/utils/documentPreviewFormats.ts'

// --- 默认交给 file-viewer 预览的文本/代码格式 ---
const PREVIEW_CASES = [
  'config.json', 'data.jsonc', 'settings.json5', 'nb.ipynb',
  'app.yaml', 'app.yml', 'Cargo.toml', 'setup.ini',
  'main.js', 'm.mjs', 'c.cjs', 'x.jsx',
  'a.ts', 'b.tsx', 'Comp.vue',
  'schema.sql', 'Main.java', 'main.go', 'lib.rs', 'x.rb', 'i.php',
  'a.c', 'a.h', 'a.cpp', 'a.hpp', 'Program.cs', 'a.swift', 'a.kt',
  'notes.txt', 'crash.log', 'style.css', 'api.http', 'fix.diff', 'change.patch',
  'CONFIG.JSON', // 大写扩展名
]

// --- 必须在画布侧渲染，绝不能交给 file-viewer ---
const CANVAS_NATIVE_CASES = [
  // markdown：画布渲染器带 mermaid + ECharts，file-viewer 无 ECharts
  'README.md', 'doc.markdown',
  // HTML：画布做 sandbox 片段渲染，file-viewer 只显示源码
  'index.html', 'page.htm',
  // 可执行脚本：画布支持运行 + 实时输出
  'deploy.sh', 'run.bash', 'main.py', 'app.py',
]

// --- 既非 Office/PDF（另有分派），也不该由本函数接管 ---
const OTHER_CASES = [
  'a.docx', 'a.doc', 'a.xlsx', 'a.xls', 'a.xlsm', 'a.pptx', // Office
  'a.ppt',                                                  // 下载
  'a.pdf',                                                  // pdf 类型
  'data.csv',                                               // 画布 PivotTable
  'a.png', 'a.jpg',                                          // 图片
  '.env', 'a.conf', 'a.rst', 'a.xyz',                        // file-viewer 不支持
  '', 'noextension',                                         // 无扩展名
]

for (const name of PREVIEW_CASES) {
  assert.equal(
    shouldPreviewWithFileViewerInCanvas(name),
    true,
    `${name} 应默认交给 file-viewer`,
  )
}

for (const name of CANVAS_NATIVE_CASES) {
  assert.equal(
    shouldPreviewWithFileViewerInCanvas(name),
    false,
    `${name} 必须在画布侧渲染（mermaid/ECharts、sandbox、脚本执行）`,
  )
}

for (const name of OTHER_CASES) {
  assert.equal(
    shouldPreviewWithFileViewerInCanvas(name),
    false,
    `${name} 不应由本函数接管`,
  )
}

// --- 非字符串输入不得抛错 ---
for (const value of [null, undefined]) {
  assert.equal(shouldPreviewWithFileViewerInCanvas(value), false, `${String(value)} 应返回 false`)
}

console.log('画布文本预览判定测试全部通过')
