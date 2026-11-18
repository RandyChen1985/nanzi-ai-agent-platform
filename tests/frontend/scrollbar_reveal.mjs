// 滚动条「滚动时才出现」的行为契约：滚动打标记、停手定时撤掉、连续滚动续期、
// 非 custom-scrollbar 目标忽略、卸载能拆干净。
// 运行：node --experimental-strip-types --no-warnings tests/frontend/scrollbar_reveal.mjs
import assert from 'node:assert/strict'

import {
  installScrollbarReveal,
  isScrollbarContainer,
  SCROLLBAR_ACTIVE_ATTR,
  SCROLLBAR_REVEAL_MS,
} from '../../frontend/src/utils/scrollbarReveal.ts'

// 极简 DOM 替身：只需要 classList.contains / setAttribute / removeAttribute，
// 以及一个能派发 scroll 的宿主。不引 jsdom——这一层逻辑跟渲染无关。
const makeElement = (classes) => {
  const attrs = new Map()
  return {
    classList: { contains: (token) => classes.includes(token) },
    setAttribute: (key, value) => attrs.set(key, value),
    getAttribute: (key) => (attrs.has(key) ? attrs.get(key) : null),
    removeAttribute: (key) => attrs.delete(key),
    has: (key) => attrs.has(key),
  }
}

const makeHost = () => {
  const listeners = new Map()
  return {
    addEventListener(type, handler, options) {
      listeners.set(type, { handler, options })
    },
    removeEventListener(type) {
      listeners.delete(type)
    },
    dispatch(type, target) {
      const entry = listeners.get(type)
      if (!entry) return false
      entry.handler({ type, target })
      return true
    },
    options: (type) => listeners.get(type)?.options,
    has: (type) => listeners.has(type),
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const DELAY = 40

// 1) 常量：600ms 是产品口径（停手约半秒多），别被随手改小成"闪一下就没"
assert.equal(SCROLLBAR_REVEAL_MS, 600, '显隐时长应为 600ms')
assert.equal(SCROLLBAR_ACTIVE_ATTR, 'data-scroll-active', '样式与脚本共用同一个标记属性')

// 2) 只认挂了 custom-scrollbar 的容器
assert.equal(isScrollbarContainer(makeElement(['custom-scrollbar'])), true)
assert.equal(isScrollbarContainer(makeElement(['custom-scrollbar-embed'])), false)
assert.equal(isScrollbarContainer(makeElement([])), false)
assert.equal(isScrollbarContainer(null), false)
assert.equal(isScrollbarContainer(undefined), false)

// 3) 监听必须是 capture + passive：scroll 不冒泡，冒泡挂法收不到；
//    passive 避免滚动路径上被当成可能 preventDefault 的监听而阻塞
{
  const host = makeHost()
  installScrollbarReveal(host, DELAY)
  const options = host.options('scroll')
  assert.equal(options?.capture, true, '必须走捕获阶段，否则收不到元素上的 scroll')
  assert.equal(options?.passive, true, '滚动监听应 passive')
}

// 4) 滚动时打标记，停手 DELAY 后撤掉
{
  const host = makeHost()
  const uninstall = installScrollbarReveal(host, DELAY)
  const list = makeElement(['custom-scrollbar'])

  assert.equal(list.has(SCROLLBAR_ACTIVE_ATTR), false, '初始状态必须是「不显示」')
  host.dispatch('scroll', list)
  assert.equal(list.has(SCROLLBAR_ACTIVE_ATTR), true, '滚动中应显示滚动条')

  await sleep(DELAY * 2)
  assert.equal(list.has(SCROLLBAR_ACTIVE_ATTR), false, '停手后应自己收起来')
  uninstall()
}

// 5) 连续滚动要续期：不是「第一次滚动后计时就到点收」
{
  const host = makeHost()
  const uninstall = installScrollbarReveal(host, DELAY)
  const list = makeElement(['custom-scrollbar'])

  host.dispatch('scroll', list)
  await sleep(DELAY * 0.6)
  host.dispatch('scroll', list) // 还在滚
  await sleep(DELAY * 0.6)
  assert.equal(list.has(SCROLLBAR_ACTIVE_ATTR), true, '持续滚动期间不能被上一次的定时器收掉')

  await sleep(DELAY * 1.2)
  assert.equal(list.has(SCROLLBAR_ACTIVE_ATTR), false, '真正停手之后才收起')
  uninstall()
}

// 6) 别的元素滚动（没挂 custom-scrollbar）不参与，避免给整页容器误挂样式
{
  const host = makeHost()
  const uninstall = installScrollbarReveal(host, DELAY)
  const plain = makeElement([])
  host.dispatch('scroll', plain)
  assert.equal(plain.has(SCROLLBAR_ACTIVE_ATTR), false)
  uninstall()
}

// 7) 卸载能拆干净（HMR / 重复挂载时不能叠出两份监听）
{
  const host = makeHost()
  const uninstall = installScrollbarReveal(host, DELAY)
  assert.equal(host.has('scroll'), true)
  uninstall()
  assert.equal(host.has('scroll'), false, '卸载后监听应被移除')
  assert.equal(host.dispatch('scroll', makeElement(['custom-scrollbar'])), false, '卸载后不再响应')
}

console.log('all assertions passed')
