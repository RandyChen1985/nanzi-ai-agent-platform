// 用 Node 真跑纯函数：产物 → 画布数据形态的判定表，静态字符串断言看不出
// 「.pptx 会不会被 .ppt 规则误伤」这类缺陷，这里直接调用真实实现。
import assert from 'node:assert/strict'

import {
  resolveGeneratedPreviewPlan,
  buildArtifactDownloadUrl,
} from '../../frontend/src/utils/generatedFilePreviewPlan.ts'

// --- Office 6 种：只传鉴权 URL，交给 file-viewer ---
for (const name of ['a.docx', 'a.doc', 'a.xlsx', 'a.xls', 'a.xlsm', 'a.pptx']) {
  assert.deepEqual(
    resolveGeneratedPreviewPlan(name),
    { kind: 'url-document', canvasType: 'document' },
    `${name} 应走 url-document/document`,
  )
}

// --- PDF 独立走画布 pdf 分支（同样只传 URL）---
assert.deepEqual(
  resolveGeneratedPreviewPlan('a.pdf'),
  { kind: 'url-document', canvasType: 'pdf' },
  '.pdf 应走 url-document/pdf',
)

// --- 图片与 CSV：取 Blob（画布自绘）---
for (const name of ['a.png', 'a.jpg', 'a.jpeg', 'a.webp', 'a.gif']) {
  assert.deepEqual(
    resolveGeneratedPreviewPlan(name),
    { kind: 'blob', canvasType: 'image' },
    `${name} 应走 blob/image`,
  )
}
assert.deepEqual(resolveGeneratedPreviewPlan('a.csv'), { kind: 'blob', canvasType: 'csv' }, '.csv 应走 blob/csv')

// --- 文本与代码：取文本；HTML 走画布 sandbox 预览 ---
assert.deepEqual(resolveGeneratedPreviewPlan('a.html'), { kind: 'text', canvasType: 'html' }, '.html 应走 text/html')
assert.deepEqual(resolveGeneratedPreviewPlan('a.htm'), { kind: 'text', canvasType: 'html' }, '.htm 应走 text/html')
for (const name of ['a.md', 'a.markdown', 'a.txt', 'a.json', 'a.py', 'a.ts', 'a.sql', 'a.yaml']) {
  assert.deepEqual(
    resolveGeneratedPreviewPlan(name),
    { kind: 'text', canvasType: 'code' },
    `${name} 应走 text/code（.md 靠 title 后缀命中画布 Markdown 渲染器）`,
  )
}

// --- 不可预览：.ppt 与未知扩展名一律 download-only ---
assert.deepEqual(resolveGeneratedPreviewPlan('a.ppt'), { kind: 'download-only', canvasType: null }, '.ppt 只能下载')
assert.deepEqual(
  resolveGeneratedPreviewPlan('北京到上海高铁车次参考清单.zip'),
  { kind: 'download-only', canvasType: null },
  '.zip 只能下载',
)
assert.deepEqual(resolveGeneratedPreviewPlan('README'), { kind: 'download-only', canvasType: null }, '无扩展名只能下载')
assert.deepEqual(resolveGeneratedPreviewPlan(''), { kind: 'download-only', canvasType: null }, '空串只能下载')

// --- 子串陷阱与大小写 ---
assert.deepEqual(
  resolveGeneratedPreviewPlan('模型对比.PPTX'),
  { kind: 'url-document', canvasType: 'document' },
  '大写 .PPTX 应可预览',
)
assert.deepEqual(
  resolveGeneratedPreviewPlan('deck.PPT'),
  { kind: 'download-only', canvasType: null },
  '大写 .PPT 只能下载',
)

// --- 下载地址：补 download=1，且不重复追加 ---
assert.equal(
  buildArtifactDownloadUrl('/api/v1/chat/generated-files/abc?token=t'),
  '/api/v1/chat/generated-files/abc?token=t&download=1',
  '已带 query 时用 & 追加',
)
assert.equal(
  buildArtifactDownloadUrl('/api/v1/chat/generated-files/abc'),
  '/api/v1/chat/generated-files/abc?download=1',
  '无 query 时用 ? 追加',
)
assert.equal(
  buildArtifactDownloadUrl('/api/v1/chat/generated-files/abc?token=t&download=1'),
  '/api/v1/chat/generated-files/abc?token=t&download=1',
  '已带 download=1 不重复追加',
)
assert.equal(
  buildArtifactDownloadUrl('https://host/api/v1/chat/generated-files/abc?token=t'),
  'https://host/api/v1/chat/generated-files/abc?token=t&download=1',
  '绝对地址同样处理',
)
assert.equal(buildArtifactDownloadUrl(''), '', '空地址保持空串')

console.log('产物预览判定测试全部通过')
