<script setup lang="ts">
/**
 * 凭据选择器：绑定点共用的下拉。
 *
 * 分「我的凭据」「系统共享」两组——系统级只显示被授权的那几条，
 * 未授权的连名称都不会出现（后端 `/secrets/picker` 已经按授权过滤过）。
 *
 * 选择器**只返回凭据 id**，不返回也不缓存任何值。
 */
import { computed, onMounted, ref } from 'vue'
import axios from '../../utils/axios'

interface PickerItem {
  id: string
  secret_key: string
  name: string
  secret_type: string
  category: string | null
  status: string
  allowed_scenarios: string[]
  value_masked: string
}

const props = withDefaults(
  defineProps<{
    modelValue?: string | null
    /** 只显示启用了该场景的凭据；留空则显示全部 */
    scene?: string
    placeholder?: string
    disabled?: boolean
  }>(),
  { modelValue: null, scene: '', placeholder: '选择凭据', disabled: false },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string | null): void
  (e: 'picked', item: PickerItem | null): void
}>()

const loading = ref(false)
const open = ref(false)
const mine = ref<PickerItem[]>([])
const shared = ref<PickerItem[]>([])

const all = computed(() => [...mine.value, ...shared.value])
const selected = computed(() => all.value.find((item) => item.id === props.modelValue) || null)

function matches(item: PickerItem): boolean {
  if (!props.scene) return true
  return item.allowed_scenarios.includes(props.scene)
}

const mineFiltered = computed(() => mine.value.filter(matches))
const sharedFiltered = computed(() => shared.value.filter(matches))

async function load() {
  loading.value = true
  try {
    const res = await axios.get('/api/portal/secrets/picker')
    const groups = res.data?.data?.groups || {}
    mine.value = groups.mine || []
    shared.value = groups.shared || []
  } catch (e) {
    console.error('加载凭据列表失败', e)
  } finally {
    loading.value = false
  }
}

function choose(item: PickerItem | null) {
  emit('update:modelValue', item ? item.id : null)
  emit('picked', item)
  open.value = false
}

onMounted(load)
defineExpose({ reload: load })
</script>

<template>
  <div class="relative">
    <button
      type="button"
      :disabled="disabled"
      class="flex w-full items-center justify-between gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-left text-sm text-gray-700 hover:border-blue-400 focus:border-blue-500 focus:outline-none disabled:cursor-not-allowed disabled:bg-gray-50"
      @click="open = !open"
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

    <div
      v-if="open"
      class="absolute z-30 mt-1 max-h-80 w-full overflow-y-auto rounded-xl border border-gray-200 bg-white p-1.5 shadow-xl"
    >
      <p v-if="loading" class="px-3 py-2 text-xs text-gray-400">加载中…</p>

      <template v-else>
        <div v-if="mineFiltered.length" class="mb-1">
          <p class="px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
            我的凭据 · {{ mineFiltered.length }}
          </p>
          <button
            v-for="item in mineFiltered"
            :key="item.id"
            type="button"
            class="flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left hover:bg-blue-50"
            @click="choose(item)"
          >
            <span class="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md bg-blue-100 text-[10px] font-bold text-blue-700">
              {{ item.secret_type === 'variable' ? '变' : '凭' }}
            </span>
            <span class="min-w-0">
              <span class="block truncate text-sm text-gray-900">{{ item.name }}</span>
              <span class="block truncate text-[11px] text-gray-500">{{ item.secret_key }}</span>
            </span>
          </button>
        </div>

        <div v-if="sharedFiltered.length">
          <p class="px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
            系统共享 · {{ sharedFiltered.length }}
          </p>
          <button
            v-for="item in sharedFiltered"
            :key="item.id"
            type="button"
            class="flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left hover:bg-emerald-50"
            @click="choose(item)"
          >
            <span class="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md bg-emerald-100 text-[10px] font-bold text-emerald-700">
              {{ item.secret_type === 'variable' ? '变' : '凭' }}
            </span>
            <span class="min-w-0">
              <span class="block truncate text-sm text-gray-900">{{ item.name }}</span>
              <span class="block truncate text-[11px] text-gray-500">{{ item.secret_key }}</span>
            </span>
          </button>
        </div>

        <p v-if="!mineFiltered.length && !sharedFiltered.length" class="px-3 py-6 text-center text-xs text-gray-400">
          没有可选的凭据。先到「个人中心 → 我的凭据」建一条。
        </p>

        <button
          v-if="selected"
          type="button"
          class="mt-1 w-full rounded-lg px-2 py-1.5 text-left text-xs text-rose-600 hover:bg-rose-50"
          @click="choose(null)"
        >
          清除绑定
        </button>
      </template>
    </div>
  </div>
</template>
