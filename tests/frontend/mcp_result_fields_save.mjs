import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'

const require = createRequire(resolve('frontend/package.json'))
const ts = require('typescript')
const { ref } = require('vue')
const { parse, compileScript } = require('@vue/compiler-sfc')
const source = readFileSync('frontend/src/components/system/McpServerRegistry.vue', 'utf8')
const { descriptor } = parse(source)
const script = ts.createSourceFile('registry.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const functions = ['submitParamMark', 'reloadParamMark']
const selected = script.statements.filter(node => ts.isFunctionDeclaration(node) && functions.includes(node.name?.text))
assert.equal(selected.length, functions.length)
const js = ts.transpileModule(selected.map(node => node.getText(script)).join('\n'), {
  compilerOptions: { module: ts.ModuleKind.ESNext },
}).outputText
const helpers = ts.transpileModule(readFileSync('frontend/src/utils/mcpCredentialProfiles.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext },
}).outputText.replace(/import axios from ['"]@\/utils\/axios['"];?/, 'const axios = undefined;')
const { splitMcpResultPaths } = await import('data:text/javascript;base64,' + Buffer.from(helpers).toString('base64'))

function harness({ text = '$', policy = { revision: 4, model_result_fields: [], stale: true, target: 'before' }, put, getConfig, loadBindings } = {}) {
  const requests = []
  const tool = { id: 'health', server_id: 'server', credential_params: '[]' }
  let persisted = policy
  const state = {
    paramMarkTarget: ref({ tool }), paramMarkBusy: ref(false), paramBindingsLoading: ref(false),
    paramProfileLoading: ref(false), paramCredentialError: ref(''), paramSaveError: ref(''),
    paramResultFields: ref(text), paramBindBusy: ref(false), paramCredentialMode: ref('legacy'),
    confirmLegacyMode: ref(false), paramFocus: ref(''),
    paramCredentialConfig: ref({ profile: null, stale: false, parameters: [], model_result_policy: policy }),
    draftUnboundNames: ref([]), draftMissingCred: ref([]), draftMissingRule: ref([]),
    paramDraft: ref({ ping: 'cred' }), paramRuleDraft: ref({}), slotRules: ref({}),
    isGlobalBinding: ref(false), headerBindings: ref({}),
    paramSlot: (_, name) => 'arg:health:' + name, ruleBindingOfSlot: () => null,
    showToast: () => {}, credentialError: (err, fallback) => err.response?.data?.detail || fallback,
    splitMcpResultPaths, fetchServers: async () => {}, buildParamDraft: () => {},
    getMcpCredentialConfig: getConfig || (async () => ({ profile: null, stale: false, parameters: [], model_result_policy: persisted })),
    loadBindingsFor: loadBindings || (async () => true),
    axios: { put: async (url, body) => {
      requests.push({ url, body })
      if (put) return await put(url, body)
      persisted = { revision: 5, model_result_fields: body.model_result_fields || [], stale: false, target: 'after' }
      return { data: { credential_params: body.params, model_result_policy: persisted } }
    } },
  }
  const actions = new Function(...Object.keys(state), js + '\nreturn { submitParamMark, reloadParamMark };')(...Object.values(state))
  return { ...actions, state, requests, tool }
}

// 底部保存要把结果草稿与最终来源一起提交，使用打开弹窗时的策略快照。
{
  const h = harness()
  await h.submitParamMark()
  assert.deepEqual(h.requests, [{ url: '/api/portal/mcp/tools/health/credential-params', body: {
    params: ['ping'], model_result_fields: ['$'], expected_revision: 4, expected_target: 'before',
  } }])
  assert.equal(h.state.paramMarkTarget.value, null)
  await h.reloadParamMark(h.tool)
  assert.equal(h.state.paramResultFields.value, '$', '重新打开必须回填保存的根路径')
}

// 清空是主动关闭结果可见性，不能被当成“未填写此更新项”。
{
  const h = harness({ text: ' \n ' })
  await h.submitParamMark()
  assert.deepEqual(h.requests[0].body.model_result_fields, [])
}

// 配置未完整加载、无 CAS 快照或绑定未保存时，不能发出任何写请求。
for (const key of ['paramBindingsLoading', 'paramProfileLoading', 'paramBindBusy']) {
  const h = harness()
  h.state[key].value = true
  await h.submitParamMark()
  assert.equal(h.requests.length, 0, key)
}
{
  const h = harness()
  h.state.paramCredentialConfig.value = null
  await h.submitParamMark()
  assert.equal(h.requests.length, 0)
}

// 同一表单的两个保存入口不能并发发送；冲突/失败保留草稿且允许重试。
{
  let reject
  const h = harness({ put: () => new Promise((_, fail) => { reject = fail }) })
  const pending = h.submitParamMark()
  await h.submitParamMark()
  assert.equal(h.requests.length, 1)
  reject({ response: { data: { detail: '配置已被更新，请重新打开' } } })
  await pending
  assert.ok(h.state.paramMarkTarget.value)
  assert.equal(h.state.paramResultFields.value, '$')
  assert.equal(h.state.paramMarkBusy.value, false)
  assert.match(h.state.paramSaveError.value, /配置已被更新/)
}

// 旧策略失效只应要求重新确认，编辑框仍显示已保存路径。
{
  const h = harness({ policy: { revision: 3, model_result_fields: ['$'], stale: true, target: 'changed' } })
  h.state.paramResultFields.value = ''
  await h.reloadParamMark(h.tool)
  assert.equal(h.state.paramResultFields.value, '$')
}

// 绑定加载失败不能用残留草稿继续保存；同工具上次打开的迟到响应也不能覆盖当前编辑。
{
  const h = harness({ loadBindings: async () => false })
  await h.reloadParamMark(h.tool)
  await h.submitParamMark()
  assert.equal(h.requests.length, 0)
  assert.match(h.state.paramCredentialError.value, /无法加载/)
}
{
  const configs = []
  const h = harness({ getConfig: () => new Promise(resolve => configs.push(resolve)) })
  const first = h.reloadParamMark(h.tool)
  const second = h.reloadParamMark(h.tool)
  configs[1]({ profile: null, parameters: [], model_result_policy: { revision: 2, model_result_fields: ['$'], stale: true, target: 'new' } })
  await second
  h.state.paramResultFields.value = '$.messages[*]'
  configs[0]({ profile: null, parameters: [], model_result_policy: { revision: 1, model_result_fields: [], stale: true, target: 'old' } })
  await first
  assert.equal(h.state.paramResultFields.value, '$.messages[*]')
  assert.equal(h.state.paramCredentialConfig.value.model_result_policy.target, 'new')
}

// 无效路径由后端关闭可见性并返回警告，保留输入方便纠正，再次提交使用最新 revision。
{
  const h = harness({ text: '$[', put: async (_, body) => ({ data: {
    credential_params: body.params,
    model_result_policy: { revision: 5, model_result_fields: [], stale: false, target: 'after' },
    warnings: [{ code: 'MCP_RESULT_FIELDS_INVALID', message: 'JSONPath 无效' }],
  } }) })
  await h.submitParamMark()
  assert.ok(h.state.paramMarkTarget.value)
  assert.equal(h.state.paramResultFields.value, '$[')
  assert.match(h.state.paramSaveError.value, /JSONPath 无效/)
  assert.equal(h.state.paramCredentialConfig.value.model_result_policy.revision, 5)
}

// 执行实际子表单的输入/保存事件，确保结果字段按钮也把父草稿及参数来源一并保存。
{
  const childSource = readFileSync('frontend/src/components/mcp/McpModelResultFieldsForm.vue', 'utf8')
  const child = compileScript(parse(childSource).descriptor, { id: 'result-form', inlineTemplate: true })
  const childJs = ts.transpileModule(child.content, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  const module = { exports: {} }
  new Function('require', 'module', 'exports', childJs)(require, module, module.exports)
  const h = harness({ text: '' })
  let submitted
  const render = module.exports.default.setup({
    modelValue: '', policy: h.state.paramCredentialConfig.value.model_result_policy, busy: false, disabled: false,
  }, { expose() {}, emit(event, value) {
    if (event === 'update:modelValue') h.state.paramResultFields.value = value
    if (event === 'save') submitted = h.submitParamMark()
  } })
  const nodes = []
  const walk = node => {
    if (!node || typeof node !== 'object') return
    nodes.push(node)
    if (Array.isArray(node.children)) node.children.forEach(walk)
  }
  walk(render({}, []))
  nodes.find(node => node.type === 'textarea').props.onInput({ target: { value: '$' } })
  nodes.find(node => node.type === 'button').props.onClick()
  await submitted
  assert.deepEqual(h.requests[0].body.model_result_fields, ['$'])
  assert.deepEqual(h.requests[0].body.params, ['ping'])
  // 父组件必须接上刚测试的草稿与保存事件，不能只显示一个无关的子表单。
  const parentForm = descriptor.template.content.match(/<McpModelResultFieldsForm\b[\s\S]*?\/>/)[0]
  assert.match(parentForm, /v-model="paramResultFields"/)
  assert.match(parentForm, /@save="submitParamMark"/)
}

console.log('MCP 结果字段与参数来源统一保存行为测试通过')
