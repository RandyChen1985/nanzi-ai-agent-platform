import { reactive } from 'vue'

/**
 * 全局确认框：替代 `window.confirm`。
 *
 * 为什么要自己做一个：原生 confirm 是浏览器样式的阻塞弹窗，和产品其余部分
 * 完全两套观感，而且它会被某些宿主环境（嵌在 iframe / 桌面容器里）拦掉或
 * 改名成「localhost:8001 显示」这种标题。确认框出现的场合又都是**破坏性
 * 操作**（清空配置、丢弃未保存改动），正是最该让用户看清楚自己在点什么的
 * 地方，不能长得像浏览器在报警。
 *
 * 用法：组件里 `const { confirm } = useConfirm()`，然后
 *
 * ```ts
 * if (!(await confirm({ title: '清空全部凭据配置', message: '…', danger: true }))) return
 * ```
 *
 * 渲染由 `ConfirmHost` 负责（挂一次在 App.vue），状态是模块级单例——确认框
 * 天然是全局模态的，同时弹两个没有意义。
 */

export interface ConfirmOptions {
  /** 标题；不传就只有正文 */
  title?: string
  message: string
  confirmText?: string
  cancelText?: string
  /** 破坏性操作用红色确认键，避免用户顺手点掉 */
  danger?: boolean
}

const state = reactive({
  open: false,
  title: undefined as string | undefined,
  message: '',
  confirmText: '确定',
  cancelText: '取消',
  danger: false,
})

let settleCurrent: ((value: boolean) => void) | null = null

export function useConfirm() {
  function confirm(options: ConfirmOptions): Promise<boolean> {
    // 上一个还没答完就被新请求覆盖时，先把它结束掉：否则那个 await 永远悬着，
    // 调用方的 finally（重置 busy 之类）不会执行。
    if (state.open) settleCurrent?.(false)
    state.title = options.title
    state.message = options.message
    state.confirmText = options.confirmText || '确定'
    state.cancelText = options.cancelText || '取消'
    state.danger = Boolean(options.danger)
    state.open = true
    return new Promise<boolean>((resolve) => {
      settleCurrent = resolve
    })
  }

  function settle(value: boolean) {
    state.open = false
    const resolve = settleCurrent
    settleCurrent = null
    resolve?.(value)
  }

  return { state, confirm, settle }
}
