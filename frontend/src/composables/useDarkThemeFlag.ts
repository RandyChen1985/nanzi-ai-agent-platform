import { onBeforeUnmount, onMounted, ref, type Ref } from "vue";

/**
 * 跟随 `<html class="dark">` 的响应式布尔值。
 *
 * 本项目的主题是直接改 DOM class 的（`EmbedChat` 的 `applyTheme`、主站的
 * `useAppTheme` 走 `data-theme`），Vue 的响应式系统感知不到 class 变化。
 * 图表这类「必须按当前主题计算 option」的场景（ECharts 是 Canvas 绘制，
 * 不接受 Tailwind 的 `dark:` 变体）就需要显式监听。
 *
 * 之所以用 `MutationObserver` 而不是读一次了事：切主题时组件不会重新挂载，
 * 只读一次会让图表永远停在首次渲染时的配色。
 */
export function useDarkThemeFlag(): Ref<boolean> {
  const isDark = ref(false);

  const sync = () => {
    if (typeof document === "undefined") return;
    isDark.value = document.documentElement.classList.contains("dark");
  };

  let observer: MutationObserver | null = null;

  // setup 阶段先同步一次：等 onMounted 再读会让深色下的首帧先按亮色画一张图。
  sync();

  onMounted(() => {
    sync();
    if (typeof MutationObserver === "undefined") return;
    observer = new MutationObserver(sync);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
  });

  onBeforeUnmount(() => {
    observer?.disconnect();
    observer = null;
  });

  return isDark;
}
