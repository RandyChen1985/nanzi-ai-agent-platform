/* 真实 Vue 选择器 + Modal，网络只返回合成凭据元数据，不访问服务或数据库。 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')
const frontend = path.resolve('frontend')
const localRequire = createRequire(path.join(frontend, 'package.json'))
const Vue = localRequire('vue')
const { parse, compileScript } = localRequire('vue/compiler-sfc')
const esbuild = localRequire('esbuild')

function node(tag, text = '') {
  return { tag, tagName: tag.toUpperCase(), text, props: {}, children: [], parent: null, listeners: {},
    get parentElement() { return this.parent },
    getBoundingClientRect() { return { left: 260, top: 300, bottom: 340, width: 400 } },
    focus() {}, scrollIntoView() {},
    contains(target) { return this === target || this.children.some(child => child.contains(target)) },
    querySelector() { return null },
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn) }, removeEventListener() {},
  }
}
const body = node('body')
global.document = { addEventListener() {}, removeEventListener() {}, activeElement: null }
global.window = { innerHeight: 900, innerWidth: 1200, addEventListener() {}, removeEventListener() {},
  getComputedStyle: n => ({ zIndex: String(n.props.style?.zIndex ?? 'auto') }),
}
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
const settle = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await Vue.nextTick() } }
async function event(n, name, extra = {}) {
  const e = { target: n, stopPropagation() {}, preventDefault() {}, ...extra }
  for (const fn of n.listeners[name] || []) fn(e)
  n.props['on' + name[0].toUpperCase() + name.slice(1)]?.(e)
  await settle()
}
async function build(relative) {
  const result = await esbuild.build({
    entryPoints: [path.join(frontend, relative)], bundle: true, write: false, format: 'cjs', platform: 'node',
    external: ['vue'], logLevel: 'silent',
    plugins: [{ name: 'real-vue-with-network-boundary', setup(build) {
      build.onResolve({ filter: /utils\/axios$/ }, () => ({ path: 'network', namespace: 'test' }))
      build.onLoad({ filter: /.*/, namespace: 'test' }, () => ({ contents: 'export default globalThis.pickerTestApi', loader: 'js' }))
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

async function run(which) {
  const shared = { id: 'shared-1', name: '系统脚本测试凭据', secret_key: 'demo.system', allowed_scenarios: ['script_env'] }
  const mine = { ...shared, id: 'mine-1', name: '个人测试凭据', secret_key: 'demo.mine' }
  global.pickerTestApi = { async get(url, options) {
    assert.equal(url, '/api/portal/secrets/picker')
    assert.deepEqual(options.params, { scene: 'script_env' })
    return { data: { data: { groups: { mine: [mine], shared: which === 'empty_system' ? [] : [shared,
      { ...shared, id: 'wrong-scene', name: '只允许MCP', allowed_scenarios: ['mcp'] }] } } } }
  } }
  const [Picker, Modal] = await Promise.all([build('src/components/secrets/CredentialPicker.vue'), build('src/components/Modal.vue')])
  const chosen = [], layer = Vue.ref(which === 'plain_page' ? null : 12500)
  const component = { setup() {
    const picker = () => Vue.h(Picker, { scene: 'script_env', systemOnly: which !== 'plain_page',
      'onUpdate:modelValue': value => chosen.push(value) })
    return () => layer.value === null ? picker() : Vue.h(Modal, { title: '绑定环境变量', zIndex: layer.value }, { default: picker })
  } }
  const root = node('root'); root.parent = body; body.children.push(root)
  const app = renderer.createApp(component); app.mount(root); await settle()
  const trigger = find(body, n => n.props.role === 'combobox')
  await event(trigger, 'click')
  const menu = find(body, n => n.props.style?.position === 'fixed')
  assert.equal(menu.parent, body, '浮层应脱离弹窗的裁剪容器')
  const options = all(menu, n => n.props.role === 'option')
  if (which === 'empty_system') {
    assert.equal(options.length, 0)
    assert.match(text(menu), /系统凭据/)
    assert.ok(!text(menu).includes('个人中心 → 我的凭据'), '全局绑定不能引导创建不可选的个人凭据')
  } else if (which === 'nested_modal' || which === 'reopen_higher_modal') {
    assert.ok(Number(menu.props.style.zIndex) > layer.value, '下拉菜单必须在绑定弹窗遮罩之上')
    assert.equal(options.length, 1, '全局绑定仅列出允许脚本场景的系统候选')
    await event(options[0], 'click')
    assert.deepEqual(chosen, [shared.id])
    assert.ok(!all(body, n => n.props.role === 'listbox').length, '选择后应关闭菜单')
    if (which === 'reopen_higher_modal') {
      layer.value = 18000; await settle(); await event(trigger, 'click')
      const reopened = find(body, n => n.props.style?.position === 'fixed')
      assert.ok(Number(reopened.props.style.zIndex) > layer.value, '重新打开时应按当前弹窗层级定位')
    }
  } else if (which === 'plain_page') {
    assert.ok(Number(menu.props.style.zIndex) > 100)
    assert.equal(options.length, 2, '非全局绑定仍允许个人和系统候选')
    const search = find(menu, n => n.tag === 'input')
    await event(search, 'keydown', { key: 'ArrowDown' })
    await event(search, 'keydown', { key: 'Enter' })
    assert.deepEqual(chosen, [shared.id], '键盘选择仍可使用')
  } else throw Error(`未知场景：${which}`)
  app.unmount()
}
run(process.argv[2]).then(() => console.log('凭据选择器浮层行为通过：' + process.argv[2]))
  .catch(error => { console.error(error); process.exitCode = 1 })
