import assert from 'node:assert/strict'
import { parseToolSchema, serializeToolSchema } from '../../frontend/src/utils/toolSchema.ts'

let passed = 0
const check = (label, fn) => { fn(); passed += 1; console.log('  ✓ ' + label) }

check('标准形态往返：type:object 与 x-nanzi-evidence-types 原样保留', () => {
  const raw = JSON.stringify({
    type: 'object',
    properties: { city: { type: 'string', description: '城市名' } },
    required: ['city'],
    'x-nanzi-evidence-types': ['weather'],
  })
  const parsed = parseToolSchema(raw)
  assert.equal(parsed.shape, 'json-schema')
  assert.deepEqual(JSON.parse(serializeToolSchema(parsed)), JSON.parse(raw))
})

check('扁平形态往返：内联 required/default 保留，形态仍为 flat', () => {
  const obj = {
    city: { type: 'string', description: '城市' },
    date: { type: 'string', required: false, default: '2026-01-01' },
  }
  const parsed = parseToolSchema(JSON.stringify(obj))
  assert.equal(parsed.shape, 'flat')
  assert.deepEqual(JSON.parse(serializeToolSchema(parsed)), obj)
})

check('极简写法 "param":"描述"：值被读作描述，且默认必填', () => {
  const parsed = parseToolSchema('{"city":"城市名"}')
  assert.equal(parsed.params[0].description, '城市名')
  assert.equal(parsed.params[0].required, true)
})

check('嵌套 object 的 properties 子结构完整保留', () => {
  const obj = {
    type: 'object',
    properties: {
      filter: { type: 'object', properties: { a: { type: 'string' } }, description: '过滤' },
    },
  }
  const parsed = parseToolSchema(JSON.stringify(obj))
  assert.deepEqual(JSON.parse(serializeToolSchema(parsed)), obj)
})

check('标准形态 + 全部非必填 → required:[] 且每个参数内联 required:false（否则运行时全按必填）', () => {
  const raw = JSON.stringify({
    type: 'object',
    properties: { a: { type: 'string' }, b: { type: 'integer' } },
    required: [],
  })
  const parsed = parseToolSchema(raw)
  assert.equal(parsed.params.every((p) => p.required === false), true)
  const out = JSON.parse(serializeToolSchema(parsed))
  assert.deepEqual(out.required, [])
  assert.equal(out.properties.a.required, false)
  assert.equal(out.properties.b.required, false)
})

check('必填参数上的默认值不写出（运行时只在非必填时读 default）', () => {
  const parsed = parseToolSchema('{"type":"object","properties":{"a":{"type":"string"}},"required":["a"]}')
  parsed.params[0].defaultValue = 'x'
  const out = JSON.parse(serializeToolSchema(parsed))
  assert.equal('default' in out.properties.a, false)
})

check('默认值按类型转换（integer→数字，boolean→布尔）', () => {
  const parsed = parseToolSchema('{"a":{"type":"string","required":false},"b":{"type":"string","required":false}}')
  parsed.params[0].type = 'integer'; parsed.params[0].defaultValue = '42'
  parsed.params[1].type = 'boolean'; parsed.params[1].defaultValue = 'true'
  const out = JSON.parse(serializeToolSchema(parsed))
  assert.equal(out.a.default, 42)
  assert.equal(out.b.default, true)
})

check('非法输入返回 null（非法 JSON / 数组 / 字符串），空串视为 {}', () => {
  assert.equal(parseToolSchema('{oops'), null)
  assert.equal(parseToolSchema('[1,2]'), null)
  assert.equal(parseToolSchema('"str"'), null)
  const empty = parseToolSchema('')
  assert.deepEqual(empty.params, [])
})

check('扁平形态下 type/required/$schema/x-* 不被当作参数，且原样保留', () => {
  const raw = JSON.stringify({
    type: 'object', required: [], '$schema': 'x',
    'x-nanzi-evidence-types': ['a'], city: { type: 'string' },
  })
  const parsed = parseToolSchema(raw)
  assert.deepEqual(parsed.params.map((p) => p.name), ['city'])
  const out = JSON.parse(serializeToolSchema(parsed))
  assert.deepEqual(out['x-nanzi-evidence-types'], ['a'])
  assert.equal(out.type, 'object')
})

check('重命名参数/新增参数/删除参数后结构正确', () => {
  const parsed = parseToolSchema('{"type":"object","properties":{"old":{"type":"string"}},"required":["old"]}')
  parsed.params[0].name = 'renamed'
  parsed.params.push({ name: 'added', type: 'integer', required: false, description: '', defaultValue: '', extra: {} })
  const out = JSON.parse(serializeToolSchema(parsed))
  assert.deepEqual(Object.keys(out.properties), ['renamed', 'added'])
  assert.deepEqual(out.required, ['renamed'])
  assert.equal(out.properties.added.required, false)
})

console.log(`\n往返测试全部通过：${passed} 项`)
