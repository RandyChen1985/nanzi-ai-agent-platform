/**
 * 产物文件名展示处理的边界测试。
 *
 * 规则只有一条：去掉开头 32 位十六进制 + 下划线。错判的代价分两种——
 * 该去的没去，卡片标题继续被截断成乱码；不该去的去了，用户会看到一个
 * 和工作区里不一样的「文件名」，进而以为文件被改名了。
 */
import { createRequire } from 'node:module'

const { loadModule } = createRequire(import.meta.url)('./ts_module_loader.cjs')

const { displayFileName, hasArtifactPrefix } = loadModule(
  'frontend/src/utils/workspaceFileName.ts',
)

const UUID = '26399a7a80ed41e58b2431a2ca949a34'
let passed = 0
const failures = []

function eq(actual, expected, label) {
  if (actual === expected) {
    passed += 1
    return
  }
  failures.push(`${label}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`)
}

// —— 该去掉的 ——
eq(
  displayFileName(`${UUID}_generated-image-20261121-004916-1-7f3ecd.png`),
  'generated-image-20261121-004916-1-7f3ecd.png',
  '标准产物名',
)
eq(displayFileName(`${UUID.toUpperCase()}_报告.xlsx`), '报告.xlsx', '大写 uuid')
eq(displayFileName(`${UUID}_a.png`), 'a.png', '短显示名')

// —— 不该动的 ——
eq('报表.xlsx', displayFileName('报表.xlsx'), '普通中文名')
eq('generated-image-20261121.png', displayFileName('generated-image-20261121.png'), '本来就是干净名')
// 31 / 33 位都不是 uuid.uuid4().hex 的产物，动了反而改变用户认知
const short31 = UUID.slice(0, 31)
eq(displayFileName(`${short31}_x.png`), `${short31}_x.png`, '只有 31 位')
eq(displayFileName(`${UUID}0_x.png`), `${UUID}0_x.png`, '33 位')
eq(displayFileName(`${UUID}.png`), `${UUID}.png`, '缺下划线')
eq(displayFileName(`pre_${UUID}_x.png`), `pre_${UUID}_x.png`, '前缀不在开头')
eq(displayFileName(`${UUID}gg_x.png`), `${UUID}gg_x.png`, '非十六进制字符')

// —— 退化输入 ——
eq(displayFileName(''), '', '空串')
eq(displayFileName(undefined), '', 'undefined')
eq(displayFileName(null), '', 'null')
// 剥完不剩东西时退回原名，不能显示空白
eq(displayFileName(`${UUID}_`), `${UUID}_`, '剥完为空退回原名')

// —— 前缀判定 ——
eq(hasArtifactPrefix(`${UUID}_a.png`), true, 'hasArtifactPrefix 真')
eq(hasArtifactPrefix('a.png'), false, 'hasArtifactPrefix 假')

if (failures.length > 0) {
  console.error('产物文件名展示测试失败：')
  for (const line of failures) console.error('  - ' + line)
  process.exit(1)
}

console.log(`产物文件名展示测试全部通过（${passed} 条）`)
