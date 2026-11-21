/**
 * 产物链接解析的边界测试。
 *
 * 生成图片只在 download_url 里出现，artifact_id 是前端唯一能拿到的凭据：
 * 解析错了会直接变成「分享不了」或「分享到别的产物」，所以这里把常见形态都钉住。
 */
import { createRequire } from 'node:module'

const { loadModule } = createRequire(import.meta.url)('./ts_module_loader.cjs')

const { extractArtifactId, isGeneratedArtifactUrl } = loadModule(
  'frontend/src/utils/generatedArtifact.ts',
)

const ID = '26399a7a80ed41e58b2431a2ca949a34'
let passed = 0
const failures = []

function eq(actual, expected, label) {
  if (actual === expected) {
    passed += 1
    return
  }
  failures.push(`${label}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`)
}

// —— 认得的形态 ——
eq(extractArtifactId(`/api/v1/chat/generated-files/${ID}?token=abc`), ID, 'chat 前缀相对地址')
eq(extractArtifactId(`/api/v1/generated-files/${ID}?token=abc`), ID, '无 chat 前缀')
eq(extractArtifactId(`https://example.com/api/v1/chat/generated-files/${ID}?token=abc`), ID, '绝对地址')
eq(extractArtifactId(`/api/v1/chat/generated-files/${ID}`), ID, '无查询串')
eq(extractArtifactId(`/api/v1/chat/generated-files/${ID}#page=2`), ID, '带 hash')
eq(extractArtifactId(`/api/v1/chat/generated-files/${ID.toUpperCase()}`), ID, '大写自动归一')

// —— 不该认的形态 ——
eq(extractArtifactId(''), '', '空串')
eq(extractArtifactId('blob:http://localhost/abc'), '', 'blob 地址')
eq(extractArtifactId('data:image/png;base64,AAAA'), '', 'data URL')
eq(extractArtifactId('/api/v1/chat/fs/uploads/admin__1/docs/a.png'), '', '工作区文件地址')
eq(extractArtifactId(`/api/v1/chat/generated-files/${ID}abc?token=x`), '', 'ID 多了尾巴')
eq(extractArtifactId(`/api/v1/chat/generated-files/${ID.slice(1)}?token=x`), '', 'ID 少一位')
eq(extractArtifactId('/api/v1/chat/generated-files/?token=x'), '', '缺 ID')
eq(
  extractArtifactId(`/api/v1/chat/generated-files/xyz?token=${'a'.repeat(32)}`),
  '',
  'ID 非法时不能误取查询串里的十六进制',
)

// —— 布尔便捷判断 ——
eq(isGeneratedArtifactUrl(`/api/v1/chat/generated-files/${ID}?token=x`), true, 'isGeneratedArtifactUrl 真')
eq(isGeneratedArtifactUrl('https://example.com/a.png'), false, 'isGeneratedArtifactUrl 假')

if (failures.length > 0) {
  console.error('产物链接解析测试失败：')
  for (const line of failures) console.error('  - ' + line)
  process.exit(1)
}

console.log(`产物链接解析测试全部通过（${passed} 条）`)
