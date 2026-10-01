/**
 * 打字机效果：逐字打出 → 停顿 → 逐字删掉 → 换下一句，循环往复。
 *
 * 抽成 composable 而不是写进页面：这类动画有一套必须做对的收尾工作（定时器清理、
 * 无障碍、后台暂停），散在页面里迟早漏一个。三处都是刻意的：
 *
 * - `prefers-reduced-motion: reduce`：**不做动画**，静态显示第一句。逐字增减的
 *   循环动画对前庭敏感用户是实打实的不适，不是可以忽略的"小瑕疵"——所以这里
 *   直接换成静态文案，而不是把速度调慢。
 * - 页面切到后台就暂停：定时器继续跑，用户切回来会看到句子停在半截，
 *   而且白白占用主线程。
 * - 卸载时清掉定时器与监听：重置成功页会在 1.5 秒后跳转，不清理就会留下
 *   一串指向已卸载组件的回调。
 */
import { onUnmounted, ref } from 'vue'

export interface TypewriterOptions {
  /** 逐字打出的间隔（毫秒/字） */
  typeSpeed?: number
  /** 整句打完后停留多久（毫秒） */
  holdDelay?: number
  /** 逐字删除的间隔（毫秒/字），一般比打字快，删得拖沓会显得笨重 */
  deleteSpeed?: number
  /** 删完到下一句开始之间停多久（毫秒） */
  nextDelay?: number
}

export function useTypewriter(texts: string[], options: TypewriterOptions = {}) {
  const {
    // 105ms 偏"机关枪"，中文逐字停顿感不足；130ms 更接近人手打字的从容，
    // 又不至于让人等得发急（整句打完约 2.2 秒）
    typeSpeed = 130,
    holdDelay = 1900,
    deleteSpeed = 38,
    nextDelay = 420,
  } = options

  const displayed = ref('')

  let timer: ReturnType<typeof setTimeout> | null = null
  let textIndex = 0
  let charIndex = 0
  let deleting = false
  let paused = false
  let stopped = false

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  const prefersReducedMotion = () => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return false
    }
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  }

  const tick = () => {
    if (stopped || paused) return
    const text = texts[textIndex] ?? ''

    if (!deleting) {
      charIndex += 1
      displayed.value = text.slice(0, charIndex)
      if (charIndex >= text.length) {
        deleting = true
        timer = setTimeout(tick, holdDelay)
        return
      }
      timer = setTimeout(tick, typeSpeed)
      return
    }

    charIndex -= 1
    displayed.value = text.slice(0, Math.max(charIndex, 0))
    if (charIndex <= 0) {
      deleting = false
      textIndex = (textIndex + 1) % Math.max(texts.length, 1)
      timer = setTimeout(tick, nextDelay)
      return
    }
    timer = setTimeout(tick, deleteSpeed)
  }

  const start = () => {
    if (prefersReducedMotion()) {
      // 静止态给完整的一句，而不是空字符串：这行字是页面的一部分，只是不表演
      displayed.value = texts[0] ?? ''
      return
    }
    stopped = false
    paused = false
    clearTimer()
    tick()
  }

  const stop = () => {
    stopped = true
    clearTimer()
  }

  const handleVisibilityChange = () => {
    if (stopped) return
    if (document.hidden) {
      paused = true
      clearTimer()
      return
    }
    paused = false
    clearTimer()
    tick()
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handleVisibilityChange)
    start()
    onUnmounted(() => {
      stop()
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    })
  }

  return { displayed, start, stop }
}
