<script setup lang="ts">
/**
 * 凭据用量统计。管理端和个人端共用同一份渲染。
 *
 * 抽出来的直接原因：两边的 `RESULT_LABEL` 已经长得不一样了——同一个
 * `denied_by_scenario`，一边写「场景未启用」一边写「场景不符」，个人端还漏了
 * `denied_disabled`。这层映射本来是纯展示口径，没有分叉的理由。
 */
import { computed, onMounted, ref, watch } from 'vue'
import axios from '../../utils/axios'
import { resultLabel } from '../../utils/secretLabels'

const props = withDefaults(defineProps<{
  /** 只看某一条；`null` 表示全部 */
  secretId?: string | null
  days?: number
}>(), {
  secretId: null,
  days: 7,
})

type Bucket = { total: number; failed: number }
interface Stats {
  days: number
  total: number
  success: number
  failed: number
  by_result: { result: string; count: number }[]
  by_secret: (Bucket & { secret_key: string })[]
  by_scene: { scene: string; count: number }[]
  by_day: (Bucket & { date: string })[]
}

const loading = ref(false)
const error = ref('')
const stats = ref<Stats | null>(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await axios.get('/api/portal/secrets/usage/stats', {
      params: {
        days: props.days,
        ...(props.secretId ? { secret_id: props.secretId } : {}),
      },
    })
    stats.value = res.data?.data || null
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载统计失败'
    stats.value = null
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => [props.secretId, props.days], load)

defineExpose({ reload: load })

/** 条形按比例画长度，最大项占满 */
const barWidth = (value: number, max: number): string =>
  max > 0 ? `${Math.max(2, Math.round((value / max) * 100))}%` : '0%'

const maxSecret = computed(() =>
  Math.max(1, ...(stats.value?.by_secret || []).map((r) => r.total)))
const maxResult = computed(() =>
  Math.max(1, ...(stats.value?.by_result || []).map((r) => r.count)))
const maxDay = computed(() =>
  Math.max(1, ...(stats.value?.by_day || []).map((r) => r.total)))
</script>

<template>
  <div class="space-y-4">
    <p v-if="loading" class="py-8 text-center text-xs text-gray-400">加载中…</p>
    <p v-else-if="error" class="py-8 text-center text-xs text-rose-600">{{ error }}</p>

    <template v-else-if="stats">
      <div class="grid grid-cols-3 gap-2">
        <div class="rounded-lg bg-gray-50 px-3 py-2">
          <div class="text-[11px] text-gray-500">调用</div>
          <div class="text-lg font-semibold text-gray-900">{{ stats.total }}</div>
        </div>
        <div class="rounded-lg bg-emerald-50 px-3 py-2">
          <div class="text-[11px] text-emerald-700">成功</div>
          <div class="text-lg font-semibold text-emerald-700">{{ stats.success }}</div>
        </div>
        <div class="rounded-lg px-3 py-2" :class="stats.failed ? 'bg-amber-50' : 'bg-gray-50'">
          <div class="text-[11px]" :class="stats.failed ? 'text-amber-700' : 'text-gray-500'">被拒绝</div>
          <div class="text-lg font-semibold" :class="stats.failed ? 'text-amber-700' : 'text-gray-900'">
            {{ stats.failed }}
          </div>
        </div>
      </div>

      <div v-if="stats.by_secret.length > 1">
        <p class="mb-1.5 text-xs font-semibold text-gray-600">按凭据</p>
        <div class="space-y-1">
          <div v-for="row in stats.by_secret" :key="row.secret_key" class="flex items-center gap-2">
            <code class="w-36 shrink-0 truncate text-[11px] text-gray-700">{{ row.secret_key }}</code>
            <div class="h-2 flex-1 overflow-hidden rounded-full bg-gray-100">
              <div
                class="h-full rounded-full"
                :class="row.failed ? 'bg-amber-400' : 'bg-emerald-400'"
                :style="{ width: barWidth(row.total, maxSecret) }"
              ></div>
            </div>
            <span class="w-16 shrink-0 text-right text-[11px] text-gray-500">
              {{ row.total }}<template v-if="row.failed"> · 败 {{ row.failed }}</template>
            </span>
          </div>
        </div>
      </div>

      <div v-if="stats.by_result.length">
        <p class="mb-1.5 text-xs font-semibold text-gray-600">按结果</p>
        <div class="space-y-1">
          <div v-for="row in stats.by_result" :key="row.result" class="flex items-center gap-2">
            <code class="w-36 shrink-0 truncate text-[11px] text-gray-700">{{ resultLabel(row.result) }}</code>
            <div class="h-2 flex-1 overflow-hidden rounded-full bg-gray-100">
              <div
                class="h-full rounded-full"
                :class="row.result === 'success' ? 'bg-emerald-400' : 'bg-amber-400'"
                :style="{ width: barWidth(row.count, maxResult) }"
              ></div>
            </div>
            <span class="w-16 shrink-0 text-right text-[11px] text-gray-500">{{ row.count }}</span>
          </div>
        </div>
      </div>

      <div v-if="stats.by_day.length">
        <p class="mb-1.5 text-xs font-semibold text-gray-600">按天</p>
        <div class="flex items-end gap-1.5">
          <div v-for="row in stats.by_day" :key="row.date" class="flex flex-1 flex-col items-center gap-1">
            <div
              class="w-full rounded-t bg-gray-200"
              :style="{ height: `${Math.max(4, Math.round((row.total / maxDay) * 64))}px` }"
              :title="`${row.date}：${row.total} 次，其中失败 ${row.failed} 次`"
            ></div>
            <span class="text-[10px] text-gray-400">{{ row.date.slice(5) }}</span>
          </div>
        </div>
      </div>

      <p class="text-[11px] leading-relaxed text-gray-400">
        被拒绝的调用也计入总量——「配了但用不了」比没人用更值得看。
      </p>

      <p v-if="!stats.total" class="py-4 text-center text-xs text-gray-400">
        近 {{ stats.days }} 天没有使用记录
      </p>
    </template>
  </div>
</template>
