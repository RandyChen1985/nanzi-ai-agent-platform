import { computed, ref } from 'vue'
import axios from '@/utils/axios'
import {
  TOUR_STEPS,
  TOUR_TOTAL_STEPS,
  TOUR_VERSION,
  type TourStep,
} from '../../components/embed/tourSteps'

/**
 * EmbedChat 功能引导的状态机。
 *
 * 持久化在账号级 portal_prefs（Redis），所以同一账号换设备不会重播；
 * 每步离开时防抖上报，中途关页面最多回退一步。接口失败时退到 localStorage，
 * 避免一次 503 就让用户每次进来都被播一遍。
 */

const PREFS_ENDPOINT = '/api/portal/portal-prefs'
const TOUR_ENDPOINT = '/api/portal/portal-prefs/embed-tour'
const STORAGE_KEY = 'embed_tour_state'
const PERSIST_DEBOUNCE_MS = 500
/** 页面结构异常（大量锚点缺失）时不播残缺引导 */
const MIN_ANCHORS = 4

export interface EmbedTourState {
  version: number
  step: number
  completed_at: string
}

const emptyState = (): EmbedTourState => ({ version: 0, step: 0, completed_at: '' })

const readLocal = (): EmbedTourState | null => {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<EmbedTourState>
    return {
      version: Number(parsed.version) || 0,
      step: Number(parsed.step) || 0,
      completed_at: String(parsed.completed_at || ''),
    }
  } catch {
    return null
  }
}

const writeLocal = (state: EmbedTourState) => {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // 隐私模式下 localStorage 不可写，忽略即可
  }
}

export function useEmbedTour(options: { isMobile: () => boolean }) {
  const active = ref(false)
  const stepIndex = ref(0)
  const loading = ref(false)
  const state = ref<EmbedTourState>(emptyState())

  const steps = TOUR_STEPS
  const total = TOUR_TOTAL_STEPS
  const currentStep = computed<TourStep | null>(() => steps[stepIndex.value] || null)
  const progressText = computed(() => `${stepIndex.value + 1} / ${total}`)
  const isLastStep = computed(() => stepIndex.value >= total - 1)

  let persistTimer: number | undefined
  let pendingState: EmbedTourState | null = null

  const tourDisabledByQuery = () => {
    try {
      const value = new URLSearchParams(window.location.search).get('tour')
      return value === '0' || value === 'off'
    } catch {
      return false
    }
  }

  const loadState = async () => {
    loading.value = true
    try {
      const res = await axios.get(PREFS_ENDPOINT)
      const data = (res.data?.data || {}) as Record<string, unknown>
      state.value = {
        version: Number(data.embed_tour_version) || 0,
        step: Number(data.embed_tour_step) || 0,
        completed_at: String(data.embed_tour_completed_at || ''),
      }
    } catch {
      state.value = readLocal() || emptyState()
    } finally {
      loading.value = false
    }
  }

  /** 只判断「要不要播」，不负责加载状态 */
  const shouldAutoPlay = () => {
    if (options.isMobile()) return false
    if (tourDisabledByQuery()) return false
    // 版本不一致：文案改过版，视为需要重播
    if (state.value.version !== TOUR_VERSION) return true
    if (state.value.completed_at) return false
    if (state.value.step >= total) return false
    return true
  }

  const availableAnchorCount = () =>
    steps.filter((step) => Boolean(document.querySelector(step.selector))).length

  const persist = (payload: EmbedTourState) => {
    pendingState = payload
    window.clearTimeout(persistTimer)
    persistTimer = window.setTimeout(() => {
      const body = pendingState
      pendingState = null
      if (!body) return
      axios
        .put(TOUR_ENDPOINT, {
          version: body.version,
          step: body.step,
          completed_at: body.completed_at,
        })
        .catch(() => writeLocal(body))
    }, PERSIST_DEBOUNCE_MS)
  }

  const recordStep = (index: number) => {
    state.value = { ...state.value, version: TOUR_VERSION, step: index }
    persist(state.value)
  }

  const goTo = (index: number) => {
    const target = Math.max(0, Math.min(index, total - 1))
    if (target === stepIndex.value) return
    stepIndex.value = target
    recordStep(target)
  }

  const finish = () => {
    const done: EmbedTourState = {
      version: TOUR_VERSION,
      step: total,
      completed_at: new Date().toISOString(),
    }
    state.value = done
    persist(done)
    active.value = false
  }

  const skip = () => {
    const current: EmbedTourState = {
      version: TOUR_VERSION,
      step: stepIndex.value,
      // 保留「已看完」标记：否则设置里重播后点跳过，下次进入又会自动播
      completed_at: state.value.completed_at,
    }
    state.value = current
    persist(current)
    active.value = false
  }

  const next = () => {
    if (isLastStep.value) {
      finish()
      return
    }
    goTo(stepIndex.value + 1)
  }

  const prev = () => goTo(stepIndex.value - 1)

  /** 正常自动播；force 用于设置里「重新播放」（跳过完成态检查） */
  const resumeOrStart = async (force = false) => {
    if (active.value) return
    if (!force) {
      await loadState()
      if (!shouldAutoPlay()) return
      if (availableAnchorCount() < MIN_ANCHORS) return
    }
    const resumable = state.value.version === TOUR_VERSION
    const startIndex = resumable ? Math.min(Math.max(state.value.step, 0), total - 1) : 0
    stepIndex.value = startIndex
    active.value = true
  }

  /** 设置里重播：只清进度、不动完成标记，避免「重播→跳过」后下次又被自动播 */
  const restart = () => {
    state.value = { ...state.value, version: TOUR_VERSION, step: 0 }
    persist(state.value)
    stepIndex.value = 0
    active.value = true
  }

  return {
    active,
    stepIndex,
    steps,
    total,
    currentStep,
    progressText,
    isLastStep,
    loading,
    state,
    loadState,
    shouldAutoPlay,
    resumeOrStart,
    goTo,
    next,
    prev,
    finish,
    skip,
    restart,
  }
}
