<template>
  <div class="flex h-full min-h-0 flex-col">
    <!-- 菜单不存在或无权限：导航树里查不到就不渲染 iframe，避免把无权限地址喂给浏览器 -->
    <div v-if="!node" class="m-4 rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-800">
      <p class="font-bold">菜单不存在或你没有访问权限</p>
      <p class="mt-1 leading-relaxed">该外链菜单可能已被停用/删除，或未授予你对应的权限。</p>
      <button
        type="button"
        class="mt-3 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-amber-700"
        @click="goBack"
      >返回上一页</button>
    </div>

    <template v-else>
      <!-- 工具条：地址与打开方式始终可见，出问题时用户有明确的逃生路径 -->
      <div class="flex flex-wrap items-center gap-2 border-b border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900">
        <MenuIcon :name="node.icon" class="h-4 w-4 text-gray-500 dark:text-gray-400" />
        <span class="text-sm font-bold text-gray-800 dark:text-gray-100">{{ node.label }}</span>
        <span class="truncate text-[11px] text-gray-400 dark:text-gray-500" :title="node.url || ''">{{ node.url }}</span>
        <div class="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            class="rounded-lg border border-gray-200 px-2.5 py-1 text-[11px] font-bold text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
            @click="reload"
          >刷新</button>
          <a
            :href="node.url || '#'"
            target="_blank"
            rel="noopener noreferrer"
            class="rounded-lg bg-blue-600 px-2.5 py-1 text-[11px] font-bold text-white hover:bg-blue-700"
          >新标签打开</a>
        </div>
      </div>

      <div class="relative flex-1 min-h-0 bg-gray-50 dark:bg-gray-950">
        <iframe
          v-show="!failed"
          :key="frameKey"
          :src="node.url || 'about:blank'"
          class="h-full w-full border-0"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
          referrerpolicy="no-referrer-when-downgrade"
          @load="handleLoad"
          @error="markFailed"
        ></iframe>

        <div v-if="loading && !failed" class="absolute inset-0 flex items-center justify-center bg-white/70 dark:bg-gray-950/70">
          <div class="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
            <span class="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-blue-500"></span>
            正在加载外部页面…
          </div>
        </div>

        <!-- 跨域无法读取 iframe 内容，无法可靠区分"被拒绝"与"加载慢"；因此以超时兜底 + 明确说明 -->
        <div v-if="failed" class="absolute inset-0 flex items-center justify-center p-6">
          <div class="max-w-lg rounded-xl border border-gray-200 bg-white p-6 text-sm shadow-sm dark:border-gray-700 dark:bg-gray-900">
            <p class="text-base font-bold text-gray-800 dark:text-gray-100">外部页面无法内嵌显示</p>
            <p class="mt-2 leading-relaxed text-gray-600 dark:text-gray-300">
              目标站点可能设置了 <code class="rounded bg-gray-100 px-1 py-0.5 text-[11px] dark:bg-gray-800">X-Frame-Options</code>
              或 <code class="rounded bg-gray-100 px-1 py-0.5 text-[11px] dark:bg-gray-800">CSP frame-ancestors</code>，
              禁止被其他站点嵌入；也可能是地址不可达或加载超时。
            </p>
            <p class="mt-2 leading-relaxed text-gray-500 dark:text-gray-400">
              内嵌方式由管理员在「菜单管理」里配置，此处无法绕过对方的安全策略。
            </p>
            <div class="mt-4 flex flex-wrap gap-2">
              <a
                :href="node.url || '#'"
                target="_blank"
                rel="noopener noreferrer"
                class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-700"
              >在新标签页打开</a>
              <button
                type="button"
                class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-bold text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
                @click="reload"
              >重试</button>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MenuIcon from '../components/menu/MenuIcon.vue'
import { ensureNavigation, useNavigation, type NavNode } from '../composables/useNavigation'

/**
 * 外链菜单的内嵌容器。
 *
 * 注意这里做不到"探测对方是否禁止嵌入"：跨域时浏览器不允许读取 iframe 的 contentDocument，
 * 被 X-Frame-Options 拒绝与加载缓慢在 JS 侧无法区分。因此策略是：
 * 加载超过 8 秒未触发 load 就判定为大概率失败，给出明确原因和一键新标签打开。
 */
const route = useRoute()
const router = useRouter()
const { findNode, groups } = useNavigation()

const LOAD_TIMEOUT_MS = 8000

const node = ref<NavNode | null>(null)
const frameKey = ref(0)
const loading = ref(true)
const failed = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

const code = computed(() => String(route.params.code || ''))

function resolveNode() {
  const found = findNode(code.value)
  node.value = found && found.node_type === 'external' ? found : null
  if (node.value && !node.value.url) node.value = null
  restart()
}

function clearTimer() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

function restart() {
  clearTimer()
  loading.value = true
  failed.value = false
  if (!node.value) return
  timer = setTimeout(() => {
    // load 没来：大概率被对方拒绝或超时，切到兜底卡片（仍保留重试入口）
    if (loading.value) markFailed()
  }, LOAD_TIMEOUT_MS)
}

function handleLoad() {
  clearTimer()
  loading.value = false
}

function markFailed() {
  clearTimer()
  loading.value = false
  failed.value = true
}

function reload() {
  frameKey.value += 1
  restart()
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/dashboard')
}

onMounted(async () => {
  await ensureNavigation()
  resolveNode()
})

watch(code, () => {
  void ensureNavigation().then(resolveNode)
})

/**
 * 导航数据可能在首屏之后才刷新到位：`ensureNavigation()` 命中本地快照时会立即返回，
 * 真数据在后台异步校验。若外链是刚加的，旧快照里查不到就会先渲染"无权限"，
 * 而本组件不重新解析便会一直卡在那张卡片上。因此数据变化后重试一次。
 */
watch(groups, () => {
  const next = findNode(code.value)
  const current = node.value
  if (!next || next.node_type !== 'external' || !next.url) {
    if (current) resolveNode()
    return
  }
  if (!current || current.url !== next.url) resolveNode()
})

onBeforeUnmount(clearTimer)
</script>
