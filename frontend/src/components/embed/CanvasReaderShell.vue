<script setup lang="ts">
/**
 * 画布 Markdown 阅读外壳。
 *
 * 长文档在画布里最难读的三件事，这里一次解决：
 *
 * 1. **没有大纲**——几千字的 FAQ 只能从头滚，找不到「常见问题」在哪；
 * 2. **宽度失控**——画布拉到全屏后每行 100+ 字符，眼睛换行会串行；
 * 3. **失败信息没用**——图片加载不出来只剩一个 `alt` 文字，看不出是路径错还是没权限。
 *
 * 实现上刻意全部在**外层容器**做：内层 `CanvasMarkdownRenderer` 用 `v-html` 渲染
 * markdown、还夹着 mermaid 与 echarts，改它内部结构风险极高；外层用 DOM 查询 +
 * 事件委托就能拿到同样的效果，且渲染器保持不变。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

const props = withDefaults(
  defineProps<{
    /** 用于生成大纲的原始 markdown 文本 */
    content: string;
    /** 正文字号倍率，1 = 100% */
    scale?: number;
    /** 把正文限制在约 68 字符行宽并居中 */
    narrow?: boolean;
  }>(),
  { scale: 1, narrow: false },
);

const scrollRef = ref<HTMLElement | null>(null);
const contentRef = ref<HTMLElement | null>(null);

interface OutlineItem {
  id: string;
  text: string;
  level: number;
}

const outline = ref<OutlineItem[]>([]);
const activeId = ref('');
/** 大纲默认展开，但用户可以收起——长文档里它也会挡住正文 */
const outlineOpen = ref(true);

let observer: IntersectionObserver | null = null;
let rebuildTimer: number | null = null;

const hasOutline = computed(() => outline.value.length > 0);

const fontSize = computed(() => `${(13.5 * props.scale).toFixed(2)}px`);

const clearObserver = () => {
  observer?.disconnect();
  observer = null;
};

/**
 * 扫描渲染结果里的 h1–h3 生成大纲。
 *
 * 必须扫 **DOM** 而不是解析 markdown 源码：标题可能来自 v-html、也可能藏在
 * mermaid/图表之间的多个 markdown 段里，只有渲染后的 DOM 才是用户真正看到的顺序。
 */
const rebuildOutline = async () => {
  await nextTick();
  const root = contentRef.value;
  if (!root) return;

  const headings = Array.from(root.querySelectorAll<HTMLElement>('h1, h2, h3'));
  outline.value = headings.map((el, index) => {
    if (!el.id) el.id = `canvas-heading-${index}`;
    return {
      id: el.id,
      text: (el.textContent || '').trim(),
      level: Number(el.tagName.substring(1)) || 1,
    };
  });

  clearObserver();
  if (!headings.length || typeof IntersectionObserver === 'undefined') return;

  // 顶部留 60px：标题刚好滑到视口上沿时就算「当前章节」，比让用户等它滚到中间更符合直觉
  observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible[0]?.target.id) activeId.value = visible[0].target.id;
    },
    { root: scrollRef.value, rootMargin: '-60px 0px -70% 0px', threshold: 0 },
  );
  headings.forEach((el) => observer?.observe(el));
};

/**
 * 给每个代码块加一条工具条（语言 / 复制 / 换行）。
 *
 * 行号没做：语法高亮是**跨行**的 span（一个 `<span class="string">` 可能横跨三行），
 * 要加行号只能重写高亮渲染。代价远大于收益，宁可只给真正高频的复制与折行。
 */
const enhanceCodeBlocks = () => {
  const root = contentRef.value;
  if (!root) return;

  root.querySelectorAll<HTMLPreElement>('pre').forEach((pre) => {
    if (pre.dataset.enhanced === '1') return;
    pre.dataset.enhanced = '1';

    const bar = document.createElement('div');
    bar.className = 'canvas-code-bar';

    const lang = document.createElement('span');
    lang.className = 'canvas-code-lang';
    const codeClass = pre.querySelector('code')?.className || '';
    const langMatch = /language-([\w+-]+)/.exec(codeClass);
    lang.textContent = (langMatch?.[1] || 'text').toLowerCase();

    const actions = document.createElement('span');
    actions.className = 'canvas-code-actions';

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.textContent = '复制';
    copyBtn.addEventListener('click', async () => {
      const text = pre.querySelector('code')?.textContent || pre.textContent || '';
      try {
        await navigator.clipboard.writeText(text);
        copyBtn.textContent = '已复制';
      } catch {
        copyBtn.textContent = '复制失败';
      }
      window.setTimeout(() => {
        copyBtn.textContent = '复制';
      }, 1600);
    });

    const wrapBtn = document.createElement('button');
    wrapBtn.type = 'button';
    wrapBtn.textContent = '换行';
    wrapBtn.addEventListener('click', () => {
      const wrapped = pre.classList.toggle('is-wrapped');
      wrapBtn.classList.toggle('is-on', wrapped);
    });

    actions.append(copyBtn, wrapBtn);
    bar.append(lang, actions);
    pre.parentNode?.insertBefore(bar, pre);
  });
};

/**
 * 图片加载失败时换成带原因的卡片。
 *
 * 用**捕获阶段**监听：`error` 事件不冒泡，挂在容器上做委托只有 capture 才收得到，
 * 否则每张图都得单独绑定，v-html 重新渲染后就失效了。
 */
const handleImageError = (event: Event) => {
  const img = event.target as HTMLImageElement | null;
  if (!img || img.tagName !== 'IMG') return;
  if (img.dataset.brokenCard === '1') return;
  img.dataset.brokenCard = '1';

  const src = img.getAttribute('src') || '';

  const card = document.createElement('div');
  card.className = 'canvas-img-error';

  const main = document.createElement('div');
  main.className = 'canvas-img-error-main';
  const icon = document.createElement('span');
  icon.className = 'canvas-img-error-icon';
  icon.textContent = '⚠';
  const text = document.createElement('span');
  text.className = 'canvas-img-error-text';
  // 带上地址：只说「加载失败」用户没法判断是路径写错、文件没了、还是没权限
  text.textContent = `图片加载失败：${src || '(无地址)'}`;
  main.append(icon, text);

  const actions = document.createElement('span');
  actions.className = 'canvas-img-error-actions';

  const retryBtn = document.createElement('button');
  retryBtn.type = 'button';
  retryBtn.textContent = '重试';
  retryBtn.addEventListener('click', () => {
    // 用一个新的 img 换回卡片：加载成功自然恢复；再失败会重新落回卡片（dataset 标记在新的 img 上）
    const retryImg = document.createElement('img');
    retryImg.src = src;
    retryImg.alt = img.getAttribute('alt') || '';
    card.replaceWith(retryImg);
  });

  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.textContent = '复制路径';
  copyBtn.addEventListener('click', async () => {
    if (!src) return;
    try {
      await navigator.clipboard.writeText(src);
      copyBtn.textContent = '已复制';
    } catch {
      copyBtn.textContent = '复制失败';
    }
    window.setTimeout(() => {
      copyBtn.textContent = '复制路径';
    }, 1600);
  });

  actions.append(retryBtn, copyBtn);
  card.append(main, actions);
  img.replaceWith(card);
};

const scrollToHeading = (id: string) => {
  const el = contentRef.value?.querySelector<HTMLElement>(`#${CSS.escape(id)}`);
  if (!el) return;
  activeId.value = id;
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

const scheduleRebuild = () => {
  if (rebuildTimer !== null) window.clearTimeout(rebuildTimer);
  // 渲染器内部有 mermaid / echarts 的异步渲染，等它们落地再扫标题，否则可能扫不到更新的内容
  rebuildTimer = window.setTimeout(() => {
    void rebuildOutline().then(enhanceCodeBlocks);
  }, 120);
};

onMounted(() => {
  scheduleRebuild();
});

watch(() => props.content, scheduleRebuild);

onBeforeUnmount(() => {
  clearObserver();
  if (rebuildTimer !== null) window.clearTimeout(rebuildTimer);
});
</script>

<template>
  <div class="canvas-reader flex h-full min-h-0">
    <!-- 大纲：只对有标题的文档出现，纯代码块内容不占这块地 -->
    <nav v-if="hasOutline" class="canvas-outline" :class="{ 'is-collapsed': !outlineOpen }">
      <button
        type="button"
        class="canvas-outline-toggle"
        :title="outlineOpen ? '收起大纲' : '展开大纲'"
        @click="outlineOpen = !outlineOpen"
      >
        <span>大纲</span>
        <span class="canvas-outline-caret">{{ outlineOpen ? '‹' : '›' }}</span>
      </button>
      <div v-if="outlineOpen" class="canvas-outline-list">
        <button
          v-for="item in outline"
          :key="item.id"
          type="button"
          class="canvas-outline-item"
          :class="[`lv${item.level}`, { 'is-active': activeId === item.id }]"
          :title="item.text"
          @click="scrollToHeading(item.id)"
        >
          {{ item.text }}
        </button>
      </div>
    </nav>

    <div ref="scrollRef" class="canvas-reader-scroll flex-1 min-w-0">
      <div
        ref="contentRef"
        class="canvas-reader-body"
        :style="{ fontSize }"
        @error.capture="handleImageError"
      >
        <div :class="narrow ? 'canvas-readable-width' : ''">
          <slot />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.canvas-outline {
  width: 168px;
  flex: none;
  border-right: 1px solid #eef1f6;
  padding: 10px 8px 10px 10px;
  overflow-y: auto;
}
.canvas-outline.is-collapsed {
  width: 42px;
  padding: 10px 4px;
}
.canvas-outline-toggle {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 2px 4px 6px;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: #94a3b8;
  text-transform: uppercase;
}
.canvas-outline.is-collapsed .canvas-outline-toggle span:first-child {
  display: none;
}
.canvas-outline-caret {
  font-size: 12px;
}
.canvas-outline-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.canvas-outline-item {
  display: block;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border-radius: 6px;
  padding: 4px 7px;
  text-align: left;
  font-size: 11.5px;
  color: #64748b;
  transition: background-color 0.12s, color 0.12s;
}
.canvas-outline-item:hover {
  background: #f4f7fb;
  color: #334155;
}
.canvas-outline-item.lv2 {
  padding-left: 17px;
  font-size: 11px;
  color: #7c8da3;
}
.canvas-outline-item.lv3 {
  padding-left: 27px;
  font-size: 10.5px;
  color: #94a3b8;
}
.canvas-outline-item.is-active {
  background: #eff6ff;
  color: var(--color-primary, #2563eb);
  font-weight: 700;
}

.canvas-reader-scroll {
  overflow-y: auto;
  scroll-behavior: smooth;
}
/* 正文左右留白随宽度增长，窄面板不浪费、宽面板不贴边 */
.canvas-reader-body {
  padding: 18px 22px 32px;
  line-height: 1.75;
  color: #1f2937;
}
.canvas-readable-width {
  max-width: 660px;
  margin: 0 auto;
}
</style>

<style>
/* 代码块工具条与图片错误卡是运行时插入的 DOM，scoped 选择器打不到 */
.canvas-reader-body .canvas-code-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 14px;
  border: 1px solid #eef1f6;
  border-bottom: none;
  border-radius: 10px 10px 0 0;
  background: #f8fafc;
  padding: 4px 10px;
  font-size: 10.5px;
  color: #94a3b8;
}
.canvas-reader-body .canvas-code-lang {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.canvas-reader-body .canvas-code-actions {
  display: flex;
  gap: 4px;
}
.canvas-reader-body .canvas-code-actions button {
  border-radius: 5px;
  padding: 1px 6px;
  font-size: 10.5px;
  color: #64748b;
  transition: background-color 0.12s, color 0.12s;
}
.canvas-reader-body .canvas-code-actions button:hover {
  background: #e8eef7;
  color: #334155;
}
.canvas-reader-body .canvas-code-actions button.is-on {
  background: #eff6ff;
  color: #2563eb;
  font-weight: 700;
}
/* 工具条与代码块之间不能有缝：贴住上圆角 */
.canvas-reader-body .canvas-code-bar + pre {
  margin-top: 0;
  border-radius: 0 0 10px 10px;
}
.canvas-reader-body pre.is-wrapped,
.canvas-reader-body pre.is-wrapped code {
  white-space: pre-wrap;
  word-break: break-word;
}

.canvas-reader-body .canvas-img-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 12px 0;
  border: 1px solid #fecaca;
  border-radius: 10px;
  background: #fef2f2;
  padding: 9px 12px;
  font-size: 12px;
  color: #b91c1c;
}
.canvas-reader-body .canvas-img-error-actions {
  display: flex;
  flex: none;
  gap: 6px;
}
.canvas-reader-body .canvas-img-error-actions button {
  border: 1px solid #fca5a5;
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 11px;
  color: #b91c1c;
  transition: background-color 0.12s;
}
.canvas-reader-body .canvas-img-error-actions button:hover {
  background: #fee2e2;
}
.canvas-reader-body .canvas-img-error-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.canvas-reader-body .canvas-img-error-text {
  overflow-wrap: anywhere;
}

/* 深色：用普通后代选择器，绝不把 .dark 包进 global 作用域。
   那样会被编译成一条全局 .dark{...}，目标类名一起丢掉，命中所有带 .dark 的元素
   （历史「深色白雾」的成因，仓库有契约测试锁着）。 */
.dark .canvas-outline {
  border-right-color: #374151;
}
.dark .canvas-outline-item {
  color: #94a3b8;
}
.dark .canvas-outline-item:hover {
  background: #374151;
  color: #e5e7eb;
}
.dark .canvas-outline-item.is-active {
  background: rgba(37, 99, 235, 0.18);
  color: #93c5fd;
}
.dark .canvas-reader-body {
  color: #e5e7eb;
}
.dark .canvas-reader-body .canvas-code-bar {
  border-color: #374151;
  border-bottom-color: transparent;
  background: #1f2937;
  color: #94a3b8;
}
.dark .canvas-reader-body .canvas-code-actions button {
  color: #9ca3af;
}
.dark .canvas-reader-body .canvas-code-actions button:hover {
  background: #374151;
  color: #e5e7eb;
}
.dark .canvas-reader-body .canvas-code-actions button.is-on {
  background: rgba(37, 99, 235, 0.22);
  color: #93c5fd;
}
.dark .canvas-reader-body .canvas-img-error {
  border-color: #7f1d1d;
  background: rgba(127, 29, 29, 0.22);
  color: #fca5a5;
}
.dark .canvas-reader-body .canvas-img-error-actions button {
  border-color: #b91c1c;
  color: #fca5a5;
}
.dark .canvas-reader-body .canvas-img-error-actions button:hover {
  background: rgba(185, 28, 28, 0.25);
}
</style>
