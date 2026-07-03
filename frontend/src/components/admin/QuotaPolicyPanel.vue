<template>
  <div class="space-y-5">
    <div
      v-if="effective"
      class="rounded-xl border border-blue-100 bg-blue-50/60 p-4 text-sm"
    >
      <p class="font-semibold text-blue-900 mb-2">本月生效额度</p>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <div>
          <p class="text-blue-600/80">已用</p>
          <TokenAmount
            :value="effective.used_tokens"
            main-class="text-lg font-bold text-blue-950 tabular-nums"
          />
        </div>
        <div>
          <p class="text-blue-600/80">上限</p>
          <TokenAmount
            :value="effective.limit_tokens"
            :is-unlimited="effective.limit_tokens == null"
            main-class="text-lg font-bold text-blue-950 tabular-nums"
          />
        </div>
        <div>
          <p class="text-blue-600/80">剩余</p>
          <TokenAmount
            :value="effective.remaining_tokens || 0"
            :is-unlimited="effective.limit_tokens == null"
            main-class="text-lg font-bold text-blue-950 tabular-nums"
          />
        </div>
        <div>
          <p class="text-blue-600/80">来源</p>
          <p class="font-medium text-blue-900 mt-1">{{ effective.source_label || sourceText(effective.source) }}</p>
        </div>
      </div>
      <p class="text-[11px] text-blue-700/70 mt-2">
        统计周期：{{ effective.period_start }} ~ {{ effective.period_end }}
      </p>
    </div>

    <div class="rounded-xl border border-gray-200 p-4 space-y-4 bg-white">
      <div class="flex items-center justify-between gap-3">
        <div>
          <p class="text-sm font-semibold text-gray-900">{{ scopeTitle }}</p>
          <p class="text-xs text-gray-500 mt-0.5">{{ scopeHint }}</p>
        </div>
        <label class="inline-flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
          <input
            v-model="form.enabled"
            type="checkbox"
            class="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          启用
        </label>
      </div>

      <div>
        <label class="block text-xs font-bold text-gray-400 uppercase mb-1">月 Token 上限</label>
        <div class="flex flex-col sm:flex-row gap-2">
          <label class="inline-flex items-center gap-2 text-sm text-gray-700">
            <input
              v-model="unlimited"
              type="radio"
              :value="true"
              class="text-blue-600 focus:ring-blue-500"
              :disabled="!form.enabled"
            />
            不限额
          </label>
          <label class="inline-flex items-center gap-2 text-sm text-gray-700 flex-1">
            <input
              v-model="unlimited"
              type="radio"
              :value="false"
              class="text-blue-600 focus:ring-blue-500"
              :disabled="!form.enabled"
            />
            自定义
            <input
              v-model.number="form.limit_tokens"
              type="number"
              min="0"
              step="1000"
              :disabled="!form.enabled || unlimited"
              placeholder="例如 500000"
              class="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none disabled:bg-gray-100"
            />
          </label>
        </div>
        <p
          v-if="form.enabled && !unlimited && limitInputHint"
          class="text-[11px] text-gray-500 mt-1.5 tabular-nums"
        >
          {{ limitInputHint }}
        </p>
      </div>

      <div v-if="inherit && !form.enabled" class="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
        当前未配置专属策略，将继承{{ scopeType === 'user' ? '角色模板或系统默认' : '系统默认' }}。
      </div>

      <div class="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          class="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          :disabled="saving"
          @click="savePolicy"
        >
          {{ saving ? '保存中...' : '保存额度' }}
        </button>
        <button
          v-if="!inherit"
          type="button"
          class="px-4 py-2 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          :disabled="saving"
          @click="clearPolicy"
        >
          清除配置
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import axios from 'axios'
import TokenAmount from '@/components/common/TokenAmount.vue'
import { formatTokenInputHint } from '@/utils/tokenFormat'

type ScopeType = 'user' | 'role' | 'system'

interface QuotaStatus {
  period_start: string
  period_end: string
  used_tokens: number
  limit_tokens: number | null
  remaining_tokens: number | null
  source: string
  source_label?: string | null
}

const props = defineProps<{
  scopeType: ScopeType
  scopeId?: number | null
  showEffective?: boolean
}>()

const emit = defineEmits<{
  saved: []
}>()

const form = ref({ enabled: false, limit_tokens: 100000 as number | null })
const unlimited = ref(true)
const inherit = ref(true)
const saving = ref(false)
const effective = ref<QuotaStatus | null>(null)

const scopeTitle = computed(() => {
  if (props.scopeType === 'user') return '用户专属额度'
  if (props.scopeType === 'role') return '角色额度模板'
  return '系统默认额度'
})

const scopeHint = computed(() => {
  if (props.scopeType === 'user') return '优先级高于角色模板；清除后继承角色/系统策略'
  if (props.scopeType === 'role') return '分配给该角色的用户将取各角色模板中的最高额度'
  return '未配置角色/用户策略时的全局默认月上限'
})

const apiBase = computed(() => {
  if (props.scopeType === 'system') return '/api/portal/quota/system'
  if (props.scopeType === 'role') return `/api/portal/quota/roles/${props.scopeId}`
  return `/api/portal/quota/users/${props.scopeId}`
})

const limitInputHint = computed(() => {
  if (!form.value.enabled || unlimited.value) return ''
  return formatTokenInputHint(form.value.limit_tokens)
})

const sourceText = (source: string) => {
  const map: Record<string, string> = {
    user: '用户专属',
    role: '角色模板',
    system: '系统默认',
    unlimited: '未限制',
    admin_bypass: '管理员豁免',
  }
  return map[source] || source
}

const applyPolicy = (data: any) => {
  inherit.value = !!data?.inherit
  form.value.enabled = !!data?.enabled
  if (data?.limit_tokens == null) {
    unlimited.value = true
    form.value.limit_tokens = 100000
  } else {
    unlimited.value = false
    form.value.limit_tokens = data.limit_tokens
  }
  if (props.showEffective && data?.effective) {
    effective.value = data.effective
  }
}

const loadPolicy = async () => {
  if (props.scopeType !== 'system' && !props.scopeId) return
  const res = await axios.get(apiBase.value)
  applyPolicy(res.data)
}

const savePolicy = async () => {
  saving.value = true
  try {
    const payload = {
      enabled: form.value.enabled,
      limit_tokens: !form.value.enabled || unlimited.value ? null : form.value.limit_tokens,
    }
    const res = await axios.put(apiBase.value, payload)
    applyPolicy(res.data)
    emit('saved')
  } catch (error: any) {
    alert(error?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

const clearPolicy = async () => {
  if (!confirm('确定清除该额度配置？')) return
  saving.value = true
  try {
    await axios.delete(apiBase.value)
    inherit.value = true
    form.value.enabled = false
    unlimited.value = true
    await loadPolicy()
    emit('saved')
  } catch (error: any) {
    alert(error?.response?.data?.detail || '清除失败')
  } finally {
    saving.value = false
  }
}

watch(
  () => [props.scopeType, props.scopeId],
  () => {
    loadPolicy().catch((error) => console.error('load quota policy failed', error))
  },
  { immediate: true },
)
</script>
