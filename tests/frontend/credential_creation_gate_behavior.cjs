/* 真 SFC + Vue 自定义 renderer：只替换网络边界，不启动服务或加载浏览器。 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')
const frontend = path.resolve('frontend')
const localRequire = createRequire(path.join(frontend, 'package.json'))
const Vue = localRequire('vue')
const { parse, compileScript, compileTemplate } = localRequire('vue/compiler-sfc')
const esbuild = localRequire('esbuild')

// 节点提供 v-model 指令需要的最小 DOM 接口；事件仍执行组件编译后的真实处理器。
function node(tag, text = '') {
  return { tag, tagName: tag.toUpperCase(), text, props: {}, children: [], parent: null, listeners: {},
    focus() {}, addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn) },
    removeEventListener() {}, get options() { return this.children.filter(n => n.tag === 'option') },
  }
}
const body = node('body')
global.document = { activeElement: null }
const renderer = Vue.createRenderer({
  createElement: tag => node(tag), createText: text => node('#text', text), createComment: () => node('#comment'),
  setText: (n, text) => { n.text = text }, setElementText: (n, text) => { n.text = text; n.children = [] },
  parentNode: n => n.parent, nextSibling: n => n.parent?.children[n.parent.children.indexOf(n) + 1],
  patchProp(n, key, old, value) { n.props[key] = value; if (key === 'value') n.value = value },
  insert(n, parent, anchor) {
    if (n.parent) n.parent.children.splice(n.parent.children.indexOf(n), 1)
    const index = anchor ? parent.children.indexOf(anchor) : -1
    parent.children.splice(index < 0 ? parent.children.length : index, 0, n); n.parent = parent
  },
  remove(n) { n.parent?.children.splice(n.parent.children.indexOf(n), 1); n.parent = null },
  querySelector: () => body, setScopeId() {}, insertStaticContent() { throw Error('unexpected static content') },
})
const text = n => n.text + n.children.map(text).join('')
const all = (root, predicate) => [root, ...root.children.flatMap(n => all(n, () => true))].filter(predicate)
const find = (root, predicate) => { const n = all(root, predicate)[0]; assert.ok(n, '找不到目标元素'); return n }
const button = (root, label) => find(root, n => n.tag === 'button' && text(n).trim() === label)
const settle = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await Vue.nextTick() } }
async function event(n, name, extra = {}) {
  const e = { target: n, stopPropagation() {}, preventDefault() {}, ...extra }
  for (const fn of n.listeners[name] || []) fn(e)
  const fn = n.props['on' + name[0].toUpperCase() + name.slice(1)]
  if (Array.isArray(fn)) fn.forEach(f => f(e)); else fn?.(e)
  await settle()
}
function mount(component, props = {}) {
  body.children = []
  const root = node('root')
  const app = renderer.createApp(component, props)
  app.mount(root)
  return { root, app }
}

async function build(relative) {
  const result = await esbuild.build({
    entryPoints: [path.join(frontend, relative)], bundle: true, write: false, format: 'cjs',
    platform: 'node', external: ['vue', 'axios'], alias: { '@': path.join(frontend, 'src') }, logLevel: 'silent',
    plugins: [{ name: 'real-vue-with-network-boundary', setup(build) {
      build.onResolve({ filter: /(?:@\/utils\/axios|utils\/axios)$/ }, () => ({ path: 'network', namespace: 'test' }))
      build.onLoad({ filter: /.*/, namespace: 'test' }, () => ({ contents: 'export default globalThis.credentialTestApi', loader: 'js' }))
      build.onLoad({ filter: /\.vue$/ }, args => {
        const { descriptor } = parse(fs.readFileSync(args.path, 'utf8'), { filename: args.path })
        const compiled = compileScript(descriptor, { id: args.path, inlineTemplate: true,
          templateOptions: { compilerOptions: { hoistStatic: false } } })
        return { contents: compiled.content, loader: 'ts', resolveDir: path.dirname(args.path) }
      })
    } }],
  })
  const moduleRef = { exports: {} }
  new Function('module', 'exports', 'require', result.outputFiles[0].text)(moduleRef, moduleRef.exports, localRequire)
  return moduleRef.exports.default
}

const globalReason = '管理员已关闭结构化凭据新建，已有凭据仍可使用和维护'
const typeReason = '此凭据类型已停用新建，已有凭据仍可使用和维护'
const schema = { type: 'object', properties: { token: { type: 'string', title: '访问令牌', writeOnly: true } }, required: ['token'], additionalProperties: false }
const typeRow = (key, enabled, canCreate, reason) => ({ type_key: key, name: key, enabled,
  can_create: canCreate, creation_disabled_reason: reason, source: 'custom', revision: 1,
  latest_version: 2, version: 2, schema,
})
const types = [typeRow('service_auth', true, false, globalReason), typeRow('paused_auth', false, false, typeReason),
  typeRow('scalar', true, true, null), typeRow('variable', true, true, null)]

async function panelCase(which) {
  global.credentialTestApi = { get: async url => ({ data: { data: url.endsWith('/versions/1') ? { ...types[0], version: 1 } : types } }) }
  const mounted = mount(await build('src/components/secrets/CredentialTypesPanel.vue'), { manage: true })
  await settle()
  const row = find(body, n => n.tag === 'button' && text(n).includes('service_auth'))
  if (which === 'type_status') {
    assert.match(text(row), /已禁用/, '全局关闭时不能继续显示类型已启用')
    assert.match(text(body), /管理员已关闭结构化凭据新建/, '须说明类型本身启用而全局禁止新建')
    assert.ok(!button(body, '禁用').props.disabled, '类型自身状态仍能管理')
  } else if (which === 'type_filter') {
    const filter = find(body, n => n.tag === 'select' && n.props['aria-label'] === '按状态筛选')
    filter.options.forEach(n => { n.selected = n.value === 'disabled' })
    await event(filter, 'change')
    const rows = all(body, n => n.tag === 'button').map(text)
    assert.ok(rows.some(t => t.includes('service_auth')), '全局关闭的类型须归入已禁用筛选')
    assert.ok(rows.some(t => t.includes('paused_auth')))
    assert.ok(!rows.some(t => t.includes('scalar')))
    filter.options.forEach(n => { n.selected = n.value === 'enabled' })
    await event(filter, 'change')
    const enabledRows = all(body, n => n.tag === 'button').map(text)
    assert.ok(enabledRows.some(t => t.includes('scalar')) && enabledRows.some(t => t.includes('variable')))
    assert.ok(!enabledRows.some(t => t.includes('service_auth')))
  } else {
    assert.ok(!row.props.disabled, '禁用的类型仍可点开')
    await event(row, 'click')
    assert.match(text(body), /访问令牌/)
    await event(button(body, '预览录入表单'), 'click')
    assert.ok(all(body, n => n.tag === 'textarea').length, '字段预览仍可展开')
    assert.match(text(body), /"properties"/)
    const version = find(body, n => n.tag === 'select' && n.props['aria-label'] === '查看版本')
    await event(version, 'change', { target: { value: '1' } })
    assert.equal(find(body, n => n.tag === 'select' && n.props['aria-label'] === '查看版本').props.value, 1)
    assert.ok(!button(body, '发布新版本').props.disabled, '关闭创建不阻止发布定义')
  }
  mounted.app.unmount()
}

async function optionsCase(which) {
  // 只渲染两页原生类型 select；页面其他生命周期不参与此选项行为断言。
  const filename = path.join(frontend, 'src/views', which === 'personal_options' ? 'SecretCenter.vue' : 'AdminSecretCenter.vue')
  const { descriptor } = parse(fs.readFileSync(filename, 'utf8'))
  const source = descriptor.template.content.match(/<select\s+v-model="typeKey"[\s\S]*?<\/select>/)[0]
  const compiled = compileTemplate({ source, filename, id: filename, compilerOptions: { hoistStatic: false } })
  assert.deepEqual(compiled.errors, [])
  const code = esbuild.transformSync(compiled.code, { loader: 'ts', format: 'cjs' }).code
  const mod = { exports: {} }
  new Function('module', 'exports', 'require', code)(mod, mod.exports, localRequire)
  const mounted = mount({ data: () => ({ typeKey: 'scalar', typeLoading: false, creationTypes: types }), render: mod.exports.render })
  const rows = all(mounted.root, n => n.tag === 'option')
  const disabled = rows.find(n => n.props.value === 'service_auth')
  assert.equal(disabled.props.disabled, true)
  assert.match(text(disabled), /已禁用/, '禁选项必须明确标注已禁用')
  assert.match(text(disabled), /管理员已关闭结构化凭据新建/, '禁选项须给出全局原因')
  assert.match(text(rows.find(n => n.props.value === 'paused_auth')), /此凭据类型已停用新建/)
  assert.ok(!rows.find(n => n.props.value === 'scalar').props.disabled)
  assert.ok(!rows.find(n => n.props.value === 'variable').props.disabled)
  mounted.app.unmount()
}

async function pickerCase(which) {
  const disabled = ['picker_global_disabled', 'picker_type_disabled'].includes(which)
  const reason = which === 'picker_type_disabled' ? typeReason : globalReason
  const candidates = [{ id: 'existing-1', name: '已有账号', account: 'alice', type_key: 'service_auth', type_version: 2 }]
  const creation = which === 'picker_existing' ? null : { type_name: '服务授权', version: 2, schema,
    host: 'api.example.test', account_hint: null, can_create: !disabled, creation_disabled_reason: disabled ? reason : null }
  let postCount = 0
  global.fetch = async (url, init) => {
    assert.equal(url, '/api/v1/chat/permissions/request-1/credential-options')
    if (init.method === 'POST') { postCount++; return { ok: false, json: async () => ({ detail: { message: globalReason } }) } }
    return { ok: true, json: async () => ({ data: candidates, creation, selection_reason: 'credential_missing' }) }
  }
  const selection = []
  const mounted = mount(await build('src/components/secrets/CredentialApprovalPicker.vue'), {
    requestId: 'request-1', modelValue: 'existing-1', 'onUpdate:modelValue': id => selection.push(id),
  })
  await settle()
  const select = find(mounted.root, n => n.tag === 'select')
  assert.ok(!select.props.disabled, '已有凭据选择不受创建开关影响')
  await event(select, 'change', { target: { value: 'existing-1' } })
  assert.equal(selection.at(-1), 'existing-1')
  if (disabled) {
    const create = button(mounted.root, '新建凭据')
    assert.equal(create.props.disabled, true, '收到禁止新建状态后必须禁用入口')
    assert.match(text(mounted.root), /服务授权.*v2.*api.example.test/, '禁止新建仍显示类型与版本信息')
    assert.ok(text(mounted.root).includes(reason))
    const before = selection.length
    await event(create, 'click') // 即使旧事件/程序化触发，也不能进入创建或清空已有选择。
    assert.equal(all(mounted.root, n => n.tag === 'form').length, 0)
    assert.equal(selection.length, before)
    assert.equal(postCount, 0)
  } else if (which === 'picker_stale') {
    await event(button(mounted.root, '新建凭据'), 'click')
    const account = find(mounted.root, n => n.tag === 'input' && n.props.required !== undefined)
    account.value = 'alice'; await event(account, 'input')
    const token = find(mounted.root, n => n.tag === 'textarea')
    token.value = 'synthetic-test-value'; await event(token, 'input')
    assert.ok(!button(mounted.root, '保存并继续').props.disabled)
    await event(find(mounted.root, n => n.tag === 'form'), 'submit')
    assert.equal(postCount, 1)
    assert.ok(text(find(mounted.root, n => n.props.role === 'alert')).includes(globalReason), '旧页面被后端拒绝时显示真实原因')
    assert.equal(find(mounted.root, n => n.tag === 'textarea').props.value, '', '提交后不保留敏感字段')
  }
  mounted.app.unmount()
}

const which = process.argv[2]
;(async () => {
  if (which.startsWith('type_')) await panelCase(which)
  else if (which.endsWith('_options')) await optionsCase(which)
  else await pickerCase(which)
})().catch(error => { console.error(error); process.exitCode = 1 })
