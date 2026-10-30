import assert from 'node:assert/strict'
import { parseToolSchema } from '../../frontend/src/utils/toolSchema.ts'
import {
  buildParamsFromForm,
  defaultValues,
  isMultiline,
  valuesFromObject,
} from '../../frontend/src/utils/scriptToolParams.ts'

/** 与组件同一条链路：parameter_schema → parseToolSchema → 表单字段 */
const fieldsOf = (schema) => parseToolSchema(JSON.stringify(schema)).params

let passed = 0
const check = (label, fn) => { fn(); passed += 1; console.log('  ✓ ' + label) }

// 用真实内置工具的参数定义当素材：测试范例与实际发布的东西保持一致
const REDACT = {
  type: 'object',
  properties: {
    text: { type: 'string', description: '待脱敏文本' },
    targets: {
      type: 'array',
      description: '要脱敏的类型，默认全部',
      items: { type: 'string', enum: ['phone', 'id_card', 'email', 'bank_card'] },
    },
  },
  required: ['text'],
}

const BASE64 = {
  type: 'object',
  properties: {
    path: { type: 'string', description: '工作区内文件路径' },
    with_data_uri: { type: 'boolean', description: '是否输出 data URI 前缀', default: false },
    max_bytes: { type: 'integer', description: '最大读取字节数', default: 10485760 },
  },
  required: ['path'],
}

check('参数定义 → 字段：类型、必填、枚举都解析出来', () => {
  const fields = fieldsOf(REDACT)
  assert.deepEqual(fields.map((f) => f.name), ['text', 'targets'])
  assert.equal(fields[0].required, true)
  assert.equal(fields[1].required, false)
  assert.deepEqual(fields[1].extra.items.enum, ['phone', 'id_card', 'email', 'bank_card'])
})

check('默认值：整数取 numeric、布尔取 false、枚举数组取空（不默认全选）', () => {
  const values = defaultValues(fieldsOf(BASE64))
  assert.equal(values.path, '')
  assert.equal(values.with_data_uri, false)
  assert.equal(values.max_bytes, 10485760)
  assert.deepEqual(defaultValues(fieldsOf(REDACT)).targets, [])
})

check('必填文本缺失时明确报错，而不是把空串发给脚本', () => {
  const result = buildParamsFromForm(fieldsOf(REDACT), { text: '', targets: [] })
  assert.equal(result.error, '请先填写必填参数：text')
  assert.deepEqual(result.params, {})
})

check('只填了空格的必填项也算没填', () => {
  const result = buildParamsFromForm(fieldsOf(REDACT), { text: '   ', targets: [] })
  assert.match(result.error, /必填参数：text/)
})

check('正常填写：文本 + 多选枚举 → 正确对象', () => {
  const result = buildParamsFromForm(fieldsOf(REDACT), {
    text: '13812345678',
    targets: ['phone', 'email'],
  })
  assert.equal(result.error, '')
  assert.deepEqual(result.params, { text: '13812345678', targets: ['phone', 'email'] })
})

check('布尔 false 必须被提交：不能当成「没填」丢掉', () => {
  const result = buildParamsFromForm(fieldsOf(BASE64), {
    path: 'a.pdf',
    with_data_uri: false,
    max_bytes: '',
  })
  assert.equal(result.error, '')
  assert.equal(result.params.with_data_uri, false)
  assert.equal('max_bytes' in result.params, false, '可选且留空的参数应省略')
})

check('整数输入带小数时截断，不会把 1.5 传给 integer 参数', () => {
  const result = buildParamsFromForm(fieldsOf(BASE64), {
    path: 'a.pdf',
    with_data_uri: true,
    max_bytes: '1024.9',
  })
  assert.equal(result.params.max_bytes, 1024)
})

check('数字参数填了非数字：报错而不是静默变成 NaN', () => {
  const result = buildParamsFromForm(fieldsOf(BASE64), { path: 'a.pdf', max_bytes: 'abc' })
  assert.match(result.error, /需要数字/)
})

check('数组：一行一个 / 逗号分隔都能识别', () => {
  const fields = fieldsOf({
    type: 'object',
    properties: { tags: { type: 'array', description: '标签' } },
    required: ['tags'],
  })
  assert.deepEqual(buildParamsFromForm(fields, { tags: 'a\nb\nc' }).params.tags, ['a', 'b', 'c'])
  assert.deepEqual(buildParamsFromForm(fields, { tags: 'a, b，c' }).params.tags, ['a', 'b', 'c'])
})

check('数组：写 JSON 数组时按其结构保留', () => {
  const fields = fieldsOf({
    type: 'object',
    properties: { ids: { type: 'array' } },
    required: ['ids'],
  })
  assert.deepEqual(buildParamsFromForm(fields, { ids: '[1, 2, 3]' }).params.ids, [1, 2, 3])
})

check('对象参数：非法 JSON 明确报错', () => {
  const fields = fieldsOf({
    type: 'object',
    properties: { opts: { type: 'object' } },
    required: ['opts'],
  })
  assert.match(buildParamsFromForm(fields, { opts: '{oops' }).error, /合法 JSON 对象/)
  assert.deepEqual(buildParamsFromForm(fields, { opts: '{"a":1}' }).params.opts, { a: 1 })
})

check('JSON 模式回填表单：数组/对象转回可编辑文本，缺失字段用默认值', () => {
  const fields = fieldsOf(BASE64)
  const values = valuesFromObject(fields, { path: 'x.pdf', with_data_uri: true })
  assert.equal(values.path, 'x.pdf')
  assert.equal(values.with_data_uri, true)
  assert.equal(values.max_bytes, 10485760)
})

check('多行判定：文本类参数走 textarea，短选项走单行', () => {
  const [text, targets] = fieldsOf(REDACT)
  assert.equal(isMultiline(text), true)
  assert.equal(isMultiline(targets), true, '数组用多行输入')
  assert.equal(isMultiline(fieldsOf(BASE64)[0]), false, 'path 是单行')
})

check('base64 数据这类长字符串走多行，短标识符仍用单行', () => {
  const [data, filename] = fieldsOf({
    type: 'object',
    properties: {
      data: { type: 'string', description: 'base64 内容' },
      filename: { type: 'string', description: '文件名' },
    },
    required: ['data'],
  })
  assert.equal(isMultiline(data), true, 'data 是长字符串')
  assert.equal(isMultiline(filename), false, 'filename 应该单行')
})

check('极简扁平写法（"path": "描述"）也能生成表单', () => {
  const fields = fieldsOf({ path: '工作区文件路径' })
  assert.equal(fields.length, 1)
  assert.equal(fields[0].name, 'path')
  assert.equal(fields[0].required, true)
})

console.log(`\n试运行参数构造测试全部通过（${passed} 项）`)
