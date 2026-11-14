/* 运行实际任务弹窗及凭据选择器。网络与计时器由测试控制。 */
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
  return {
    tag, tagName: tag.toUpperCase(), text, props: {}, children: [], parent: null,
    addEventListener() {}, removeEventListener() {},
  }
}
const body = node('body')
const renderer = Vue.createRenderer({
  createElement: tag => node(tag), createText: text => node('#text'), createComment: () => node('#comment'),
  setText(n, text) { n.text = text }, setElementText(n, text) { n.text = text; n.children = [] },
  parentNode: n => n.parent, nextSibling: n => n.parent?.children[n.parent.children.indexOf(n) + 1],
  patchProp(n, key, old, value) { n.props[key] = value },
  insert(n, parent, anchor) {
    if (n.parent) n.parent.children.splice(n.parent.children.indexOf(n), 1)
    const index = anchor ? parent.children.indexOf(anchor) : -1
    parent.children.splice(index < 0 ? parent.children.length : index, 0, n)
    n.parent = parent
  },
  remove(n) { n.parent?.children.splice(n.parent.children.indexOf(n), 1); n.parent = null },
  querySelector: () => body, setScopeId() {},
})

const content = n => n.text + n.children.map(content).join('')
const find = (root, predicate) => predicate(root) ? root : root.children.map(n => find(n, predicate)).find(Boolean)
const button = label => {
  const n = find(body, n => n.tag === 'button' && content(n).trim() === label)
  assert.ok(n, '找不到按钮 ' + label)
  return n
}
const settle = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await Vue.nextTick() } }
const click = async label => { button(label).props.onClick(); await settle() }
function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
const status = (request_id, state) => ({ data: { data: {
  request_id, trace_id: 'trace', status: state, expires_at: '2030-01-01T00:00:00Z',
} } })

async function loadComponent() {
  const result = await esbuild.build({
    entryPoints: [path.join(frontend, 'src/components/task/TaskCredentialWaitModal.vue')],
    bundle: true, write: false, format: 'cjs', platform: 'node',
    external: ['vue', 'axios'], alias: { '@': path.join(frontend, 'src') }, logLevel: 'silent',
    plugins: [{ name: 'task-wait-boundaries', setup(build) {
      build.onResolve({ filter: /\/utils\/axios$/ }, () => ({ path: 'network', namespace: 'test' }))
      build.onLoad({ filter: /.*/, namespace: 'test' }, () => ({
        contents: 'export default globalThis.taskWaitTestApi', loader: 'js',
      }))
      build.onLoad({ filter: /\.vue$/ }, args => {
        const { descriptor } = parse(fs.readFileSync(args.path, 'utf8'), { filename: args.path })
        const compiled = compileScript(descriptor, { id: args.path, inlineTemplate: true,
          templateOptions: { compilerOptions: { hoistStatic: false } } })
        return { contents: compiled.content, loader: 'ts', resolveDir: path.dirname(args.path) }
      })
    } }],
  })
  const mod = { exports: {} }
  new Function('module', 'exports', 'require', result.outputFiles[0].text)(mod, mod.exports, localRequire)
  return mod.exports.default
}

async function main() {
  global.document = { activeElement: null }
  global.fetch = async () => ({ ok: true, json: async () => ({ data: [
    { id: 'credential-1', name: '测试账号', account: 'alice' },
  ] }) })
  let nextTimer = 0
  const timers = new Map()
  global.window = {
    location: { href: 'https://example.test/' },
    setTimeout(fn) { timers.set(++nextTimer, fn); return nextTimer },
    clearTimeout(id) { timers.delete(id) },
  }
  async function tick() {
    const entry = timers.entries().next().value
    assert.ok(entry, '恢复中应安排下一次刷新')
    timers.delete(entry[0])
    entry[1]()
    await settle()
  }

  const test = process.argv[2]
  const oldGet = deferred()
  const oldPost = deferred()
  let reads = 0
  global.taskWaitTestApi = {
    get(url) {
      reads++
      if (test === 'late_get' && reads === 1) return oldGet.promise
      if (test === 'poll' || test === 'poll_limit') {
        return Promise.resolve(status('req-1', test === 'poll' && reads > 1 ? 'completed' : 'resuming'))
      }
      return Promise.resolve(status(url.includes('/2/') ? 'req-2' : 'req-1', 'pending'))
    },
    post() { return oldPost.promise },
  }
  const modal = await loadComponent()
  const taskId = Vue.ref(1)
  let updates = 0
  const app = renderer.createApp({
    setup: () => () => Vue.h(modal, { taskId: taskId.value, taskName: '测试任务', onUpdated: () => updates++ }),
  })
  app.mount(node('root'))
  await settle()

  if (test === 'late_get') {
    taskId.value = 2
    await settle()
    assert.equal(reads, 2, '切换任务后读取新任务')
    oldGet.resolve(status('req-1', 'failed'))
    await settle()
    assert.ok(find(body, n => n.tag === 'select'), '旧请求失败不得移除新任务的凭据控件')
  } else if (test === 'late_post' || test === 'background') {
    const select = find(body, n => n.tag === 'select')
    select.props.onChange({ target: { value: 'credential-1' } })
    await settle()
    await click('授权并继续')
    if (test === 'background') {
      assert.match(content(body), /后台继续/)
      app.unmount()
      oldPost.resolve(status('req-1', 'completed'))
      await settle()
      assert.equal(updates, 0, '关闭后的迟到提交不再触发更新')
      assert.equal(timers.size, 0)
      return
    }
    taskId.value = 2
    await settle()
    oldPost.resolve(status('req-1', 'completed'))
    await settle()
    assert.equal(updates, 0, '旧提交不能刷新新的任务')
    assert.ok(find(body, n => n.tag === 'select'), '旧提交不能覆盖新任务的等待')
  } else if (test === 'poll') {
    assert.match(content(body), /后台继续/)
    await tick()
    assert.match(content(body), /已恢复并完成/)
    assert.equal(updates, 1)
    assert.equal(timers.size, 0)
  } else {
    for (let index = 0; index < 40 && timers.size; index++) await tick()
    assert.ok(reads > 1)
    assert.equal(timers.size, 0, '持续恢复中的自动刷新应有上限')
    assert.ok(!button('刷新状态').props.disabled, '到达刷新上限后还可手动查看')
  }
  app.unmount()
  assert.equal(timers.size, 0)
}

main().catch(error => { console.error(error); process.exitCode = 1 })
