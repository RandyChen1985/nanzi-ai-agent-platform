<template>
  <div ref="root" class="relative">
    <button
      type="button"
      class="flex w-full items-center gap-2 rounded-lg border border-gray-200 px-2.5 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-50 disabled:bg-gray-50 disabled:text-gray-400 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100 dark:hover:bg-gray-800 dark:disabled:bg-gray-800"
      :class="open ? 'ring-2 ring-blue-500/30' : ''"
      :disabled="disabled"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
    >
      <MenuIcon :name="modelValue" class="h-4 w-4 opacity-80" />
      <span class="min-w-0 flex-1 truncate">{{ currentLabel }}</span>
      <span class="hidden text-[10px] text-gray-400 sm:inline">{{ modelValue || '—' }}</span>
      <svg class="h-3.5 w-3.5 flex-shrink-0 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
      </svg>
    </button>

    <!--
      Teleport 到 body：面板要能超出属性面板的滚动容器。
      留在原地时 `main` 的 `overflow-y-auto` 会把面板下半截裁掉，滚不到后面的图标。
    -->
    <Teleport to="body">
      <div
        v-if="open"
        ref="panelEl"
        class="custom-scrollbar fixed z-[70] overflow-y-auto overscroll-contain rounded-xl border border-gray-200 bg-white p-2 shadow-2xl dark:border-gray-700 dark:bg-gray-900"
        :style="panelStyle"
        role="listbox"
      >
        <button
          type="button"
          class="mb-1 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs font-bold"
          :class="!modelValue
            ? 'bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300'
            : 'text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-800'"
          @click="pick(null)"
        >
          <span class="flex h-4 w-4 items-center justify-center rounded border border-dashed border-gray-300 text-[10px] text-gray-400 dark:border-gray-600">∅</span>
          不设置图标
        </button>

        <div v-for="category in categories" :key="category.title" class="mb-0.5">
          <p class="px-1 pb-1 pt-1.5 text-[10px] font-bold tracking-wide text-gray-400 dark:text-gray-500">
            {{ category.title }}
          </p>
          <div class="grid grid-cols-4 gap-1">
            <button
              v-for="key in category.keys"
              :key="key"
              type="button"
              :data-current="modelValue === key ? 'true' : 'false'"
              class="flex flex-col items-center gap-1 rounded-lg border px-1 py-2 transition-colors"
              :class="modelValue === key
                ? 'border-blue-400 bg-blue-50 text-blue-700 dark:border-blue-500/50 dark:bg-blue-500/15 dark:text-blue-300'
                : 'border-transparent text-gray-600 hover:border-gray-200 hover:bg-gray-50 dark:text-gray-300 dark:hover:border-gray-700 dark:hover:bg-gray-800'"
              :title="`${labelOf(key)}（${key}）`"
              @click="pick(key)"
            >
              <MenuIcon :name="key" class="h-5 w-5" />
              <span class="w-full truncate text-center text-[10px] leading-tight">{{ labelOf(key) }}</span>
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import MenuIcon from './MenuIcon.vue'
import { MENU_ICON_CATEGORIES, MENU_ICON_LABELS } from '../../constants/menuIcons'

/**
 * 图标选择器（带预览）。
 *
 * 原生 `<select>` 只能显示 key，而 `chat_bubble_left_right` 这种名字不看到图形根本选不准；
 * 这里用「按钮 + 网格面板」，每格是真实图标 + 中文名。
 *
 * 三个必须注意的实现细节：
 * 1. 面板 Teleport 到 body 并用 `fixed` 定位——留在表单里会被 `main` 的 `overflow-y-auto` 裁掉，
 *    用户滚不到后面的图标；
 * 2. 面板高度按按钮上下可用空间现算（下方不够就向上弹），保证全部图标都能滚动看全；
 *    图标按用途分组、打开时自动滚到当前项——几十个图标平铺一屏是找不到的；
 * 3. 关闭监听用捕获阶段，因此**必须排除面板自身**：`mousedown` 在面板上、以及面板自己的滚动
 *    都不能被当成"点到外面/页面滚动"，否则一滚就收起、根本没法翻页。
 */
const props = withDefaults(defineProps<{ modelValue?: string | null; disabled?: boolean }>(), {
  modelValue: null,
  disabled: false,
})
const emit = defineEmits<{ (event: 'update:modelValue', value: string | null): void }>()

const categories = MENU_ICON_CATEGORIES
const root = ref<HTMLElement | null>(null)
const panelEl = ref<HTMLElement | null>(null)
const open = ref(false)
const anchor = ref<{ left: number; top: number; bottom: number; width: number } | null>(null)

const labelOf = (key: string) => MENU_ICON_LABELS[key] || key
const currentLabel = computed(() => (props.modelValue ? labelOf(props.modelValue) : '不设置图标'))

const PANEL_WIDTH = 320
const MIN_PANEL_HEIGHT = 200
const GAP = 6

const panelStyle = computed(() => {
  const rect = anchor.value
  if (!rect) return { left: '0px', top: '0px', width: `${PANEL_WIDTH}px` }
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  const width = Math.min(PANEL_WIDTH, viewportWidth - 16)
  const left = Math.max(8, Math.min(rect.left, viewportWidth - width - 8))

  const spaceBelow = viewportHeight - rect.bottom - GAP - 8
  const spaceAbove = rect.top - GAP - 8
  // 下方放不下完整面板、且上方更宽裕时向上展开
  const openUp = spaceBelow < 260 && spaceAbove > spaceBelow
  const maxHeight = Math.max(
    MIN_PANEL_HEIGHT,
    Math.min(openUp ? spaceAbove : spaceBelow, Math.round(viewportHeight * 0.62)),
  )
  const top = openUp
    ? Math.max(8, rect.top - GAP - maxHeight)
    : Math.min(rect.bottom + GAP, viewportHeight - maxHeight - 8)

  return {
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    maxHeight: `${maxHeight}px`,
  }
})

function measure() {
  const rect = root.value?.getBoundingClientRect()
  anchor.value = rect
    ? { left: rect.left, top: rect.top, bottom: rect.bottom, width: rect.width }
    : null
}

async function toggle() {
  if (props.disabled) return
  if (open.value) {
    open.value = false
    return
  }
  measure()
  open.value = true
  // Teleport 渲染后再落一次位置，避免首帧用旧 rect 闪一下
  await nextTick()
  measure()
  // 图标多了以后当前选中项常在视野外，打开就把它滚到中间
  panelEl.value?.querySelector('[data-current="true"]')?.scrollIntoView({ block: 'center' })
}

function pick(value: string | null) {
  emit('update:modelValue', value)
  open.value = false
}

function onDocumentPointerDown(event: MouseEvent) {
  if (!open.value) return
  const target = event.target as Node | null
  if (target && (root.value?.contains(target) || panelEl.value?.contains(target))) return
  open.value = false
}

function onScroll(event: Event) {
  if (!open.value) return
  const target = event.target
  // 面板自己的滚动（翻看更多图标）不能关闭面板
  if (target instanceof Node && panelEl.value?.contains(target)) return
  open.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

function onResize() {
  if (open.value) measure()
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentPointerDown, true)
  document.addEventListener('keydown', onKeydown)
  window.addEventListener('scroll', onScroll, true)
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentPointerDown, true)
  document.removeEventListener('keydown', onKeydown)
  window.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', onResize)
})
</script>
