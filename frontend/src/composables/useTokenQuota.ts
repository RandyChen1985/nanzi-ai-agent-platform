import { computed, ref } from 'vue'
import axios from '@/utils/axios'
import { formatTokenCompact } from '@/utils/tokenFormat'

export interface TokenQuotaStatus {
  period_start: string
  period_end: string
  used_tokens: number
  limit_tokens: number | null
  remaining_tokens: number | null
  source: string
  source_label?: string | null
  is_admin_bypass: boolean
}

export function useTokenQuota() {
  const quotaStatus = ref<TokenQuotaStatus | null>(null)
  const loading = ref(false)

  const usagePercent = computed(() => {
    if (!quotaStatus.value?.limit_tokens) return 0
    return Math.min(
      100,
      Math.round((quotaStatus.value.used_tokens / quotaStatus.value.limit_tokens) * 100),
    )
  })

  const isBlocked = computed(() => {
    if (!quotaStatus.value || quotaStatus.value.is_admin_bypass) return false
    if (quotaStatus.value.limit_tokens == null) return false
    return quotaStatus.value.used_tokens >= quotaStatus.value.limit_tokens
  })

  const isWarning = computed(() => {
    if (!quotaStatus.value || quotaStatus.value.is_admin_bypass) return false
    if (quotaStatus.value.limit_tokens == null) return false
    if (isBlocked.value) return false
    return usagePercent.value >= 80
  })

  const bannerMessage = computed(() => {
    if (!quotaStatus.value || quotaStatus.value.is_admin_bypass) return ''
    if (quotaStatus.value.limit_tokens == null) return ''
    if (isBlocked.value) {
      return `本月 Token 额度已用尽（已用 ${formatTokenCompact(quotaStatus.value.used_tokens)} / 上限 ${formatTokenCompact(quotaStatus.value.limit_tokens)}），请联系管理员。`
    }
    if (isWarning.value) {
      return `本月 Token 额度即将用尽（已用 ${formatTokenCompact(quotaStatus.value.used_tokens)} / 上限 ${formatTokenCompact(quotaStatus.value.limit_tokens)}，剩余 ${formatTokenCompact(quotaStatus.value.remaining_tokens || 0)}）。`
    }
    return ''
  })

  const refreshQuota = async () => {
    loading.value = true
    try {
      const res = await axios.get('/api/portal/quota/me')
      quotaStatus.value = res.data
    } catch (error) {
      console.error('Failed to load token quota', error)
    } finally {
      loading.value = false
    }
  }

  const ensureCanSend = async (): Promise<string | null> => {
    await refreshQuota()
    if (isBlocked.value) {
      return bannerMessage.value || '本月 Token 额度已用尽，请联系管理员。'
    }
    return null
  }

  return {
    quotaStatus,
    loading,
    usagePercent,
    isBlocked,
    isWarning,
    bannerMessage,
    refreshQuota,
    ensureCanSend,
  }
}
