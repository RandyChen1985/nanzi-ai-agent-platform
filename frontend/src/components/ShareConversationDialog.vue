<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      aria-label="分享会话"
      @click.self="close"
    >
      <div class="w-full max-w-[480px] overflow-hidden rounded-2xl border border-white/60 bg-white shadow-2xl">
        <!-- 结果态 -->
        <template v-if="result">
          <div class="px-5 pt-6 pb-2 text-center">
            <div class="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-full bg-emerald-500/10 text-emerald-600">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6">
                <path d="M4.5 12.5l5 5 10-11" />
              </svg>
            </div>
            <h3 class="text-[15px] font-extrabold text-gray-900">分享已创建</h3>
            <p class="mt-1.5 text-[11.5px] text-gray-400">
              点「复制分享信息」一次带走主题、链接和密码，对方打开后输入密码即可查看
            </p>
          </div>

          <div class="px-5 pb-2">
            <!-- 二维码只编码链接：把密码也编进去等于谁扫码谁就能看，密钥就失去意义 -->
            <div v-if="qrDataUrl" class="mt-1 flex flex-col items-center gap-1.5">
              <img
                :src="qrDataUrl"
                alt="分享链接二维码"
                class="h-[132px] w-[132px] rounded-lg border border-gray-200 bg-white p-1"
              />
              <p class="text-[10.5px] text-gray-400">
                {{ result.secret ? '手机扫码打开后仍需输入密码' : '手机扫码即可打开' }}
              </p>
            </div>

            <div class="mt-2 flex items-center gap-2 rounded-[10px] border border-gray-200 bg-gray-50/70 px-2.5 py-2">
              <span class="w-11 shrink-0 text-[10px] font-extrabold tracking-wider text-gray-400">链接</span>
              <span class="min-w-0 flex-1 truncate text-[11.5px] text-gray-700">{{ result.url }}</span>
              <button
                class="shrink-0 rounded-md border border-primary/30 px-2 py-1 text-[11px] font-bold text-primary"
                :aria-label="copied === 'url' ? '链接已复制' : '复制链接'"
                @click="copy(result.url)"
              >
                {{ copied === 'url' ? '已复制' : '复制' }}
              </button>
            </div>
            <div v-if="result.secret" class="mt-2 flex items-center gap-2 rounded-[10px] border border-gray-200 bg-gray-50/70 px-2.5 py-2">
              <span class="w-11 shrink-0 text-[10px] font-extrabold tracking-wider text-gray-400">密码</span>
              <span class="min-w-0 flex-1 truncate font-mono text-[12px] font-bold tracking-[0.16em] text-gray-800">{{ result.secret }}</span>
              <button
                class="shrink-0 rounded-md border border-primary/30 px-2 py-1 text-[11px] font-bold text-primary"
                :aria-label="copied === 'secret' ? '密码已复制' : '复制密码'"
                @click="copy(result.secret, 'secret')"
              >
                {{ copied === 'secret' ? '已复制' : '复制' }}
              </button>
            </div>
          </div>

          <div class="flex items-center gap-2 border-t border-gray-100 bg-slate-50/60 px-5 py-3.5">
            <button
              class="flex-1 rounded-lg bg-primary px-4 py-2 text-[12.5px] font-semibold text-white shadow-sm transition-all active:scale-[0.98]"
              :aria-label="copied === 'all' ? '分享信息已复制' : '复制分享信息'"
              @click="copyShareInfo"
            >
              {{ copied === 'all' ? '已复制，可直接粘贴' : '复制分享信息' }}
            </button>
            <button class="rounded-lg px-3 py-2 text-[12.5px] font-semibold text-gray-500 hover:bg-gray-100" @click="openPreview">打开预览</button>
            <button class="rounded-lg px-3 py-2 text-[12.5px] font-semibold text-gray-500 hover:bg-gray-100" @click="close">完成</button>
          </div>
        </template>

        <!-- 配置态 -->
        <template v-else>
          <div class="flex items-start gap-2.5 px-5 pt-4 pb-3">
            <div class="min-w-0">
              <h3 class="text-[15px] font-extrabold text-gray-900">分享会话</h3>
              <p class="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-gray-400">{{ conversationTitle }}</p>
            </div>
            <button class="ml-auto text-gray-400 hover:text-gray-600" aria-label="关闭" @click="close">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>

          <div class="px-5 pb-1">
            <div class="mt-1 text-[11.5px] font-bold text-gray-700">访问方式</div>
            <div class="mt-2 grid grid-cols-2 gap-2">
              <button
                v-for="option in ACCESS_OPTIONS"
                :key="option.value"
                class="rounded-[11px] border px-2.5 py-2.5 text-left transition-colors"
                :class="accessMode === option.value
                  ? 'border-primary bg-primary/5 ring-1 ring-primary/25'
                  : 'border-gray-200 hover:border-gray-300'"
                @click="accessMode = option.value"
              >
                <span class="flex items-center gap-1.5 text-[12.5px] font-bold text-gray-800">
                  <span
                    class="grid h-3.5 w-3.5 place-items-center rounded-full border"
                    :class="accessMode === option.value ? 'border-primary' : 'border-gray-300'"
                  >
                    <span v-if="accessMode === option.value" class="h-1.5 w-1.5 rounded-full bg-primary"></span>
                  </span>
                  {{ option.label }}
                </span>
                <span class="mt-1 block text-[10.5px] leading-snug text-gray-400">{{ option.desc }}</span>
              </button>
            </div>

            <template v-if="accessMode === 'secret'">
              <div class="mt-3 flex items-center gap-1.5 text-[11.5px] font-bold text-gray-700">
                访问密码
                <span class="font-medium text-gray-400">可自动生成，也可以自己改</span>
              </div>
              <div class="mt-2 flex gap-2">
                <input
                  v-model="secret"
                  maxlength="32"
                  aria-label="访问密码"
                  class="h-9 flex-1 rounded-[9px] border border-gray-200 px-2.5 font-mono text-[13px] tracking-[0.14em] text-gray-800 outline-none focus:border-primary"
                />
                <button class="h-9 shrink-0 rounded-lg border border-gray-200 px-3 text-[12px] font-semibold text-gray-600" @click="secret = generateShareSecret()">
                  换一个
                </button>
              </div>
            </template>

            <div class="mt-3 text-[11.5px] font-bold text-gray-700">有效期</div>
            <div
              class="mt-2 flex flex-wrap gap-1.5"
              role="group"
              aria-label="有效期，可选 1 小时、24 小时、7 天、30 天、永久，也可按小时或天数自定义"
            >
              <button
                v-for="preset in EXPIRY_PRESETS"
                :key="preset.value"
                class="h-[30px] rounded-lg border px-2.5 text-[12px] font-semibold"
                :class="isPresetActive(preset.value)
                  ? 'border-primary bg-primary text-white'
                  : 'border-gray-200 text-gray-700 hover:border-gray-300'"
                @click="selectPreset(preset.value)"
              >
                {{ preset.label }}
              </button>
            </div>
            <div class="mt-2 flex items-center gap-1.5">
              <input
                v-model.number="customValue"
                type="number"
                min="1"
                placeholder="自定义"
                aria-label="自定义有效期数值"
                class="h-8 w-[76px] rounded-lg border border-gray-200 px-2 text-[12.5px] outline-none focus:border-primary"
                @focus="expiryMode = 'custom'"
              />
              <select
                v-model="customUnit"
                aria-label="自定义有效期单位"
                class="h-8 rounded-lg border border-gray-200 px-2 text-[12.5px] outline-none focus:border-primary"
                @focus="expiryMode = 'custom'"
                @change="expiryMode = 'custom'"
              >
                <option value="hour">小时</option>
                <option value="day">天</option>
              </select>
            </div>

            <div class="mt-3 flex items-start gap-1.5 rounded-[9px] border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-800">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mt-0.5 shrink-0">
                <circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.5" />
              </svg>
              <span>
                只包含已完成的 <b>{{ turnCount }}</b> 轮问答{{ running ? '（当前仍在生成，后续轮次不会带出）' : '' }}；
                思考过程、工具调用与生成的文件不会随分享带出。
              </span>
            </div>

            <p v-if="existingActiveCount > 0" class="mt-2 text-[10.5px] text-gray-400">
              该会话已有 {{ existingActiveCount }} 条生效中的分享，本次会创建一条新的独立链接。
            </p>
            <p v-if="errorMessage" class="mt-2 text-[11px] text-red-500">{{ errorMessage }}</p>
          </div>

          <div class="mt-3 flex items-center gap-2 border-t border-gray-100 bg-slate-50/60 px-5 py-3.5">
            <span class="text-[10.5px] text-gray-400">{{ expiryHint }}</span>
            <div class="ml-auto flex gap-2">
              <button class="rounded-lg px-3 py-2 text-[12.5px] font-semibold text-gray-500 hover:bg-gray-100" @click="close">取消</button>
              <button
                class="rounded-lg bg-primary px-4 py-2 text-[12.5px] font-semibold text-white disabled:opacity-60"
                :disabled="submitting"
                @click="submit"
              >
                {{ submitting ? '创建中…' : '创建分享' }}
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * 会话分享弹窗：配置态（访问方式 / 密码 / 有效期）→ 结果态（链接与密码分开复制）。
 *
 * 有效期换算、密码生成都走 `shareExpiry` 里的纯函数，不在这里重算——前端自己算一套
 * 就会出现「弹窗写 7 天、列表显示 6 天后过期」。创建请求统一走 `chatShare` 的
 * `createConversationShare`，错误提示原样带到弹窗里，不静默吞掉。
 */
import { computed, ref, watch } from 'vue'

import {
  createConversationShare,
  type AuthHeaders,
  type ShareExpiryPayload,
  type ShareListItem,
} from '@/api/chatShare'
import { copyToClipboard } from '@/utils/clipboard'
import { generateQRCodeDataUrl } from '@/utils/qrcode'
import { buildShareCopyText } from '@/utils/shareCopy'
import { readErrorDetail } from '@/utils/httpErrorDetail'
import { formatInPlatformTimezone } from '@/utils/platformTimezone'
import {
  EXPIRY_PRESETS,
  describeExpiry,
  generateShareSecret,
  resolveExpiry,
  type ExpiryChoice,
  type ExpiryPresetValue,
} from '@/utils/shareExpiry'

const props = withDefaults(
  defineProps<{
    visible: boolean
    conversationId: string
    conversationTitle?: string
    turnCount?: number
    running?: boolean
    existingActiveCount?: number
    /** 嵌入场景由调用方注入 Bearer / X-API-Key，门户页不传（走 Cookie）。 */
    authHeaders?: AuthHeaders
  }>(),
  {
    conversationTitle: '',
    turnCount: 0,
    running: false,
    existingActiveCount: 0,
    authHeaders: undefined,
  },
)

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', item: ShareListItem): void
  (e: 'toast', message: string, type: 'success' | 'error' | 'info' | 'warning'): void
}>()

const ACCESS_OPTIONS = [
  { value: 'public' as const, label: '公开链接', desc: '拿到链接的人都能直接查看' },
  { value: 'secret' as const, label: '密钥访问', desc: '需要输入访问密码才能查看' },
]

const accessMode = ref<'public' | 'secret'>('secret')
const secret = ref(generateShareSecret())
const expiryMode = ref<'preset' | 'custom' | 'permanent'>('preset')
const presetValue = ref<ExpiryPresetValue>('7d')
const customValue = ref<number | null>(null)
const customUnit = ref<'hour' | 'day'>('day')
const submitting = ref(false)
const errorMessage = ref('')
const result = ref<ShareListItem | null>(null)
const copied = ref<'' | 'url' | 'secret' | 'all'>('')
/** 结果态二维码：只编码链接，不编码密码（扫码者仍需自行输入密码）。 */
const qrDataUrl = ref('')

const expiryChoice = computed<ExpiryChoice>(() => {
  if (expiryMode.value === 'permanent') return { mode: 'permanent' }
  if (expiryMode.value === 'custom') {
    return { mode: 'custom', value: Number(customValue.value) || undefined, unit: customUnit.value }
  }
  return { mode: 'preset', preset: presetValue.value }
})

/** 自定义模式尚未填值：既不能提交，也不该拿「null 天后失效」去骗用户。 */
const customInvalid = computed(
  () => expiryMode.value === 'custom' && !(Number(customValue.value) > 0),
)

const expiryHint = computed(() => {
  if (customInvalid.value) return '请填写自定义有效期'
  const choice = expiryChoice.value
  const target = resolveExpiry(choice)
  if (!target) return describeExpiry(choice)
  return `${describeExpiry(choice)}（${formatInPlatformTimezone(target)}）`
})

function isPresetActive(value: ExpiryPresetValue): boolean {
  return expiryMode.value === 'preset' && presetValue.value === value
}

function selectPreset(value: ExpiryPresetValue): void {
  if (value === 'permanent') {
    expiryMode.value = 'permanent'
    return
  }
  expiryMode.value = 'preset'
  presetValue.value = value
}

/** 每次打开都回到默认值：上次填的密码不该在别人面前残留。 */
function reset(): void {
  accessMode.value = 'secret'
  secret.value = generateShareSecret()
  expiryMode.value = 'preset'
  presetValue.value = '7d'
  customValue.value = null
  customUnit.value = 'day'
  submitting.value = false
  errorMessage.value = ''
  result.value = null
  qrDataUrl.value = ''
  copied.value = ''
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) reset()
  },
)

function close(): void {
  emit('close')
}

async function copy(text: string, kind: 'url' | 'secret' | 'all' = 'url'): Promise<void> {
  const ok = await copyToClipboard(text)
  if (!ok) {
    emit('toast', '复制失败，请手动选中复制', 'error')
    return
  }
  copied.value = kind
  emit('toast', '已复制到剪贴板', 'success')
  window.setTimeout(() => {
    copied.value = ''
  }, 1800)
}

/** 生成二维码；库不可用或链接为空时静默降级（详情行仍可复制）。 */
async function loadQrCode(url: string): Promise<void> {
  qrDataUrl.value = url ? await generateQRCodeDataUrl(url) : ''
}

/** 一键复制「介绍 + 链接 + 密码」，省掉分别复制两次。 */
async function copyShareInfo(): Promise<void> {
  if (!result.value) return
  const text = buildShareCopyText({
    title: result.value.title,
    url: result.value.url,
    secret: result.value.secret,
  })
  if (!text) {
    emit('toast', '分享链接缺失，无法复制', 'error')
    return
  }
  await copy(text, 'all')
}

function openPreview(): void {
  if (!result.value) return
  window.open(result.value.url, '_blank', 'noopener')
}

async function submit(): Promise<void> {
  if (!props.conversationId) {
    errorMessage.value = '当前会话还不支持分享'
    return
  }
  if (accessMode.value === 'secret' && secret.value.trim().length < 6) {
    errorMessage.value = '访问密码至少 6 位'
    return
  }
  if (customInvalid.value) {
    errorMessage.value = '请填写自定义有效期'
    return
  }

  const expiry: ShareExpiryPayload =
    expiryMode.value === 'preset'
      ? { mode: 'preset', preset: presetValue.value }
      : expiryMode.value === 'permanent'
        ? { mode: 'permanent' }
        : { mode: 'custom', value: Number(customValue.value), unit: customUnit.value }

  submitting.value = true
  errorMessage.value = ''
  try {
    const created = await createConversationShare(
      {
        conversation_id: props.conversationId,
        access_mode: accessMode.value,
        secret: accessMode.value === 'secret' ? secret.value.trim() : undefined,
        expiry,
      },
      props.authHeaders,
    )
    result.value = created
    void loadQrCode(created.url)
    emit('created', created)
  } catch (error) {
    const detail = await readErrorDetail(
      (error as { response?: { data?: unknown } })?.response?.data,
    )
    errorMessage.value = detail || '创建分享失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>
