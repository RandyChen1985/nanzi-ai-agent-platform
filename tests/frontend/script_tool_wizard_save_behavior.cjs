/* 真实脚本向导、Modal 和 Vue 数字输入指令；编辑器外观替身不参与超时逻辑，无服务/数据库。 */
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
global.window = { location: { href: 'http://localhost/' }, innerHeight: 900, innerWidth: 1200, addEventListener() {}, removeEventListener() {},
  getComputedStyle: n => ({ zIndex: String(n.props.style?.zIndex ?? 'auto') }),
}
const renderer = Vue.createRenderer({
  createElement: tag => node(tag), createText: text => node('#text', text), createComment: () => node('#comment'),
  setText: (n, text) => { n.text = text }, setElementText: (n, text) => { n.text = text; n.children = [] },
  parentNode: n => n.parent, nextSibling: n => n.parent?.children[n.parent.children.indexOf(n) + 1],
  patchProp(n, key, old, value) { n.props[key] = value; if (key === 'value' || key === 'type') n[key] = value },
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
    external: ['vue', 'axios', '@heroicons/vue/24/outline'], alias: { '@': path.join(frontend, 'src') }, logLevel: 'silent',
    plugins: [{ name: 'real-vue-with-network-boundary', setup(build) {
      build.onResolve({ filter: /utils\/axios$/ }, () => ({ path: 'network', namespace: 'test' }))
      build.onLoad({ filter: /.*/, namespace: 'test' }, () => ({ contents: 'export default globalThis.pickerTestApi', loader: 'js' }))
      build.onResolve({ filter: /(?:CodeEditor|ToolParamEditor|GroupSelect)\.vue$/ }, args => ({ path: args.path, namespace: 'editor' }))
      // CodeMirror 等编辑器依赖浏览器 DOM；本回归只通过公开 v-model 契约提供代码和 schema。
      build.onLoad({ filter: /.*/, namespace: 'editor' }, args => ({ contents: `
        import { h } from 'vue';
        export default { props: ['modelValue'], emits: ['update:modelValue'], setup(props, { emit }) {
          return () => h('textarea', { 'data-editor': ${JSON.stringify(path.basename(args.path))},
            value: props.modelValue, onInput: event => emit('update:modelValue', event.target.value) });
        } }`, loader: 'js' }))
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
  global.pickerTestApi = { async get(url) {
    assert.equal(url, '/api/portal/secrets/bindings')
    return { data: { data: [] } }
  } }
  const Wizard = await build('src/components/system/ScriptToolWizard.vue')
  const submitted = [], errors = []
  const tool = { id: 'script-1', name: 'credential_env_probe', display_name: '脚本凭据验收', description: '测试',
    source: 'custom', language: 'python', code: 'print("ok")', parameter_schema: {}, requirements: [],
    timeout_seconds: which === 'edit_numeric_timeout' ? null : 30, group_name: null, is_active: true }
  const props = Vue.reactive({ show: true, tool: which === 'create_numeric_timeout' ? null : tool, saving: false })
  const app = renderer.createApp({ setup: () => () => Vue.h(Wizard, {
    ...props, onSubmit: payload => submitted.push(payload),
  }) })
  app.config.errorHandler = error => errors.push(error)
  const root = node('root'); root.parent = body; body.children.push(root)
  app.mount(root); await settle()
  const button = label => find(body, n => n.tag === 'button' && text(n).trim() === label)
  const fill = async (input, value) => { input.value = value; await event(input, 'input') }
  if (which === 'create_numeric_timeout') {
    await fill(find(body, n => n.props.placeholder === 'e.g. read_pdf_page'), 'credential_env_probe')
    await event(button('下一步'), 'click')
    await event(button('下一步'), 'click')
    await fill(find(body, n => n.props['data-editor'] === 'CodeEditor.vue'), 'print("ok")')
    await event(button('下一步'), 'click')
  } else {
    await event(find(body, n => n.tag === 'button' && text(n).includes('依赖与超时')), 'click')
  }
  const input = find(body, n => n.tag === 'input' && n.props.type === 'number')
  const summary = () => text(find(body, n => n.tag === 'li' && text(n).startsWith('· 超时：')))
  const noErrors = () => assert.deepEqual(errors.map(error => error.message), [], '超时输入不能中断渲染或保存')
  const saveWith = async expected => {
    assert.ok(!button('保存').props.disabled, '合法超时应允许保存')
    const before = submitted.length
    await event(button('保存'), 'click')
    noErrors()
    assert.equal(submitted.length, before + 1, '点击保存应提交一次')
    assert.equal(submitted.at(-1).timeout_seconds, expected, '提交整数秒或 null，不能丢失填写的超时')
    assert.equal(submitted.at(-1).name, 'credential_env_probe')
    assert.equal(submitted.at(-1).code, 'print("ok")')
  }
  if (which === 'create_numeric_timeout' || which === 'edit_numeric_timeout') {
    await fill(input, '30')
    noErrors()
    assert.match(summary(), /30 秒/)
    await saveWith(30)
  } else if (which === 'clear_timeout') {
    await fill(input, '45')
    noErrors()
    assert.match(summary(), /45 秒/)
    await fill(input, '')
    assert.match(summary(), /跟随平台配置/)
    await saveWith(null)
  } else if (which === 'integer_bounds') {
    for (const value of ['0', '-1', '1.5', '3601']) {
      await fill(input, value)
      noErrors()
      assert.ok(button('保存').props.disabled, `非法超时 ${value} 必须阻止提交`)
      assert.match(text(body), /超时填正整数|超时不要超过/)
      await event(button('保存'), 'click')
      assert.equal(submitted.length, 0)
    }
    await fill(input, '1'); await saveWith(1)
    await fill(input, '3600'); await saveWith(3600)
  } else if (which === 'loaded_timeout') {
    assert.match(summary(), /30 秒/)
    await saveWith(30)
  } else if (which === 'saving_guard') {
    await fill(input, '30')
    noErrors()
    props.saving = true; await settle()
    const busy = button('保存中…')
    assert.ok(busy.props.disabled)
    await event(busy, 'click')
    assert.equal(submitted.length, 0, '保存进行中不能重复提交')
    props.saving = false; await settle()
    await saveWith(30)
  } else throw Error(`未知场景：${which}`)
  noErrors()
  app.unmount()
}
run(process.argv[2]).then(() => console.log('脚本向导保存行为通过：' + process.argv[2]))
  .catch(error => { console.error(error); process.exitCode = 1 })
