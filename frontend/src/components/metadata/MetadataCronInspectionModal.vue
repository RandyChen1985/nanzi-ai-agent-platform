<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import Modal from '@/components/Modal.vue'
import { useToast } from '@/composables/useToast'
import { useUser } from '@/composables/useUser'
import axios from '../../utils/axios'
import { metadataApi, type CronInspectionConfig } from '@/api/metadata'

const props = withDefaults(
  defineProps<{
    show?: boolean
    visible?: boolean
  }>(),
  {
    show: false,
    visible: false,
  }
)

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'updated'): void
}>()

const router = useRouter()
const { showToast } = useToast()
const { isAdmin } = useUser()
const canEdit = computed(() => isAdmin.value)

const isVisible = computed(() => props.show || props.visible)

// 常用预设周期选项
const PRESET_CRON_OPTIONS = [
  { label: '每天凌晨 02:00', desc: '业务低峰期（推荐）', value: '0 2 * * *' },
  { label: '每天凌晨 04:00', desc: '清晨执行', value: '0 4 * * *' },
  { label: '每周一凌晨 03:00', desc: '每周初巡检', value: '0 3 * * 1' },
  { label: '每 6 小时一次', desc: '高频度检查', value: '0 */6 * * *' },
  { label: '自定义 Cron', desc: '自由设定执行频率', value: 'custom' },
]

const loading = ref(false)
const saving = ref(false)
const triggering = ref(false)

const config = ref<CronInspectionConfig>({
  enabled: false,
  cron_expr: '0 2 * * *',
  task_id: null,
  next_run_at: null,
  last_run_at: null,
  run_count: 0,
  health_status: 'unknown',
  last_status: null,
  last_message: null,
  last_error: null,
})

const selectedPreset = ref<string>('0 2 * * *')
const customCronInput = ref<string>('')
const notificationChannels = ref<string[]>(['portal'])

const isChannelSelected = (ch: string) => notificationChannels.value.includes(ch)

const toggleChannel = (ch: string) => {
  if (!canEdit.value) return
  if (ch === 'portal') return // 站内信必选锁定
  if (!isNotificationChannelReady(ch)) return // 未配置/未启用的渠道禁止勾选
  if (notificationChannels.value.includes(ch)) {
    notificationChannels.value = notificationChannels.value.filter((item) => item !== ch)
  } else {
    notificationChannels.value.push(ch)
  }
}

// 个人中心消息通知配置（决定外部渠道是否可勾选）
const personalNotificationConfigs = ref<Record<string, any>>({})
const personalNotificationLoading = ref(false)

/** 渠道是否已配置且启用：未配置的渠道禁止勾选，避免"勾了也发不出去" */
const isNotificationChannelReady = (channel: string): boolean => {
  if (channel === 'portal') return true
  const cfg = personalNotificationConfigs.value[channel]
  if (!cfg || !cfg.is_enabled) return false
  if (channel === 'dingtalk' || channel === 'wechat_work' || channel === 'feishu') {
    return Boolean(String(cfg.webhook_url || '').trim())
  }
  if (channel === 'email') {
    return Boolean(String(cfg.smtp_host || '').trim() && String(cfg.smtp_user || '').trim())
  }
  return false
}

const CHANNEL_OPTIONS = [
  { value: 'portal', label: '站内消息' },
  { value: 'dingtalk', label: '钉钉群' },
  { value: 'wechat_work', label: '企业微信' },
  { value: 'feishu', label: '飞书群' },
  { value: 'email', label: '邮件通知' },
]

const unavailableExternalChannels = computed(() =>
  CHANNEL_OPTIONS.filter((c) => c.value !== 'portal' && !isNotificationChannelReady(c.value)).map(
    (c) => c.label
  )
)

const pruneUnavailableNotificationChannels = () => {
  notificationChannels.value = notificationChannels.value.filter((channel) =>
    isNotificationChannelReady(channel)
  )
}

const fetchPersonalNotificationConfigs = async () => {
  personalNotificationLoading.value = true
  try {
    const res = await axios.get('/api/portal/notifications/config')
    personalNotificationConfigs.value = res.data || {}
    pruneUnavailableNotificationChannels()
  } catch (error) {
    console.warn('Failed to load personal notification configs', error)
    personalNotificationConfigs.value = {}
  } finally {
    personalNotificationLoading.value = false
  }
}

const openPersonalNotificationSettings = () => {
  router.push({ path: '/dashboard/personal', query: { tab: 'notifications' } })
}

// 载入最新配置
const fetchConfig = async () => {
  loading.value = true
  try {
    const res = await metadataApi.getCronInspectionConfig()
    config.value = res.data

    if (res.data.notification_channels && res.data.notification_channels.length > 0) {
      notificationChannels.value = [...res.data.notification_channels]
      if (!notificationChannels.value.includes('portal')) {
        notificationChannels.value.unshift('portal')
      }
    } else {
      notificationChannels.value = ['portal']
    }
    // 剔除本用户未配置/未启用的外部渠道，避免"勾了也发不出去"
    pruneUnavailableNotificationChannels()

    const isPreset = PRESET_CRON_OPTIONS.some(
      (opt) => opt.value === res.data.cron_expr && opt.value !== 'custom'
    )
    if (isPreset) {
      selectedPreset.value = res.data.cron_expr
      customCronInput.value = ''
    } else {
      selectedPreset.value = 'custom'
      customCronInput.value = res.data.cron_expr
    }
  } catch (err: any) {
    showToast(err.response?.data?.detail || err.message || '获取定时巡检配置失败', 'error')
  } finally {
    loading.value = false
  }
}

watch(
  () => isVisible.value,
  (val) => {
    if (val) {
      fetchConfig()
      fetchPersonalNotificationConfigs()
    }
  },
  { immediate: true }
)

const handlePresetChange = (val: string) => {
  selectedPreset.value = val
  if (val !== 'custom') {
    config.value.cron_expr = val
  } else if (customCronInput.value) {
    config.value.cron_expr = customCronInput.value
  }
}

const handleCustomCronChange = () => {
  if (selectedPreset.value === 'custom') {
    config.value.cron_expr = customCronInput.value.trim()
  }
}

// 保存配置
const handleSave = async () => {
  if (!canEdit.value) return

  let finalCron = config.value.cron_expr
  if (selectedPreset.value === 'custom') {
    finalCron = customCronInput.value.trim()
  }

  if (!finalCron) {
    showToast('请输入有效的 Cron 表达式', 'warning')
    return
  }

  saving.value = true
  try {
    const res = await metadataApi.updateCronInspectionConfig({
      enabled: config.value.enabled,
      cron_expr: finalCron,
      notification_channels: notificationChannels.value,
    })
    config.value = res.data
    showToast('定时巡检配置已成功保存并同步调度引擎', 'success')
    emit('updated')
  } catch (err: any) {
    showToast(err.response?.data?.detail || err.message || '保存定时巡检配置失败', 'error')
  } finally {
    saving.value = false
  }
}

// 手动立即触发一次
const handleRunImmediately = async () => {
  if (!canEdit.value) return
  triggering.value = true
  try {
    const res = await metadataApi.triggerCronInspectionImmediately()
    showToast(res.data.message || '已触发全量定时巡检立即执行', 'success')
    // 延迟 1 秒后刷新状态
    setTimeout(() => {
      fetchConfig()
    }, 1200)
  } catch (err: any) {
    showToast(err.response?.data?.detail || err.message || '立即执行失败', 'error')
  } finally {
    triggering.value = false
  }
}

// 跳转到任务中心
const navigateToTaskCenter = () => {
  emit('close')
  router.push('/tasks')
}

const formatDate = (dateStr?: string | null) => {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return d.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  } catch {
    return dateStr
  }
}

const getHealthBadge = (health: string) => {
  switch (health) {
    case 'healthy':
      return {
        label: '运行正常',
        bgClass: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        dotClass: 'bg-emerald-500',
      }
    case 'warning':
      return {
        label: '轻度告警',
        bgClass: 'bg-amber-50 text-amber-700 border-amber-200',
        dotClass: 'bg-amber-500',
      }
    case 'error':
      return {
        label: '执行异常',
        bgClass: 'bg-rose-50 text-rose-700 border-rose-200',
        dotClass: 'bg-rose-500',
      }
    case 'skipped':
      return {
        label: '已跳过',
        bgClass: 'bg-blue-50 text-blue-700 border-blue-200',
        dotClass: 'bg-blue-500',
      }
    default:
      return {
        label: config.value.enabled ? '待首次运行' : '未开启',
        bgClass: config.value.enabled ? 'bg-emerald-100 text-emerald-800 border-emerald-300' : 'bg-slate-200 text-slate-700 border-slate-300',
        dotClass: config.value.enabled ? 'bg-emerald-600 animate-pulse' : 'bg-slate-400',
      }
  }
}
</script>

<template>
  <Modal
    :show="isVisible"
    title="全量元数据定时一致性巡检"
    size="max-w-2xl"
    @close="emit('close')"
  >
    <template #header-extra>
      <span
        class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border"
        :class="getHealthBadge(config.health_status).bgClass"
      >
        <span
          class="w-2 h-2 rounded-full"
          :class="getHealthBadge(config.health_status).dotClass"
        ></span>
        {{ getHealthBadge(config.health_status).label }}
      </span>
    </template>

    <div v-if="loading" class="py-12 flex flex-col items-center justify-center gap-3 text-slate-500">
      <svg class="animate-spin w-8 h-8 text-primary" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      <span class="text-sm">正在加载定时巡检调度配置...</span>
    </div>

    <div v-else class="space-y-6 text-slate-700 text-sm">
      <!-- 顶部功能简介横幅 -->
      <div class="rounded-xl p-4 bg-gradient-to-r from-blue-50/80 via-indigo-50/50 to-blue-50/30 border border-blue-100 flex items-start gap-3">
        <div class="w-9 h-9 rounded-lg bg-blue-100/80 text-blue-600 flex items-center justify-center shrink-0 shadow-sm text-lg">
          ⏱️
        </div>
        <div class="space-y-1">
          <h4 class="font-semibold text-slate-800 text-sm">自动化物理结构一致性巡检</h4>
          <p class="text-xs text-slate-600 leading-relaxed">
            定时任务由系统 APScheduler 后台自动调度，零 LLM Token 消耗。巡检引擎会自动扫描全量纳管数据集所绑定的物理数据库，并与平台元数据对比，将检出的字段缺失或新增自动沉淀至【全局巡检漂移大盘】。
          </p>
        </div>
      </div>

      <!-- 开关配置区域（根据开启/关闭展现清晰的容器视觉差） -->
      <div
        class="p-4 rounded-xl border transition-all duration-200 shadow-xs space-y-4"
        :class="
          config.enabled
            ? 'border-emerald-300 ring-2 ring-emerald-100/80 bg-gradient-to-br from-emerald-50/40 via-white to-white'
            : 'border-slate-200 bg-white'
        "
      >
        <div class="flex items-center justify-between gap-4">
          <div class="space-y-1">
            <div class="font-bold text-slate-900 text-sm flex items-center gap-2.5">
              <span>启用定时巡检</span>
              <span
                v-if="config.enabled"
                class="px-2.5 py-0.5 text-xs font-bold rounded-full bg-emerald-600 text-white shadow-xs flex items-center gap-1.5"
              >
                <span class="w-1.5 h-1.5 rounded-full bg-white animate-pulse"></span>
                已开启运行
              </span>
              <span
                v-else
                class="px-2.5 py-0.5 text-xs font-bold rounded-full bg-slate-200 text-slate-700 flex items-center gap-1.5"
              >
                <span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span>
                已暂停关闭
              </span>
            </div>
            <p class="text-xs text-slate-500">
              {{ config.enabled ? '当前已激活：系统将按照设定的时间周期自动巡检，并在任务中心显示运行状态。' : '当前已暂停：系统不会自动唤起巡检，任务在任务中心处于休眠暂停状态。' }}
            </p>
          </div>

          <!-- 现代高辨识度 Switch 开关组件 -->
          <button
            type="button"
            role="switch"
            :aria-checked="config.enabled"
            :disabled="!canEdit"
            @click="config.enabled = !config.enabled"
            class="relative inline-flex h-7 w-12 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 shadow-inner"
            :class="config.enabled ? 'bg-emerald-600' : 'bg-slate-300'"
            :title="config.enabled ? '点击关闭定时巡检' : '点击开启定时巡检'"
          >
            <span
              class="pointer-events-none inline-block h-6 w-6 transform rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out flex items-center justify-center text-[10px] select-none"
              :class="config.enabled ? 'translate-x-5 text-emerald-600 font-bold' : 'translate-x-0 text-slate-400 font-bold'"
            >
              {{ config.enabled ? '✓' : '✕' }}
            </span>
          </button>
        </div>

        <!-- 周期设定 -->
        <div class="pt-3 border-t border-slate-100 space-y-3">
          <div class="flex items-center justify-between">
            <label class="block text-xs font-bold text-slate-700">巡检时间周期 (Cron 计划)</label>
            <span v-if="!config.enabled" class="text-[11px] text-amber-600 bg-amber-50 px-2 py-0.5 rounded border border-amber-200/60">
              ⚠️ 任务处于暂停状态，保存后需开启开关才会自动执行
            </span>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            <button
              v-for="opt in PRESET_CRON_OPTIONS"
              :key="opt.value"
              type="button"
              @click="handlePresetChange(opt.value)"
              class="text-left px-3.5 py-2.5 rounded-lg border transition-all flex flex-col justify-center cursor-pointer"
              :class="
                selectedPreset === opt.value
                  ? 'border-blue-500 bg-blue-50/80 text-blue-900 ring-2 ring-blue-400/50 font-medium'
                  : 'border-slate-200 hover:border-slate-300 bg-white text-slate-700 hover:bg-slate-50/50'
              "
            >
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs">{{ opt.label }}</span>
                <span
                  v-if="opt.value !== 'custom'"
                  class="font-mono text-[11px]"
                  :class="selectedPreset === opt.value ? 'text-blue-600 font-bold' : 'text-slate-400'"
                >
                  {{ opt.value }}
                </span>
              </div>
              <span class="text-[11px] text-slate-500 mt-0.5">{{ opt.desc }}</span>
            </button>
          </div>

          <!-- 自定义 Cron 输入框 -->
          <div v-if="selectedPreset === 'custom'" class="pt-2">
            <div class="flex items-center gap-2">
              <input
                type="text"
                v-model="customCronInput"
                @input="handleCustomCronChange"
                placeholder="如: 0 1,13 * * * (分 时 日 月 周)"
                class="flex-1 font-mono text-xs px-3 py-2 border rounded-lg border-slate-300 focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
              />
              <span class="text-xs text-slate-400 font-mono">5位标准 Cron</span>
            </div>
            <p class="text-[11px] text-slate-400 mt-1">
              格式说明：分(0-59) 时(0-23) 日(1-31) 月(1-12) 星期(0-6或1-7)。
            </p>
          </div>
        </div>
      </div>

      <!-- 异常告警通知渠道配置（仅当发现漂移/异常时通知） -->
      <div class="p-4 rounded-xl border border-slate-200 bg-white space-y-3 shadow-xs">
        <div class="flex items-center justify-between">
          <div class="space-y-0.5">
            <span class="font-bold text-xs text-slate-800 flex items-center gap-1.5">
              🔔 异常告警通知渠道
            </span>
            <p class="text-[11px] text-slate-500">
              仅当巡检检出 Schema 结构漂移（缺失/新增/类型不一致）或数据源连接异常时发送通知，未发现异常时不打扰。
            </p>
          </div>
          <span class="text-[11px] font-medium text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200/60 shrink-0">
            ✓ 仅异常时触发
          </span>
        </div>

        <div class="grid grid-cols-2 sm:grid-cols-5 gap-2 pt-1">
          <!-- 站内信（必选锁定） -->
          <div
            class="flex items-center gap-2 p-2 rounded-lg border border-emerald-300 bg-emerald-50/60 cursor-not-allowed select-none"
            title="站内信小铃铛通知为平台默认告警渠道，始终开启"
          >
            <input
              type="checkbox"
              checked
              disabled
              class="w-4 h-4 rounded text-emerald-600 focus:ring-emerald-500 cursor-not-allowed"
            />
            <div class="flex items-center gap-1 min-w-0">
              <span class="text-xs font-semibold text-slate-800">站内信</span>
              <span class="text-[10px] text-emerald-600 font-bold bg-emerald-100/80 px-1 rounded">必选</span>
            </div>
          </div>

          <!-- 钉钉 -->
          <label
            class="flex items-center gap-2 p-2 rounded-lg border transition-all select-none"
            :class="
              isNotificationChannelReady('dingtalk')
                ? (isChannelSelected('dingtalk') ? 'border-primary bg-primary/5 text-primary font-medium cursor-pointer' : 'border-slate-200 hover:border-slate-300 text-slate-700 cursor-pointer')
                : 'border-slate-200 bg-slate-50 text-slate-300 cursor-not-allowed'
            "
            :title="isNotificationChannelReady('dingtalk') ? '' : '请先在个人中心 → 消息通知中配置并启用钉钉 Webhook'"
          >
            <input
              type="checkbox"
              :checked="isChannelSelected('dingtalk')"
              :disabled="!canEdit || !isNotificationChannelReady('dingtalk')"
              @change="toggleChannel('dingtalk')"
              class="w-4 h-4 rounded text-primary focus:ring-primary"
            />
            <span class="text-xs">钉钉群</span>
          </label>

          <!-- 企业微信 -->
          <label
            class="flex items-center gap-2 p-2 rounded-lg border transition-all select-none"
            :class="
              isNotificationChannelReady('wechat_work')
                ? (isChannelSelected('wechat_work') ? 'border-primary bg-primary/5 text-primary font-medium cursor-pointer' : 'border-slate-200 hover:border-slate-300 text-slate-700 cursor-pointer')
                : 'border-slate-200 bg-slate-50 text-slate-300 cursor-not-allowed'
            "
            :title="isNotificationChannelReady('wechat_work') ? '' : '请先在个人中心 → 消息通知中配置并启用企业微信 Webhook'"
          >
            <input
              type="checkbox"
              :checked="isChannelSelected('wechat_work')"
              :disabled="!canEdit || !isNotificationChannelReady('wechat_work')"
              @change="toggleChannel('wechat_work')"
              class="w-4 h-4 rounded text-primary focus:ring-primary"
            />
            <span class="text-xs">企业微信</span>
          </label>

          <!-- 飞书 -->
          <label
            class="flex items-center gap-2 p-2 rounded-lg border transition-all select-none"
            :class="
              isNotificationChannelReady('feishu')
                ? (isChannelSelected('feishu') ? 'border-primary bg-primary/5 text-primary font-medium cursor-pointer' : 'border-slate-200 hover:border-slate-300 text-slate-700 cursor-pointer')
                : 'border-slate-200 bg-slate-50 text-slate-300 cursor-not-allowed'
            "
            :title="isNotificationChannelReady('feishu') ? '' : '请先在个人中心 → 消息通知中配置并启用飞书 Webhook'"
          >
            <input
              type="checkbox"
              :checked="isChannelSelected('feishu')"
              :disabled="!canEdit || !isNotificationChannelReady('feishu')"
              @change="toggleChannel('feishu')"
              class="w-4 h-4 rounded text-primary focus:ring-primary"
            />
            <span class="text-xs">飞书群</span>
          </label>

          <!-- 邮件 -->
          <label
            class="flex items-center gap-2 p-2 rounded-lg border transition-all select-none"
            :class="
              isNotificationChannelReady('email')
                ? (isChannelSelected('email') ? 'border-primary bg-primary/5 text-primary font-medium cursor-pointer' : 'border-slate-200 hover:border-slate-300 text-slate-700 cursor-pointer')
                : 'border-slate-200 bg-slate-50 text-slate-300 cursor-not-allowed'
            "
            :title="isNotificationChannelReady('email') ? '' : '请先在个人中心 → 消息通知中配置并启用 SMTP 邮箱'"
          >
            <input
              type="checkbox"
              :checked="isChannelSelected('email')"
              :disabled="!canEdit || !isNotificationChannelReady('email')"
              @change="toggleChannel('email')"
              class="w-4 h-4 rounded text-primary focus:ring-primary"
            />
            <span class="text-xs">邮件通知</span>
          </label>
        </div>

        <p class="text-[11px] text-slate-400">
          💡 外部群机器人与邮箱将使用当前用户在【个人中心 ➔ 消息通知设置】中配置的 Webhook 与邮箱地址；未配置或未启用的渠道无法勾选。
        </p>

        <div
          v-if="unavailableExternalChannels.length"
          class="flex items-center justify-between gap-2 py-2 px-3 rounded-lg bg-amber-50 border border-amber-200"
        >
          <p class="text-[11px] leading-relaxed text-amber-700">
            {{ unavailableExternalChannels.join('、') }} 尚未在个人中心配置或未启用，已禁止勾选。
          </p>
          <button
            type="button"
            @click="openPersonalNotificationSettings"
            class="flex items-center gap-1 text-[11px] font-medium text-primary-600 hover:text-primary-800 underline shrink-0 cursor-pointer"
          >
            去个人中心配置消息通知 →
          </button>
        </div>
      </div>

      <!-- 执行状态与历史指标面板 -->
      <div class="p-4 rounded-xl border border-slate-200/80 bg-slate-50/50 space-y-3">
        <div class="flex items-center justify-between">
          <span class="font-semibold text-xs text-slate-700 flex items-center gap-1.5">
            📊 调度引擎运行指标
          </span>
          <button
            type="button"
            @click="fetchConfig"
            class="text-xs text-primary-600 hover:text-primary-700 flex items-center gap-1 cursor-pointer"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            刷新状态
          </button>
        </div>

        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
          <div class="bg-white p-2.5 rounded-lg border border-slate-200/70">
            <span class="text-[11px] text-slate-400 block">累计巡检次数</span>
            <span class="text-base font-bold text-slate-800 font-mono">{{ config.run_count }}</span>
          </div>
          <div class="bg-white p-2.5 rounded-lg border border-slate-200/70">
            <span class="text-[11px] text-slate-400 block">上次运行状态</span>
            <span class="text-xs font-semibold" :class="config.last_status === 'failed' ? 'text-rose-600' : 'text-emerald-600'">
              {{ config.last_status === 'failed' ? '失败' : (config.last_status === 'success' ? '成功' : (config.last_status || '-')) }}
            </span>
          </div>
          <div class="bg-white p-2.5 rounded-lg border border-slate-200/70">
            <span class="text-[11px] text-slate-400 block">上次运行时间</span>
            <span class="text-[11px] font-mono text-slate-700 truncate block" :title="formatDate(config.last_run_at)">
              {{ formatDate(config.last_run_at) }}
            </span>
          </div>
          <div class="bg-white p-2.5 rounded-lg border border-slate-200/70">
            <span class="text-[11px] text-slate-400 block">下次预计调度</span>
            <span class="text-[11px] font-mono text-slate-700 truncate block" :title="formatDate(config.next_run_at)">
              {{ config.enabled ? formatDate(config.next_run_at) : '已暂停' }}
            </span>
          </div>
        </div>

        <div v-if="config.last_message || config.last_error" class="p-2.5 rounded-lg bg-white border border-slate-200 text-xs">
          <span class="text-slate-400 block mb-0.5">最近一次巡检结果汇报：</span>
          <p class="text-slate-700 leading-relaxed font-mono">
            {{ config.last_message || config.last_error }}
          </p>
        </div>

        <div class="flex items-center justify-between pt-1">
          <div class="flex items-center gap-1.5 text-xs text-slate-500">
            <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>此任务在「任务中心」受系统保护（仅可查看/启停/立即执行，不可编辑配置）</span>
          </div>
          <button
            type="button"
            @click="navigateToTaskCenter"
            class="text-xs font-medium text-primary-600 hover:text-primary-800 hover:underline flex items-center gap-0.5 cursor-pointer"
          >
            去任务中心查看 →
          </button>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="flex items-center justify-between w-full">
        <div>
          <button
            type="button"
            @click="handleRunImmediately"
            :disabled="triggering || !canEdit"
            class="px-3.5 py-2 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 active:bg-slate-100 disabled:opacity-50 flex items-center gap-1.5 shadow-xs cursor-pointer"
          >
            <svg v-if="triggering" class="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span v-else>⚡</span>
            立即运行一次
          </button>
        </div>

        <div class="flex items-center gap-2.5">
          <button
            type="button"
            @click="emit('close')"
            class="px-4 py-2 text-xs font-medium text-slate-600 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 cursor-pointer"
          >
            关闭
          </button>
          <button
            v-if="canEdit"
            type="button"
            @click="handleSave"
            :disabled="saving"
            class="px-4 py-2 text-xs font-medium text-white bg-primary hover:bg-primary-hover active:bg-primary-active rounded-lg shadow-xs flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
          >
            <svg v-if="saving" class="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span>保存配置</span>
          </button>
        </div>
      </div>
    </template>
  </Modal>
</template>
