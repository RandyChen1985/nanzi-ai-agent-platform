<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue"
import { ShareIcon } from "@heroicons/vue/24/outline"

const props = withDefaults(defineProps<{
  mode?: "data" | "regenerate" | "more" | "both"
  hasConversationDataFile?: boolean
  showDataOnMobile?: boolean
  reusableResultId?: string | null
  hasConversationReusableResult?: boolean
  hasConversationArtifact?: boolean
  conversationReusableCount?: number
  conversationArtifactCount?: number
  canExport?: boolean
  hasTrace?: boolean
  reusableCount?: number | null
  artifactCount?: number
  canRegenerate?: boolean
  hasTokenStats?: boolean
  canSaveReport?: boolean
}>(), {
  mode: "both",
  hasConversationDataFile: false,
  showDataOnMobile: false,
  reusableResultId: null,
  hasConversationReusableResult: false,
  hasConversationArtifact: false,
  conversationReusableCount: 0,
  conversationArtifactCount: 0,
  canExport: false,
  hasTrace: false,
  reusableCount: null,
  artifactCount: 0,
  canRegenerate: false,
  hasTokenStats: false,
  canSaveReport: false,
})

const emit = defineEmits<{
  exportData: []
  openReusableResults: []
  openArtifacts: []
  regenerate: []
  openTrace: []
  openStats: []
  saveReport: []
}>()

const openMenu = ref<"data" | "more" | null>(null)
const root = ref<HTMLElement | null>(null)
const moreButton = ref<HTMLButtonElement | null>(null)
const moreMenu = ref<HTMLElement | null>(null)
const moreMenuStyle = ref<Record<string, string>>({})

const hasReusableResult = computed(() => Boolean(props.reusableCount && props.reusableCount > 0))
const hasReusableResultEntry = computed(() => Boolean(hasReusableResult.value || props.hasConversationReusableResult))
const hasArtifactEntry = computed(() => Boolean((props.artifactCount || 0) > 0 || props.hasConversationArtifact))
const hasDataFile = computed(() => Boolean(props.hasConversationDataFile))
const hasDesktopMore = computed(() => Boolean(
  props.canExport || props.hasTrace || props.hasTokenStats || props.canSaveReport,
))
const hasMobileDataMenu = computed(() => Boolean(
  props.showDataOnMobile && (hasDataFile.value || props.canExport),
))
const hasMore = computed(() => Boolean(
  hasDesktopMore.value || hasMobileDataMenu.value,
))

const closeOnOutside = (event: PointerEvent) => {
  const target = event.target as Node
  if (openMenu.value && !root.value?.contains(target) && !moreMenu.value?.contains(target)) openMenu.value = null
}
const closeOnEscape = (event: KeyboardEvent) => {
  if (event.key === "Escape") openMenu.value = null
}
const repositionMoreMenu = async () => {
  if (openMenu.value !== "more" || !moreButton.value) return
  const buttonRect = moreButton.value.getBoundingClientRect()
  const menuWidth = Math.min(192, Math.max(0, window.innerWidth - 16))
  const left = Math.min(
    Math.max(8, buttonRect.right - menuWidth),
    Math.max(8, window.innerWidth - menuWidth - 8),
  )

  moreMenuStyle.value = {
    position: "fixed",
    left: `${left}px`,
    top: "8px",
    width: `${menuWidth}px`,
    visibility: "hidden",
  }
  await nextTick()

  const menuRect = moreMenu.value?.getBoundingClientRect()
  const menuHeight = menuRect?.height ?? 0
  let top = buttonRect.top - menuHeight - 8
  if (top < 8) top = buttonRect.bottom + 8
  if (top + menuHeight > window.innerHeight - 8) top = Math.max(8, window.innerHeight - menuHeight - 8)
  moreMenuStyle.value = {
    position: "fixed",
    left: `${left}px`,
    top: `${top}px`,
    width: `${menuWidth}px`,
    maxHeight: "calc(100vh - 16px)",
    overflowY: "auto",
  }
}
const toggle = (menu: "data" | "more") => {
  openMenu.value = openMenu.value === menu ? null : menu
  if (openMenu.value === "more") void repositionMoreMenu()
}
const run = (action: () => void) => {
  openMenu.value = null
  moreMenuStyle.value = {}
  action()
}

onMounted(() => {
  document.addEventListener("pointerdown", closeOnOutside)
  document.addEventListener("keydown", closeOnEscape)
  window.addEventListener("resize", repositionMoreMenu)
  window.addEventListener("scroll", repositionMoreMenu, true)
})
onUnmounted(() => {
  document.removeEventListener("pointerdown", closeOnOutside)
  document.removeEventListener("keydown", closeOnEscape)
  window.removeEventListener("resize", repositionMoreMenu)
  window.removeEventListener("scroll", repositionMoreMenu, true)
})
</script>

<template>
  <div ref="root" class="flex shrink-0 items-center gap-1.5">
    <div v-if="(mode === 'data' || mode === 'both')" class="relative">
      <button
        type="button"
        class="flex min-h-8 shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-[11px] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
        :class="[
          { 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-200': openMenu === 'data' },
          !hasDataFile ? 'cursor-not-allowed opacity-60 hover:bg-transparent hover:text-gray-500 dark:hover:bg-transparent dark:hover:text-gray-400' : '',
        ]"
        :disabled="!hasDataFile"
        :aria-disabled="!hasDataFile"
        :aria-expanded="openMenu === 'data'"
        aria-haspopup="menu"
        :title="hasDataFile ? '查看本会话的数据和文件' : '本会话暂无数据或文件'"
        @click="hasDataFile && toggle('data')"
      >
        <span aria-hidden="true">▤</span>
        <span>数据 / 文件</span>
        <span v-if="reusableCount && reusableCount > 0" class="rounded-full bg-primary px-1.5 py-0.5 text-[9px] font-semibold leading-none text-white">数据 {{ reusableCount }}</span>
        <span v-if="artifactCount > 0" class="rounded-full bg-primary px-1.5 py-0.5 text-[9px] font-semibold leading-none text-white">文件 {{ artifactCount }}</span>
        <span v-if="hasDataFile" class="text-[9px]">⌄</span>
      </button>
      <div v-if="openMenu === 'data'" class="absolute bottom-full left-0 z-50 mb-2 w-52 rounded-xl border border-gray-200 bg-white p-1.5 shadow-xl dark:border-gray-700 dark:bg-gray-900" role="menu">
        <button v-if="hasReusableResultEntry" type="button" class="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-xs text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800" role="menuitem" @click="run(() => emit('openReusableResults'))">
          <span>▤ 查看可复用结果</span><span v-if="conversationReusableCount > 0" class="text-[10px] text-gray-400">{{ conversationReusableCount }} 条</span>
        </button>
        <button v-if="hasArtifactEntry" type="button" class="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-xs text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800" role="menuitem" @click="run(() => emit('openArtifacts'))">
          <span>▣ 查看文件产物</span><span v-if="conversationArtifactCount > 0" class="text-[10px] text-gray-400">{{ conversationArtifactCount }} 个</span>
        </button>
        <div v-if="!hasReusableResultEntry && !hasArtifactEntry" class="px-2.5 py-2 text-xs text-gray-400 dark:text-gray-500">本会话暂无可查看的数据或文件</div>
      </div>
    </div>

    <template v-if="mode === 'regenerate' || mode === 'both'">
      <div v-if="canRegenerate" class="group relative inline-flex items-center justify-center">
        <button
          type="button"
          class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-400 hover:text-primary transition-colors hover:bg-gray-100 dark:hover:bg-gray-800 dark:text-gray-400 dark:hover:text-primary-active"
          aria-label="重新生成"
          @click="run(() => emit('regenerate'))"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>
        <div class="pointer-events-none absolute top-full left-1/2 -translate-x-1/2 mt-1.5 opacity-0 group-hover:opacity-100 transition-all duration-150 flex flex-col items-center z-50 transform -translate-y-0.5 group-hover:translate-y-0">
          <div class="w-1.5 h-1.5 bg-gray-900/90 dark:bg-gray-800/95 rotate-45 -mb-0.5"></div>
          <div class="rounded-md bg-gray-900/90 dark:bg-gray-800/95 px-2 py-0.5 text-[10px] font-medium text-white shadow-lg backdrop-blur-sm whitespace-nowrap">
            重新生成
          </div>
        </div>
      </div>
    </template>
    <template v-if="mode === 'more' || mode === 'both'">
      <div v-if="hasMore" class="relative" :class="{ 'sm:hidden': showDataOnMobile && !hasDesktopMore }">
        <button ref="moreButton" type="button" class="flex min-h-8 shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-[11px] font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200" :class="{ 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-200': openMenu === 'more' }" :aria-expanded="openMenu === 'more'" aria-haspopup="menu" title="更多操作" @click="toggle('more')">⋯ 更多</button>
        <Teleport to="body">
          <div v-if="openMenu === 'more'" ref="moreMenu" :style="moreMenuStyle" class="z-[120] rounded-xl border border-gray-200 bg-white p-1.5 shadow-xl dark:border-gray-700 dark:bg-gray-900" role="menu">
          <div v-if="hasMobileDataMenu" class="sm:hidden">
            <div v-if="hasDataFile" class="flex items-center gap-1 px-2.5 py-2 text-[10px] font-semibold text-gray-400 dark:text-gray-500">
              <span>▤ 数据 / 文件</span>
              <span v-if="reusableCount && reusableCount > 0" class="rounded-full bg-primary px-1.5 py-0.5 text-[9px] font-semibold leading-none text-white">数据 {{ reusableCount }}</span>
              <span v-if="artifactCount > 0" class="rounded-full bg-primary px-1.5 py-0.5 text-[9px] font-semibold leading-none text-white">文件 {{ artifactCount }}</span>
            </div>
            <button v-if="hasReusableResultEntry" type="button" class="menu-item" role="menuitem" @click="run(() => emit('openReusableResults'))">▤ 查看可复用结果 <span v-if="conversationReusableCount > 0" class="float-right text-[10px] text-gray-400">{{ conversationReusableCount }} 条</span></button>
            <button v-if="canExport" type="button" class="menu-item" role="menuitem" @click="run(() => emit('exportData'))">↓ 导出数据（Excel）</button>
            <button v-if="hasArtifactEntry" type="button" class="menu-item" role="menuitem" @click="run(() => emit('openArtifacts'))">▣ 查看文件产物 <span v-if="conversationArtifactCount > 0" class="float-right text-[10px] text-gray-400">{{ conversationArtifactCount }} 个</span></button>
            <div v-if="hasTrace || hasTokenStats || canSaveReport" class="my-1 border-t border-gray-100 dark:border-gray-800" />
          </div>
          <button v-if="canExport" type="button" class="menu-item desktop-export-item" role="menuitem" @click="run(() => emit('exportData'))">↓ 导出数据（Excel）</button>
          <button v-if="hasTrace" type="button" class="menu-item flex items-center gap-1.5 whitespace-nowrap" role="menuitem" @click="run(() => emit('openTrace'))"><ShareIcon class="h-4 w-4 shrink-0 text-gray-500" /> 查看执行链路</button>
          <button v-if="hasTokenStats" type="button" class="menu-item" role="menuitem" @click="run(() => emit('openStats'))">▤ 查看调用详情</button>
          <button v-if="canSaveReport" type="button" class="menu-item" role="menuitem" @click="run(() => emit('saveReport'))">☆ 添加固化报表</button>
          </div>
        </Teleport>
      </div>
    </template>
  </div>
</template>

<style scoped>
.menu-item {
  display: flex;
  align-items: center;
  width: 100%;
  border-radius: 0.5rem;
  padding: 0.5rem 0.625rem;
  text-align: left;
  font-size: 0.75rem;
  color: rgb(75 85 99);
}
.desktop-export-item { display: none; }
@media (min-width: 640px) {
  .desktop-export-item { display: flex; }
}
.menu-item:hover { background: rgb(249 250 251); }
:global(.dark) .menu-item { color: rgb(209 213 219); }
:global(.dark) .menu-item:hover { background: rgb(31 41 55); }
</style>
