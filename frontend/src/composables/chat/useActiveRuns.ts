import { computed, ref } from 'vue'

export type ActiveRunState = 'active' | 'stale'

export interface ActiveRunItem {
  conversation_id: string
  trace_id: string | null
  ttl_seconds: number | null
  state: ActiveRunState
}

export interface ActiveRunsSnapshot {
  ttlSeconds: number
  staleBeforeTtlSeconds: number
  items: ActiveRunItem[]
}

export type ActiveRunsFetcher = () => Promise<ActiveRunsSnapshot>

/** 面板打开时的刷新间隔：用户正盯着列表，需要及时看到终止结果。 */
export const OPEN_POLL_INTERVAL_MS = 3000
/** 面板关闭时的低频刷新：只为更新顶部角标，能提示「还有会话在跑」即可。 */
export const IDLE_POLL_INTERVAL_MS = 15000

/**
 * 「进行中的会话」列表控制器。
 *
 * 只负责取数与轮询节奏；页面可见性由调用方通过 start()/stop() 控制，
 * 这样模块本身不依赖 document，便于契约测试与复用。
 */
export function createActiveRunsController(fetchRuns: ActiveRunsFetcher) {
  const items = ref<ActiveRunItem[]>([])
  const loading = ref(false)
  const failed = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  let inflight = false
  let open = false
  // 暂停标志：只清定时器挡不住 in-flight 请求 finally 里的 schedule()，
  // 否则会出现「页面已 hidden 却仍在每 15s 请求」。
  let stopped = false

  const intervalMs = () => (open ? OPEN_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS)

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  /** 暂停轮询：必须置 stopped，光清定时器挡不住在飞请求的 finally。 */
  const stop = () => {
    stopped = true
    clearTimer()
  }

  const schedule = () => {
    // 已暂停就不再排期：in-flight 请求完成时也会走到这里
    if (stopped) return
    clearTimer()
    timer = setTimeout(() => {
      timer = null
      void refresh()
    }, intervalMs())
  }

  const refresh = async (): Promise<void> => {
    // 单飞去重：并发请求不会让列表更快，只会放大后端压力
    if (inflight) return
    inflight = true
    loading.value = true
    try {
      const snapshot = await fetchRuns()
      items.value = snapshot.items
      failed.value = false
    } catch (error) {
      // 关键：失败保留上一次数据。清零会让角标消失，用户就再也发现不了
      // 「锁还在、界面却没有停止入口」的会话——这正是本功能要兜住的场景。
      console.warn('[ActiveRuns] 刷新进行中会话失败，保留上次结果:', error)
      failed.value = true
    } finally {
      inflight = false
      loading.value = false
      schedule()
    }
  }

  /** 恢复轮询并立即刷新一次（挂载、页面重新可见、面板重新打开）。 */
  const start = () => {
    stopped = false
    void refresh()
  }

  /** 面板开合时切换轮询节奏；打开时立即刷新一次，不等下一个周期。 */
  const setOpen = (value: boolean) => {
    open = value
    if (value) {
      // 用户主动打开面板必然可见，顺带解除暂停
      stopped = false
      void refresh()
    } else {
      schedule()
    }
  }

  const reset = () => {
    stop()
    items.value = []
    failed.value = false
  }

  const count = computed(() => items.value.length)
  const staleCount = computed(() => items.value.filter((item) => item.state === 'stale').length)

  return {
    items,
    count,
    staleCount,
    loading,
    failed,
    refresh,
    start,
    stop,
    setOpen,
    reset,
  }
}
