<script setup lang="ts">
/**
 * 凭据选择器：绑定点共用的下拉。
 *
 * 分「我的凭据」「系统共享」两组——系统级只显示被授权的那几条，
 * 未授权的连名称都不会出现（后端 `/secrets/picker` 已经按授权过滤过）。
 *
 * 选择器**只返回凭据 id**，不返回也不缓存任何值。
 *
 * 为什么带搜索：一个人攒下几十条凭据是常态，纯列表只能逐条翻；而绑定凭据的
 * 场景里用户通常记得的是「标识」（`stripe_key`）而不是显示名。所以搜索同时匹配
 * 名称、标识和分类，多关键词按 AND 收窄，命中处高亮，并支持 ↑↓ / Enter / Esc
 * 全程不离开键盘。
 *
 * 浮层 Teleport 到 body：这些绑定弹窗都带遮罩和固定宽度，浮层留在弹窗内会被
 * 裁掉下半截，而且 z-index 要压过 `z-[100]` 的遮罩。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import axios from '../../utils/axios'
import {
  credentialMatchesScene,
  filterCredentialItems,
  highlightCredentialField,
  normalizeCredentialQuery,
} from '../../utils/credentialPicker'
import type { CredentialPickerItem } from '../../utils/credentialPicker'

const props = withDefaults(
  defineProps<{
    modelValue?: string | null
    /** 只显示启用了该场景的凭据；留空则显示全部 */
    scene?: string
    placeholder?: string
    systemOnly?: boolean
    disabled?: boolean
  }>(),
  {
    modelValue: null,
    scene: '',
    placeholder: '选择凭据',
    disabled: false,
    systemOnly: false,
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string | null): void
  (e: 'picked', item: CredentialPickerItem | null): void
}>()

const loading = ref(false)
const open = ref(false)
const query = ref('')
const activeIndex = ref(-1)

const triggerRef = ref<HTMLButtonElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
const listRef = ref<HTMLElement | null>(null)
const searchRef = ref<HTMLInputElement | null>(null)
const menuStyle = ref<Record<string, string>>({})

const mine = ref<CredentialPickerItem[]>([])
const shared = ref<CredentialPickerItem[]>([])

const all = computed(() => [...mine.value, ...shared.value])
const selected = computed(() => all.value.find((item) => item.id === props.modelValue) || null)

/**
 * 已绑定，但这条凭据**不在可选项里**了：被停用、被撤销授权、或已删除。
 *
 * 这种情况最需要「清除绑定」——留着一条永远取不到值的绑定，界面还显示
 * 「已绑定」，用户既用不了也不知道为什么。
 */
const orphaned = computed(() => !!props.modelValue && !selected.value)

const tokens = computed(() => normalizeCredentialQuery(query.value))
const searching = computed(() => tokens.value.length > 0)

/** 场景允许的全部候选（不含搜索词），用于「2 / 7」里的分母 */
const mineSceneAll = computed(() => mine.value.filter((item) => credentialMatchesScene(item, props.scene)))
const sharedSceneAll = computed(() =>
  shared.value.filter((item) => credentialMatchesScene(item, props.scene)),
)

const mineFiltered = computed(() => filterCredentialItems(mine.value, props.scene, query.value))
const sharedFiltered = computed(() => filterCredentialItems(shared.value, props.scene, query.value))
const flatFiltered = computed(() => [...mineFiltered.value, ...sharedFiltered.value])
const hasOptions = computed(() => flatFiltered.value.length > 0)

/**
 * 行数据带上全局 index 与高亮片段：分组渲染时不用再算键盘序号，
 * 也不在模板里重复调 highlight（否则每次输入都重算一遍列表）。
 */
type Row = {
  item: CredentialPickerItem
  index: number
  nameSegments: ReturnType<typeof highlightCredentialField>
  keySegments: ReturnType<typeof highlightCredentialField>
}

function toRows(items: CredentialPickerItem[], offset: number): Row[] {
  return items.map((item, index) => ({
    item,
    index: offset + index,
    nameSegments: highlightCredentialField(item.name, tokens.value),
    keySegments: highlightCredentialField(item.secret_key, tokens.value),
  }))
}

const mineRows = computed<Row[]>(() => toRows(mineFiltered.value, 0))
const sharedRows = computed<Row[]>(() => toRows(sharedFiltered.value, mineFiltered.value.length))

/**
 * 两个分组只描述数据，配色差异交给 `tint`——否则 40 行模板要原样写两遍，
 * 改一处（比如加「分类」列）必然漏掉另一处。
 */
type OptionGroup = {
  key: string
  label: string
  rows: Row[]
  total: number
  tint: 'mine' | 'shared'
}

const optionGroups = computed<OptionGroup[]>(() =>
  [
    { key: 'mine', label: '我的凭据', rows: mineRows.value, total: mineSceneAll.value.length, tint: 'mine' as const },
    { key: 'shared', label: '系统共享', rows: sharedRows.value, total: sharedSceneAll.value.length, tint: 'shared' as const },
  ].filter((group) => group.rows.length > 0),
)

function countLabel(matched: number, total: number): string {
  return searching.value ? `${matched} / ${total}` : String(matched)
}

const emptyText = computed(() => {
  if (searching.value) return `没有匹配「${query.value.trim()}」的凭据，换个关键词试试。`
  return '没有可选的凭据。先到「个人中心 → 我的凭据」建一条，或确认它启用了当前使用场景。'
})

async function load() {
  loading.value = true
  try {
    const res = await axios.get('/api/portal/secrets/picker', { params: props.scene === 'script_env' ? { scene: 'script_env' } : {} })
    const groups = res.data?.data?.groups || {}
    mine.value = props.systemOnly ? [] : (groups.mine || [])
    shared.value = groups.shared || []
  } catch (e) {
    console.error('加载凭据列表失败', e)
  } finally {
    loading.value = false
  }
}

function updateMenuPosition() {
  const el = triggerRef.value
  if (!el || typeof window === 'undefined') return
  const rect = el.getBoundingClientRect()
  const width = Math.max(rect.width, 240)
  const estimatedHeight = menuRef.value?.offsetHeight || 320
  const spaceBelow = window.innerHeight - rect.bottom - 8
  // 下方放不下整块、且上方更宽敞时向上弹，避免浮层顶到视口外
  const openUpward = spaceBelow < estimatedHeight && rect.top > spaceBelow
  const available = openUpward ? rect.top - 12 : spaceBelow
  // 硬上限：宁可让列表内部滚动，也不让浮层伸到视口外
  const maxHeight = Math.max(
    180,
    Math.min(Math.round(window.innerHeight * 0.7), available),
  )
  menuStyle.value = {
    position: 'fixed',
    left: `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`,
    width: `${width}px`,
    maxHeight: `${maxHeight}px`,
    zIndex: '10100',
    ...(openUpward
      ? { top: 'auto', bottom: `${Math.max(8, window.innerHeight - rect.top + 4)}px` }
      : { top: `${rect.bottom + 4}px`, bottom: 'auto' }),
  }
}

function scrollActiveIntoView() {
  void nextTick(() => {
    const row = listRef.value?.querySelector<HTMLElement>(`[data-row-index="${activeIndex.value}"]`)
    row?.scrollIntoView({ block: 'nearest' })
  })
}

function close() {
  open.value = false
  query.value = ''
  activeIndex.value = -1
}

async function openMenu() {
  if (props.disabled) return
  query.value = ''
  activeIndex.value = flatFiltered.value.length ? 0 : -1
  open.value = true
  updateMenuPosition()
  await nextTick()
  updateMenuPosition()
  searchRef.value?.focus()
  scrollActiveIntoView()
}

function toggle() {
  if (open.value) close()
  else void openMenu()
}

function choose(item: CredentialPickerItem | null) {
  emit('update:modelValue', item ? item.id : null)
  emit('picked', item)
  close()
  triggerRef.value?.focus()
}

function moveActive(delta: number) {
  const total = flatFiltered.value.length
  if (!total) return
  const current = activeIndex.value
  activeIndex.value = current < 0 ? (delta > 0 ? 0 : total - 1) : (current + delta + total) % total
  scrollActiveIntoView()
}

function confirmActive() {
  const item = flatFiltered.value[activeIndex.value]
  if (item) choose(item)
}

function onTriggerKeydown(event: KeyboardEvent) {
  if (props.disabled) return
  if (open.value) return
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    void openMenu()
  }
}

function onSearchKeydown(event: KeyboardEvent) {
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
    close()
    triggerRef.value?.focus()
  }
}

function onDocumentPointerDown(event: MouseEvent) {
  if (!open.value) return
  const target = event.target as Node | null
  if (target && (triggerRef.value?.contains(target) || menuRef.value?.contains(target))) return
  close()
}

function onViewportChange(event?: Event) {
  if (!open.value) return
  // 浮层内部滚动不重新定位，否则列表滚动会抖
  const target = (event?.target || null) as Node | null
  if (target && menuRef.value?.contains(target)) return
  updateMenuPosition()
}

// 搜索词变化后回到第一条：用户打完字按 Enter 就该选中最匹配的那条
watch(query, () => {
  activeIndex.value = flatFiltered.value.length ? 0 : -1
  scrollActiveIntoView()
})

watch(
  () => props.modelValue,
  () => {
    if (open.value) close()
  },
)

watch(
  () => props.disabled,
  (value) => {
    if (value && open.value) close()
  },
)

onMounted(() => {
  void load()
  document.addEventListener('mousedown', onDocumentPointerDown)
  window.addEventListener('resize', onViewportChange)
  window.addEventListener('scroll', onViewportChange, true)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentPointerDown)
  window.removeEventListener('resize', onViewportChange)
  window.removeEventListener('scroll', onViewportChange, true)
})

defineExpose({ reload: load })
</script>

<template>
  <div>
    <button
      ref="triggerRef"
      type="button"
      role="combobox"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :disabled="disabled"
      class="flex w-full items-center justify-between gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-left text-sm text-gray-700 hover:border-blue-400 focus:border-blue-500 focus:outline-none disabled:cursor-not-allowed disabled:bg-gray-50"
      @click="toggle"
      @keydown="onTriggerKeydown"
    >
      <span v-if="selected" class="flex min-w-0 items-center gap-2">
        <span class="truncate font-medium text-gray-900">{{ selected.name }}</span>
        <code class="truncate rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600">{{ selected.secret_key }}</code>
      </span>
      <span v-else class="text-gray-400">{{ placeholder }}</span>
      <svg class="h-4 w-4 shrink-0 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
      </svg>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="menuRef"
        :style="menuStyle"
        class="flex max-h-[70vh] flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl"
      >
        <div class="shrink-0 border-b border-gray-100 p-2">
          <div class="relative">
            <svg
              class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-4.35-4.35M17 10a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              ref="searchRef"
              v-model="query"
              type="search"
              autocomplete="off"
              placeholder="搜索名称、标识或分类"
              aria-label="搜索凭据"
              class="w-full rounded-lg border border-gray-200 py-1.5 pl-8 pr-3 text-xs text-gray-700 outline-none placeholder:text-gray-400 focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              @keydown="onSearchKeydown"
            />
          </div>
          <p v-if="searching" class="mt-1 px-0.5 text-[10px] text-gray-400">
            {{ flatFiltered.length }} 条匹配 · ↑↓ 选择 · Enter 选中 · Esc 关闭
          </p>
        </div>

        <div ref="listRef" role="listbox" class="max-h-72 min-h-0 flex-1 overflow-y-auto p-1.5">
          <p v-if="loading" class="px-3 py-2 text-xs text-gray-400">加载中…</p>

          <template v-else>
            <div v-for="group in optionGroups" :key="group.key" class="mb-1 last:mb-0">
              <p class="px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                {{ group.label }} · {{ countLabel(group.rows.length, group.total) }}
              </p>
              <button
                v-for="row in group.rows"
                :key="row.item.id"
                type="button"
                role="option"
                :aria-selected="row.item.id === modelValue"
                :data-row-index="row.index"
                class="flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left"
                :class="
                  row.index === activeIndex
                    ? group.tint === 'mine'
                      ? 'bg-blue-50'
                      : 'bg-emerald-50'
                    : group.tint === 'mine'
                      ? 'hover:bg-blue-50'
                      : 'hover:bg-emerald-50'
                "
                @mouseenter="activeIndex = row.index"
                @click="choose(row.item)"
              >
                <span
                  class="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md text-[10px] font-bold"
                  :class="group.tint === 'mine' ? 'bg-blue-100 text-blue-700' : 'bg-emerald-100 text-emerald-700'"
                >
                  {{ row.item.secret_type === 'variable' ? '变' : '凭' }}
                </span>
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-sm text-gray-900">
                    <span
                      v-for="(segment, i) in row.nameSegments"
                      :key="i"
                      :class="segment.hit ? 'rounded bg-amber-100 px-0.5 text-amber-900' : ''"
                    >{{ segment.text }}</span>
                  </span>
                  <span class="block truncate text-[11px] text-gray-500">
                    <span
                      v-for="(segment, i) in row.keySegments"
                      :key="i"
                      :class="segment.hit ? 'rounded bg-amber-100 px-0.5 text-amber-900' : ''"
                    >{{ segment.text }}</span>
                  </span>
                </span>
                <svg
                  v-if="row.item.id === modelValue"
                  class="mt-0.5 h-4 w-4 shrink-0"
                  :class="group.tint === 'mine' ? 'text-blue-600' : 'text-emerald-600'"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
                </svg>
              </button>
            </div>

            <p v-if="!hasOptions" class="px-3 py-6 text-center text-xs text-gray-400">
              {{ emptyText }}
            </p>

            <p v-if="orphaned" class="px-2 py-1.5 text-[11px] leading-relaxed text-amber-700">
              当前绑定（{{ modelValue }}）已不在可选列表里——可能被停用、撤销授权或删除。
              用它调用会失败，建议解除绑定后重选。
            </p>
          </template>
        </div>

        <div v-if="selected || orphaned" class="shrink-0 border-t border-gray-100 p-1.5">
          <button
            type="button"
            class="w-full rounded-lg px-2 py-1.5 text-left text-xs text-rose-600 hover:bg-rose-50"
            @click="choose(null)"
          >
            清除绑定
          </button>
        </div>
      </div>
    </Teleport>
  </div>
</template>
