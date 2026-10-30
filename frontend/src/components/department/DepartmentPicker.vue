<template>
  <div class="relative" data-department-picker>
    <button
      type="button"
      class="flex w-full items-center justify-between rounded-lg border border-gray-300 bg-white px-4 py-2 text-left text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
      :disabled="disabled"
      @click="toggle"
    >
      <span class="truncate" :class="selected.length ? 'text-gray-900 dark:text-gray-100' : 'text-gray-400'">
        {{ summary }}
      </span>
      <svg class="h-4 w-4 flex-shrink-0 text-gray-400 transition-transform" :class="open ? 'rotate-180' : ''" viewBox="0 0 20 20" fill="currentColor">
        <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.17l3.71-3.94a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
      </svg>
    </button>

    <div
      v-if="open"
      ref="panelRef"
      class="absolute z-[12000] mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-gray-200 bg-white p-1 shadow-xl dark:border-gray-700 dark:bg-gray-900"
    >
      <div v-if="loading" class="px-3 py-4 text-center text-xs text-gray-400">部门加载中…</div>
      <div v-else-if="loadError" class="px-3 py-4 text-center text-xs text-red-500">{{ loadError }}</div>
      <div v-else-if="!flat.length" class="px-3 py-4 text-center text-xs text-gray-400">
        还没有可选的部门
      </div>
      <template v-else>
        <div class="sticky top-0 z-10 -mx-1 -mt-1 bg-white px-1 pb-1.5 pt-1 dark:bg-gray-900">
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索部门名称或代码"
            data-department-search
            class="w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-xs text-gray-900 placeholder:text-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            @input="onSearchInput"
            @keydown.esc.stop="keyword = ''"
          />
        </div>
        <div v-if="!filtered.length" class="px-3 py-3 text-center text-xs text-gray-400">
          没有匹配「{{ keyword.trim() }}」的部门
        </div>
        <div
          v-for="row in filtered"
          :key="row.node.id"
          class="flex items-center gap-2 rounded px-2 py-1 text-xs hover:bg-gray-50 dark:hover:bg-gray-800"
        >
          <input
            :id="`dept-pick-${row.node.id}`"
            type="checkbox"
            :checked="selected.includes(row.node.id)"
            :disabled="disabled || row.node.status === 0"
            class="h-3.5 w-3.5 flex-shrink-0 rounded border-gray-300"
            @change="toggleDept(row.node)"
          />
          <label
            :for="`dept-pick-${row.node.id}`"
            class="min-w-0 flex-1 cursor-pointer truncate text-gray-700 dark:text-gray-200"
            :style="{ paddingLeft: isSearching ? '0' : `${row.depth * 12}px` }"
            :title="row.path.join(' / ')"
          >
            <span>{{ row.node.name }}</span>
            <span
              v-if="isSearching && row.path.length > 1"
              class="ml-1.5 text-[11px] text-gray-400 dark:text-gray-500"
            >{{ row.path.slice(0, -1).join(' / ') }}</span>
          </label>
          <span v-if="row.node.status === 0" class="text-[11px] text-amber-500">停用</span>
          <button
            v-if="selected.includes(row.node.id)"
            type="button"
            class="flex-shrink-0 rounded px-1 text-[11px] font-bold"
            :class="primaryId === row.node.id ? 'bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-300' : 'text-gray-400 hover:text-gray-600'"
            title="设为主部门（决定部门代码与数据过滤口径）"
            @click.stop="setPrimary(row.node.id)"
          >主</button>
        </div>
      </template>
    </div>

    <p v-if="!disabled" class="mt-1 text-[11px] leading-relaxed text-gray-400">
      可多选。带「主」标记的是主部门，决定该用户的部门代码与数据权限口径；未指定时取第一个。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import axios from '../../utils/axios'
import type { DeptNode } from './DepartmentTree.vue'

/**
 * 用户侧的部门选择器：树形多选 + 主部门单选。
 *
 * 替代原先「部门代码」「组织全路径」两个自由输入框——那两个框没有任何校验，
 * 随手就能造出 `DEPT001` 与 `dept001` 两种"同一个部门"，还会被 SQL 行级过滤当真。
 * 现在 dept_code / org_path 由后端按主部门派生，前端只提交部门集合。
 */
const props = withDefaults(
  defineProps<{
    modelValue?: number[]
    primaryId?: number | null
    disabled?: boolean
  }>(),
  { modelValue: () => [], primaryId: null, disabled: false }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: number[]): void
  (e: 'update:primaryId', value: number | null): void
}>()

const open = ref(false)
const loading = ref(false)
const loadError = ref('')
const tree = ref<DeptNode[]>([])
const keyword = ref('')
const panelRef = ref<HTMLElement | null>(null)

const selected = computed(() => props.modelValue || [])

const flat = computed(() => {
  const rows: { node: DeptNode; depth: number; path: string[] }[] = []
  const walk = (nodes: DeptNode[], depth: number, parents: string[]) => {
    for (const node of nodes) {
      const path = [...parents, node.name]
      rows.push({ node, depth, path })
      if (node.children?.length) walk(node.children, depth + 1, path)
    }
  }
  walk(tree.value, 0, [])
  return rows
})

const isSearching = computed(() => keyword.value.trim().length > 0)

/**
 * 部门数量可能上百，靠缩进一层层找太慢。
 * 搜索时把缩进去掉、改成在名称后补灰色父级路径——层级信息不丢，
 * 又不会被深度撑到右侧看不见。
 */
const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return flat.value
  return flat.value.filter((row) => {
    const name = (row.node.name || '').toLowerCase()
    const code = (row.node.code || '').toLowerCase()
    return name.includes(kw) || code.includes(kw)
  })
})

const summary = computed(() => {
  if (!selected.value.length) return '未选择部门'
  const names = selected.value
    .map((id) => flat.value.find((row) => row.node.id === id)?.node.name)
    .filter(Boolean) as string[]
  if (!names.length) return `已选 ${selected.value.length} 个部门`
  const primaryName = props.primaryId
    ? flat.value.find((row) => row.node.id === props.primaryId)?.node.name
    : null
  const head = names.slice(0, 2).join('、')
  const more = names.length > 2 ? ` +${names.length - 2}` : ''
  return primaryName ? `${head}${more}（主：${primaryName}）` : `${head}${more}`
})

const loadTree = async () => {
  if (tree.value.length) return
  loading.value = true
  loadError.value = ''
  try {
    const response = await axios.get('/api/portal/departments/tree', {
      params: { include_disabled: true }
    })
    tree.value = response.data.items || []
  } catch (e: any) {
    loadError.value = e?.response?.status === 403 ? '没有查看部门的权限' : '部门加载失败'
  } finally {
    loading.value = false
  }
}

const toggle = () => {
  open.value = !open.value
  if (open.value) {
    // 每次打开都重置关键词：否则上次搜过的词还在，列表看起来像"部门没了"。
    keyword.value = ''
    loadTree()
  }
}

const onSearchInput = () => {
  // 过滤后条目变少，原来的滚动位置可能落在空白处，看起来像"搜不到"。
  panelRef.value?.scrollTo({ top: 0 })
}

const toggleDept = (node: DeptNode) => {
  if (props.disabled || node.status === 0) return
  const current = [...selected.value]
  const index = current.indexOf(node.id)
  let next: number[]
  if (index >= 0) {
    next = current.filter((id) => id !== node.id)
  } else {
    next = [...current, node.id]
  }
  emit('update:modelValue', next)

  // 主部门必须始终是已选部门之一：取消勾选主部门时改选第一个，清空则置空
  if (props.primaryId !== null && !next.includes(props.primaryId)) {
    emit('update:primaryId', next[0] ?? null)
  } else if (props.primaryId === null && next.length) {
    emit('update:primaryId', next[0] ?? null)
  }
}

const setPrimary = (id: number) => {
  if (!selected.value.includes(id)) return
  emit('update:primaryId', id)
}

const onDocumentClick = (event: MouseEvent) => {
  const target = event.target as HTMLElement
  if (!target.closest('[data-department-picker]')) open.value = false
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onUnmounted(() => document.removeEventListener('click', onDocumentClick))
</script>
