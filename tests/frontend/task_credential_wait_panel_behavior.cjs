/* 运行实际任务凭据卡片及选择器。网络与计时器由测试控制。 */
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
const root = node('root')
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
  const n = find(root, n => n.tag === 'button' && content(n).trim() === label)
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
    entryPoints: [path.join(frontend, 'src/components/task/TaskCredentialWaitPanel.vue')],
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
    { id: 'credential-1', name: '测试账号', account: 'alice', owner_scope: 'user' },
    { id: 'credential-2', name: '共享账号', account: 'bob', owner_scope: 'system' },
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
  const posts = []
  global.taskWaitTestApi = {
    get(url) {
      reads++
      if (test === 'late_get' && reads === 1) return oldGet.promise
      if (test === 'expired') {
        const response = status('req-1', 'pending')
        response.data.data.expires_at = new Date(Date.now() - 1000).toISOString()
        return Promise.resolve(response)
      }
      if (test === 'poll' || test === 'poll_limit') {
        return Promise.resolve(status('req-1', test === 'poll' && reads > 1 ? 'completed' : 'resuming'))
      }
      return Promise.resolve(status(url.includes('/2/') ? 'req-2' : 'req-1', 'pending'))
    },
    post(url, payload) {
      posts.push({ url, payload })
      return test === 'inline' ? Promise.resolve(status('req-1', 'completed')) : oldPost.promise
    },
  }
  const panel = await loadComponent()
  const taskId = Vue.ref(1)
  let updates = 0
  const app = renderer.createApp({
    setup: () => () => Vue.h(panel, {
      taskId: taskId.value, taskName: '测试任务', traceId: test === 'stale_trace' ? 'past-trace' : 'trace',
      onUpdated: () => updates++,
    }),
  })
  app.mount(root)
  await settle()

  if (test === 'inline') {
    assert.match(content(root), /测试账号.*alice.*个人.*共享账号.*bob.*系统共享/)
    assert.ok(!find(root, n => n.props.role === 'dialog'))
    assert.equal(body.children.length, 0, '凭据卡片应随运行记录渲染')
    assert.equal(button('授权并继续').props.disabled, true)
    const radio = find(root, n => n.tag === 'input' && n.props.value === 'credential-2')
    radio.props.onChange()
    await settle()
    assert.equal(posts.length, 0, '选择选项后仍需明确确认')
    await click('授权并继续')
    assert.deepEqual(posts, [{
      url: '/api/v1/tasks/1/credential-wait',
      payload: { request_id: 'req-1', cancelled: false, secret_id: 'credential-2', remember_credential: true },
    }])
    assert.match(content(root), /已恢复并完成/)
    assert.ok(!find(root, n => n.tag === 'input' && n.props.type === 'radio'))
  } else if (test === 'stale_trace') {
    assert.ok(!find(root, n => n.tag === 'input' && n.props.type === 'radio'))
    assert.match(content(root), /该次运行暂无/)
  } else if (test === 'expired') {
    await tick()
    assert.match(content(root), /已过期/)
    assert.ok(!find(root, n => n.tag === 'input' && n.props.type === 'radio'))
    assert.equal(posts.length, 0)
  } else if (test === 'late_get') {
    taskId.value = 2
    await settle()
    assert.equal(reads, 2, '切换任务后读取新任务')
    oldGet.resolve(status('req-1', 'failed'))
    await settle()
    assert.ok(find(root, n => n.tag === 'input' && n.props.type === 'radio'), '旧请求失败不得移除新任务的凭据控件')
  } else if (test === 'late_post' || test === 'background') {
    const radio = find(root, n => n.tag === 'input' && n.props.value === 'credential-1')
    radio.props.onChange()
    await settle()
    await click('授权并继续')
    if (test === 'background') {
      assert.match(content(root), /后台继续/)
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
    assert.ok(find(root, n => n.tag === 'input' && n.props.type === 'radio'), '旧提交不能覆盖新任务的等待')
  } else if (test === 'poll') {
    assert.match(content(root), /后台继续/)
    await tick()
    assert.match(content(root), /已恢复并完成/)
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
