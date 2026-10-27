/**
 * 把「真实可视高度」写进 CSS 变量 `--app-height`。
 *
 * ## 为什么 100vh 不够
 *
 * 移动端有两种高度不等于 100vh：
 * 1. **地址栏伸缩**（iOS Safari / 微信内置浏览器）：`100vh` 是**大视口**高度，地址栏
 *    展开时真实可视区更矮，底部输入框会被地址栏压住一截；
 * 2. **软键盘弹出**：`100vh` 和 `100dvh` 都**不会**跟着缩，键盘直接盖住输入区。
 *
 * `window.visualViewport.height` 是唯一能同时反映这两者的量，所以以它为准；
 * 不支持 visualViewport 的老浏览器退回 `innerHeight`（至少能跟随地址栏）。
 *
 * ## 消费方约定
 *
 * ```
 * height: var(--app-height, 100dvh)
 * ```
 *
 * 变量还没算出来时退回 `dvh`；两条都不认的老浏览器仍由原有的 `h-screen` 类兜底。
 * 只在「定高 + 内部滚动」的布局上使用（平台 chat 页、/embed/* 布局）——
 * 普通文档流页面这么干会把内容裁掉。
 */

import { onMounted, onUnmounted } from "vue";

const CSS_VAR = "--app-height";

/**
 * 只在「窄屏 / 触屏」这一侧改写高度。
 *
 * **Web 桌面端一律不动**：桌面没有软键盘，也没有会伸缩的地址栏，
 * `visualViewport.height` 与 `100vh` 本来就是同一个值。而且桌面用户会在长页面里
 * 正常滚动，任何「顺手把文档拉回原点」的补偿都会变成「页面莫名跳回顶部」。
 * 不清空变量则桌面仍会走 CSS 里的 `100dvh`（== `100vh`），与改动前完全一致。
 */
const MOBILE_QUERY = "(max-width: 1023px)";

/** 上一次真正生效的高度：值没变就什么都不做，避免无意义的重排与滚动补偿。 */
let lastHeight = 0;
let frame = 0;

const apply = () => {
  frame = 0;

  if (!window.matchMedia(MOBILE_QUERY).matches) {
    // 桌面：交给 CSS 的 100dvh/100vh，行为与改动前一致
    if (lastHeight !== 0) {
      lastHeight = 0;
      document.documentElement.style.removeProperty(CSS_VAR);
    }
    return;
  }

  const height = Math.round(window.visualViewport?.height ?? window.innerHeight);
  if (!height || height === lastHeight) return;
  lastHeight = height;
  document.documentElement.style.setProperty(CSS_VAR, `${height}px`);
  // 定高布局不该有页面级滚动。iOS 在键盘弹出时会把文档整体上推（哪怕 overflow 已隐藏），
  // 导致容器顶部变负、底部输入框反而更靠下，所以高度变化后把文档拉回原点。
  if (window.scrollX !== 0 || window.scrollY !== 0) {
    window.scrollTo(0, 0);
  }
};

/** rAF 合并：键盘弹出时 visualViewport 会连发几十个 resize，不必每个都写一次样式。 */
const schedule = () => {
  if (frame) return;
  frame = window.requestAnimationFrame(apply);
};

export const useAppViewportHeight = () => {
  if (typeof window === "undefined") return;
  const viewport = window.visualViewport ?? null;

  onMounted(() => {
    apply();
    // scroll 也要听：部分 iOS 版本键盘弹出只改变 offsetTop + 触发 scroll，不触发 resize
    viewport?.addEventListener("resize", schedule);
    viewport?.addEventListener("scroll", schedule);
    window.addEventListener("resize", schedule);
    window.addEventListener("orientationchange", schedule);
  });

  onUnmounted(() => {
    viewport?.removeEventListener("resize", schedule);
    viewport?.removeEventListener("scroll", schedule);
    window.removeEventListener("resize", schedule);
    window.removeEventListener("orientationchange", schedule);
    if (frame) {
      window.cancelAnimationFrame(frame);
      frame = 0;
    }
  });
};
