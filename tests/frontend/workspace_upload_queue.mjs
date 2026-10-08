// 上传队列引擎的运行时行为测试：直接加载真实实现，不复制逻辑。
//
// 为什么必须是行为测试而不是源码字符串断言：并发上限、速率窗口、取消后释放槽位
// 这类时序语义，静态断言完全看不出来。这里注入假上传函数与假时钟，把时间轴握在手里。
import assert from 'node:assert/strict'

import { createWorkspaceUploadQueue } from '../../frontend/src/utils/workspaceUploadQueue.ts'

let passed = 0
const check = (name, fn) => {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

/** 造一个手动控制完成时机的假上传函数。 */
function makeUploader() {
  const calls = []
  const upload = (file, options) =>
    new Promise((resolve, reject) => {
      calls.push({ file, options, resolve, reject })
    })
  return { upload, calls }
}

/** 把定时器换成手动推进的假实现，避免测试依赖真实时间。 */
function makeClock() {
  let now = 0
  const timers = new Map()
  let seq = 0
  return {
    now: () => now,
    setTimer: (fn, ms) => {
      const id = ++seq
      timers.set(id, { fn, ms, nextAt: now + ms })
      return id
    },
    clearTimer: (id) => { timers.delete(id) },
    advance: (ms) => {
      now += ms
      for (const timer of [...timers.values()]) timer.nextAt = now + timer.ms
      for (const timer of [...timers.values()]) timer.fn()
    },
  }
}

const fakeFile = (name, size) => ({ name, size, type: 'application/octet-stream' })
const fileAt = (queue, index) => queue.snapshot().tasks[index]
const flush = async () => {
  for (let i = 0; i < 5; i += 1) await Promise.resolve()
}

// --- 1. 并发上限与补位 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({
    upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer,
  })

  queue.enqueue(
    [fakeFile('a.bin', 100), fakeFile('b.bin', 100), fakeFile('c.bin', 100), fakeFile('d.bin', 100), fakeFile('e.bin', 100)],
    '/ws',
  )

  check('并发上限为 3，只启动前 3 个', () => {
    assert.equal(calls.length, 3)
    assert.deepEqual(calls.map((c) => c.file.name), ['a.bin', 'b.bin', 'c.bin'])
  })

  check('未启动的任务处于 queued，已启动的处于 transferring', () => {
    const byName = Object.fromEntries(queue.snapshot().tasks.map((t) => [t.name, t.status]))
    assert.equal(byName['a.bin'], 'transferring')
    assert.equal(byName['d.bin'], 'queued')
  })

  check('目标目录逐任务携带', () => {
    assert.equal(calls[0].options.parentPath, '/ws')
  })

  calls[0].options.onCommit()
  calls[0].resolve({ data: { data: { name: 'a.bin', stored_name: 'a_1a2b.bin', size: 100 } } })
  await flush()

  check('任务完成后释放槽位并补位启动下一个', () => {
    assert.equal(calls.length, 4)
    assert.equal(calls[3].file.name, 'd.bin')
  })

  queue.destroy()
}

// --- 2. 阶段状态：传输结束必须进入 committing，而不是直接 done ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({
    upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer,
  })
  queue.enqueue([fakeFile('big.pdf', 1000)], '/ws')

  calls[0].options.onProgress({ loaded: 400, total: 1000 })
  clock.advance(400)

  check('传输中进度按 loaded/total 计算', () => {
    const task = fileAt(queue, 0)
    assert.equal(task.status, 'transferring')
    assert.equal(task.percent, 40)
    assert.equal(task.loadedBytes, 400)
  })

  calls[0].options.onCommit()

  check('请求体发完后进入 committing（服务端写入中）', () => {
    assert.equal(fileAt(queue, 0).status, 'committing')
  })

  check('committing 阶段保持 100% 且不再显示剩余时间', () => {
    const task = fileAt(queue, 0)
    assert.equal(task.percent, 100)
    assert.equal(task.remainingSeconds, null)
  })

  calls[0].resolve({ data: { data: { name: 'big.pdf', stored_name: 'big_9f8e.pdf', size: 1000 } } })
  await flush()

  check('成功后落盘名回填到任务（供 UI 解释重名后缀）', () => {
    const task = fileAt(queue, 0)
    assert.equal(task.status, 'done')
    assert.equal(task.storedName, 'big_9f8e.pdf')
  })

  queue.destroy()
}

// --- 3. 速率滑动窗口 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({
    upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer,
  })
  queue.enqueue([fakeFile('x.bin', 10_000_000)], '/ws')

  calls[0].options.onProgress({ loaded: 1_000_000, total: 10_000_000 })
  clock.advance(400)

  check('首个进度样本不虚报速率', () => {
    assert.equal(fileAt(queue, 0).speedBps, null)
  })

  calls[0].options.onProgress({ loaded: 3_000_000, total: 10_000_000 })
  clock.advance(400)

  check('第二个样本给出滑动窗口平均速率（2MB / 0.4s）', () => {
    assert.equal(fileAt(queue, 0).speedBps, 5_000_000)
  })

  check('给出剩余时间估算（7MB / 5MBps ≈ 2s）', () => {
    assert.equal(fileAt(queue, 0).remainingSeconds, 2)
  })

  queue.destroy()
}

// --- 4. 取消 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({
    upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer,
  })
  const [task] = queue.enqueue(
    [fakeFile('a.bin', 10), fakeFile('b.bin', 10), fakeFile('c.bin', 10), fakeFile('d.bin', 10)],
    '/ws',
  )

  check('取消时把 AbortSignal 传给上传函数', () => {
    assert.ok(calls[0].options.signal)
    assert.equal(calls[0].options.signal.aborted, false)
  })

  queue.cancel(task.id)

  check('取消后 signal 已中止', () => {
    assert.equal(calls[0].options.signal.aborted, true)
  })

  check('取消立即释放槽位并补位启动 d.bin', () => {
    assert.equal(calls.length, 4)
    assert.equal(calls[3].file.name, 'd.bin')
  })

  check('取消是终态', () => {
    assert.equal(queue.snapshot().tasks.find((t) => t.id === task.id).status, 'canceled')
  })

  queue.destroy()
}

// --- 5. 失败与重试 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({
    upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer,
  })
  const [task] = queue.enqueue([fakeFile('a.bin', 10)], '/ws')

  calls[0].options.onCommit()
  calls[0].reject({ response: { status: 413, data: { detail: '文件大小超出 200MB 上限' } } })
  await flush()

  check('失败任务保留服务端 detail 文案', () => {
    const t = fileAt(queue, 0)
    assert.equal(t.status, 'failed')
    assert.equal(t.error, '文件大小超出 200MB 上限')
  })

  queue.retry(task.id)

  check('重试重新回到 transferring 并重新调用上传', () => {
    assert.equal(fileAt(queue, 0).status, 'transferring')
    assert.equal(calls.length, 2)
    assert.equal(calls[1].file.name, 'a.bin')
  })

  queue.destroy()
}

// --- 6. 前端预检：超限不入队发起请求 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({
    upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer, maxBytes: 1000,
  })
  queue.enqueue([fakeFile('huge.bin', 2000)], '/ws')

  check('超限任务被标记 failed 且不发起请求', () => {
    assert.equal(calls.length, 0)
    const t = fileAt(queue, 0)
    assert.equal(t.status, 'failed')
    assert.match(t.error, /上限/)
  })

  check('超限任务重试后仍被拦截', () => {
    queue.retry(fileAt(queue, 0).id)
    assert.equal(calls.length, 0)
    assert.equal(fileAt(queue, 0).status, 'failed')
  })

  queue.destroy()
}

// --- 7. 汇总与活动判定 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({
    upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer,
  })
  queue.enqueue([fakeFile('a.bin', 100), fakeFile('b.bin', 300)], '/ws')

  check('汇总统计总数与总字节', () => {
    const agg = queue.snapshot().aggregate
    assert.equal(agg.total, 2)
    assert.equal(agg.totalBytes, 400)
    assert.equal(agg.done, 0)
    assert.equal(agg.active, true)
  })

  calls[0].options.onProgress({ loaded: 100, total: 100 })
  calls[0].options.onCommit()
  calls[0].resolve({ data: { data: { name: 'a.bin', stored_name: 'a_1.bin' } } })
  await flush()

  queue.cancel(fileAt(queue, 1).id)

  check('全部进入终态后 active 为 false（据此触发一次目录刷新）', () => {
    assert.equal(queue.snapshot().aggregate.active, false)
  })

  check('汇总区分成功与取消数', () => {
    const agg = queue.snapshot().aggregate
    assert.equal(agg.done, 1)
    assert.equal(agg.canceled, 1)
  })

  check('clearFinished 移除全部终态任务', () => {
    queue.clearFinished()
    assert.equal(queue.snapshot().tasks.length, 0)
  })

  queue.destroy()
}

// --- 8. 订阅与销毁 ---
{
  const clock = makeClock()
  const { upload } = makeUploader()
  const queue = createWorkspaceUploadQueue({
    upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer,
  })
  let notifications = 0
  queue.subscribe(() => { notifications += 1 })
  queue.enqueue([fakeFile('a.bin', 10)], '/ws')

  check('入队即通知订阅者', () => {
    assert.ok(notifications > 0)
  })

  const before = notifications
  queue.destroy()
  queue.enqueue([fakeFile('z.bin', 10)], '/ws')

  check('destroy 后不再通知订阅者', () => {
    assert.equal(notifications, before)
  })
}

// --- 9. 边界：0 字节文件不应被算成 0% 而卡住 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({
    upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer,
  })
  queue.enqueue([fakeFile('empty.txt', 0)], '/ws')
  calls[0].resolve({ data: { data: { name: 'empty.txt', stored_name: 'empty_ab.bin' } } })
  await flush()

  check('0 字节文件完成后为 100% 而不是 0%', () => {
    assert.equal(fileAt(queue, 0).percent, 100)
    assert.equal(fileAt(queue, 0).status, 'done')
  })

  queue.destroy()
}

console.log(`工作空间上传队列行为测试全部通过（${passed} 项）`)
