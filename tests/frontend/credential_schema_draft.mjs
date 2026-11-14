import assert from 'node:assert/strict'

const editor = await import('../../frontend/src/utils/credentialSchemaDraft.ts').catch(error => {
  if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error
  assert.fail('缺少可视化凭据字段转换')
})
const { buildCredentialSchema, credentialFieldRows, setCredentialFieldKind } = editor

const initial = {
  type: 'object',
  'x-icon': '☸️',
  title: '集群',
  additionalProperties: true,
  properties: {
    endpoint: { type: 'string', minLength: 2, description: '服务地址' },
    port: { type: 'integer', title: '端口', minimum: 1, maximum: 65535, default: 443 },
    labels: { type: 'array', items: { type: 'string' } },
    settings: {
      type: 'object', additionalProperties: false,
      properties: {
        region: { type: 'string', enum: ['east, 1', 'west'], default: 'west' },
        token: { type: 'string', format: 'password', writeOnly: true },
      },
      required: ['token'],
    },
  },
  required: ['settings'],
}
const rows = credentialFieldRows(initial)
assert.deepEqual(buildCredentialSchema(initial, rows), initial)
rows[0].name = 'cluster_url'
rows[0].required = true
rows[3].children[0].spec.title = '区域'
const updated = buildCredentialSchema(initial, rows)
assert.equal(updated.properties.endpoint, undefined)
assert.deepEqual(updated.required, ['cluster_url', 'settings'])
assert.deepEqual(updated.properties.cluster_url, { type: 'string', minLength: 2, description: '服务地址' })
assert.deepEqual(updated.properties.settings.properties.region, {
  type: 'string', title: '区域', enum: ['east, 1', 'west'], default: 'west',
})
assert.equal(initial.properties.settings.properties.region.title, undefined)

rows[3].children[0].name = 'token'
assert.throws(() => buildCredentialSchema(initial, rows), /settings\.token/)
rows[3].children[0].name = 'region'
setCredentialFieldKind(rows[1], 'array')
assert.deepEqual(buildCredentialSchema(initial, rows).properties.port, {
  type: 'array', title: '端口', items: { type: 'string' },
})
console.log('credential_schema_draft: all assertions passed')
