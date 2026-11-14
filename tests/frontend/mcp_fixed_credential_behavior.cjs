/* 真 SFC + Vue 自定义 renderer：只替换网络边界，不启动服务或加载浏览器。 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')
const frontend = path.resolve('frontend')
const localRequire = createRequire(path.join(frontend, 'package.json'))
const Vue = localRequire('vue')
const { parse, compileScript } = localRequire('vue/compiler-sfc')
const esbuild = localRequire('esbuild')

// 节点提供 v-model 指令需要的最小 DOM 接口；事件仍执行组件编译后的真实处理器。
function node(tag, text = '') {
  return { tag, tagName: tag.toUpperCase(), text, props: {}, children: [], parent: null, listeners: {},
    focus() {}, addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn) },
    removeEventListener() {}, get options() { return this.children.filter(n => n.tag === 'option') },
    get selectedIndex() { return this.options.findIndex(option => option.selected) },
    set selectedIndex(index) { this.options.forEach((option, i) => { option.selected = i === index }) },
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

const baseUrl = '/api/portal/mcp/tools/tool-1/credential-profile'
const typeRow = (key = 'mail_auth', version = 1) => ({
  type_key: key, name: key === 'mail_auth' ? '邮箱凭据' : '另一类型', enabled: true,
  can_create: false, creation_disabled_reason: '已关闭新建', source: 'custom', revision: 1,
  latest_version: 2, version,
  schema: { type: 'object', properties: { password: { type: 'string', writeOnly: true } }, required: ['password'] },
})
const defaultProfile = { profile_id: 'profile-1', revision: 3, type_key: 'mail_auth', type_version: 1,
  selection_mode: 'infer', mappings: { password: 'password' }, model_result_fields: ['$'] }
const personal = { id: 'personal-1', name: '自己的邮箱', account: 'alice', owner_scope: 'user' }
const shared = { id: 'shared-1', name: '部门邮箱', account: 'support', owner_scope: 'system' }
const rows = [personal, shared]
const selectFor = (root, label) => {
  const owner = find(root, n => n.tag === 'label' && text(n).trim().startsWith(label))
  return find(owner, n => n.tag === 'select')
}
const selectedValue = select => select.options.find(option => option.selected)?.value ?? ''
async function choose(root, label, value) {
  const select = selectFor(root, label)
  assert.ok(select.options.some(option => String(option.value) === String(value)), `缺少选项：${label}/${value}`)
  select.options.forEach(option => { option.selected = String(option.value) === String(value) })
  await event(select, 'change')
}
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function response(items = rows, fixed_secret_id = null) {
  return { data: { data: { profile: defaultProfile, items, fixed_secret_id } } }
}
async function harness({ profile = defaultProfile, getCandidates = async () => response(), put } = {}) {
  const gets = [], writes = [], saves = []
  global.credentialTestApi = {
    get: async (url, options) => {
      if (url === baseUrl + '/candidates') {
        gets.push(options?.params)
        return await getCandidates(options?.params, gets.length)
      }
      if (url === '/api/portal/credential-types') return { data: { data: [typeRow(), typeRow('other_auth')] } }
      const version = url.match(/\/credential-types\/([^/]+)\/versions\/(\d+)$/)
      assert.ok(version, `意外 GET：${url}`)
      return { data: { data: typeRow(version[1], Number(version[2])) } }
    },
    put: async (url, body) => {
      writes.push({ url, body })
      if (put) return await put(url, body)
      return { data: { data: { ...defaultProfile, ...body, revision: body.expected_revision + 1 } } }
    },
  }
  const mounted = mount(await build('src/components/mcp/McpCredentialProfileForm.vue'), {
    toolId: 'tool-1', config: { profile, stale: false,
      parameters: [{ name: 'password', type: 'string', required: true, sensitive: true, group_binding_required: true }],
      model_result_policy: { revision: 0, target: 'target-1', model_result_fields: [], stale: false } },
    onSaved: value => saves.push(value),
  })
  await settle()
  return { ...mounted, gets, writes, saves }
}

async function run(which) {
  if (which === 'immediate_selector') {
    const h = await harness()
    await choose(h.root, '凭据选择方式', 'fixed')
    const select = selectFor(h.root, '我的固定凭据')
    assert.deepEqual(h.gets, [{ type_key: 'mail_auth', type_version: 1 }])
    assert.ok(!select.props.disabled)
    assert.ok(text(select).includes('自己的邮箱') && text(select).includes('部门邮箱'))
    assert.match(text(select), /系统共享/)
    assert.equal(selectedValue(select), '', '不能自动绑定第一条凭据')
    const nodes = all(h.root, () => true)
    assert.ok(nodes.indexOf(select) < nodes.findIndex(n => n.tag === 'h4' && text(n) === '字段映射'), '固定候选必须紧跟选择模式，位于字段映射前')
    assert.equal(h.writes.length, 0, '选择模式仅编辑草稿，不隐式保存')
    h.app.unmount()
  } else if (which === 'new_profile') {
    const h = await harness({ profile: null })
    await choose(h.root, '凭据选择方式', 'fixed')
    assert.deepEqual(h.gets.at(-1), { type_key: 'mail_auth', type_version: 2 })
    assert.equal(selectFor(h.root, '我的固定凭据').options.length, 3)
    assert.equal(h.writes.length, 0)
    h.app.unmount()
  } else if (which === 'restore_fixed') {
    const h = await harness({ profile: { ...defaultProfile, selection_mode: 'fixed' },
      getCandidates: async () => response(rows, shared.id) })
    assert.equal(selectedValue(selectFor(h.root, '我的固定凭据')), shared.id)
    assert.ok(!button(h.root, '保存设置').props.disabled, '关闭新建不阻断已有固定凭据选择')
    h.app.unmount()
  } else if (which === 'type_version_reload') {
    const h = await harness({ getCandidates: async params => response(params.type_key === 'other_auth'
      ? [{ ...shared, id: 'other-1' }] : params.type_version === 2 ? [{ ...personal, id: 'v2-1' }] : rows) })
    await choose(h.root, '凭据选择方式', 'fixed')
    await choose(h.root, '我的固定凭据', shared.id)
    await choose(h.root, '类型版本', 2)
    assert.deepEqual(h.gets.at(-1), { type_key: 'mail_auth', type_version: 2 })
    assert.equal(selectedValue(selectFor(h.root, '我的固定凭据')), '')
    await choose(h.root, '我的固定凭据', 'v2-1')
    await choose(h.root, '凭据类型', 'other_auth')
    assert.deepEqual(h.gets.at(-1), { type_key: 'other_auth', type_version: 2 })
    assert.equal(selectedValue(selectFor(h.root, '我的固定凭据')), '')
    assert.ok(selectFor(h.root, '我的固定凭据').options.some(option => option.value === 'other-1'))
    h.app.unmount()
  } else if (which === 'late_response') {
    const old = deferred(), latest = deferred()
    const h = await harness({ getCandidates: params => params.type_version === 1 ? old.promise : latest.promise })
    await choose(h.root, '凭据选择方式', 'fixed')
    assert.ok(selectFor(h.root, '我的固定凭据').props.disabled)
    assert.ok(button(h.root, '保存设置').props.disabled, '候选加载中不可保存')
    await choose(h.root, '类型版本', 2)
    latest.resolve(response([{ ...personal, id: 'v2-1' }]))
    await settle()
    await choose(h.root, '我的固定凭据', 'v2-1')
    old.resolve(response(rows, shared.id))
    await settle()
    assert.equal(selectedValue(selectFor(h.root, '我的固定凭据')), 'v2-1', '旧响应不能覆盖当前选择')
    assert.ok(!selectFor(h.root, '我的固定凭据').options.some(option => option.value === shared.id))
    h.app.unmount()
  } else if (which === 'error_retry') {
    const h = await harness({ getCandidates: async (_, count) => {
      if (count === 1) throw { isAxiosError: true, response: { data: { detail: '候选查询暂不可用' } } }
      return response()
    } })
    await choose(h.root, '凭据选择方式', 'fixed')
    assert.match(text(h.root), /候选查询暂不可用/)
    assert.ok(button(h.root, '保存设置').props.disabled)
    const retry = button(h.root, '重新加载')
    await event(retry, 'click')
    assert.equal(h.gets.length, 2)
    assert.ok(selectFor(h.root, '我的固定凭据').options.some(option => option.value === personal.id))
    assert.ok(!button(h.root, '保存设置').props.disabled)
    h.app.unmount()
  } else if (which === 'empty_candidates') {
    const h = await harness({ getCandidates: async () => response([]) })
    await choose(h.root, '凭据选择方式', 'fixed')
    assert.equal(selectFor(h.root, '我的固定凭据').options.length, 1)
    assert.match(text(h.root), /暂无.*凭据|没有.*凭据/)
    assert.equal(h.writes.length, 0)
    h.app.unmount()
  } else if (which === 'save_together') {
    const h = await harness()
    await choose(h.root, '凭据选择方式', 'fixed')
    await choose(h.root, '我的固定凭据', shared.id)
    await event(button(h.root, '保存设置'), 'click')
    assert.equal(h.writes.length, 1, '配置及绑定须一次保存，不发第二次 fixed 请求')
    assert.equal(h.writes[0].url, baseUrl)
    assert.equal(h.writes[0].body.fixed_secret_id, shared.id)
    assert.equal(h.writes[0].body.selection_mode, 'fixed')
    assert.equal(h.writes[0].body.expected_revision, 3)
    assert.equal(h.writes[0].body.expected_profile_id, 'profile-1')
    assert.deepEqual(h.writes[0].body.mappings, { password: 'password' })
    assert.deepEqual(h.writes[0].body.model_result_fields, ['$'])
    assert.equal(h.saves.length, 1)
    assert.equal(selectedValue(selectFor(h.root, '我的固定凭据')), shared.id)
    h.app.unmount()
  } else if (which === 'save_failure') {
    const h = await harness({ put: async () => { throw { isAxiosError: true,
      response: { data: { detail: '凭据不可用' } } } } })
    await choose(h.root, '凭据选择方式', 'fixed')
    await choose(h.root, '我的固定凭据', shared.id)
    await event(button(h.root, '保存设置'), 'click')
    assert.match(text(h.root), /凭据不可用/)
    assert.equal(h.saves.length, 0)
    assert.equal(selectedValue(selectFor(h.root, '我的固定凭据')), shared.id)
    assert.ok(!button(h.root, '保存设置').props.disabled, '失败仍可修改重试')
    h.app.unmount()
  } else if (which === 'unavailable_binding') {
    const h = await harness({ profile: { ...defaultProfile, selection_mode: 'fixed' },
      getCandidates: async () => response(rows, 'unavailable-1') })
    assert.match(text(h.root), /已绑定.*不可用/)
    assert.ok(button(h.root, '保存设置').props.disabled, '不可用旧绑定不能被默默解绑')
    await choose(h.root, '我的固定凭据', '')
    assert.ok(!button(h.root, '保存设置').props.disabled, '用户明确解绑后可保存')
    await event(button(h.root, '保存设置'), 'click')
    assert.equal(h.writes[0].body.fixed_secret_id, null)
    h.app.unmount()
  } else if (which === 'infer_unchanged') {
    const h = await harness()
    assert.equal(h.gets.length, 0)
    assert.ok(!all(h.root, n => n.tag === 'label').some(n => text(n).includes('我的固定凭据')))
    await event(button(h.root, '保存设置'), 'click')
    assert.equal(h.writes.length, 1)
    assert.ok(!Object.hasOwn(h.writes[0].body, 'fixed_secret_id'))
    assert.equal(h.writes[0].body.selection_mode, 'infer')
    h.app.unmount()
  } else throw Error(`未知场景：${which}`)
}
run(process.argv[2]).then(() => console.log('固定凭据表单行为通过：' + process.argv[2]))
  .catch(error => { console.error(error); process.exitCode = 1 })
