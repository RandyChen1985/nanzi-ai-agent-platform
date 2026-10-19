import assert from 'node:assert/strict'
import {
  parseToolSchema,
  serializeToolSchema,
  validateSystemParams,
  SYSTEM_PARAMS_KEY,
} from '../../frontend/src/utils/toolSchema.ts'
import { buildDraftSchema } from '../../frontend/src/utils/toolDraft.ts'

let passed = 0
const check = (label, fn) => { fn(); passed += 1; console.log('  ✓ ' + label) }

const schema = {
  type: 'object',
  properties: { amount: { type: 'number' } },
  required: ['amount'],
  'x-nanzi-system-params': [
    { name: 'applicant', type: 'string', value: '{user.user_name}', description: '申请人' },
  ],
  'x-other': { keep: true },
}

check('解析系统级参数且不放进普通参数', () => {
  const parsed = parseToolSchema(JSON.stringify(schema))
  assert.equal(parsed.systemParams.length, 1)
  assert.equal(parsed.systemParams[0].name, 'applicant')
  assert.deepEqual(parsed.params.map((p) => p.name), ['amount'])
})

check('系统级参数往返保留，required 不再引用隐藏参数', () => {
  const parsed = parseToolSchema(JSON.stringify({
    ...schema,
    required: ['amount', 'applicant'],
  }))
  const out = JSON.parse(serializeToolSchema(parsed))
  assert.deepEqual(out[SYSTEM_PARAMS_KEY], schema['x-nanzi-system-params'])
  assert.deepEqual(out.required, ['amount'])
  assert.equal(out.properties.applicant, undefined)
  assert.deepEqual(out['x-other'], { keep: true })
})

check('扁平形态保持扁平，扩展不被当成普通参数', () => {
  const flat = {
    amount: { type: 'number' },
    [SYSTEM_PARAMS_KEY]: [
      { name: 'applicant', type: 'string', value: '{user.user_name}', description: '' },
    ],
  }
  const parsed = parseToolSchema(JSON.stringify(flat))
  assert.equal(parsed.shape, 'flat')
  assert.deepEqual(parsed.params.map((p) => p.name), ['amount'])
  const out = JSON.parse(serializeToolSchema(parsed))
  assert.equal(out.amount.type, 'number')
  assert.equal(out[SYSTEM_PARAMS_KEY][0].name, 'applicant')
})

check('校验拦截空名、重名、普通参数冲突、空值', () => {
  const errors = validateSystemParams([
    { name: '', type: 'string', value: 'x', description: '' },
    { name: 'applicant', type: 'string', value: '', description: '' },
    { name: 'applicant', type: 'string', value: 'x', description: '' },
    { name: 'amount', type: 'string', value: 'x', description: '' },
  ], [{ name: 'amount' }])
  assert.equal(errors.some((e) => e.includes('缺少名称')), true)
  assert.equal(errors.some((e) => e.includes('重复')), true)
  assert.equal(errors.some((e) => e.includes('重名')), true)
  assert.equal(errors.some((e) => e.includes('固定值')), true)
})

check('向导重新生成普通参数时保留系统级参数', () => {
  const raw = buildDraftSchema(
    [{ name: 'amount', type: 'number', required: true, description: '', defaultValue: '', location: 'body', reason: '' }],
    [{ name: 'applicant', type: 'string', value: '{user.user_name}', description: '申请人' }],
  )
  const out = JSON.parse(raw)
  assert.equal(out.amount.type, 'number')
  assert.equal(out[SYSTEM_PARAMS_KEY][0].name, 'applicant')
})

console.log(`\n系统级参数 schema 测试全部通过：${passed} 项`)
