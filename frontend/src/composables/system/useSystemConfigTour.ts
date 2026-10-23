import { computed, ref } from 'vue'
import axios from '@/utils/axios'
import {
  SYSTEM_CONFIG_TOUR_STEPS,
  SYSTEM_CONFIG_TOUR_VERSION,
} from '../../components/system/systemConfigTourSteps'

/**
 * 系统配置引导状态机。
 *
 * 与 EmbedChat 引导最大的差别：完成态是**全局一份**（Redis 单键），
 * 所以断点只放 sessionStorage —— 全局存步数会让下一个人从半路开始播。
 *
 * 谁能自动播（element:system:config_save、桌面端、?tour=0）由页面判定，
 * 这里只负责「读全局状态 / 判要不要播 / 写完成态 / 记断点」。
 */

const ENDPOINT = '/api/portal/system/tour'
const STORAGE_KEY = 'sysconfig_tour_progress'

export interface SystemConfigTourState {
  version: number
  completed_at: string
  completed_by: number
  completed_by_name: string
  skipped: boolean
}

const emptyState = (): SystemConfigTourState => ({
  version: 0,
  completed_at: '',
  completed_by: 0,
  completed_by_name: '',
  skipped: false,
})

/** 断点只在当前标签页内有效；版本变了就当没看过 */
const readProgress = (): number => {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return 0
    const parsed = JSON.parse(raw) as { version?: number; step?: number }
    if (Number(parsed.version) !== SYSTEM_CONFIG_TOUR_VERSION) return 0
    const step = Number(parsed.step) || 0
    return Math.min(Math.max(step, 0), SYSTEM_CONFIG_TOUR_STEPS.length - 1)
  } catch {
    return 0
  }
}

const writeProgress = (step: number) => {
  try {
    window.sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: SYSTEM_CONFIG_TOUR_VERSION, step }),
    )
  } catch {
    // 隐私模式写不进去，忽略即可
  }
}

export function useSystemConfigTour() {
  const state = ref<SystemConfigTourState>(emptyState())

  const isCompletedForCurrentVersion = computed(
    () =>
      Boolean(state.value.completed_at) &&
      state.value.version === SYSTEM_CONFIG_TOUR_VERSION,
  )

  const loadState = async (): Promise<SystemConfigTourState> => {
    try {
      const res = await axios.get(ENDPOINT)
      const data = (res.data?.data || {}) as Partial<SystemConfigTourState>
      state.value = {
        version: Number(data.version) || 0,
        completed_at: String(data.completed_at || ''),
        completed_by: Number(data.completed_by) || 0,
        completed_by_name: String(data.completed_by_name || ''),
        skipped: Boolean(data.skipped),
      }
    } catch {
      // 接口失败按未完成处理：宁可多播一次，也不让新部署的人漏掉引导
      state.value = emptyState()
    }
    return state.value
  }

  const shouldAutoPlay = () => !isCompletedForCurrentVersion.value

  /**
   * 结束播放：看完或跳过都写全局完成态。
   *
   * 「跳过也算结束」是有意的——引导全局只有一份，否则每个管理员进来都被播一遍，
   * 第一线配置的人会很烦。想再看一次的人点 tab 栏的「引导」即可。
   */
  const markCompleted = async (skipped: boolean) => {
    const payload = {
      version: SYSTEM_CONFIG_TOUR_VERSION,
      completed_at: new Date().toISOString(),
      skipped,
    }
    state.value = {
      ...state.value,
      version: payload.version,
      completed_at: payload.completed_at,
      skipped,
    }
    try {
      await axios.put(ENDPOINT, payload)
    } catch {
      // 失败不重试：下次进来仍是未完成，可接受
    }
  }

  return {
    state,
    isCompletedForCurrentVersion,
    loadState,
    shouldAutoPlay,
    markCompleted,
    initialStep: readProgress,
    persistStep: writeProgress,
  }
}
