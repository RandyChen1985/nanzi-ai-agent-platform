<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-[1300] flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      aria-label="分享文件"
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
              点「复制分享信息」一次带走文件名、链接和密码，对方打开后输入密码即可下载
            </p>
          </div>

          <div class="px-5 pb-2">
            <!-- 二维码只编码链接：把密码也编进去等于谁扫码谁就能下 -->
            <div v-if="qrDataUrl" class="mt-1 flex flex-col items-center gap-1.5">
              <img
                :src="qrDataUrl"
                alt="分享链接二维码"
                class="h-[132px] w-[132px] rounded-lg border border-gray-200 bg-white p-1"
              />
              <p class="text-[10.5px] text-gray-400">
                {{ result.secret ? '手机扫码打开后仍需输入密码' : '手机扫码即可下载' }}
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

          <div class="flex items-center justify-between gap-2 border-t border-gray-100 px-5 py-3">
            <span class="text-[11px] text-gray-500">{{ resultExpiryHint }}</span>
            <div class="flex items-center gap-2">
              <button class="rounded-lg px-3 py-1.5 text-xs font-bold text-gray-500 hover:bg-gray-100" @click="openPreview">
                打开试试
              </button>
              <button class="rounded-lg bg-primary px-4 py-1.5 text-xs font-bold text-white shadow-sm" @click="copyShareInfo">
                {{ copied === 'all' ? '已复制' : '复制分享信息' }}
              </button>
            </div>
          </div>
        </template>

        <!-- 配置态 -->
        <template v-else>
          <div class="flex items-start justify-between gap-3 px-5 pt-5">
            <div class="min-w-0">
              <h3 class="text-[15px] font-extrabold text-gray-900">分享文件</h3>
              <p class="mt-1 flex items-center gap-1.5 text-[11.5px] text-gray-500">
                <span class="max-w-[260px] truncate font-semibold text-gray-700">{{ fileName }}</span>
                <span v-if="fileSizeLabel">· {{ fileSizeLabel }}</span>
              </p>
            </div>
            <button class="shrink-0 rounded-md p-1 text-gray-400 hover:bg-gray-100" aria-label="关闭" @click="close">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>

          <div class="px-5 pt-4">
            <p class="mb-2 text-xs font-bold text-gray-700">访问方式</p>
            <div class="grid grid-cols-2 gap-2">
              <button
                v-for="option in ACCESS_OPTIONS"
                :key="option.value"
                class="rounded-xl border px-3 py-2 text-left transition-colors"
                :class="accessMode === option.value
                  ? 'border-primary bg-primary/5 ring-1 ring-primary'
                  : 'border-gray-200 hover:border-gray-300'"
                @click="accessMode = option.value"
              >
                <span class="flex items-center gap-1.5 text-[12.5px] font-bold text-gray-800">
                  <span
                    class="grid h-3.5 w-3.5 place-items-center rounded-full border"
                    :class="accessMode === option.value ? 'border-primary' : 'border-gray-300'"
                  >
                    <span v-if="accessMode === option.value" class="h-1.5 w-1.5 rounded-full bg-primary" />
                  </span>
                  {{ option.label }}
                </span>
                <span class="mt-1 block pl-5 text-[10.5px] leading-5 text-gray-500">{{ option.desc }}</span>
              </button>
            </div>

            <template v-if="accessMode === 'secret'">
              <p class="mt-3.5 mb-2 text-xs font-bold text-gray-700">
                访问密码<span class="ml-1 font-normal text-gray-400">可自动生成，也可以自己改</span>
              </p>
              <div class="flex items-center gap-2">
                <input
                  v-model="secret"
                  type="text"
                  maxlength="32"
                  class="min-w-0 flex-1 rounded-[10px] border border-gray-200 px-3 py-2 text-[12.5px] font-mono tracking-[0.14em] text-gray-800 outline-none focus:border-primary"
                  aria-label="访问密码"
                  @input="errorMessage = ''"
                />
                <button
                  class="shrink-0 rounded-[10px] border border-gray-200 px-3 py-2 text-xs font-bold text-gray-600 hover:bg-gray-50"
                  @click="secret = generateShareSecret()"
                >
                  换一个
                </button>
              </div>
            </template>

            <p class="mt-3.5 mb-2 text-xs font-bold text-gray-700">有效期</p>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="preset in EXPIRY_PRESETS"
                :key="preset.value"
                class="rounded-lg border px-2.5 py-1.5 text-xs"
                :class="isPresetActive(preset.value)
                  ? 'border-primary bg-primary text-white font-bold'
                  : 'border-gray-200 text-gray-600 hover:border-gray-300'"
                @click="selectPreset(preset.value)"
              >
                {{ preset.label }}
              </button>
            </div>
            <div class="mt-2 flex items-center gap-2">
              <input
                v-model.number="customValue"
                type="number"
                min="1"
                placeholder="自定义"
                class="w-24 rounded-[10px] border border-gray-200 px-3 py-1.5 text-xs outline-none focus:border-primary"
                :class="expiryMode === 'custom' ? 'border-primary' : ''"
                aria-label="自定义有效期数量"
                @focus="expiryMode = 'custom'"
              />
              <select
                v-model="customUnit"
                class="rounded-[10px] border border-gray-200 px-2 py-1.5 text-xs text-gray-700 outline-none focus:border-primary"
                aria-label="自定义有效期单位"
                @focus="expiryMode = 'custom'"
              >
                <option value="hour">小时</option>
                <option value="day">天</option>
              </select>
            </div>

            <div class="mt-3.5 flex gap-2 rounded-[10px] border border-amber-200 bg-amber-50/70 px-3 py-2 text-[11.5px] leading-5 text-amber-700">
              <span aria-hidden="true">⚠</span>
              <span>
                分享的是<b>此刻的文件副本</b>：之后你再编辑、重命名或删除，链接里的内容不会变。
                <template v-if="isSensitiveName">文件名看着像业务数据，请确认对方可以拿到这份内容。</template>
              </span>
            </div>

            <p v-if="errorMessage" class="mt-2 text-[11.5px] font-semibold text-red-500">{{ errorMessage }}</p>
          </div>

          <div class="mt-3 flex items-center justify-between gap-2 border-t border-gray-100 bg-gray-50/60 px-5 py-3">
            <span class="text-[11px] text-gray-500">{{ expiryHint }}</span>
            <div class="flex items-center gap-2">
              <button class="rounded-lg px-3 py-1.5 text-xs font-bold text-gray-500 hover:bg-gray-100" @click="close">
                取消
              </button>
              <button
                class="rounded-lg bg-primary px-4 py-1.5 text-xs font-bold text-white shadow-sm disabled:opacity-50"
                :disabled="submitting || customInvalid"
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
 * 文件分享弹窗：配置态（访问方式 / 密码 / 有效期）→ 结果态（链接与密码分开复制）。
 *
 * **层级为什么是 1300 而不是会话分享弹窗的 70**：本弹窗有两个入口，其中一个是从
 * 「浏览工作空间」抽屉里点分享——抽屉自身是 `z-[125]`、它内部弹窗是 `z-[131]`，
 * 70 会被整块压在下面（表现为弹窗只有左边露出来）。1300 同时在抽屉（131）、
 * 画布拖拽遮罩（300）与聊天输入框浮层（1200）之上，又在 toast（9950+）之下。
 *
 * 与会话分享弹窗刻意保持同一套交互与文案：有效期换算、密码生成全部复用
 * `shareExpiry` 的纯函数，不在这里重算——前端自己算一套就会出现「弹窗写 7 天、列表
 * 显示 6 天后过期」。唯一独有的东西是文件语义的那条黄色警告：分享的是**内容指纹**，
 * 源文件被改写后链接就失效，这句话必须让用户在点「创建分享」之前看到。
 */
import { computed, ref, watch } from 'vue'

import {
  createFileShare,
  type AuthHeaders,
  type FileShareItem,
  type ShareExpiryPayload,
} from '@/api/fileShare'
import { copyToClipboard } from '@/utils/clipboard'
import { generateQRCodeDataUrl } from '@/utils/qrcode'
import { buildShareCopyText } from '@/utils/shareCopy'
import { readErrorDetail } from '@/utils/httpErrorDetail'
import { formatInPlatformTimezone } from '@/utils/platformTimezone'
import {
  EXPIRY_PRESETS,
  describeExpiry,
  generateShareSecret,
  isExpiryPresetActive,
  resolveExpiry,
  type ExpiryChoice,
  type ExpiryPresetValue,
} from '@/utils/shareExpiry'

const props = withDefaults(
  defineProps<{
    visible: boolean
    /** 工作区路径（绝对路径或相对工作区根），原样交给后端解析与校验。 */
    filePath: string
    /**
     * 产物 ID。生成的图片只有 `download_url`、没有工作区路径，此时用 ID 让后端反查，
     * 与 `filePath` 二选一即可。
     */
    artifactId?: string
    fileName: string
    fileSize?: number
    /** 嵌入场景由调用方注入 Bearer / X-API-Key，门户页不传（走 Cookie）。 */
    authHeaders?: AuthHeaders
  }>(),
  {
    fileSize: 0,
    artifactId: '',
    authHeaders: undefined,
  },
)

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', item: FileShareItem): void
  (e: 'toast', message: string, type: 'success' | 'error' | 'info' | 'warning'): void
}>()

const ACCESS_OPTIONS = [
  { value: 'public' as const, label: '公开链接', desc: '拿到链接的人都能直接下载' },
  { value: 'secret' as const, label: '密钥访问', desc: '需要输入访问密码才能下载' },
]

const accessMode = ref<'public' | 'secret'>('secret')
const secret = ref(generateShareSecret())
const expiryMode = ref<'preset' | 'custom' | 'permanent'>('preset')
const presetValue = ref<ExpiryPresetValue>('7d')
const customValue = ref<number | null>(null)
const customUnit = ref<'hour' | 'day'>('day')
const submitting = ref(false)
const errorMessage = ref('')
const result = ref<FileShareItem | null>(null)
const copied = ref<'' | 'url' | 'secret' | 'all'>('')
/** 结果态二维码：只编码链接，不编码密码（扫码者仍需自行输入密码）。 */
const qrDataUrl = ref('')
const resultExpiryHint = ref('')

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

/** 业务数据类文件名给一句额外提醒：分享出去最容易出事的就是这类表。 */
const isSensitiveName = computed(() =>
  /(cmdb|资产|明细|清单|名单|客户|合同|财务|工资|薪酬|密码|密钥|token|export)/i.test(props.fileName),
)

const fileSizeLabel = computed(() => {
  const size = Number(props.fileSize || 0)
  if (!(size > 0)) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
})

function isPresetActive(value: ExpiryPresetValue): boolean {
  return isExpiryPresetActive(expiryChoice.value, value)
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
  resultExpiryHint.value = ''
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

/** 生成二维码；库不可用或链接为空时静默降级（链接行仍可复制）。 */
async function loadQrCode(url: string): Promise<void> {
  qrDataUrl.value = url ? await generateQRCodeDataUrl(url) : ''
}

/** 一键复制「文件名 + 链接 + 密码」，省掉分别复制两次。 */
async function copyShareInfo(): Promise<void> {
  if (!result.value) return
  const text = buildShareCopyText({
    title: result.value.file_name,
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
  if (!props.filePath && !props.artifactId) {
    errorMessage.value = '缺少文件路径，无法分享'
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

  // 提交的 expiry 与弹窗展示的 expiry 必须是同一份数据，否则会出现
  // 「底部写永久有效、实际创建了 7 天」这种只有用户过几天才发现的偏差。
  const expiry: ShareExpiryPayload = { ...expiryChoice.value }
  const hintText = expiryHint.value

  submitting.value = true
  errorMessage.value = ''
  try {
    const created = await createFileShare(
      {
        path: props.filePath || undefined,
        artifact_id: props.artifactId || undefined,
        access_mode: accessMode.value,
        secret: accessMode.value === 'secret' ? secret.value.trim() : undefined,
        expiry,
      },
      props.authHeaders,
    )
    result.value = created
    resultExpiryHint.value = hintText
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
