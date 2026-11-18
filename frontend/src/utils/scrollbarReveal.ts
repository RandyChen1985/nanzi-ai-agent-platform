/**
 * 滚动条「滚动时才出现」的显隐控制。
 *
 * 样式在 `style.css` 的 `.custom-scrollbar` 里：6px、轨道透明、滑块默认透明。
 * 纯 CSS 没有「正在滚动」这个状态——`:hover` 只能说明鼠标停在容器上（侧栏这种
 * 常驻区域等于一直显示），所以要的是 macOS 那种「滚动时闪一下、停手就消失」，
 * 只能在这里监听 scroll、给容器打标记，停手 `SCROLLBAR_REVEAL_MS` 后再撤掉。
 *
 * 为什么用文档级 capture 监听：scroll 事件不冒泡，挂在 document 上用冒泡收不到；
 * 但捕获阶段会从 window 一路传到目标，所以 `{ capture: true }` 一次就能覆盖全站
 * 所有滚动容器——不用给几十个组件各挂一个监听，也不怕路由切换后容器增删。
 *
 * 为什么用 WeakMap 存定时器：弹窗/路由销毁容器后键自动失效，不会留下一个还握着
 * 已卸载元素的定时器；同一容器连续滚动时先清掉上一个定时器，保证「停手 600ms」
 * 而不是「第一次滚动后 600ms 无论是否还在滚都收起来」。
 */

/** 最后一次滚动之后，滚动条再显示多久（毫秒）。 */
export const SCROLLBAR_REVEAL_MS = 600;

/** 写着「正在滚动」的标记属性；样式侧据此显示滑块。 */
export const SCROLLBAR_ACTIVE_ATTR = "data-scroll-active";

/** 只需要这两个方法注入，node 里测行为时不必搭一整套 DOM。 */
type ScrollHost = Pick<Document, "addEventListener" | "removeEventListener">;

/**
 * 该目标是不是挂了 `custom-scrollbar` 的滚动容器。
 *
 * 用 `classList.contains` 而不是 `closest`：scroll 事件的 target 就是滚动容器
 * 本身，往上找祖先只会误判（外层恰好也有这个类时，会把标记打到错误元素上）。
 */
export function isScrollbarContainer(target: unknown): target is Element {
  const el = target as Element | null | undefined;
  if (!el || typeof el.setAttribute !== "function") return false;
  return el.classList?.contains("custom-scrollbar") === true;
}

/**
 * 装上全站滚动条显隐控制，返回卸载函数。
 *
 * 只在 `main.ts` 调用一次；重复调用会各挂一份监听，容器属性虽然幂等，
 * 但定时器会翻倍，所以不要在多处调用。
 */
export function installScrollbarReveal(
  host: ScrollHost = document,
  delay = SCROLLBAR_REVEAL_MS,
): () => void {
  const timers = new WeakMap<Element, ReturnType<typeof setTimeout>>();

  const onScroll = (event: Event) => {
    const target = event.target;
    if (!isScrollbarContainer(target)) return;

    target.setAttribute(SCROLLBAR_ACTIVE_ATTR, "");
    const pending = timers.get(target);
    if (pending !== undefined) clearTimeout(pending);
    timers.set(
      target,
      setTimeout(() => {
        target.removeAttribute(SCROLLBAR_ACTIVE_ATTR);
        timers.delete(target);
      }, delay),
    );
  };

  host.addEventListener("scroll", onScroll, { capture: true, passive: true });
  return () => host.removeEventListener("scroll", onScroll, { capture: true });
}
