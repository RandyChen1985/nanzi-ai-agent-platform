/**
 * 工作空间上传队列引擎（零运行时依赖）。
 *
 * 为什么不用 Vue 实现：并发调度、速率窗口、取消/重试这些是纯粹的时序逻辑。抽成零依赖
 * 模块后可以被 `node --experimental-strip-types` 直接加载做行为测试
 * （见 tests/frontend/workspace_upload_queue.mjs），无需 jsdom 或组件挂载。
 *
 * 为什么进度由定时器驱动而非进度事件：浏览器把请求体写入内核 socket 缓冲区即视为
 * 「发送完成」，实测 5MB 文件只触发一次 onUploadProgress（0%→100%）、60MB 也只触发两次。
 * 因此进度事件只用来更新已传字节数，UI 刷新节奏由 400ms 定时器保证；同时显式建模
 * committing（服务端写入中）阶段，避免进度条在 100% 处无解释地滞留。
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
  /** 用户选择的原始文件名。 */
  name: string
  /** 服务端实际落盘名（防重名会追加短后缀），仅成功后可知。 */
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
  /** 是否仍有未进入终态的任务；调用方据此决定何时刷新目录。 */
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
  onProgress: (payload: WorkspaceUploadProgressPayload) => void
  /** 请求体已发送完毕、开始等待服务端落盘与响应。 */
  onCommit: () => void
  signal: AbortSignal
}

export interface UploadableFile {
  name: string
  size: number
  type?: string
}

export type WorkspaceUploadFn = (
  file: UploadableFile,
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
  enqueue: (files: UploadableFile[], parentPath: string) => WorkspaceUploadTask[]
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

/**
 * 是否应当自动清除上传面板：仅当全部任务成功结束（且确实有成功的）才成立。
 *
 * 有任何失败或取消都必须保留条目——失败要留给用户重试，取消要让用户看到
 * 「已取消，可能已保存」的提示；自动清除等于把这两种情况静默吞掉。
 */
export const shouldAutoDismissUploads = (aggregate: WorkspaceUploadAggregate): boolean =>
  !aggregate.active && aggregate.done > 0 && aggregate.failed === 0 && aggregate.canceled === 0

function toPercent(loaded: number, size: number): number {
  if (!size || size <= 0) return 100
  const ratio = loaded / size
  if (!Number.isFinite(ratio) || ratio <= 0) return 0
  if (ratio >= 1) return 100
  return Math.max(1, Math.floor(ratio * 100))
}

function formatLimit(bytes: number): string {
  if (!Number.isFinite(bytes)) return '不限'
  if (bytes >= 1024 * 1024 * 1024) return `${Math.round(bytes / (1024 * 1024 * 1024))}GB`
  return `${Math.round(bytes / (1024 * 1024))}MB`
}

function extractServerMessage(error: any): string {
  const data = error?.response?.data
  const detail = data?.detail ?? data?.message
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]
    if (typeof first?.msg === 'string' && first.msg.trim()) return first.msg
  }
  const message = error?.message
  if (typeof message === 'string' && message.trim() && message !== 'canceled') return message
  return '上传失败'
}

function isAbortError(error: any): boolean {
  return (
    error?.code === 'ERR_CANCELED' ||
    error?.name === 'CanceledError' ||
    error?.name === 'AbortError'
  )
}

function isTerminal(status: WorkspaceUploadStatus): boolean {
  return status === 'done' || status === 'failed' || status === 'canceled'
}

export function createWorkspaceUploadQueue(
  options: WorkspaceUploadQueueOptions,
): WorkspaceUploadQueue {
  const upload = options.upload
  const now = options.now ?? (() => Date.now())
  const setTimer = options.setTimer ?? ((fn: () => void, ms: number) => setInterval(fn, ms) as any)
  const clearTimer = options.clearTimer ?? ((id: any) => clearInterval(id))
  const concurrency = Math.max(1, Math.floor(options.concurrency ?? DEFAULT_UPLOAD_CONCURRENCY))
  const maxBytes = options.maxBytes ?? Number.POSITIVE_INFINITY

  const tasks = new Map<string, WorkspaceUploadTask>()
  const controllers = new Map<string, AbortController>()
  const rateBuckets = new Map<string, Array<{ at: number; total: number; delta: number }>>()
  const lastLoaded = new Map<string, number>()
  const files = new Map<string, UploadableFile>()
  const listeners = new Set<() => void>()

  let seq = 0
  let timerId: any = null
  let destroyed = false
  let lastPushed = ''

  const snapshot = (): WorkspaceUploadSnapshot => {
    const list = [...tasks.values()]
    return {
      tasks: list,
      aggregate: {
        total: list.length,
        done: list.filter((t) => t.status === 'done').length,
        failed: list.filter((t) => t.status === 'failed').length,
        canceled: list.filter((t) => t.status === 'canceled').length,
        totalBytes: list.reduce((sum, t) => sum + (t.size || 0), 0),
        loadedBytes: list.reduce(
          (sum, t) => sum + Math.min(t.loadedBytes || 0, t.size || t.loadedBytes || 0),
          0,
        ),
        active: list.some((t) => !isTerminal(t.status)),
      },
    }
  }

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

  const ensureTimer = () => {
    if (destroyed || timerId !== null) return
    timerId = setTimer(() => {
      tickRates()
      notify()
      stopTimerIfIdle()
    }, UPLOAD_PROGRESS_REFRESH_MS)
  }

  const stopTimerIfIdle = () => {
    const hasTransferring = [...tasks.values()].some((t) => t.status === 'transferring')
    if (!hasTransferring && timerId !== null) {
      clearTimer(timerId)
      timerId = null
    }
  }

  /** 累积速率分桶，并按 2 秒窗口求平均（含时间跨度归一化）。 */
  const tickRates = () => {
    const current = now()
    for (const task of tasks.values()) {
      if (task.status !== 'transferring') {
        if (task.status !== 'committing') {
          task.speedBps = null
          task.remainingSeconds = null
        }
        continue
      }
      const loaded = lastLoaded.get(task.id) ?? 0
      const buckets = rateBuckets.get(task.id) ?? []
      const last = buckets.length ? buckets[buckets.length - 1] : null
      if (!last) {
        buckets.push({ at: current, total: loaded, delta: 0 })
      } else {
        last.delta += Math.max(0, loaded - last.total)
        last.total = loaded
        if (current - last.at >= RATE_BUCKET_MS) {
          buckets.push({ at: current, total: loaded, delta: 0 })
        }
      }
      while (buckets.length && current - (buckets[0]?.at ?? current) > RATE_WINDOW_MS) {
        buckets.shift()
      }
      rateBuckets.set(task.id, buckets)

      const samples = buckets.filter((bucket) => bucket.delta > 0)
      const totalDelta = samples.reduce((sum, bucket) => sum + bucket.delta, 0)
      if (totalDelta <= 0) {
        // 首个样本没有可比较的增量，宁可不显示速率也不要虚报
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
    if (!file) {
      task.status = 'failed'
      task.error = '文件已失效，请重新选择'
      return
    }

    const controller = new AbortController()
    controllers.set(task.id, controller)
    task.status = 'transferring'
    task.error = null
    task.loadedBytes = 0
    task.percent = 0
    task.speedBps = null
    task.remainingSeconds = null
    task.storedName = null
    lastLoaded.set(task.id, 0)
    rateBuckets.set(task.id, [])
    ensureTimer()

    const settle = (apply: () => void) => {
      controllers.delete(task.id)
      apply()
      stopTimerIfIdle()
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
          if (task.status !== 'transferring') return
          task.status = 'committing'
          task.percent = 100
          task.speedBps = null
          task.remainingSeconds = null
          notify()
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

  const enqueue = (incoming: UploadableFile[], parentPath: string): WorkspaceUploadTask[] => {
    if (destroyed) return []
    const created: WorkspaceUploadTask[] = []
    for (const file of incoming) {
      seq += 1
      const task: WorkspaceUploadTask = {
        id: `upload_${seq}`,
        name: String(file?.name ?? `file_${seq}`),
        storedName: null,
        size: Number(file?.size) || 0,
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
    if (!task || isTerminal(task.status)) return
    task.status = 'canceled'
    task.speedBps = null
    task.remainingSeconds = null
    controllers.get(taskId)?.abort()
    controllers.delete(taskId)
    notify()
    pump()
  }

  const retry = (taskId: string) => {
    const task = tasks.get(taskId)
    if (!task || (task.status !== 'failed' && task.status !== 'canceled')) return
    task.status = 'queued'
    task.error = null
    task.loadedBytes = 0
    task.percent = 0
    notify()
    pump()
    notify()
  }

  const clearFinished = () => {
    for (const [id, task] of [...tasks.entries()]) {
      if (!isTerminal(task.status)) continue
      tasks.delete(id)
      files.delete(id)
      rateBuckets.delete(id)
      lastLoaded.delete(id)
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
    return () => {
      listeners.delete(listener)
    }
  }

  return { enqueue, snapshot, subscribe, cancel, retry, clearFinished, destroy }
}
