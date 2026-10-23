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
 * Toast 停留时长按文案长度自适应：中文阅读约 4–6 字/秒，另加约 0.6s 用于视觉定位与
 * 消失动画。一刀切 2 秒既让「创建成功」这类 4 字确认白占视线，又让 20 字以上的
 * 说明读不完（全库约 52% 的 success/info 文案 ≤6 字，约 31% ≥20 字）。
 * - success/info：1.2s（短确认）～ 4s（长说明）
 * - warning/error：2.5s ～ 6s，比普通提示多留一点排障时间
 */
export const TOAST_DURATION_RULES: Record<
  ToastType,
  { base: number; perChar: number; min: number; max: number }
> = {
  success: { base: 600, perChar: 130, min: 1200, max: 4000 },
  info: { base: 600, perChar: 130, min: 1200, max: 4000 },
  warning: { base: 1000, perChar: 160, min: 2500, max: 6000 },
  error: { base: 1000, perChar: 160, min: 2500, max: 6000 },
}

/**
 * 权限、凭证、不可逆操作这类提示，哪怕只有「无导出权限」5 个字也要留足阅读与判断时间：
 * 用户读完还要决定「找谁开通」，按字数算出来的 2.5s 明显不够。全库命中约 39 处。
 */
const CRITICAL_TOAST_PATTERN = /权限|授权|未授权|无权|凭证|过期|锁定|禁用|不可恢复|永久/

/** 按文案长度算停留时长；显式传入 duration 时仍然优先生效。 */
export function resolveAutoDuration(message: string, type: ToastType): number {
  const text = String(message || '')
  const rule = TOAST_DURATION_RULES[type] || TOAST_DURATION_RULES.info
  // Array.from 让 emoji / 代理对按 1 个字计，避免高估长度
  const len = Array.from(text).length
  const computed = Math.min(rule.max, Math.max(rule.min, Math.round(rule.base + len * rule.perChar)))
  if ((type === "error" || type === "warning") && CRITICAL_TOAST_PATTERN.test(text)) {
    return Math.max(computed, type === "error" ? 6000 : 4500)
  }
  return computed
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
      const text = messageOrOptions.message
        || messageOrOptions.title
        || messageOrOptions.description
        || ''
      const duration = messageOrOptions.duration !== undefined
        ? messageOrOptions.duration
        : resolveAutoDuration(text, type)

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
        : resolveAutoDuration(String(messageOrOptions || ''), type)

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
