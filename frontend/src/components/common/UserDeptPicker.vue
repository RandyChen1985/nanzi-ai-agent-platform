<script setup lang="ts">
/**
 * 选用户 / 选部门。
 *
 * 从「凭据中心 → 授权」里抽出来的：那里原来要求手填用户 ID 或部门编码，
 * 管理员得先去别处查「7 是谁」「部门编码是什么」，输错了还会安静地授权给一个
 * 不存在的对象——被授权者永远看不到，管理员以为授权好了。
 *
 * **有意和 Roles.vue / DepartmentManagement.vue 的那两个弹窗不同**：那两个是
 * 分页浏览 + 跨页全选 + 复合筛选（排除已在角色/部门的、仅未分配部门的、含子部门），
 * 核心价值是「在几千人里批量操作」。这里只解决轻量场景：搜一下、勾几个。硬套同一个
 * 组件会让两边都为对方的特例付出复杂度，所以没有合并。
 *
 * ``v-model`` 是 ``'user:7'`` / ``'dept:3'`` 这样的带前缀 key：类型和 id 一起传，
 * 调用方不需要再单独维护一个 grantee_type。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import axios from '../../utils/axios'

const props = withDefaults(defineProps<{
  modelValue: string[]
  /** 只允许选一种类型时不显示切换 */
  mode?: 'both' | 'user' | 'dept'
  /** 已经存在的对象：显示为不可选，避免重复添加 */
  disabledKeys?: string[]
  /** 空态文案 */
  emptyHint?: string
}>(), {
  mode: 'both',
  disabledKeys: () => [],
  emptyHint: '',
})

const emit = defineEmits<{ (e: 'update:modelValue', value: string[]): void }>()

type Candidate = { id: string; label: string; sub: string; depth?: number }

const activeType = ref<'user' | 'dept'>(props.mode === 'dept' ? 'dept' : 'user')
const keyword = ref('')
const candidates = ref<Candidate[]>([])
const loading = ref(false)

const disabledSet = computed(() => new Set(props.disabledKeys))
const selectedSet = computed(() => new Set(props.modelValue))

const keyOf = (candidate: Candidate) => `${activeType.value}:${candidate.id}`
const isDisabled = (candidate: Candidate) => disabledSet.value.has(keyOf(candidate))
const isSelected = (candidate: Candidate) => selectedSet.value.has(keyOf(candidate))

async function load() {
  loading.value = true
  try {
    if (activeType.value === 'user') {
      const res = await axios.get('/api/portal/management/users', {
        params: { search: keyword.value.trim() || undefined, page: 1, size: 30 },
      })
      candidates.value = (res.data?.items || []).map((user: any) => ({
        id: String(user.id),
        label: user.real_name || user.user_name || String(user.id),
        sub: [user.user_name, user.dept_name, user.role === 'admin' ? '管理员' : '']
          .filter(Boolean)
          .join(' · '),
      }))
    } else {
      const res = await axios.get('/api/portal/departments/tree', {
        params: { include_disabled: true },
      })
      const flat: Candidate[] = []
      const walk = (nodes: any[], depth: number) => {
        for (const node of nodes || []) {
          flat.push({
            id: String(node.id),
            label: node.name || String(node.id),
            sub: node.path_name || node.code || '',
            depth,
          })
          walk(node.children || [], depth + 1)
        }
      }
      walk(res.data?.items || [], 0)
      // 部门树规模有限，拉全量后本地过滤比每敲一个字打一次请求快
      const kw = keyword.value.trim().toLowerCase()
      candidates.value = kw
        ? flat.filter((item) => `${item.label} ${item.sub}`.toLowerCase().includes(kw))
        : flat
    }
  } catch {
    candidates.value = []
  } finally {
    loading.value = false
  }
}

let timer: ReturnType<typeof setTimeout> | null = null
function onInput() {
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => void load(), 250)
}

function switchType(next: 'user' | 'dept') {
  if (activeType.value === next) return
  activeType.value = next
  keyword.value = ''
  // 切换类型必须清空：提交时只能有一个 grantee_type，混着两种 key 会一半失败
  emit('update:modelValue', [])
  void load()
}

function toggle(candidate: Candidate) {
  if (isDisabled(candidate)) return
  const key = keyOf(candidate)
  const next = props.modelValue.includes(key)
    ? props.modelValue.filter((item) => item !== key)
    : [...props.modelValue, key]
  emit('update:modelValue', next)
}

watch(() => props.mode, (mode) => {
  if (mode !== 'both') {
    activeType.value = mode
    void load()
  }
})

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})

void load()
</script>

<template>
  <div>
    <div class="flex flex-wrap items-center gap-2">
      <div v-if="mode === 'both'" class="inline-flex rounded-lg bg-gray-100 p-1">
        <button
          type="button"
          class="rounded-md px-2.5 py-1 text-xs"
          :class="activeType === 'user' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'"
          @click="switchType('user')"
        >用户</button>
        <button
          type="button"
          class="rounded-md px-2.5 py-1 text-xs"
          :class="activeType === 'dept' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'"
          @click="switchType('dept')"
        >部门</button>
      </div>
      <input
        v-model="keyword"
        type="search"
        :placeholder="activeType === 'user' ? '搜索姓名 / 账号 / 邮箱' : '搜索部门名称'"
        class="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        @input="onInput"
      />
    </div>

    <div class="mt-2 max-h-64 overflow-y-auto rounded-lg border border-gray-200">
      <p v-if="loading" class="px-3 py-6 text-center text-xs text-gray-400">加载中…</p>
      <p v-else-if="!candidates.length" class="px-3 py-6 text-center text-xs text-gray-400">
        {{ emptyHint || (activeType === 'user' ? '没有匹配的用户' : '没有匹配的部门') }}
      </p>
      <button
        v-for="candidate in candidates"
        :key="keyOf(candidate)"
        type="button"
        :disabled="isDisabled(candidate)"
        class="flex w-full items-center gap-2.5 border-b border-gray-100 px-3 py-2 text-left last:border-b-0 disabled:cursor-not-allowed disabled:bg-gray-50"
        :class="!isDisabled(candidate) && isSelected(candidate) ? 'bg-blue-50' : ''"
        @click="toggle(candidate)"
      >
        <span
          class="flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] text-white"
          :class="isDisabled(candidate)
            ? 'border-gray-300 bg-gray-300'
            : (isSelected(candidate) ? 'border-blue-600 bg-blue-600' : 'border-gray-300 bg-white')"
        >{{ isDisabled(candidate) ? '·' : (isSelected(candidate) ? '✓' : '') }}</span>
        <span class="min-w-0 flex-1">
          <span
            class="block text-xs font-medium"
            :class="isDisabled(candidate) ? 'text-gray-400' : 'text-gray-900'"
            :style="candidate.depth ? { paddingLeft: `${candidate.depth * 12}px` } : undefined"
          >{{ candidate.label }}</span>
          <span v-if="candidate.sub" class="block truncate text-[11px] text-gray-400">{{ candidate.sub }}</span>
        </span>
        <span v-if="isDisabled(candidate)" class="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">
          已添加
        </span>
      </button>
    </div>

    <p v-if="modelValue.length" class="mt-1.5 text-[11px] text-gray-500">
      已选 {{ modelValue.length }} 个
    </p>
    <slot name="hint" />
  </div>
</template>
