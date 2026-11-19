/**
 * 行为测试：编辑已有 MCP 服务时，名字不带当前 scope 前缀的服务（平台 Echo、
 * 手工命名或历史无用户名前缀）保存时不得被静默改名。
 *
 * 事故背景：`syncFullServerName()` 无条件重新拼前缀，`NanZi Echo 测试 MCP`
 * 编辑保存后变成 `mcp-public-<user>-NanZi-Echo-MCP`；后端随后连带迁移工具标识
 * 与所有智能体版本引用，平台 Echo 的创建入口再按固定 tool_id 重建时主键冲突。
 *
 * 2026-11 起前缀从 `mcp-private-<完整用户名>-` / `mcp-public-<用户名>-` 换成
 * `priv-<base36(user_id)>-` / `pub-<base36(user_id)>-`；存量服务不迁移，所以这里
 * 同时守住「旧名不被改写」与「新名不被改写」。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  buildMcpServerNamePrefix,
  describeMcpServerNameSuffixError,
  resolveEditedServerName,
  stripMcpServerNamePrefix,
  suggestUniqueServerNameSuffix,
} from '../../frontend/src/utils/mcpServerName.ts'

// admin 的 user_id 假定为 1（base36 → '1'），bob 为 52108（base36 → '147g'）
assert.equal(
  resolveEditedServerName('NanZi Echo 测试 MCP', 'global', 1, 'NanZi Echo 测试 MCP', 'admin'),
  'NanZi Echo 测试 MCP',
  '无前缀名（平台 Echo）编辑回填后必须原样保留',
)
assert.equal(
  resolveEditedServerName('mcp-public-legacy-name', 'global', 1, 'legacy-name', 'admin'),
  'mcp-public-legacy-name',
  '历史无用户名前缀的名字不该在保存时被改写',
)
assert.equal(
  resolveEditedServerName('mcp-public-admin-bar', 'global', 1, 'bar', 'admin'),
  'mcp-public-admin-bar',
  '存量旧前缀（mcp-public-<完整用户名>-）编辑保存后必须仍是原名',
)
assert.equal(
  resolveEditedServerName('priv-147g-hcp', 'personal', 52108, 'hcp', 'bob'),
  'priv-147g-hcp',
  '新前缀的名字同样不该被二次加工',
)
assert.equal(
  resolveEditedServerName('mcp-private-lisi-hcp', 'personal', 52108, 'hcp', 'bob'),
  'mcp-private-lisi-hcp',
  '别人分享来的旧格式名字（前缀不是当前用户）也不能被改名',
)
assert.equal(
  resolveEditedServerName('priv-8x2-hcp', 'personal', 52108, 'hcp', 'bob'),
  'priv-8x2-hcp',
  '别人分享来的新格式名字（归属段不是自己）也不能被改名',
)
assert.equal(
  resolveEditedServerName('mcp-public-admin-bar', 'global', 1, 'baz', 'admin'),
  'pub-1-baz',
  '只有用户主动改后缀时才重组（并换成新前缀）',
)
assert.equal(
  resolveEditedServerName(
    'pub-2-hcp',
    'global',
    1,
    stripMcpServerNamePrefix('pub-2-hcp', 'global', 1, 'admin'),
    'admin',
  ),
  'pub-2-hcp',
  '编辑别人建的平台服务：不改后缀就不能改名，否则等于把服务抢到自己名下',
)
assert.equal(
  resolveEditedServerName('mcp-private-509b99', 'personal', 178, 'mcp-private-509b99', 'chenxiaolong'),
  'mcp-private-509b99',
  '存量「mcp-private-<6位hex>」只有两段，不能被当成「前缀 + 后缀」拆开',
)
assert.equal(
  resolveEditedServerName('mcp-public-admin-hcp', 'personal', 178, 'mcp-public-admin-hcp', 'chenxiaolong'),
  'mcp-public-admin-hcp',
  '个人视图收到历史平台副本名时也不能改名',
)
assert.equal(
  resolveEditedServerName('', 'personal', 52108, 'echo'),
  'priv-147g-echo',
  '新建态按 scope + base36(user_id) 拼前缀',
)

assert.equal(
  buildMcpServerNamePrefix('personal', 52108),
  'priv-147g-',
  '归属段是 user_id 的 base36：不可读但不是乱码，更不会与别人的用户名混淆',
)
assert.equal(buildMcpServerNamePrefix('global', 1), 'pub-1-')
assert.equal(
  buildMcpServerNamePrefix('personal', null),
  'priv-user-',
  '拿不到 user_id 时退化固定段，撞名交给后端全局唯一约束',
)
assert.equal(
  buildMcpServerNamePrefix('personal', 'not-a-number'),
  'priv-user-',
  '非数字 id 同样退化，绝不能拼出 priv-nan- 这种半截编码',
)

assert.equal(
  suggestUniqueServerNameSuffix('https://mcp.example.com/sse', 'personal', 52108, []),
  'mcp-example-com',
)
assert.equal(
  suggestUniqueServerNameSuffix(
    'https://mcp.example.com/sse',
    'personal',
    52108,
    ['mcp-private-admin-mcp-example-com'],
  ),
  'mcp-example-com',
  '旧前缀的存量服务不占新前缀命名空间（两者名字本来就不同）',
)
assert.equal(
  suggestUniqueServerNameSuffix(
    'https://mcp.example.com/sse',
    'personal',
    52108,
    ['priv-147g-mcp-example-com'],
  ),
  'mcp-example-com-2',
  '与新前缀的已有服务重名时加序号',
)
assert.equal(
  stripMcpServerNamePrefix('priv-user-hcp', 'personal', null, ''),
  'hcp',
  '拿不到 user_id 时生成的 priv-user- 前缀同样要能剥',
)
assert.equal(suggestUniqueServerNameSuffix('', 'personal', 52108, []), 'server')

// 后缀字符集：中文/全角/空格必须被明确拒绝，而不是静默归一化成空串
assert.equal(describeMcpServerNameSuffixError('hcp'), '')
assert.equal(describeMcpServerNameSuffixError('my-hcp_1'), '')
assert.equal(describeMcpServerNameSuffixError(''), '请填写服务名称后缀')
assert.equal(describeMcpServerNameSuffixError('   '), '请填写服务名称后缀')
for (const bad of ['工具', 'hcp 测试', 'ｈｃｐ', 'a:b', 'hcp.test']) {
  assert.match(
    describeMcpServerNameSuffixError(bad),
    /只能用英文字母、数字/,
    `「${bad}」必须被拒绝并说明原因`,
  )
}

const source = readFileSync('frontend/src/components/system/McpServerRegistry.vue', 'utf8')
assert.match(source, /resolveEditedServerName\(/, '组件必须走保留原名的解析函数')
assert.match(source, /suggestUniqueServerNameSuffix\(/, '保存前要能按地址补默认后缀')
assert.match(source, /owner_display_name/, '名字里只有 base36 编码，列表必须补出人可读的创建人')
assert.match(source, /serverNameSuffixError/, '弹窗必须显示后缀字符集错误')
assert.match(source, /untouchedSuffix/, '回填的存量名（含中文/空格）不改动时应跳过校验')

const saveButton = source.indexOf('@click="addServer()"')
assert.ok(saveButton > 0, '找不到第二步保存按钮')
assert.match(
  source.slice(saveButton, saveButton + 300),
  /savingServer/,
  '保存按钮必须用 savingServer 禁用重复提交',
)

console.log('MCP 编辑命名行为测试通过')
