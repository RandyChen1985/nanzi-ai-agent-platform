/**
 * 行为测试：编辑已有 MCP 服务时，名字不带当前 scope 前缀的服务（平台 Echo、
 * 手工命名或历史无用户名前缀）保存时不得被静默改名。
 *
 * 事故背景：`syncFullServerName()` 无条件重新拼前缀，`NanZi Echo 测试 MCP`
 * 编辑保存后变成 `mcp-public-<user>-NanZi-Echo-MCP`；后端随后连带迁移工具标识
 * 与所有智能体版本引用，平台 Echo 的创建入口再按固定 tool_id 重建时主键冲突。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  resolveEditedServerName,
  suggestUniqueServerNameSuffix,
} from '../../frontend/src/utils/mcpServerName.ts'

assert.equal(
  resolveEditedServerName('NanZi Echo 测试 MCP', 'global', 'admin', 'NanZi Echo 测试 MCP'),
  'NanZi Echo 测试 MCP',
  '无前缀名（平台 Echo）编辑回填后必须原样保留',
)
assert.equal(
  resolveEditedServerName('mcp-public-legacy-name', 'global', 'admin', 'legacy-name'),
  'mcp-public-legacy-name',
  '历史无用户名前缀的名字不该在保存时被改写',
)
assert.equal(
  resolveEditedServerName('mcp-public-admin-bar', 'global', 'admin', 'bar'),
  'mcp-public-admin-bar',
)
assert.equal(
  resolveEditedServerName('mcp-public-admin-bar', 'global', 'admin', 'baz'),
  'mcp-public-admin-baz',
  '只有用户主动改后缀时才重组',
)
assert.equal(
  resolveEditedServerName('', 'personal', 'admin', 'echo'),
  'mcp-private-admin-echo',
  '新建态仍按 scope 拼前缀',
)

assert.equal(
  suggestUniqueServerNameSuffix('https://mcp.example.com/sse', 'personal', 'admin', []),
  'mcp-example-com',
)
assert.equal(
  suggestUniqueServerNameSuffix(
    'https://mcp.example.com/sse',
    'personal',
    'admin',
    ['mcp-private-admin-mcp-example-com'],
  ),
  'mcp-example-com-2',
  '与已有服务名冲突时加序号',
)
assert.equal(suggestUniqueServerNameSuffix('', 'personal', 'admin', []), 'server')

const source = readFileSync('frontend/src/components/system/McpServerRegistry.vue', 'utf8')
assert.match(source, /resolveEditedServerName\(/, '组件必须走保留原名的解析函数')
assert.match(source, /suggestUniqueServerNameSuffix\(/, '保存前要能按地址补默认后缀')

const saveButton = source.indexOf('@click="addServer()"')
assert.ok(saveButton > 0, '找不到第二步保存按钮')
assert.match(
  source.slice(saveButton, saveButton + 300),
  /savingServer/,
  '保存按钮必须用 savingServer 禁用重复提交',
)

console.log('MCP 编辑命名行为测试通过')
