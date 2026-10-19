<template>
  <Teleport to="body">
    <div
      v-if="open && entries.length"
      ref="menuRef"
      role="menu"
      aria-label="文件操作"
      class="fixed z-[130] min-w-[11rem] py-1 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-xl ring-1 ring-black/5 text-xs font-bold text-gray-700 dark:text-gray-200 overflow-y-auto custom-scrollbar"
      :style="{ left: `${position.left}px`, top: `${position.top}px`, maxHeight: `${maxHeight}px` }"
      @keydown.esc.stop.prevent="emit('close')"
      @keydown.down.stop.prevent="focusItem(1)"
      @keydown.up.stop.prevent="focusItem(-1)"
      @keydown.tab="emit('close')"
      @click.stop
      @contextmenu.prevent.stop
    >
      <template v-for="(entry, index) in entries" :key="`${entry.key}-${index}`">
        <div
          v-if="entry.separatorBefore"
          class="mx-3 my-1 border-t border-gray-100 dark:border-gray-800"
        />
        <div
          v-if="entry.key === 'header'"
          class="px-3 py-1.5 text-[10px] font-bold border-b border-gray-100 dark:border-gray-800 flex items-center gap-1 select-none text-primary/80 dark:text-primary/70"
        >
          <span aria-hidden="true">{{ entry.icon }}</span>
          <span>{{ entry.label }}</span>
        </div>
        <button
          v-else
          type="button"
          role="menuitem"
          class="w-full min-h-[40px] inline-flex items-center gap-2 px-3 py-2 text-left transition-colors focus:outline-none focus:bg-gray-50 dark:focus:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
          :class="entry.danger
            ? 'text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20'
            : 'hover:bg-gray-50 dark:hover:bg-gray-800'"
          :disabled="entry.disabled"
          :aria-disabled="entry.disabled ? 'true' : undefined"
          :title="entry.disabled ? entry.disabledHint : undefined"
          @click="onSelect(entry)"
        >
          <span aria-hidden="true">{{ entry.icon }}</span>
          <span class="truncate">{{ entry.label }}</span>
        </button>
      </template>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { WorkspaceActionEntry, WorkspaceActionKey } from '@/utils/workspaceActionEntries'

const props = defineProps<{
  open: boolean
  entries: WorkspaceActionEntry[]
  anchor: { x: number; y: number } | null
}>()

const emit = defineEmits<{
  select: [key: WorkspaceActionKey]
  close: []
}>()

// 与抽屉里原右键菜单一致的边距：贴着视口边缘会显得像被裁掉
const MENU_MARGIN = 8

const menuRef = ref<HTMLElement | null>(null)
const position = ref({ left: 0, top: 0 })
const maxHeight = ref(typeof window === 'undefined' ? 0 : window.innerHeight)

/**
 * 按实测尺寸决定落点：下方放不下就向上翻，右侧放不下就向左收，最后夹进视口。
 * 只翻转是不够的——菜单本身高于剩余空间时，翻上去照样露不全，所以还要限制高度。
 */
const adjustPosition = () => {
  const element = menuRef.value
  const anchor = props.anchor
  if (!element || !anchor) return

  const margin = MENU_MARGIN
  const { width } = element.getBoundingClientRect()
  const available = Math.max(120, window.innerHeight - margin * 2)
  const height = Math.min(element.scrollHeight, available)
  maxHeight.value = height

  let left = anchor.x
  let top = anchor.y
  if (left + width > window.innerWidth - margin) {
    left = Math.max(margin, window.innerWidth - width - margin)
  }
  if (top + height > window.innerHeight - margin) {
    top = anchor.y - height
  }
  if (top < margin) top = margin
  if (top + height > window.innerHeight - margin) {
    top = Math.max(margin, window.innerHeight - height - margin)
  }
  position.value = { left, top }
}

/** 只在可用（未禁用）项之间移动焦点，避免键盘用户停在灰掉的项上。 */
const focusItem = (direction: 1 | -1) => {
  const buttons = Array.from(
    menuRef.value?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])') || [],
  )
  if (!buttons.length) return
  const current = document.activeElement as HTMLElement | null
  const index = buttons.indexOf(current as HTMLButtonElement)
  const next = index < 0
    ? (direction === 1 ? 0 : buttons.length - 1)
    : (index + direction + buttons.length) % buttons.length
  const target = buttons[next]
  if (!target) return
  target.focus()
}

const onSelect = (entry: WorkspaceActionEntry) => {
  if (entry.disabled) return
  emit('select', entry.key)
}

const onDocumentMousedown = (event: MouseEvent) => {
  if (!props.open) return
  if (menuRef.value?.contains(event.target as Node)) return
  emit('close')
}

watch(
  () => [props.open, props.anchor] as const,
  async ([open]) => {
    if (!open) return
    await nextTick()
    adjustPosition()
    focusItem(1)
  },
  { immediate: true },
)

onMounted(() => {
  document.addEventListener('mousedown', onDocumentMousedown)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentMousedown)
})
</script>
