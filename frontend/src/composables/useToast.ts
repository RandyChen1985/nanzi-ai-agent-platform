import { ref } from 'vue'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface ToastAction {
  label: string
  onClick: () => void
}

export interface ToastOptions {
  message?: string
  title?: string
  description?: string
  type?: ToastType
  duration?: number
  action?: ToastAction
}

export interface ToastMessage {
  id: number
  message: string
  title?: string
  description?: string
  type: ToastType
  duration?: number
  action?: ToastAction
}

/**
 * 默认停留时长阶梯策略：
 * - 成功/常规提示（success/info）：2000ms（2秒），轻快利落不遮挡视线；
 * - 警告/报错（warning/error）：3500ms（3.5秒），预留充足阅读与排障时间。
 */
export const DEFAULT_TOAST_DURATIONS: Record<ToastType, number> = {
  success: 2000,
  info: 2000,
  warning: 3500,
  error: 3500,
}

/** 同屏最多并存 Toast 数量，避免长报错或密集提示遮挡核心操作区 */
const MAX_TOASTS = 4

const toasts = ref<ToastMessage[]>([])
let nextId = 0

export function useToast() {
  function showToast(options: ToastOptions): number
  function showToast(
    message: string,
    type?: ToastType,
    duration?: number,
  ): number
  function showToast(
    messageOrOptions: string | ToastOptions,
    typeArg: ToastType = 'info',
    durationArg?: number,
  ): number {
    const id = nextId++
    let toast: ToastMessage

    if (typeof messageOrOptions === 'object' && messageOrOptions !== null) {
      const type = messageOrOptions.type || 'info'
      const duration = messageOrOptions.duration !== undefined
        ? messageOrOptions.duration
        : DEFAULT_TOAST_DURATIONS[type]

      toast = {
        id,
        message: messageOrOptions.message || messageOrOptions.title || '',
        title: messageOrOptions.title,
        description: messageOrOptions.description,
        type,
        duration,
        action: messageOrOptions.action,
      }
    } else {
      const type = typeArg
      const duration = durationArg !== undefined
        ? durationArg
        : DEFAULT_TOAST_DURATIONS[type]

      toast = {
        id,
        message: String(messageOrOptions || ''),
        type,
        duration,
      }
    }

    toasts.value.push(toast)

    // 限制最大同屏数量，超出的从头部淘汰
    if (toasts.value.length > MAX_TOASTS) {
      toasts.value.splice(0, toasts.value.length - MAX_TOASTS)
    }

    return id
  }

  const removeToast = (id: number) => {
    const index = toasts.value.findIndex(t => t.id === id)
    if (index !== -1) {
      toasts.value.splice(index, 1)
    }
  }

  const clearAllToasts = () => {
    toasts.value = []
  }

  return {
    toasts,
    showToast,
    removeToast,
    clearAllToasts,
  }
}
