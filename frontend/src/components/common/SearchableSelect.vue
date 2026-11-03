<script setup lang="ts">
/**
 * 可搜索下拉：用于实体关系的源表 / 目标表 / 关联字段选择。
 *
 * 原生 <select> 无法搜索，表和字段一多只能逐条翻；这里沿用 ModelSelect 的
 * 「触发器 + Teleport 到 body 的浮层」结构，避免被弹窗的 overflow-hidden 裁掉，
 * 同时补齐分组标题、空选项与键盘导航，保持与业务无关，其它表单可直接复用。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

export type SearchableSelectOption = {
  value: string | number
  label: string
  /** 只参与搜索、不展示的补充关键词，例如中文业务名、物理字段名 */
  keywords?: string[]
}

export type SearchableSelectGroup = {
  /** 分组标题；为空时不渲染标题 */
  label?: string
  options: SearchableSelectOption[]
}

const props = withDefaults(defineProps<{
  modelValue: string | number | null | undefined
  groups?: SearchableSelectGroup[]
  placeholder?: string
  disabled?: boolean
  /** 是否提供「清空 / 未选择」的空选项（关联字段需要） */
  allowEmpty?: boolean
  emptyLabel?: string
  /** 选中空选项时 emit 的值；字段场景传 ''，避免父组件的 string 绑定出现 null */
  emptyValue?: string | number | null
  searchPlaceholder?: string
  /** 没有任何候选时的文案 */
  emptyText?: string
  ariaLabel?: string
  /** md 对齐表选择框，sm 对齐关联字段选择框 */
  size?: 'md' | 'sm'
}>(), {
  groups: () => [],
  placeholder: '请选择...',
  disabled: false,
  allowEmpty: false,
  emptyLabel: '-- 请选择 --',
  emptyValue: null,
  searchPlaceholder: '搜索...',
  emptyText: '暂无可选项',
  ariaLabel: undefined,
  size: 'md',
})

const emit = defineEmits<{ 'update:modelValue': [value: string | number | null] }>()

const open = ref(false)
const query = ref('')
const activeIndex = ref(0)
const triggerRef = ref<HTMLButtonElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
const searchRef = ref<HTMLInputElement | null>(null)
const listRef = ref<HTMLElement | null>(null)
const menuStyle = ref<Record<string, string>>({})

const allOptions = computed<SearchableSelectOption[]>(() =>
  props.groups.flatMap((group) => group.options),
)

const selectedOption = computed(
  () => allOptions.value.find((option) => option.value === props.modelValue) || null,
)
const isEmptySelected = computed(() => selectedOption.value === null)
const currentLabel = computed(() => {
  if (selectedOption.value) return selectedOption.value.label
  return props.allowEmpty ? props.emptyLabel : props.placeholder
})

const hasAnyOption = computed(() =>
  props.groups.some((group) => group.options.length > 0),
)
const showEmptyOption = computed(() => props.allowEmpty && hasAnyOption.value)

function matchesQuery(option: SearchableSelectOption, keyword: string): boolean {
  const haystacks = [option.label, ...(option.keywords || [])]
  return haystacks.some((text) => text.toLowerCase().includes(keyword))
}

/** 过滤后的分组；每项带上全局 index，避免分组渲染时再算键盘序号 */
const filteredGroups = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  let cursor = showEmptyOption.value ? 1 : 0
  return props.groups
    .map((group) => {
      const options = group.options
        .filter((option) => !keyword || matchesQuery(option, keyword))
        .map((option) => ({ ...option, index: cursor++ }))
      return { label: group.label, options }
    })
    .filter((group) => group.options.length > 0)
})

const flatOptions = computed(() =>
  filteredGroups.value.flatMap((group) => group.options),
)
const optionCount = computed(
  () => flatOptions.value.length + (showEmptyOption.value ? 1 : 0),
)
const hasNoOptions = computed(() => {
  if (filteredGroups.value.length > 0) return false
  // 有搜索词时优先告诉用户「没搜到」，而不是列为空时的「暂无可选项」
  return query.value.trim().length > 0 || !showEmptyOption.value
})
const selectionIndex = computed(() => {
  const index = flatOptions.value.findIndex(
    (option) => option.value === props.modelValue,
  )
  return index < 0 ? -1 : index + (showEmptyOption.value ? 1 : 0)
})

const updateMenuPosition = () => {
  const el = triggerRef.value
  if (!el || typeof window === 'undefined') return
  const rect = el.getBoundingClientRect()
  const estimatedMenuHeight = 320
  const spaceBelow = window.innerHeight - rect.bottom
  // 下方空间不足且上方更宽敞时向上弹，避免浮层贴到视口外
  const openUpward = spaceBelow < Math.min(estimatedMenuHeight, 224) && rect.top > spaceBelow
  menuStyle.value = {
    position: 'fixed',
    left: `${Math.max(8, rect.left)}px`,
    width: `${rect.width}px`,
    zIndex: '10100',
    ...(openUpward
      ? { top: 'auto', bottom: `${Math.max(8, window.innerHeight - rect.top + 4)}px` }
      : { top: `${rect.bottom + 4}px`, bottom: 'auto' }),
  }
}

const scrollActiveIntoView = () => {
  void nextTick(() => {
    const item = listRef.value?.querySelector<HTMLElement>(
      `[data-option-index="${activeIndex.value}"]`,
    )
    item?.scrollIntoView({ block: 'nearest' })
  })
}

const closeMenu = () => {
  open.value = false
  query.value = ''
}

const openMenu = async () => {
  if (props.disabled) return
  query.value = ''
  open.value = true
  updateMenuPosition()
  await nextTick()
  activeIndex.value = selectionIndex.value >= 0 ? selectionIndex.value : 0
  searchRef.value?.focus()
  scrollActiveIntoView()
}

const toggleMenu = () => {
  if (open.value) closeMenu()
  else void openMenu()
}

const selectOption = (option: SearchableSelectOption) => {
  emit('update:modelValue', option.value)
  closeMenu()
  triggerRef.value?.focus()
}

const selectEmpty = () => {
  emit('update:modelValue', props.emptyValue)
  closeMenu()
  triggerRef.value?.focus()
}

const moveActive = (delta: number) => {
  const total = optionCount.value
  if (!total) return
  activeIndex.value = (activeIndex.value + delta + total) % total
  scrollActiveIntoView()
}

const confirmActive = () => {
  if (showEmptyOption.value && activeIndex.value === 0) {
    selectEmpty()
    return
  }
  const option = flatOptions.value.find((item) => item.index === activeIndex.value)
  if (option) selectOption(option)
}

const onTriggerKeydown = (event: KeyboardEvent) => {
  if (props.disabled) return
  if (!open.value) {
    if (
      event.key === 'ArrowDown' ||
      event.key === 'ArrowUp' ||
      event.key === 'Enter' ||
      event.key === ' '
    ) {
      event.preventDefault()
      void openMenu()
    }
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    moveActive(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    moveActive(-1)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    confirmActive()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    closeMenu()
  }
}

const onSearchKeydown = (event: KeyboardEvent) => {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    moveActive(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    moveActive(-1)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    confirmActive()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    closeMenu()
    triggerRef.value?.focus()
  }
}

const onDocumentPointerDown = (event: MouseEvent) => {
  if (!open.value) return
  const target = event.target as Node | null
  if (target && (triggerRef.value?.contains(target) || menuRef.value?.contains(target))) return
  closeMenu()
}

const onViewportChange = (event?: Event) => {
  if (!open.value) return
  const target = (event?.target || null) as Node | null
  // 浮层内部滚动不需要重新定位，否则列表滚动会抖
  if (target && menuRef.value?.contains(target)) return
  updateMenuPosition()
}

watch(
  () => props.modelValue,
  () => {
    if (open.value) closeMenu()
  },
)
watch(
  () => props.disabled,
  (value) => {
    if (value && open.value) closeMenu()
  },
)

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
  <div class="relative">
    <button
      ref="triggerRef"
      type="button"
      role="combobox"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :aria-label="ariaLabel"
      :disabled="disabled"
      class="flex w-full items-center justify-between gap-2 border bg-white text-left outline-none transition-colors disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400"
      :class="[
        size === 'sm'
          ? 'rounded px-2 py-1 text-xs'
          : 'rounded-lg px-3 py-2 text-sm',
        open
          ? 'border-gray-300 ring-2 ring-primary/20'
          : 'hover:border-gray-300',
        size === 'sm' ? 'border-gray-200' : 'border-gray-300',
      ]"
      @click="toggleMenu"
      @keydown="onTriggerKeydown"
    >
      <span class="truncate" :class="isEmptySelected ? 'text-gray-400' : 'text-gray-900'">
        {{ currentLabel }}
      </span>
      <svg
        class="flex-shrink-0 text-gray-400 transition-transform duration-200"
        :class="[size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4', { 'rotate-180': open }]"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
      </svg>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="menuRef"
        :style="menuStyle"
        class="flex max-h-80 flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl"
      >
        <div class="border-b border-gray-100 p-2">
          <input
            ref="searchRef"
            v-model="query"
            type="text"
            autocomplete="off"
            :placeholder="searchPlaceholder"
            class="w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs text-gray-700 outline-none focus:ring-2 focus:ring-primary/20"
            @keydown="onSearchKeydown"
          />
        </div>
        <div ref="listRef" role="listbox" class="max-h-64 overflow-y-auto p-1.5">
          <button
            v-if="showEmptyOption"
            type="button"
            role="option"
            :aria-selected="isEmptySelected"
            data-option-index="0"
            class="flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-xs transition-colors"
            :class="
              activeIndex === 0
                ? 'bg-primary/10 text-primary'
                : 'text-gray-700 hover:bg-gray-50'
            "
            @mouseenter="activeIndex = 0"
            @click="selectEmpty"
          >
            <span class="truncate">{{ emptyLabel }}</span>
            <svg
              v-if="isEmptySelected"
              class="h-3.5 w-3.5 flex-shrink-0 text-primary"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
            </svg>
          </button>

          <div v-for="(group, groupIndex) in filteredGroups" :key="group.label || groupIndex">
            <p
              v-if="group.label"
              class="px-2.5 pb-1 pt-2 text-[10px] font-bold uppercase tracking-wide text-gray-400"
            >
              {{ group.label }}
            </p>
            <button
              v-for="option in group.options"
              :key="option.value"
              type="button"
              role="option"
              :aria-selected="option.value === modelValue"
              :data-option-index="option.index"
              class="flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-xs transition-colors"
              :class="[
                option.index === activeIndex
                  ? 'bg-primary/10 text-primary'
                  : 'text-gray-700 hover:bg-gray-50',
                option.value === modelValue ? 'font-semibold' : '',
              ]"
              @mouseenter="activeIndex = option.index"
              @click="selectOption(option)"
            >
              <span class="truncate">{{ option.label }}</span>
              <svg
                v-if="option.value === modelValue"
                class="h-3.5 w-3.5 flex-shrink-0 text-primary"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
              </svg>
            </button>
          </div>

          <p v-if="hasNoOptions" class="px-2.5 py-6 text-center text-xs text-gray-400">
            {{ query.trim() ? '无匹配项' : emptyText }}
          </p>
        </div>
      </div>
    </Teleport>
  </div>
</template>
