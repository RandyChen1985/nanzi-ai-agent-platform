<script setup lang="ts">
/**
 * 「知识库检索范围确认」的模式选择器。
 *
 * 不用原生 ``<select>``：原生选项只能渲染单行纯文本，而这里的每个模式都必须带上
 * 一行「选了会发生什么」的说明——四种模式的区别（尤其「强制开启」受不受全局总闸约束）
 * 光看名字根本分不出来，那正是之前配错的原因。原生选项也没法放图标。
 *
 * 浮层 Teleport 到 body：这个控件在智能体编辑弹窗里，弹窗内容区有 overflow，
 * 就地展开会被裁掉。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

export type ScopeGateMode = 'inherit' | 'on' | 'off' | 'bound_only'

interface ScopeGateOption {
  value: ScopeGateMode
  label: string
  hint: string
  /** heroicons outline path，直接内联避免额外依赖解析 */
  iconPath: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

// 每项都带用途说明：只写选项名时，没人知道「跟随全局」到底跟的是什么、谁优先。
const OPTIONS: ScopeGateOption[] = [
  {
    value: 'inherit',
    label: '跟随全局',
    hint: '系统配置里关了就不生效；开着时按全局阈值判断要不要确认范围。',
    // globe-alt
    iconPath: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0 0a8.95 8.95 0 004.63-1.29M12 21a8.95 8.95 0 01-4.63-1.29M3.6 9h16.8M3.6 15h16.8',
  },
  {
    value: 'on',
    label: '强制开启',
    hint: '仅在全局总闸开启时生效，含义是本智能体不跟随后续灰度；总闸关闭时这项设置不起作用。',
    // bolt
    iconPath: 'M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z',
  },
  {
    value: 'off',
    label: '关闭',
    hint: '不做范围确认，按「绑定库 + 用户可访问库」的合并范围检索。',
    // no-symbol
    iconPath: 'M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636',
  },
  {
    value: 'bound_only',
    label: '仅用绑定库',
    hint: '只检索本智能体绑定的知识库，忽略用户本轮选择与用户可访问的其他库；范围已由配置钉死，因此不再询问——绑定数超过阈值也不问。需先绑定知识库，否则检索不到内容。',
    // lock-closed
    iconPath:
      'M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z',
  },
]

const normalizedValue = computed<ScopeGateMode>(() => {
  const raw = String(props.modelValue || '').trim().toLowerCase()
  return (OPTIONS.find((option) => option.value === raw)?.value || 'inherit') as ScopeGateMode
})
// 四个选项写死在同一个数组里，一定非空；断言只是为了满足 noUncheckedIndexedAccess
const currentOption = computed<ScopeGateOption>(
  () => OPTIONS.find((option) => option.value === normalizedValue.value) ?? (OPTIONS[0] as ScopeGateOption),
)

const open = ref(false)
const triggerRef = ref<HTMLElement | null>(null)
const listRef = ref<HTMLElement | null>(null)
const menuStyle = ref<Record<string, string>>({})

const updateMenuPosition = () => {
  const el = triggerRef.value
  if (!el || typeof window === 'undefined') return
  const rect = el.getBoundingClientRect()
  const estimatedMenuHeight = 260
  const spaceBelow = window.innerHeight - rect.bottom
  // 下方空间不足且上方更宽敞时向上弹，避免浮层超出视口
  const openUpward = spaceBelow < Math.min(estimatedMenuHeight, 220) && rect.top > spaceBelow
  menuStyle.value = {
    position: 'fixed',
    left: `${Math.max(8, rect.left)}px`,
    width: `${Math.max(280, rect.width)}px`,
    zIndex: '10100',
    ...(openUpward
      ? { top: 'auto', bottom: `${Math.max(8, window.innerHeight - rect.top + 4)}px` }
      : { top: `${rect.bottom + 4}px`, bottom: 'auto' }),
  }
}

const closeMenu = () => {
  open.value = false
}

const toggleMenu = async () => {
  if (props.disabled) return
  open.value = !open.value
  if (!open.value) return
  updateMenuPosition()
  await nextTick()
  listRef.value?.focus()
}

const selectOption = (option: ScopeGateOption) => {
  emit('update:modelValue', option.value)
  closeMenu()
}

const onDocumentPointerDown = (event: MouseEvent) => {
  if (!open.value) return
  const target = event.target as Node | null
  if (triggerRef.value?.contains(target) || listRef.value?.contains(target)) return
  closeMenu()
}

const onViewportChange = () => {
  if (open.value) updateMenuPosition()
}

const onKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') {
    closeMenu()
    return
  }
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
  event.preventDefault()
  const index = OPTIONS.findIndex((option) => option.value === normalizedValue.value)
  const step = event.key === 'ArrowDown' ? 1 : -1
  const next = OPTIONS[(index + step + OPTIONS.length) % OPTIONS.length]
  if (!next) return
  emit('update:modelValue', next.value)
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentPointerDown)
  window.addEventListener('resize', onViewportChange)
  window.addEventListener('scroll', onViewportChange, true)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentPointerDown)
  window.removeEventListener('resize', onViewportChange)
  window.removeEventListener('scroll', onViewportChange, true)
})
</script>

<template>
  <div class="w-full sm:w-72">
    <button
      ref="triggerRef"
      type="button"
      class="flex w-full items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-left text-sm outline-none transition-colors hover:border-gray-400 focus:ring-2 focus:ring-primary disabled:cursor-not-allowed disabled:bg-gray-50"
      :disabled="disabled"
      role="combobox"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="toggleMenu"
      @keydown.down.prevent="toggleMenu"
    >
      <svg
        class="h-4 w-4 shrink-0 text-indigo-500"
        fill="none"
        stroke="currentColor"
        stroke-width="1.6"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path stroke-linecap="round" stroke-linejoin="round" :d="currentOption.iconPath" />
      </svg>
      <span class="min-w-0 flex-1 truncate font-medium text-gray-800">{{ currentOption.label }}</span>
      <svg
        class="h-4 w-4 shrink-0 text-gray-400 transition-transform"
        :class="open ? 'rotate-180' : ''"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
      </svg>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="listRef"
        class="max-h-80 overflow-y-auto rounded-xl border border-gray-200 bg-white py-1 shadow-xl focus:outline-none"
        :style="menuStyle"
        role="listbox"
        tabindex="-1"
        @keydown="onKeydown"
      >
        <button
          v-for="option in OPTIONS"
          :key="option.value"
          type="button"
          role="option"
          :aria-selected="option.value === normalizedValue"
          class="flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-gray-50"
          :class="option.value === normalizedValue ? 'bg-indigo-50/70' : ''"
          @click="selectOption(option)"
        >
          <svg
            class="mt-0.5 h-4 w-4 shrink-0"
            :class="option.value === normalizedValue ? 'text-indigo-600' : 'text-gray-400'"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path stroke-linecap="round" stroke-linejoin="round" :d="option.iconPath" />
          </svg>
          <span class="min-w-0 flex-1">
            <span class="flex items-center gap-1.5">
              <span class="text-xs font-semibold text-gray-900">{{ option.label }}</span>
              <svg
                v-if="option.value === normalizedValue"
                class="h-3.5 w-3.5 shrink-0 text-indigo-600"
                fill="none"
                stroke="currentColor"
                stroke-width="2.5"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            </span>
            <span class="mt-0.5 block text-[11px] leading-relaxed text-gray-500">{{ option.hint }}</span>
          </span>
        </button>
      </div>
    </Teleport>
  </div>
</template>
