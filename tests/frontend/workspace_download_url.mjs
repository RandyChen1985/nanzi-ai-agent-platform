// 用 Node 真跑工作区下载 URL 构造：路径/会话 ID 的编码错误（空格、&、中文、
// 缺 conversation_id）用源码字符串断言看不出来，必须调用真实实现。
import assert from 'node:assert/strict'

import {
  WORKSPACE_DOWNLOAD_ENDPOINT,
  buildWorkspaceDownloadUrl,
} from '../../frontend/src/utils/workspaceDownloadUrl.ts'

assert.equal(
  WORKSPACE_DOWNLOAD_ENDPOINT,
  '/api/v1/chat/fs/download',
  '下载必须走独立端点，不能复用 /api/v1/chat/fs/preview',
)

// --- 基础：不带会话 ID ---
assert.equal(
  buildWorkspaceDownloadUrl('/data/agent_workspaces/alice__1/conv/a.zip'),
  '/api/v1/chat/fs/download?path=%2Fdata%2Fagent_workspaces%2Falice__1%2Fconv%2Fa.zip',
  '无会话 ID 时不应出现 conversation_id 参数',
)

// --- 带会话 ID：两个参数都要被编码 ---
const withConv = buildWorkspaceDownloadUrl(
  '/data/agent_workspaces/alice__1/conv/季度 报告&v2.zip',
  'conv/2026 01',
)
assert.ok(withConv.startsWith('/api/v1/chat/fs/download?path='), withConv)
assert.ok(withConv.includes('&conversation_id='), withConv)
assert.ok(withConv.includes('%20'), '空格必须被编码，否则 query 会被截断')
assert.ok(withConv.includes('%26'), '路径里的 & 必须被编码，否则会拆出伪参数')
assert.ok(withConv.includes('%2Fdata%2F'), '路径分隔符必须被整体编码进 path 参数')
assert.ok(withConv.includes(encodeURIComponent('季度 报告&v2.zip')), '中文文件名必须被编码')
assert.ok(withConv.includes(encodeURIComponent('conv/2026 01')), '会话 ID 必须被编码')

// --- 边界：空路径不构造请求（否则会打到目录接口拿到 HTML） ---
assert.equal(buildWorkspaceDownloadUrl(''), '')
assert.equal(buildWorkspaceDownloadUrl('   '), '')
assert.equal(buildWorkspaceDownloadUrl(null), '')
assert.equal(buildWorkspaceDownloadUrl('/a.zip', null), buildWorkspaceDownloadUrl('/a.zip'))
assert.equal(buildWorkspaceDownloadUrl('/a.zip', ''), buildWorkspaceDownloadUrl('/a.zip'))

console.log('工作区下载 URL 构造测试全部通过')
