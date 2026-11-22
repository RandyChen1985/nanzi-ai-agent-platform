<template>
  <Teleport to="body">
    <div
      v-if="visible"
      data-update-notes
      class="update-notes-layer"
      @click.self="emit('close')"
    >
      <div
        class="update-notes-card"
        :class="lightTheme ? 'is-light' : 'is-dark'"
        role="dialog"
        aria-modal="true"
        aria-labelledby="update-notes-title"
      >
        <header class="update-notes-head">
          <div class="min-w-0">
            <h2 id="update-notes-title" class="update-notes-title">
              更新内容<span v-if="version"> {{ version }}</span>
            </h2>
            <p class="update-notes-sub">
              <span v-if="publishedAt">{{ publishedAt }} 发布</span>
              <span v-if="publishedAt && hasMigrations" class="mx-1">·</span>
              <span v-if="hasMigrations">含数据库迁移，升级前请先执行迁移脚本</span>
              <span v-if="!publishedAt && !hasMigrations">来自上游发行说明</span>
            </p>
          </div>
          <button type="button" class="update-notes-icon-btn" aria-label="关闭更新内容" @click="emit('close')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" stroke-linecap="round" />
            </svg>
          </button>
        </header>

        <div ref="bodyRef" class="update-notes-body" tabindex="-1">
          <!-- 正文来自 Gitee 发行说明，已在 renderMarkdown 里做过 sanitize -->
          <div v-if="renderedHtml" class="markdown-body" v-html="renderedHtml" />
          <p v-else class="update-notes-empty">这次发行没有填写说明，可以到仓库发行版页查看历史版本。</p>
        </div>

        <!-- 两条仓库外链：弹层正文是我们缓存的副本（超长还会截断），想核对原文
             或把链接发给别人时需要它。Gitee 与 GitHub 两边都给——内网里常常只有
             其中一个通。 -->
        <footer class="update-notes-foot">
          <div class="update-notes-links">
            <a
              v-if="releaseUrl"
              :href="releaseUrl"
              target="_blank"
              rel="noopener noreferrer"
              class="update-notes-link"
            >在 Gitee 打开 ↗</a>
            <a
              v-if="githubUrl"
              :href="githubUrl"
              target="_blank"
              rel="noopener noreferrer"
              class="update-notes-link"
            >在 GitHub 打开 ↗</a>
          </div>
          <button type="button" class="update-notes-primary" @click="emit('close')">知道了</button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * 新版本更新内容弹层。
 *
 * 内容来自后端已缓存的发行说明正文（不是现场请求 Gitee），所以内网/离线也能看——
 * 这也是它比「新标签打开仓库」更好的地方：打不打得开 Gitee 都不影响阅读。
 *
 * 交互约定：
 * - 点遮罩或按 Esc 关闭；打开时焦点落在关闭按钮，关闭后还给触发者；
 * - 正文区独立滚动，头部与底部动作常驻，长文不会把按钮顶出屏幕；
 * - 不改 URL、不叠路由——它是菜单里的一次临时查看，不是页面。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue"
import { renderMarkdown } from "@/utils/markdown"

const props = withDefaults(
  defineProps<{
    visible: boolean
    version?: string
    publishedAt?: string
    hasMigrations?: boolean
    body?: string
    releaseUrl?: string
    githubUrl?: string
    lightTheme?: boolean
  }>(),
  {
    version: "",
    publishedAt: "",
    hasMigrations: false,
    body: "",
    releaseUrl: "",
    githubUrl: "",
    lightTheme: false,
  },
)

const emit = defineEmits<{ (e: "close"): void }>()

const bodyRef = ref<HTMLElement | null>(null)
/** 缓存渲染结果：同一次打开里正文不会变，没必要每帧重解析 markdown */
const renderedHtml = computed(() => (props.body ? renderMarkdown(props.body) : ""))

let lastFocused: HTMLElement | null = null

const onKeydown = (event: KeyboardEvent) => {
  if (event.key === "Escape" && props.visible) {
    // 捕获阶段处理：菜单自己也监听 Esc，这里先一步收掉弹层，
    // 不能让「关弹层」顺带把整个账户菜单关掉
    event.stopPropagation()
    emit("close")
  }
}

watch(
  () => props.visible,
  async (visible) => {
    if (visible) {
      lastFocused = (document.activeElement as HTMLElement | null) ?? null
      document.addEventListener("keydown", onKeydown, true)
      await nextTick()
      bodyRef.value?.scrollTo?.({ top: 0 })
      // 焦点落在正文区：键盘用户可以直接用方向键/PageDown 滚动，读完再按 Esc
      bodyRef.value?.focus?.()
      return
    }
    document.removeEventListener("keydown", onKeydown, true)
    lastFocused?.focus?.()
    lastFocused = null
  },
)

onBeforeUnmount(() => document.removeEventListener("keydown", onKeydown, true))
</script>

<style scoped>
.update-notes-layer {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(8, 12, 20, 0.55);
  backdrop-filter: blur(2px);
}

.update-notes-card {
  width: 100%;
  max-width: 760px;
  max-height: 82vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 14px;
  border: 1px solid transparent;
  box-shadow: 0 28px 70px -24px rgba(2, 6, 23, 0.7);
}

.update-notes-card.is-dark {
  --notes-bg: #171d27;
  --notes-fg: #e2e8f0;
  --notes-muted: #94a3b8;
  --notes-line: rgba(255, 255, 255, 0.1);
  --notes-accent: #93c5fd;
  --notes-code-bg: rgba(255, 255, 255, 0.07);
}

.update-notes-card.is-light {
  --notes-bg: #ffffff;
  --notes-fg: #1f2937;
  --notes-muted: #6b7280;
  --notes-line: rgba(15, 23, 42, 0.1);
  --notes-accent: #1677ff;
  --notes-code-bg: rgba(15, 23, 42, 0.06);
}

.update-notes-card {
  background: var(--notes-bg);
  color: var(--notes-fg);
  border-color: var(--notes-line);
}

.update-notes-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px 12px;
  border-bottom: 1px solid var(--notes-line);
}

.update-notes-title {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  margin: 0;
}

.update-notes-sub {
  margin: 3px 0 0;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--notes-muted);
}

.update-notes-icon-btn {
  margin-left: auto;
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  color: var(--notes-muted);
  background: transparent;
  border: 0;
  cursor: pointer;
}

.update-notes-icon-btn:hover {
  background: var(--notes-code-bg);
}

.update-notes-icon-btn svg {
  width: 15px;
  height: 15px;
}

.update-notes-body {
  flex: 1 1 auto;
  min-height: 120px;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 14px 18px;
  font-size: 13px;
  line-height: 1.75;
}

.update-notes-empty {
  margin: 0;
  color: var(--notes-muted);
  font-size: 12.5px;
}

.update-notes-foot {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-top: 1px solid var(--notes-line);
}

.update-notes-links {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 14px;
}

.update-notes-link {
  font-size: 12px;
  color: var(--notes-accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.update-notes-primary {
  margin-left: auto;
  padding: 5px 14px;
  font-size: 12.5px;
  font-weight: 500;
  border-radius: 8px;
  border: 1px solid var(--notes-line);
  background: var(--notes-code-bg);
  color: var(--notes-fg);
  cursor: pointer;
}

.update-notes-primary:hover {
  filter: brightness(1.06);
}

/* 正文排版：只覆盖必要的间距与层级，其余交给 markdown-body 的现有样式 */
.update-notes-body :deep(.markdown-body) {
  color: var(--notes-fg);
  font-size: 13px;
}

.update-notes-body :deep(h1) {
  font-size: 17px;
  font-weight: 700;
  margin: 6px 0 10px;
}

.update-notes-body :deep(h2) {
  font-size: 15px;
  font-weight: 700;
  margin: 18px 0 8px;
  padding-bottom: 5px;
  border-bottom: 1px solid var(--notes-line);
}

.update-notes-body :deep(h3) {
  font-size: 13.5px;
  font-weight: 600;
  margin: 14px 0 6px;
}

.update-notes-body :deep(p),
.update-notes-body :deep(ul),
.update-notes-body :deep(ol) {
  margin: 6px 0;
}

.update-notes-body :deep(ul),
.update-notes-body :deep(ol) {
  padding-left: 20px;
}

.update-notes-body :deep(li) {
  margin: 2px 0;
}

.update-notes-body :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--notes-code-bg);
}

.update-notes-body :deep(pre) {
  margin: 8px 0;
  padding: 10px 12px;
  border-radius: 8px;
  overflow-x: auto;
  background: var(--notes-code-bg);
}

.update-notes-body :deep(pre code) {
  padding: 0;
  background: transparent;
}

.update-notes-body :deep(a) {
  color: var(--notes-accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.update-notes-body :deep(blockquote) {
  margin: 8px 0;
  padding: 4px 0 4px 10px;
  border-left: 3px solid var(--notes-line);
  color: var(--notes-muted);
}

.update-notes-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 12px;
}

.update-notes-body :deep(th),
.update-notes-body :deep(td) {
  border: 1px solid var(--notes-line);
  padding: 5px 8px;
  text-align: left;
}

.update-notes-body :deep(hr) {
  margin: 14px 0;
  border: 0;
  border-top: 1px solid var(--notes-line);
}

@media (max-width: 640px) {
  .update-notes-layer {
    padding: 10px;
  }

  .update-notes-card {
    max-height: 88vh;
  }

  .update-notes-body {
    padding: 12px 14px;
  }
}
</style>
