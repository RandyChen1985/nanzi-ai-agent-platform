<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import axios from '@/utils/axios'
import Modal from '../Modal.vue'
import {
  groupRedisKeys,
  getRedisKeyCategoryLabel,
  type RedisKeyGroupMode,
  type RedisKeyListItem,
} from '../../utils/redisKeyGroups'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  close: []
  deleted: [payload: { deletedCount: number; message: string }]
}>()

const loading = ref(false)
const deleting = ref(false)
const keys = ref<RedisKeyListItem[]>([])
const groupMode = ref<RedisKeyGroupMode>('redis_type')
const searchKeyword = ref('')
const selectedKeys = ref<Set<string>>(new Set())
const expandedGroups = ref<Set<string>>(new Set())
const loadError = ref('')
const detailKey = ref<string | null>(null)
const keyDetail = ref<{ name: string; type: string; ttl: number; value: any } | null>(null)
const detailLoading = ref(false)

const formatRedisValue = (value: any): string => {
  if (value === null || value === undefined) return '(null)'
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const formatTtl = (ttl: number) => {
  if (ttl === -1) return '永不过期'
  if (ttl === -2) return '已过期'
  return `${ttl} 秒`
}

const fetchKeyDetail = async (key: string) => {
  if (detailKey.value === key && keyDetail.value) {
    detailKey.value = null
    keyDetail.value = null
    return
  }

  detailKey.value = key
  keyDetail.value = null
  detailLoading.value = true
  try {
    const res = await axios.get('/api/portal/system/redis/key-detail', { params: { key } })
    keyDetail.value = res.data
  } catch (e: any) {
    loadError.value = e.response?.data?.detail || e.message || '获取 Key 详情失败'
    detailKey.value = null
  } finally {
    detailLoading.value = false
  }
}

const filteredKeys = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase()
  if (!keyword) return keys.value
  return keys.value.filter((item) => item.name.toLowerCase().includes(keyword))
})

const groupedKeys = computed(() => groupRedisKeys(filteredKeys.value, groupMode.value))

const selectedCount = computed(() => selectedKeys.value.size)

const isGroupFullySelected = (groupId: string) => {
  const group = groupedKeys.value.find((item) => item.id === groupId)
  if (!group || group.keys.length === 0) return false
  return group.keys.every((item) => selectedKeys.value.has(item.name))
}

const isGroupPartiallySelected = (groupId: string) => {
  const group = groupedKeys.value.find((item) => item.id === groupId)
  if (!group || group.keys.length === 0) return false
  const selectedInGroup = group.keys.filter((item) => selectedKeys.value.has(item.name)).length
  return selectedInGroup > 0 && selectedInGroup < group.keys.length
}

const toggleGroup = (groupId: string, checked: boolean) => {
  const group = groupedKeys.value.find((item) => item.id === groupId)
  if (!group) return
  const next = new Set(selectedKeys.value)
  for (const item of group.keys) {
    if (checked) next.add(item.name)
    else next.delete(item.name)
  }
  selectedKeys.value = next
}

const toggleKey = (key: string, checked: boolean) => {
  const next = new Set(selectedKeys.value)
  if (checked) next.add(key)
  else next.delete(key)
  selectedKeys.value = next
}

const toggleExpanded = (groupId: string) => {
  const next = new Set(expandedGroups.value)
  if (next.has(groupId)) next.delete(groupId)
  else next.add(groupId)
  expandedGroups.value = next
}

const selectAllVisible = () => {
  selectedKeys.value = new Set(filteredKeys.value.map((item) => item.name))
}

const clearSelection = () => {
  selectedKeys.value = new Set()
}

const fetchKeys = async () => {
  loading.value = true
  loadError.value = ''
  keys.value = []
  selectedKeys.value = new Set()
  expandedGroups.value = new Set()
  detailKey.value = null
  keyDetail.value = null
  try {
    const res = await axios.get('/api/portal/system/redis/keys-list', {
      params: { pattern: '*' },
    })
    keys.value = res.data.keys || []
    expandedGroups.value = new Set(groupRedisKeys(keys.value, groupMode.value).map((g) => g.id))
  } catch (e: any) {
    loadError.value = e.response?.data?.detail || e.message || '加载 Redis Keys 失败'
  } finally {
    loading.value = false
  }
}

const executeDelete = async () => {
  if (selectedCount.value === 0) return
  deleting.value = true
  try {
    const res = await axios.post('/api/portal/system/redis/delete-keys', {
      keys: Array.from(selectedKeys.value),
    })
    emit('deleted', {
      deletedCount: res.data.deleted_count ?? 0,
      message: res.data.message || '删除完成',
    })
    emit('close')
  } catch (e: any) {
    loadError.value = e.response?.data?.detail || e.message || '删除失败'
  } finally {
    deleting.value = false
  }
}

watch(
  () => props.show,
  (visible) => {
    if (visible) {
      searchKeyword.value = ''
      groupMode.value = 'redis_type'
      detailKey.value = null
      keyDetail.value = null
      fetchKeys()
    }
  },
)

watch(groupMode, () => {
  expandedGroups.value = new Set(groupedKeys.value.map((g) => g.id))
})
</script>

<template>
  <Modal
    :show="show"
    title="选择性清理 Redis Keys"
    size="max-w-4xl"
    :z-index="80"
    @close="emit('close')"
  >
    <div class="-m-6 flex flex-col max-h-[78vh]">
      <div class="px-6 py-4 space-y-4 flex-1 min-h-0 flex flex-col">
      <p class="text-sm text-gray-500">
        按类型分组展示当前 Redis 库中的 Key，可勾选整组或单个 Key 进行清理。最多加载 5000 条。
      </p>

      <div class="flex flex-wrap items-center gap-3">
        <div class="flex items-center bg-gray-100 p-1 rounded-lg border border-gray-200">
          <button
            type="button"
            class="px-3 py-1.5 rounded-md text-xs font-semibold transition-all"
            :class="groupMode === 'redis_type' ? 'bg-white shadow text-primary' : 'text-gray-500'"
            @click="groupMode = 'redis_type'"
          >
            按 Redis 类型
          </button>
          <button
            type="button"
            class="px-3 py-1.5 rounded-md text-xs font-semibold transition-all"
            :class="groupMode === 'business' ? 'bg-white shadow text-primary' : 'text-gray-500'"
            @click="groupMode = 'business'"
          >
            按业务分类
          </button>
        </div>

        <input
          v-model="searchKeyword"
          type="search"
          placeholder="搜索 Key 名称..."
          class="flex-1 min-w-[12rem] shadow-sm focus:ring-primary focus:border-primary block sm:text-sm border-gray-300 rounded-md bg-gray-50 p-2 border"
        />

        <button
          type="button"
          class="text-xs text-primary hover:underline"
          @click="selectAllVisible"
        >
          全选当前
        </button>
        <button
          type="button"
          class="text-xs text-gray-500 hover:underline"
          @click="clearSelection"
        >
          清空选择
        </button>
      </div>

      <div
        v-if="loadError"
        class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
      >
        {{ loadError }}
      </div>

      <div class="flex-1 min-h-[20rem] overflow-y-auto border border-gray-200 rounded-lg custom-scrollbar">
        <div v-if="loading" class="p-12 text-center text-gray-400">
          <span class="inline-block animate-spin h-8 w-8 border-2 border-primary border-t-transparent rounded-full mb-3" />
          <p class="text-sm">正在加载 Redis Keys...</p>
        </div>

        <div v-else-if="groupedKeys.length === 0" class="p-12 text-center text-gray-400 text-sm italic">
          暂无匹配的 Key
        </div>

        <div v-else class="divide-y divide-gray-100">
          <div v-for="group in groupedKeys" :key="group.id" class="bg-white">
            <div class="flex items-center gap-3 px-4 py-3 bg-gray-50/80">
              <input
                type="checkbox"
                class="rounded border-gray-300 text-primary focus:ring-primary"
                :checked="isGroupFullySelected(group.id)"
                :indeterminate="isGroupPartiallySelected(group.id)"
                @change="toggleGroup(group.id, ($event.target as HTMLInputElement).checked)"
              />
              <button
                type="button"
                class="flex-1 min-w-0 text-left"
                @click="toggleExpanded(group.id)"
              >
                <div class="flex items-center gap-2">
                  <span class="text-sm font-bold text-gray-900">{{ group.label }}</span>
                  <span class="text-[10px] px-2 py-0.5 rounded-full bg-gray-200 text-gray-600 font-bold">
                    {{ group.keys.length }}
                  </span>
                </div>
                <p class="text-[11px] text-gray-500 mt-0.5">{{ group.description }}</p>
              </button>
              <button
                type="button"
                class="text-gray-400 hover:text-gray-600"
                @click="toggleExpanded(group.id)"
              >
                <svg
                  class="w-4 h-4 transition-transform"
                  :class="{ 'rotate-180': expandedGroups.has(group.id) }"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
            </div>

            <div v-if="expandedGroups.has(group.id)" class="divide-y divide-gray-50">
              <div
                v-for="item in group.keys"
                :key="item.name"
              >
                <div class="flex items-start gap-3 px-4 py-2.5 hover:bg-gray-50">
                  <input
                    type="checkbox"
                    class="mt-0.5 rounded border-gray-300 text-primary focus:ring-primary"
                    :checked="selectedKeys.has(item.name)"
                    @change="toggleKey(item.name, ($event.target as HTMLInputElement).checked)"
                  />
                  <div class="min-w-0 flex-1">
                    <div class="text-xs font-mono text-gray-800 break-all">{{ item.name }}</div>
                    <div class="mt-1 flex items-center gap-2">
                      <span
                        class="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase border"
                        :class="
                          item.type === 'string' ? 'bg-green-50 text-green-700 border-green-100' :
                          item.type === 'hash' ? 'bg-primary/5 text-primary-dark border-primary/10' :
                          item.type === 'list' ? 'bg-yellow-50 text-yellow-700 border-yellow-100' :
                          'bg-gray-50 text-gray-600 border-gray-100'
                        "
                      >
                        {{ item.type }}
                      </span>
                      <span
                        v-if="groupMode === 'redis_type'"
                        class="text-[10px] text-gray-400"
                      >
                        {{ getRedisKeyCategoryLabel(item.name) }}
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    class="shrink-0 text-[11px] font-medium px-2 py-1 rounded-md border transition-colors"
                    :class="detailKey === item.name
                      ? 'text-primary border-primary/30 bg-primary/5'
                      : 'text-gray-500 border-gray-200 hover:text-primary hover:border-primary/30 hover:bg-primary/5'"
                    @click="fetchKeyDetail(item.name)"
                  >
                    {{ detailKey === item.name ? '收起' : '查看详情' }}
                  </button>
                </div>

                <div
                  v-if="detailKey === item.name"
                  class="mx-4 mb-3 rounded-lg border border-gray-200 bg-gray-50 overflow-hidden"
                >
                  <div v-if="detailLoading" class="px-4 py-6 text-center text-gray-400 text-sm">
                    <span class="inline-block animate-spin h-5 w-5 border-2 border-primary border-t-transparent rounded-full mb-2" />
                    <p>正在加载 Key 内容...</p>
                  </div>
                  <div v-else-if="keyDetail" class="p-3 space-y-2">
                    <div class="flex flex-wrap items-center gap-2 text-[10px]">
                      <span class="px-1.5 py-0.5 rounded-full font-bold uppercase bg-primary/5 text-primary-dark border border-primary/10">
                        {{ keyDetail.type }}
                      </span>
                      <span class="font-mono text-gray-500">TTL: {{ formatTtl(keyDetail.ttl) }}</span>
                    </div>
                    <div class="max-h-48 overflow-y-auto bg-gray-950 rounded-lg p-3 font-mono text-[11px] text-green-400 custom-scrollbar border border-gray-900">
                      <pre class="whitespace-pre-wrap break-all select-text">{{ formatRedisValue(keyDetail.value) }}</pre>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      </div>

      <div class="px-6 py-4 border-t border-gray-100 bg-gray-50/50 flex items-center justify-between flex-shrink-0">
        <span class="text-sm text-gray-500">
          已选择 <strong class="text-gray-900">{{ selectedCount }}</strong> 个 Key
        </span>
        <div class="flex items-center gap-3">
          <button
            type="button"
            class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
            :disabled="deleting"
            @click="emit('close')"
          >
            取消
          </button>
          <button
            type="button"
            class="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:opacity-50"
            :disabled="deleting || selectedCount === 0"
            @click="executeDelete"
          >
            {{ deleting ? '删除中...' : `删除所选 (${selectedCount})` }}
          </button>
        </div>
      </div>
    </div>
  </Modal>
</template>
