# 工作空间上传进度与状态反馈 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让工作空间「上传到此目录」的每个阶段（排队 / 传输 / 服务端写入 / 完成 / 失败）都可见、可取消、可重试，并把后端上传从「一次性读入内存 + 20MB 硬上限」改为分块流式落盘 + 可配置上限。

**Architecture:** 前端新增零依赖的上传队列引擎（`workspaceUploadQueue.ts`，纯逻辑、可被 Node 直接加载测试），由 Vue composable 持有实例、由抽屉组件渲染浮层面板；后端 `upload_to_workspace` 改为 1MiB 分块流式落盘，大小上限来自 `WORKSPACE_UPLOAD_MAX_MB` 配置，超限中途截断并清理半截文件。

**Tech Stack:** Vue 3 + TypeScript + Vite 7 + Tailwind 3（前端）、FastAPI + Pydantic 2（后端）、pytest + Node 行为测试脚本（测试）。

**设计依据：** [`docs/superpowers/specs/2026-10-08-workspace-upload-progress-design.md`](../specs/2026-10-08-workspace-upload-progress-design.md)

---

## 关键前置事实（实施者必读）

1. **`onUploadProgress` 事件粒度不可依赖。** 实测：5MB 文件只触发 1 次（0%→100%）、60MB 触发 2 次、耗时 114ms。因此进度刷新由 **400ms 定时器**驱动，进度事件仅作为 `loaded` 的更新来源；`committing`（服务端写入中）阶段必须显式建模。
2. **前端测试有两种既有形态**，本计划两种都用：
   - 源码契约：pytest 读源码做字符串断言（如 `tests/frontend/test_canvas_text_preview_contract.py`）。
   - 运行时行为：`.mjs` 脚本用 `node --experimental-strip-types --no-warnings` 直接 import `.ts` 纯逻辑模块（如 `tests/frontend/document_preview_formats.mjs`），由对应 pytest 用 `subprocess.run` 驱动。
3. **`.ts` 模块必须零运行时依赖**才能被 Node 直接加载。上传队列引擎**禁止** import `axios`、`@/utils/axios`、任何 `.vue`。上传动作以函数注入。
4. **本仓库当前无 nginx/ingress 参与上传链路**（dev 走 Vite 代理，生产由 `app/main.py:484` 的 `StaticFiles` 托管）。网关缓冲对齐已由用户明确排除，**不要**改 nginx 配置。
5. **不要执行 `git commit`**（项目强制约定）。计划中所有「提交」步骤改为「暂存并汇报」，由用户自行提交。
6. **不要执行 `./dev.sh`** 或任何服务启停脚本。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `frontend/src/utils/workspaceUploadQueue.ts`（新增） | 零依赖纯逻辑：任务状态机、并发调度、速率估算、取消/重试、汇总快照 |
| `tests/frontend/workspace_upload_queue.mjs`（新增） | Node 运行时行为测试，直接 import 上述 `.ts` |
| `tests/frontend/test_workspace_upload_contract.py`（新增） | 驱动 `.mjs` + 对 `.ts`/`.vue`/`workspaceFilePreview.ts` 做源码契约断言 |
| `frontend/src/utils/workspaceFilePreview.ts`（修改） | `uploadToWorkspaceDir` 增加 `onProgress` + `signal`，返回 `stored_name` |
| `frontend/src/components/embed/WorkspaceBrowserDrawer.vue`（修改） | 上传浮层面板、批量刷新策略、接线上传函数 |
| `app/core/config.py`（修改） | 新增 `WORKSPACE_UPLOAD_MAX_MB` |
| `env.example` / `docker/env.example`（修改） | 同步环境变量与注释 |
| `app/api/v1/endpoints/fs.py`（修改） | 分块流式落盘、动态上限、413、失败清理、响应字段 |
| `tests/api/v1/test_fs_browser.py`（修改） | 新增上传接口用例（当前零覆盖） |

---

## Task 1: 上传队列引擎（状态机 + 并发 + 速率）

**Files:**
- Create: `frontend/src/utils/workspaceUploadQueue.ts`
- Test: `tests/frontend/workspace_upload_queue.mjs`, `tests/frontend/test_workspace_upload_contract.py`

- [ ] **Step 1: 写失败测试（Node 行为测试）**

创建 `tests/frontend/workspace_upload_queue.mjs`：

```js
// 上传队列引擎的运行时行为测试：直接加载真实实现，不复制逻辑。
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

/** 把定时器换成手动推进的假实现。 */
function makeClock() {
  let now = 0
  const timers = new Map()
  let seq = 0
  return {
    now: () => now,
    setTimer: (fn, ms) => {
      const id = ++seq
      timers.set(id, { fn, ms, nextAt: now + ms, repeat: true })
      return id
    },
    clearTimer: (id) => { timers.delete(id) },
    advance: (ms) => {
      now += ms
      for (const timer of [...timers.values()]) {
        if (timer.nextAt <= now) timer.nextAt = now + timer.ms
      }
      for (const timer of [...timers.values()]) timer.fn()
    },
  }
}

const fakeFile = (name, size) => ({ name, size, type: 'application/octet-stream' })

// --- 1. 并发上限：入队 5 个只启动 3 个，且顺序为入队顺序 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({ upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer })

  queue.enqueue([fakeFile('a.bin', 100), fakeFile('b.bin', 100), fakeFile('c.bin', 100), fakeFile('d.bin', 100), fakeFile('e.bin', 100)], '/ws')

  check('并发上限为 3，只启动前 3 个', () => {
    assert.equal(calls.length, 3)
    assert.deepEqual(calls.map((c) => c.file.name), ['a.bin', 'b.bin', 'c.bin'])
  })

  check('未启动的任务处于 queued', () => {
    const byName = Object.fromEntries(queue.snapshot().tasks.map((t) => [t.name, t.status]))
    assert.equal(byName['d.bin'], 'queued')
    assert.equal(byName['a.bin'], 'transferring')
  })

  check('目标目录逐任务携带', () => {
    assert.equal(calls[0].options.parentPath, '/ws')
  })

  // 完成第一个后，应立刻补位启动第四个
  calls[0].options.onCommit()
  calls[0].resolve({ data: { data: { name: 'a.bin', stored_name: 'a_1a2b.bin', path: '/ws/a_1a2b.bin', size: 100 } } })

  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()

  check('任务完成后释放槽位并启动下一个', () => {
    assert.equal(calls.length, 4)
    assert.equal(calls[3].file.name, 'd.bin')
  })

  queue.destroy()
}

// --- 2. 阶段状态：传输结束必须进入 committing 而不是直接 done ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({ upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer })
  queue.enqueue([fakeFile('big.pdf', 1000)], '/ws')

  calls[0].options.onProgress({ loaded: 400, total: 1000 })
  clock.advance(400)

  check('传输中进度按 loaded/total 计算', () => {
    const task = queue.snapshot().tasks[0]
    assert.equal(task.status, 'transferring')
    assert.equal(task.percent, 40)
    assert.equal(task.loadedBytes, 400)
  })

  calls[0].options.onCommit()

  check('请求体发完后进入 committing（服务端写入中）', () => {
    assert.equal(queue.snapshot().tasks[0].status, 'committing')
  })

  check('committing 阶段 percent 保持 100 且不再有 remaining', () => {
    const task = queue.snapshot().tasks[0]
    assert.equal(task.percent, 100)
    assert.equal(task.remainingSeconds, null)
  })

  calls[0].resolve({ data: { data: { name: 'big.pdf', stored_name: 'big_9f8e.pdf', path: '/ws/big_9f8e.pdf', size: 1000 } } })
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve()

  check('成功后落盘名回填到任务', () => {
    const task = queue.snapshot().tasks[0]
    assert.equal(task.status, 'done')
    assert.equal(task.storedName, 'big_9f8e.pdf')
  })

  queue.destroy()
}

// --- 3. 速率：滑动窗口平均，且首个样本不虚报 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({ upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer })
  queue.enqueue([fakeFile('x.bin', 10_000_000)], '/ws')

  calls[0].options.onProgress({ loaded: 1_000_000, total: 10_000_000 })
  clock.advance(400)

  check('首个进度样本不虚报速率', () => {
    assert.equal(queue.snapshot().tasks[0].speedBps, null)
  })

  calls[0].options.onProgress({ loaded: 3_000_000, total: 10_000_000 })
  clock.advance(400)

  check('第二个样本给出滑动窗口平均速率', () => {
    // 窗口内 2,000,000 字节 / 0.4 秒 = 5,000,000 B/s
    assert.equal(queue.snapshot().tasks[0].speedBps, 5_000_000)
  })

  check('给出剩余时间估算（7,000,000 / 5,000,000 = 1.4s）', () => {
    assert.equal(queue.snapshot().tasks[0].remainingSeconds, 2)
  })

  queue.destroy()
}

// --- 4. 取消：释放槽位、进入终态、不再重试 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({ upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer })
  const [task] = queue.enqueue([fakeFile('a.bin', 10), fakeFile('b.bin', 10), fakeFile('c.bin', 10), fakeFile('d.bin', 10)], '/ws')

  check('取消时把 AbortSignal 传给上传函数', () => {
    assert.ok(calls[0].options.signal)
    assert.equal(calls[0].options.signal.aborted, false)
  })

  queue.cancel(task.id)

  check('取消后 signal 已中止', () => {
    assert.equal(calls[0].options.signal.aborted, true)
  })

  check('取消立即释放槽位并启动 d.bin', () => {
    assert.equal(calls.length, 4)
    assert.equal(calls[3].file.name, 'd.bin')
  })

  check('取消是终态', () => {
    const target = queue.snapshot().tasks.find((t) => t.id === task.id)
    assert.equal(target.status, 'canceled')
  })

  queue.destroy()
}

// --- 5. 失败与重试 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({ upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer })
  const [task] = queue.enqueue([fakeFile('a.bin', 10)], '/ws')

  calls[0].options.onCommit()
  calls[0].reject({ response: { status: 413, data: { detail: '文件大小超出 200MB 限制' } } })
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve()

  check('失败任务带服务端 detail 文案', () => {
    const t = queue.snapshot().tasks[0]
    assert.equal(t.status, 'failed')
    assert.equal(t.error, '文件大小超出 200MB 限制')
  })

  queue.retry(task.id)

  check('重试重新回到 transferring 并重新调用上传', () => {
    assert.equal(queue.snapshot().tasks[0].status, 'transferring')
    assert.equal(calls.length, 2)
    assert.equal(calls[1].file.name, 'a.bin')
  })

  queue.destroy()
}

// --- 6. 预检：超出上限的任务不入队发起请求 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({
    upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer, maxBytes: 1000,
  })
  queue.enqueue([fakeFile('huge.bin', 2000)], '/ws')

  check('超限任务被标记 failed 且不发起请求', () => {
    assert.equal(calls.length, 0)
    const t = queue.snapshot().tasks[0]
    assert.equal(t.status, 'failed')
    assert.match(t.error, /上限/)
  })

  check('超限任务不可重试后仍然被拦截', () => {
    queue.retry(queue.snapshot().tasks[0].id)
    assert.equal(calls.length, 0)
  })

  queue.destroy()
}

// --- 7. 汇总与活动判定 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({ upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer })
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
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve()

  check('全部终态后 active 为 false（用于触发一次目录刷新）', () => {
    queue.cancel(queue.snapshot().tasks[1].id)
    assert.equal(queue.snapshot().aggregate.active, false)
  })

  check('汇总区分成功与失败数', () => {
    const agg = queue.snapshot().aggregate
    assert.equal(agg.done, 1)
    assert.equal(agg.canceled, 1)
  })

  check('clearFinished 只移除终态任务', () => {
    queue.clearFinished()
    assert.equal(queue.snapshot().tasks.length, 0)
  })

  queue.destroy()
}

// --- 8. 订阅：状态变化通知，destroy 后停止 ---
{
  const clock = makeClock()
  const { upload, calls } = makeUploader()
  const queue = createWorkspaceUploadQueue({ upload, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer })
  let notifications = 0
  queue.subscribe(() => { notifications += 1 })
  queue.enqueue([fakeFile('a.bin', 10)], '/ws')
  check('入队即通知订阅者', () => { assert.ok(notifications > 0) })

  const before = notifications
  queue.destroy()
  queue.enqueue([fakeFile('z.bin', 10)], '/ws')
  check('destroy 后不再通知', () => { assert.equal(notifications, before) })
}

console.log(`工作空间上传队列行为测试全部通过（${passed} 项）`)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && node --experimental-strip-types --no-warnings tests/frontend/workspace_upload_queue.mjs`

Expected: 失败，报错形如 `Cannot find module '.../frontend/src/utils/workspaceUploadQueue.ts'`（模块尚不存在），退出码非 0。

- [ ] **Step 3: 实现队列引擎**

创建 `frontend/src/utils/workspaceUploadQueue.ts`：

```ts
/**
 * 工作空间上传队列引擎（零运行时依赖）。
 *
 * 为什么不用 Vue 实现：并发调度、速率窗口、取消/重试这些是纯粹的时序逻辑，
 * 抽成零依赖模块后可以被 `node --experimental-strip-types` 直接加载做行为测试
 * （见 tests/frontend/workspace_upload_queue.mjs），无需 jsdom 或组件挂载。
 *
 * 为什么进度由定时器驱动而非进度事件：浏览器把请求体写入内核 socket 缓冲区即
 * 视为「发送完成」，5MB 文件只会触发一次 onUploadProgress（实测），60MB 也只触发
 * 两次。因此进度事件只用来更新已传字节数，UI 刷新节奏由 400ms 定时器保证；同时
 * 显式建模 committing（服务端写入中）阶段，避免进度条在 100% 处无解释地滞留。
 */

export type WorkspaceUploadStatus =
  | 'queued'
  | 'transferring'
  | 'committing'
  | 'done'
  | 'failed'
  | 'canceled'

export interface WorkspaceUploadTask {
  id: string
  name: string
  storedName: string | null
  size: number
  loadedBytes: number
  percent: number
  status: WorkspaceUploadStatus
  error: string | null
  speedBps: number | null
  remainingSeconds: number | null
  parentPath: string
}

export interface WorkspaceUploadAggregate {
  total: number
  done: number
  failed: number
  canceled: number
  totalBytes: number
  loadedBytes: number
  active: boolean
}

export interface WorkspaceUploadSnapshot {
  tasks: WorkspaceUploadTask[]
  aggregate: WorkspaceUploadAggregate
}

/** 进度回调载荷：与 axios onUploadProgress 的 ProgressEvent 结构兼容。 */
export interface WorkspaceUploadProgressPayload {
  loaded: number
  total?: number
}

export interface WorkspaceUploadOptions {
  /** 目标目录绝对路径。 */
  parentPath: string
  /** 进度更新。 */
  onProgress: (payload: WorkspaceUploadProgressPayload) => void
  /** 请求体已发送完毕、开始等待服务端落盘与响应。 */
  onCommit: () => void
  /** 中止传输。 */
  signal: AbortSignal
}

export type WorkspaceUploadFn = (
  file: File | { name: string; size: number; type?: string },
  options: WorkspaceUploadOptions,
) => Promise<any>

export interface WorkspaceUploadQueueOptions {
  upload: WorkspaceUploadFn
  now?: () => number
  setTimer?: (fn: () => void, ms: number) => any
  clearTimer?: (id: any) => void
  concurrency?: number
  /** 前端预检上限（字节）。超限任务直接标记失败，不发起请求。 */
  maxBytes?: number
}

export interface WorkspaceUploadQueue {
  enqueue: (
    files: Array<File | { name: string; size: number; type?: string }>,
    parentPath: string,
  ) => WorkspaceUploadTask[]
  snapshot: () => WorkspaceUploadSnapshot
  subscribe: (listener: () => void) => () => void
  cancel: (taskId: string) => void
  retry: (taskId: string) => void
  clearFinished: () => void
  destroy: () => void
}

export const DEFAULT_UPLOAD_CONCURRENCY = 3
/** UI 刷新间隔：进度事件可能只触发一次，界面节奏必须自己保证。 */
export const UPLOAD_PROGRESS_REFRESH_MS = 400
/** 速率滑动窗口，避免事件稀疏导致速率剧烈抖动。 */
const RATE_WINDOW_MS = 2000
const RATE_BUCKET_MS = 250

function toPercent(loaded: number, size: number): number {
  if (!size || size <= 0) return 100
  const ratio = loaded / size
  if (!Number.isFinite(ratio) || ratio <= 0) return 0
  if (ratio >= 1) return 100
  return Math.max(1, Math.floor(ratio * 100))
}

function extractServerMessage(error: any): string {
  const data = error?.response?.data
  const detail = data?.detail ?? data?.message
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]
    if (typeof first?.msg === 'string') return first.msg
  }
  if (typeof error?.message === 'string' && error.message && error.message !== 'canceled') {
    return error.message
  }
  return '上传失败'
}

function isAbortError(error: any): boolean {
  return error?.code === 'ERR_CANCELED' || error?.name === 'CanceledError' || error?.name === 'AbortError'
}

export function createWorkspaceUploadQueue(
  options: WorkspaceUploadQueueOptions,
): WorkspaceUploadQueue {
  const upload = options.upload
  const now = options.now ?? (() => Date.now())
  const setTimer = options.setTimer ?? ((fn, ms) => setInterval(fn, ms) as any)
  const clearTimer = options.clearTimer ?? ((id) => clearInterval(id as any))
  const concurrency = Math.max(1, Math.floor(options.concurrency ?? DEFAULT_UPLOAD_CONCURRENCY))
  const maxBytes = options.maxBytes ?? Number.POSITIVE_INFINITY

  const tasks = new Map<string, WorkspaceUploadTask>()
  const controllers = new Map<string, AbortController>()
  const rateBuckets = new Map<string, Array<{ at: number; delta: number }>>()
  const lastLoaded = new Map<string, number>()
  const files = new Map<string, any>()
  const listeners = new Set<() => void>()

  let seq = 0
  let timerId: any = null
  let destroyed = false

  const snapshot = (): WorkspaceUploadSnapshot => {
    const list = [...tasks.values()]
    const aggregate: WorkspaceUploadAggregate = {
      total: list.length,
      done: list.filter((t) => t.status === 'done').length,
      failed: list.filter((t) => t.status === 'failed').length,
      canceled: list.filter((t) => t.status === 'canceled').length,
      totalBytes: list.reduce((sum, t) => sum + (t.size || 0), 0),
      loadedBytes: list.reduce((sum, t) => sum + Math.min(t.loadedBytes || 0, t.size || t.loadedBytes || 0), 0),
      active: list.some((t) => t.status === 'queued' || t.status === 'transferring' || t.status === 'committing'),
    }
    return { tasks: list, aggregate }
  }

  let lastPushed = ''
  const notify = () => {
    if (destroyed) return
    const serialized = JSON.stringify(snapshot())
    if (serialized === lastPushed) return
    lastPushed = serialized
    for (const listener of listeners) {
      try {
        listener()
      } catch (error) {
        console.error('[workspaceUploadQueue] listener failed', error)
      }
    }
  }

  const clearTimerIfIdle = () => {
    const hasActive = [...tasks.values()].some((t) => t.status === 'transferring')
    if (!hasActive && timerId !== null) {
      clearTimer(timerId)
      timerId = null
    }
  }

  const ensureTimer = () => {
    if (destroyed || timerId !== null) return
    timerId = setTimer(() => {
      tickRates()
      notify()
      clearTimerIfIdle()
    }, UPLOAD_PROGRESS_REFRESH_MS)
  }

  /** 累积速率分桶，并按 2 秒窗口求平均（含时间跨度归一化）。 */
  const tickRates = () => {
    const current = now()
    for (const task of tasks.values()) {
      if (task.status !== 'transferring') {
        if (task.status !== 'committing') task.speedBps = null
        continue
      }
      const loaded = lastLoaded.get(task.id) ?? 0
      const buckets = rateBuckets.get(task.id) ?? []
      const last = buckets.length ? buckets[buckets.length - 1] : null
      const delta = Math.max(0, loaded - (last ? last.total : 0))
      if (!last) {
        buckets.push({ at: current, total: loaded, delta: 0 })
      } else {
        last.delta += delta
        last.total = loaded
        if (current - last.at >= RATE_BUCKET_MS) {
          buckets.push({ at: current, total: loaded, delta: 0 })
        }
      }
      while (buckets.length && current - buckets[0].at > RATE_WINDOW_MS) buckets.shift()
      rateBuckets.set(task.id, buckets)

      const samples = buckets.filter((b) => b.delta > 0)
      const totalDelta = samples.reduce((sum, b) => sum + b.delta, 0)
      if (totalDelta <= 0) {
        task.speedBps = null
        task.remainingSeconds = null
        continue
      }
      const span = Math.max(current - (samples[0]?.at ?? current), RATE_BUCKET_MS)
      const speed = totalDelta / (span / 1000)
      task.speedBps = Math.round(speed)
      const remainingBytes = Math.max(0, (task.size || 0) - loaded)
      task.remainingSeconds = remainingBytes > 0 && speed > 0 ? Math.ceil(remainingBytes / speed) : null
    }
  }

  const pump = () => {
    if (destroyed) return
    let running = [...tasks.values()].filter(
      (t) => t.status === 'transferring' || t.status === 'committing',
    ).length
    for (const task of tasks.values()) {
      if (running >= concurrency) break
      if (task.status !== 'queued') continue
      running += 1
      startTask(task)
    }
  }

  const startTask = (task: WorkspaceUploadTask) => {
    if (task.size > maxBytes) {
      task.status = 'failed'
      task.error = `文件大小超出 ${formatLimit(maxBytes)} 上限`
      return
    }
    const file = files.get(task.id)
    const controller = new AbortController()
    controllers.set(task.id, controller)
    task.status = 'transferring'
    task.error = null
    task.loadedBytes = 0
    task.percent = 0
    task.speedBps = null
    task.remainingSeconds = null
    lastLoaded.set(task.id, 0)
    rateBuckets.set(task.id, [])
    ensureTimer()

    const settle = (fn: () => void) => {
      controllers.delete(task.id)
      fn()
      clearTimerIfIdle()
      notify()
      pump()
    }

    Promise.resolve(
      upload(file, {
        parentPath: task.parentPath,
        signal: controller.signal,
        onProgress: (payload) => {
          const loaded = Math.max(0, Number(payload?.loaded) || 0)
          const total = Number(payload?.total) || task.size || 0
          lastLoaded.set(task.id, loaded)
          task.loadedBytes = total > 0 ? Math.min(loaded, total) : loaded
          task.percent = toPercent(task.loadedBytes, total || task.size)
          notify()
        },
        onCommit: () => {
          if (task.status === 'transferring') {
            task.status = 'committing'
            task.percent = 100
            task.speedBps = null
            task.remainingSeconds = null
            notify()
          }
        },
      }),
    )
      .then((response) => {
        const data = response?.data?.data ?? response?.data ?? {}
        settle(() => {
          task.status = 'done'
          task.percent = 100
          task.loadedBytes = task.size || task.loadedBytes
          task.speedBps = null
          task.remainingSeconds = null
          task.storedName = typeof data?.stored_name === 'string' ? data.stored_name : null
        })
      })
      .catch((error) => {
        settle(() => {
          if (isAbortError(error) || task.status === 'canceled') {
            task.status = 'canceled'
            task.error = null
          } else {
            task.status = 'failed'
            task.error = extractServerMessage(error)
          }
          task.speedBps = null
          task.remainingSeconds = null
        })
      })
  }

  const enqueue = (incoming: Array<any>, parentPath: string): WorkspaceUploadTask[] => {
    if (destroyed) return []
    const created: WorkspaceUploadTask[] = []
    for (const file of incoming) {
      seq += 1
      const size = Number(file?.size) || 0
      const task: WorkspaceUploadTask = {
        id: `upload_${seq}`,
        name: String(file?.name ?? `file_${seq}`),
        storedName: null,
        size,
        loadedBytes: 0,
        percent: 0,
        status: 'queued',
        error: null,
        speedBps: null,
        remainingSeconds: null,
        parentPath,
      }
      tasks.set(task.id, task)
      files.set(task.id, file)
      created.push(task)
    }
    notify()
    pump()
    notify()
    return created
  }

  const cancel = (taskId: string) => {
    const task = tasks.get(taskId)
    if (!task) return
    if (task.status === 'done' || task.status === 'failed' || task.status === 'canceled') return
    task.status = 'canceled'
    task.speedBps = null
    task.remainingSeconds = null
    if (task.status === 'canceled') {
      controllers.get(taskId)?.abort()
      controllers.delete(taskId)
    }
    notify()
    pump()
  }

  const retry = (taskId: string) => {
    const task = tasks.get(taskId)
    if (!task) return
    if (task.status !== 'failed' && task.status !== 'canceled') return
    task.status = 'queued'
    task.error = null
    task.loadedBytes = 0
    task.percent = 0
    notify()
    pump()
  }

  const clearFinished = () => {
    for (const [id, task] of [...tasks.entries()]) {
      if (task.status === 'done' || task.status === 'failed' || task.status === 'canceled') {
        tasks.delete(id)
        files.delete(id)
        rateBuckets.delete(id)
        lastLoaded.delete(id)
      }
    }
    notify()
  }

  const destroy = () => {
    destroyed = true
    for (const controller of controllers.values()) controller.abort()
    controllers.clear()
    if (timerId !== null) {
      clearTimer(timerId)
      timerId = null
    }
    listeners.clear()
  }

  const subscribe = (listener: () => void) => {
    listeners.add(listener)
    return () => listeners.delete(listener)
  }

  return { enqueue, snapshot, subscribe, cancel, retry, clearFinished, destroy }
}

function formatLimit(bytes: number): string {
  if (!Number.isFinite(bytes)) return '不限'
  if (bytes >= 1024 * 1024 * 1024) return `${Math.round(bytes / (1024 * 1024 * 1024))}GB`
  return `${Math.round(bytes / (1024 * 1024))}MB`
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && node --experimental-strip-types --no-warnings tests/frontend/workspace_upload_queue.mjs`

Expected: 全部通过，最后一行输出 `工作空间上传队列行为测试全部通过（N 项）`，退出码 0。

- [ ] **Step 5: 写源码契约测试**

创建 `tests/frontend/test_workspace_upload_contract.py`：

```python
"""工作空间上传进度与状态反馈的契约。

用户诉求：上传（尤其是大文件）不能没有任何进度反馈。
实测结论：onUploadProgress 事件粒度不可依赖（5MB 只触发 1 次、60MB 触发 2 次），
因此界面节奏必须由定时器驱动，并显式区分「传输中」与「服务端写入中」。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_upload_queue_behaviour_via_node():
    script = Path("tests/frontend/workspace_upload_queue.mjs")
    assert script.exists(), "上传队列行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "工作空间上传队列行为测试全部通过" in proc.stdout


def test_queue_engine_is_dependency_free():
    source = _source("frontend/src/utils/workspaceUploadQueue.ts")

    # 零运行时依赖：必须能被 node --experimental-strip-types 直接加载
    assert "from 'axios'" not in source
    assert "@/utils/axios" not in source
    assert ".vue" not in source
    # 阶段状态必须含 committing，否则进度条会在 100% 处无解释滞留
    for status in ("'queued'", "'transferring'", "'committing'", "'done'", "'failed'", "'canceled'"):
        assert status in source, f"缺少状态 {status}"
    assert "DEFAULT_UPLOAD_CONCURRENCY = 3" in source
    assert "UPLOAD_PROGRESS_REFRESH_MS = 400" in source


def test_upload_transport_passes_progress_and_signal():
    source = _source("frontend/src/utils/workspaceFilePreview.ts")

    assert "onUploadProgress" in source, "上传未接进度回调"
    assert "signal" in source, "上传未接取消信号"
    assert "stored_name" in source, "未回传落盘名，UI 无法解释重名后缀"


def test_drawer_wires_queue_and_renders_committing_state():
    drawer = _source("frontend/src/components/embed/WorkspaceBrowserDrawer.vue")

    assert "createWorkspaceUploadQueue" in drawer, "抽屉未接上传队列引擎"
    assert "服务端写入中" in drawer, "缺少服务端写入阶段的可见文案"
    # 批量上传不应每个文件都刷新目录，避免互相打断滚动位置
    assert "aggregate.active" in drawer, "未按队列活动状态收敛目录刷新"
```

- [ ] **Step 7: 运行契约测试确认失败**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest tests/frontend/test_workspace_upload_contract.py -q --confcutdir=tests/frontend`

Expected: `test_upload_queue_behaviour_via_node` 与 `test_queue_engine_is_dependency_free` **通过**；`test_upload_transport_passes_progress_and_signal` 与 `test_drawer_wires_queue_and_renders_committing_state` **失败**（尚未接线）。这正是后续任务的 RED。

---

## Task 2: 上传传输层接入进度与取消

**Files:**
- Modify: `frontend/src/utils/workspaceFilePreview.ts:380-386`
- Test: `tests/frontend/test_workspace_upload_contract.py::test_upload_transport_passes_progress_and_signal`

- [ ] **Step 1: 确认测试当前失败（RED）**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest tests/frontend/test_workspace_upload_contract.py::test_upload_transport_passes_progress_and_signal -q --confcutdir=tests/frontend`

Expected: FAIL，`AssertionError: 上传未接进度回调`。

- [ ] **Step 2: 改造上传函数**

把 `frontend/src/utils/workspaceFilePreview.ts` 中的 `uploadToWorkspaceDir` 替换为：

```ts
export interface WorkspaceUploadTransportOptions {
  /** 进度回调：axios 会透传 ProgressEvent。快链路下可能只触发一次。 */
  onProgress?: (payload: { loaded: number; total?: number }) => void
  /** 请求体发送完毕、开始等待服务端落盘与响应。 */
  onCommit?: () => void
  /** 取消信号。 */
  signal?: AbortSignal
}

export async function uploadToWorkspaceDir(
  parentPath: string,
  file: File,
  options?: WorkspaceUploadTransportOptions,
) {
  const form = new FormData()
  form.append('file', file)
  return axios.post('/api/v1/chat/fs/upload', form, {
    params: { parent_path: parentPath },
    signal: options?.signal,
    // 大文件落盘可能超过默认 60s；此处不设 timeout，交由用户取消或服务端 413 终止
    timeout: 0,
    onUploadProgress: (event: any) => {
      const total = Number(event?.total) || file.size
      options?.onProgress?.({ loaded: Number(event?.loaded) || 0, total })
      // 请求体已写入 socket，后续等待即服务端落盘阶段
      if (total > 0 && Number(event?.loaded) >= total) options?.onCommit?.()
    },
  })
}
```

- [ ] **Step 3: 运行该测试确认通过（GREEN）**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest tests/frontend/test_workspace_upload_contract.py::test_upload_transport_passes_progress_and_signal -q --confcutdir=tests/frontend`

Expected: PASS。

- [ ] **Step 4: 类型检查**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform/frontend && npx vue-tsc --noEmit`

Expected: 无新增类型错误（既有错误若存在需确认与本次改动无关）。

---

## Task 3: 后端分块流式落盘与可配置上限

**Files:**
- Modify: `app/core/config.py`（在 `MCP_RATE_LIMIT_USER_PER_MINUTE` 之后追加）
- Modify: `app/api/v1/endpoints/fs.py:756-761`（响应模型）、`fs.py:668-669`（常量）、`fs.py:932-970`（接口）
- Modify: `env.example`、`docker/env.example`
- Test: `tests/api/v1/test_fs_browser.py`

- [ ] **Step 1: 写失败测试**

在 `tests/api/v1/test_fs_browser.py` 末尾追加：

```python
# --------------------------------------------------------------------------- #
# 工作空间上传：分块流式落盘、可配置上限、失败清理
# --------------------------------------------------------------------------- #


def _first_workspace_dir(api_key: str) -> str:
    """取一个当前用户可写的目录作为上传目标。"""
    return get_user_private_workspace_root(_user_info_for(api_key)) or get_data_base_dir()


@pytest.mark.asyncio
async def test_upload_writes_file_and_returns_original_and_stored_name(db_session, valid_api_key):
    target = get_user_uploads_dir(_user_info_for(valid_api_key))
    os.makedirs(target, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/fs/upload",
            params={"parent_path": target},
            files={"file": ("季度报告.pdf", b"%PDF-1.4 fake content", "application/pdf")},
            headers={"X-API-Key": valid_api_key},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # name 必须是原始文件名（历史实现错误地返回了加过后缀的落盘名）
    assert data["name"] == "季度报告.pdf"
    # stored_name 是防重名后的真实落盘名，且确实存在于磁盘
    assert data["stored_name"] != "季度报告.pdf"
    assert os.path.isfile(os.path.join(target, data["stored_name"]))
    assert data["size"] == len(b"%PDF-1.4 fake content")


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file_without_residue(db_session, valid_api_key, monkeypatch):
    from app.api.v1.endpoints import fs as fs_module

    target = get_user_uploads_dir(_user_info_for(valid_api_key))
    os.makedirs(target, exist_ok=True)
    before = set(os.listdir(target))

    # 把上限压到 1MB，避免测试真的构造 200MB 负载
    monkeypatch.setattr(fs_module.settings, "WORKSPACE_UPLOAD_MAX_MB", 1, raising=False)

    payload = b"x" * (2 * 1024 * 1024)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/fs/upload",
            params={"parent_path": target},
            files={"file": ("huge.bin", payload, "application/octet-stream")},
            headers={"X-API-Key": valid_api_key},
        )

    assert resp.status_code == 413, resp.text
    assert "上限" in resp.json()["detail"]
    # 关键：超限失败不得留下半截文件
    assert set(os.listdir(target)) == before


@pytest.mark.asyncio
async def test_upload_rejects_forbidden_extension_without_residue(db_session, valid_api_key):
    target = get_user_uploads_dir(_user_info_for(valid_api_key))
    os.makedirs(target, exist_ok=True)
    before = set(os.listdir(target))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/fs/upload",
            params={"parent_path": target},
            files={"file": ("payload.py", b"print(1)", "text/x-python")},
            headers={"X-API-Key": valid_api_key},
        )

    assert resp.status_code == 403, resp.text
    assert set(os.listdir(target)) == before


@pytest.mark.asyncio
async def test_upload_into_other_users_workspace_is_rejected(db_session, valid_api_key):
    base = get_data_base_dir()
    other_dir = os.path.join(base, "agent_workspaces", "other_user__999", "conv-upload")
    os.makedirs(other_dir, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/fs/upload",
            params={"parent_path": other_dir},
            files={"file": ("evil.txt", b"x", "text/plain")},
            headers={"X-API-Key": valid_api_key},
        )

    assert resp.status_code in (403, 404), resp.text
    assert not os.path.exists(os.path.join(other_dir, "evil.txt"))


@pytest.mark.asyncio
async def test_upload_limit_comes_from_config(db_session, valid_api_key, monkeypatch):
    from app.api.v1.endpoints import fs as fs_module

    target = get_user_uploads_dir(_user_info_for(valid_api_key))
    os.makedirs(target, exist_ok=True)

    monkeypatch.setattr(fs_module.settings, "WORKSPACE_UPLOAD_MAX_MB", 4, raising=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/fs/upload",
            params={"parent_path": target},
            files={"file": ("ok.bin", b"y" * (2 * 1024 * 1024), "application/octet-stream")},
            headers={"X-API-Key": valid_api_key},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["size"] == 2 * 1024 * 1024
```

同时在该文件顶部补两个辅助函数（放在 import 之后、`FakeRedis` 之前）：

```python
def _user_info_for(api_key: str) -> dict:
    """按测试约定的 api key 反推用户身份，用于解析该用户的可写目录。"""
    if api_key.startswith("TestAdmin"):
        return {"user_name": "test_admin", "role": "admin"}
    return {"user_name": "test_user", "role": "user"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest tests/api/v1/test_fs_browser.py -q -k "upload"`

Expected: 失败。`test_upload_writes_file_and_returns_original_and_stored_name` 报 `KeyError: 'stored_name'`（或 name 断言不符），超限用例返回 400 而非 413。

- [ ] **Step 3: 新增配置项**

在 `app/core/config.py` 的 `MCP_RATE_LIMIT_USER_PER_MINUTE: int = 60` 之后追加：

```python
    # 工作空间「上传到此目录」单文件上限（MB）。上限同时用于接口校验与前端预检。
    WORKSPACE_UPLOAD_MAX_MB: int = 200
```

- [ ] **Step 4: 扩展响应模型**

把 `app/api/v1/endpoints/fs.py:756-761` 的 `FileUploadResponse` 改为：

```python
class FileUploadResponse(BaseModel):
    path: str
    # 用户选择的原始文件名
    name: str
    # 实际落盘名（防重名会追加短后缀），UI 据此解释重名后缀
    stored_name: Optional[str] = None
    size: int
    mtime: float
```

- [ ] **Step 5: 替换常量与流式落盘实现**

把 `app/api/v1/endpoints/fs.py:668-669` 的两个常量替换为：

```python
# 上传上限来自配置（WORKSPACE_UPLOAD_MAX_MB），默认 200MB。
DEFAULT_UPLOAD_MAX_BYTES = 200 * 1024 * 1024
# 分块落盘大小：避免把整个文件读进内存（大文件会造成等量内存峰值）。
UPLOAD_CHUNK_BYTES = 1024 * 1024
FORBIDDEN_UPLOAD_EXTENSIONS = {".exe", ".bat", ".sh", ".cmd", ".com", ".msi", ".php", ".jsp", ".asp", ".py", ".pl"}


def _workspace_upload_max_bytes() -> int:
    """读取上传上限。配置缺失或非法时回退默认值，保持接口可用。"""
    try:
        from app.core.config import settings

        megabytes = int(getattr(settings, "WORKSPACE_UPLOAD_MAX_MB", 0) or 0)
    except Exception:
        megabytes = 0
    if megabytes <= 0:
        return DEFAULT_UPLOAD_MAX_BYTES
    return megabytes * 1024 * 1024


def _format_size_limit(limit_bytes: int) -> str:
    if limit_bytes >= 1024 * 1024 * 1024:
        return f"{limit_bytes // (1024 * 1024 * 1024)}GB"
    return f"{max(1, limit_bytes // (1024 * 1024))}MB"
```

把 `fs.py:932-970` 的接口替换为：

```python
@router.post(
    "/upload",
    response_model=StandardResponse[FileUploadResponse],
    summary="上传文件到工作空间指定目录",
)
async def upload_to_workspace(
    parent_path: str = Query(..., description="目标目录绝对路径"),
    file: UploadFile = File(...),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    parent = _resolve_writable_entry_path(parent_path, user_info)
    if not os.path.isdir(parent):
        raise HTTPException(status_code=400, detail="目标路径不是目录。")

    # 扩展名校验必须早于任何写盘动作，避免「先落盘再拒绝」
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext in FORBIDDEN_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=403, detail=f"禁止上传该类型文件: {ext}")

    max_bytes = _workspace_upload_max_bytes()
    original_name = os.path.basename(file.filename or "") or "upload"

    try:
        target, handle = open_upload_storage_file(parent, original_name)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"上传失败: {exc}") from exc

    stored_name = os.path.basename(target)
    written = 0
    try:
        # 分块流式落盘：内存占用与文件大小无关
        while True:
            chunk = await file.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件大小超出 {_format_size_limit(max_bytes)} 上限",
                )
            handle.write(chunk)
        handle.flush()
    except HTTPException:
        # 超限即中止，且不留下半截文件
        handle.close()
        try:
            os.remove(target)
        except OSError:
            pass
        raise
    except OSError as exc:
        handle.close()
        try:
            os.remove(target)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"上传失败: {exc}") from exc
    finally:
        if not handle.closed:
            handle.close()

    stat = os.stat(target)
    return StandardResponse(
        data=FileUploadResponse(
            path=target,
            name=original_name,
            stored_name=stored_name,
            size=stat.st_size,
            mtime=stat.st_mtime,
        )
    )
```

- [ ] **Step 6: 运行测试确认通过（GREEN）**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest tests/api/v1/test_fs_browser.py -q -k "upload"`

Expected: 5 个用例全部 PASS。

- [ ] **Step 7: 运行整个 fs 测试文件确认无回归**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest tests/api/v1/test_fs_browser.py -q`

Expected: 全绿（既有用例无回归）。

- [ ] **Step 8: 同步环境变量样例**

在 `env.example` 与 `docker/env.example` 的适当位置（与其它业务可调项同区）追加：

```bash
# 工作空间「上传到此目录」单文件上限（MB）。默认 200；调大需同步确认磁盘配额与反向代理 body 上限。
WORKSPACE_UPLOAD_MAX_MB=200
```

---

## Task 4: 抽屉上传面板与接线

**Files:**
- Modify: `frontend/src/components/embed/WorkspaceBrowserDrawer.vue`
- Test: `tests/frontend/test_workspace_upload_contract.py::test_drawer_wires_queue_and_renders_committing_state`

- [ ] **Step 1: 确认测试当前失败（RED）**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest tests/frontend/test_workspace_upload_contract.py::test_drawer_wires_queue_and_renders_committing_state -q --confcutdir=tests/frontend`

Expected: FAIL，`AssertionError: 抽屉未接上传队列引擎`。

- [ ] **Step 2: 新增 composable 持有队列实例**

创建 `frontend/src/composables/useWorkspaceUploadQueue.ts`：

```ts
import { onBeforeUnmount, ref, shallowRef } from 'vue'
import {
  createWorkspaceUploadQueue,
  type WorkspaceUploadQueue,
  type WorkspaceUploadSnapshot,
} from '@/utils/workspaceUploadQueue'
import { uploadToWorkspaceDir } from '@/utils/workspaceFilePreview'

/**
 * 把零依赖的上传队列引擎接到 Vue 生命周期上。
 * 引擎本身不依赖 Vue，因此可以被 Node 直接加载做行为测试；这里只负责持有实例与响应式快照。
 */
export function useWorkspaceUploadQueue(options?: { maxBytes?: number }) {
  const snapshot = ref<WorkspaceUploadSnapshot>({ tasks: [], aggregate: { total: 0, done: 0, failed: 0, canceled: 0, totalBytes: 0, loadedBytes: 0, active: false } })
  const queue = shallowRef<WorkspaceUploadQueue | null>(null)

  const ensure = (): WorkspaceUploadQueue => {
    if (queue.value) return queue.value
    const instance = createWorkspaceUploadQueue({
      maxBytes: options?.maxBytes,
      upload: (file, uploadOptions) =>
        uploadToWorkspaceDir(uploadOptions.parentPath, file as File, {
          onProgress: uploadOptions.onProgress,
          onCommit: uploadOptions.onCommit,
          signal: uploadOptions.signal,
        }),
    })
    instance.subscribe(() => {
      snapshot.value = instance.snapshot()
    })
    queue.value = instance
    return instance
  }

  onBeforeUnmount(() => {
    queue.value?.destroy()
    queue.value = null
  })

  return {
    snapshot,
    enqueue: (files: File[], parentPath: string) => ensure().enqueue(files, parentPath),
    cancel: (taskId: string) => queue.value?.cancel(taskId),
    retry: (taskId: string) => queue.value?.retry(taskId),
    clearFinished: () => queue.value?.clearFinished(),
  }
}
```

- [ ] **Step 3: 抽屉接入队列并替换串行上传循环**

在 `WorkspaceBrowserDrawer.vue` 的 `<script setup>` 中：

1. 在既有 import 区追加：

```ts
import { useWorkspaceUploadQueue } from '@/composables/useWorkspaceUploadQueue'
```

2. 在 `const { showToast } = useToast()` 附近初始化队列与 UI 状态：

```ts
const {
  snapshot: uploadSnapshot,
  enqueue: enqueueWorkspaceUpload,
  cancel: cancelWorkspaceUpload,
  retry: retryWorkspaceUpload,
  clearFinished: clearFinishedWorkspaceUpload,
} = useWorkspaceUploadQueue()
const uploadPanelCollapsed = ref(false)

const formatBytes = (bytes: number) => {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 100 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

const formatSpeed = (bps: number | null) => (bps && bps > 0 ? `${formatBytes(bps)}/s` : '')

const formatEta = (seconds: number | null) => {
  if (!seconds || seconds <= 0) return ''
  if (seconds < 60) return `约 ${seconds} 秒`
  return `约 ${Math.ceil(seconds / 60)} 分钟`
}

const UPLOAD_STATUS_TEXT: Record<string, string> = {
  queued: '等待中',
  transferring: '传输中',
  committing: '服务端写入中…',
  done: '已完成',
  failed: '失败',
  canceled: '已取消',
}

const uploadTaskStatusText = (status: string) => UPLOAD_STATUS_TEXT[status] ?? status

const uploadTaskHint = (task: { status: string; error: string | null; speedBps: number | null; remainingSeconds: number | null }) => {
  if (task.status === 'failed') return task.error || '上传失败'
  if (task.status === 'canceled') return '已取消，可能已保存'
  if (task.status === 'done') return '已保存'
  if (task.status === 'committing') return '文件已送达，正在写入工作空间…'
  const parts = [formatSpeed(task.speedBps), formatEta(task.remainingSeconds)].filter(Boolean)
  return parts.join(' · ')
}
```

3. 用队列替换 `handleUploadFiles`：

```ts
const handleUploadFiles = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const files = input.files
  const target = uploadTargetPath.value
  if (!files?.length || !target) return
  enqueueWorkspaceUpload(Array.from(files), target)
  input.value = ''
}
```

4. 目录刷新改为「队列全部终态后刷新一次」。在 `<script setup>` 中加入监听：

```ts
watch(
  () => uploadSnapshot.value.aggregate.active,
  async (active, wasActive) => {
    if (wasActive && !active) {
      const doneCount = uploadSnapshot.value.aggregate.done
      const failedCount = uploadSnapshot.value.aggregate.failed
      if (doneCount > 0) {
        await fetchDirectory(currentPath.value, { preserveSearch: true })
      }
      if (failedCount > 0) {
        showToast(`${doneCount} 个文件上传成功，${failedCount} 个失败`, 'error')
      } else if (doneCount > 0) {
        showToast(`${doneCount} 个文件上传完成`, 'success')
      }
    }
  },
)
```

（`watch` 若未导入需补进既有 `vue` 导入列表。）

- [ ] **Step 4: 渲染上传面板**

在 `WorkspaceBrowserDrawer.vue` 的 `<template>` 中，把上传浮层放在抽屉根容器内、底部操作按钮区之前（与「添加后保持」按钮同层，使用绝对定位贴底）：

```html
<!-- 上传队列面板：传输/服务端写入/失败重试都在这里可见，避免上传期间界面静默 -->
<div
  v-if="uploadSnapshot.tasks.length > 0"
  class="pointer-events-auto absolute bottom-3 left-3 z-30 w-[min(22rem,calc(100%-1.5rem))] rounded-xl border border-gray-200 bg-white/95 shadow-lg ring-1 ring-black/5 backdrop-blur dark:border-gray-700 dark:bg-gray-900/95"
>
  <div class="flex items-center justify-between gap-2 border-b border-gray-100 px-3 py-2 dark:border-gray-800">
    <div class="flex min-w-0 items-center gap-2">
      <span class="text-[11px] font-bold text-gray-700 dark:text-gray-200">
        上传 {{ uploadSnapshot.aggregate.done }}/{{ uploadSnapshot.aggregate.total }}
      </span>
      <span class="truncate text-[10px] text-gray-400">
        {{ formatBytes(uploadSnapshot.aggregate.loadedBytes) }} / {{ formatBytes(uploadSnapshot.aggregate.totalBytes) }}
      </span>
    </div>
    <div class="flex items-center gap-1">
      <button
        type="button"
        class="rounded px-1.5 py-0.5 text-[10px] text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
        @click="uploadPanelCollapsed = !uploadPanelCollapsed"
      >{{ uploadPanelCollapsed ? '展开' : '收起' }}</button>
      <button
        type="button"
        class="rounded px-1.5 py-0.5 text-[10px] text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
        @click="clearFinishedWorkspaceUpload()"
      >清除已完成</button>
    </div>
  </div>

  <div v-if="!uploadPanelCollapsed" class="max-h-56 overflow-y-auto px-3 py-2 space-y-2">
    <div v-for="task in uploadSnapshot.tasks" :key="task.id" class="space-y-1">
      <div class="flex items-center justify-between gap-2">
        <span class="min-w-0 flex-1 truncate text-[11px] font-medium text-gray-700 dark:text-gray-200" :title="task.name">
          {{ task.name }}
        </span>
        <button
          v-if="task.status === 'transferring' || task.status === 'committing' || task.status === 'queued'"
          type="button"
          class="shrink-0 rounded px-1.5 py-0.5 text-[10px] text-gray-400 hover:text-red-500"
          @click="cancelWorkspaceUpload(task.id)"
        >取消</button>
        <button
          v-else-if="task.status === 'failed'"
          type="button"
          class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold text-primary hover:underline"
          @click="retryWorkspaceUpload(task.id)"
        >重试</button>
      </div>

      <div class="h-1.5 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
        <div
          class="h-full rounded-full transition-all"
          :class="task.status === 'failed'
            ? 'bg-red-400'
            : task.status === 'canceled'
              ? 'bg-gray-300 dark:bg-gray-600'
              : task.status === 'committing'
                ? 'bg-primary/60 animate-pulse'
                : 'bg-primary'"
          :style="{ width: `${task.percent}%` }"
        />
      </div>

      <div class="flex items-center justify-between gap-2 text-[10px]">
        <span :class="task.status === 'failed' ? 'text-red-500' : 'text-gray-500 dark:text-gray-400'">
          {{ uploadTaskStatusText(task.status) }}
        </span>
        <span class="truncate text-gray-400">{{ uploadTaskHint(task) }}</span>
      </div>

      <!-- 落盘名与原名不同时说明重名后缀，避免用户以为自己传错了文件 -->
      <div v-if="task.status === 'done' && task.storedName && task.storedName !== task.name" class="truncate text-[10px] text-gray-400" :title="task.storedName">
        已存为 {{ task.storedName }}（加后缀避免重名）
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 5: 运行契约测试确认通过（GREEN）**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest tests/frontend/test_workspace_upload_contract.py -q --confcutdir=tests/frontend`

Expected: 4 个用例全部 PASS。

- [ ] **Step 6: 类型检查与构建**

Run: `cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform/frontend && npx vue-tsc --noEmit`

Expected: 无与本次改动相关的类型错误。

---

## Task 5: 端到端手工验收（必须由用户执行）

- [ ] **Step 1: 请用户重启服务**

Agent **不得**执行 `./dev.sh`。请用户自行在控制台执行 `./dev.sh`。

- [ ] **Step 2: 按设计文档验收标准逐条确认**

1. 上传 1 个 50MB 文件：可见「等待中 → 传输中(含已传字节/速率) → 服务端写入中… → 已完成」，且列表随之刷新，全程无静默期。
2. 一次选 10 个小文件：并发 3 执行，面板显示 `上传 x/10`，可取消任一任务。
3. 上传超过 `WORKSPACE_UPLOAD_MAX_MB` 的文件：面板直接标红说明超限，且服务端目录无残留文件。
4. 取消一个任务：面板显示「已取消，可能已保存」，不出现错误 toast。
5. 上传后列表中的落盘名带短后缀时，面板显示「已存为 xxx（加后缀避免重名）」。

---

## 自检记录

- **规格覆盖**：设计文档 §3.1（分层）→ 文件结构表；§3.2 状态机 → Task 1；§3.3 进度与速率 → Task 1 Step 3 `tickRates`；§3.4 并发 → Task 1；§3.5 文件名反馈 → Task 3 Step 4/5 + Task 4 Step 4；§3.6 取消语义 → Task 1 `cancel` + Task 3 Step 5 清理；§3.7 后端改造 → Task 3；§3.8 错误处理 → Task 1 `extractServerMessage` + Task 4 文案；§3.9 刷新策略 → Task 4 Step 3 第 4 点；§4 测试策略 → Task 1/3/4 的测试步骤。
- **类型一致性**：`WorkspaceUploadTask.status` 联合类型在 Task 1 定义，Task 4 的文案映射与模板分支使用同一组字面量；`stored_name`（后端 snake_case）与 `storedName`（前端 camelCase）的映射在 Task 1 Step 3 的 `.then` 中完成。
- **已知偏差**：设计文档 §3.6 假设「取消会传导到服务端使读取抛错」。实际上 uvicorn 通常在写响应时才发现连接中断，服务端很可能仍完成落盘，因此取消的常态是「文件已完整保存」。Task 4 的文案按此写成「已取消，可能已保存」，不承诺删除。
