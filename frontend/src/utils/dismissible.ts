/**
 * 可关闭的提示条。
 *
 * 有些提示看完一次就够了（「凭据建完即不可读」这类），但它又必须存在——新用户
 * 第一次进来正需要它。所以做成「默认显示、关掉后记住」。
 *
 * 存在 ``localStorage`` 里而不是后端：这是纯界面上的一次性说明，不值得为它加一张
 * 表、一次请求。代价是换浏览器/清缓存后会重新出现——对提示条来说可以接受，
 * 甚至是对的（换了环境就当新用户看一遍）。
 *
 * 用法：**在组件顶层解构**，别在模板里写 ``hint.visible``——嵌套在对象里的 ref
 * 不会被模板自动解包，那样写 ``v-if`` 永远是真。
 *
 * ```ts
 * const { visible: showHint, dismiss: hideHint } = useDismissible('secret-center:security')
 * ```
 */
import { ref } from 'vue'

const PREFIX = 'dsh:dismissed:'

const read = (key: string): boolean => {
  try {
    return window.localStorage.getItem(PREFIX + key) !== '1'
  } catch {
    // 隐私模式下 localStorage 会抛异常；抛了就当没关过，提示照常显示
    return true
  }
}

export const useDismissible = (key: string) => {
  const visible = ref(read(key))

  const dismiss = () => {
    visible.value = false
    try {
      window.localStorage.setItem(PREFIX + key, '1')
    } catch {
      // 存不下就只在本次会话内生效
    }
  }

  const reset = () => {
    visible.value = true
    try {
      window.localStorage.removeItem(PREFIX + key)
    } catch {
      // 同上
    }
  }

  return { visible, dismiss, reset }
}
