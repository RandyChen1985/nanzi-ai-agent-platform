<script setup lang="ts">
/**
 * 请求签名开关与密钥管理。
 *
 * 密钥在浏览器生成，提交后服务端只保存密文；编辑已有工具时密钥框永远为空，
 * 留空表示沿用已保存密钥。组件不自己持有表单状态，全部通过 v-model 写回父级
 * 的 toolForm——表单模式和向导模式必须共用同一份数据。
 */
import { computed, ref, watch } from 'vue'
import CopyButton from '../common/CopyButton.vue'
import ConfirmModal from '../ConfirmModal.vue'
import RequestSigningHelpModal from './RequestSigningHelpModal.vue'
import {
  generateSigningKeyId,
  generateSigningSecret,
  isValidSigningKeyId,
  isValidSigningSecret,
} from '../../utils/requestSigning'

const props = withDefaults(defineProps<{
  enabled?: boolean
  keyId?: string | null
  secret?: string
  configured?: boolean
}>(), {
  enabled: false,
  keyId: null,
  secret: '',
  configured: false,
})

const emit = defineEmits<{
  (e: 'update:enabled', value: boolean): void
  (e: 'update:keyId', value: string | null): void
  (e: 'update:secret', value: string): void
}>()

const showHelp = ref(false)
const showSecret = ref(false)
const generationError = ref('')
const confirmingRotate = ref(false)
// 用户手工粘贴密钥时，不能沿用旧 Key-Id：后端要求轮换必须换版本。
// 在 change（失焦/回车）时生成一次，避免每敲一个字符都改 Key-Id。
const manualRotationApplied = ref(false)

// 切换到另一个工具时（密钥框为空）重置手工轮换标记，避免上一个表单的
// “已经换过 Key-Id”状态把下一个工具的密钥粘贴拦住。
watch(
  [() => props.keyId, () => props.configured],
  () => {
    if (!props.secret) manualRotationApplied.value = false
  },
)

watch(
  () => props.secret,
  (value) => {
    // 清空密钥时同步收起明文显示，避免下一次生成的新密钥默认以明文出现。
    if (!value) showSecret.value = false
  },
)

const keyIdValue = computed(() => String(props.keyId || '').trim())
const hasNewSecret = computed(() => Boolean(props.secret))
const keyIdError = computed(() => Boolean(keyIdValue.value) && !isValidSigningKeyId(keyIdValue.value))
const secretError = computed(() => Boolean(props.secret) && !isValidSigningSecret(props.secret))

const generate = () => {
  generationError.value = ''
  manualRotationApplied.value = false
  try {
    emit('update:keyId', generateSigningKeyId())
    emit('update:secret', generateSigningSecret())
    emit('update:enabled', true)
    showSecret.value = true
  } catch (error: any) {
    generationError.value = error?.message || '生成签名密钥失败'
  }
}

const toggleEnabled = (event: Event) => {
  emit('update:enabled', (event.target as HTMLInputElement).checked)
}

const requestGenerate = () => {
  // 已有密钥时先确认，避免误点把还没交付给业务方的密钥覆盖掉。
  if (keyIdValue.value || props.configured) {
    confirmingRotate.value = true
    return
  }
  generate()
}

const confirmGenerate = () => {
  confirmingRotate.value = false
  generate()
}

const ensureKeyIdForManualSecret = () => {
  if (!props.secret) {
    manualRotationApplied.value = false
    return
  }
  if (manualRotationApplied.value) return
  if (!keyIdValue.value || props.configured) {
    emit('update:keyId', generateSigningKeyId())
    emit('update:enabled', true)
    manualRotationApplied.value = true
  }
}
</script>

<template>
  <section class="rounded-xl border border-gray-200 bg-gray-50/60 p-3.5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="flex min-w-0 items-start gap-2.5">
        <label class="relative mt-0.5 inline-flex cursor-pointer items-center">
          <input
            type="checkbox"
            class="peer sr-only"
            :checked="enabled"
            aria-label="开启请求签名"
            @change="toggleEnabled"
          />
          <span class="h-5 w-9 rounded-full bg-gray-300 transition-colors peer-checked:bg-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary/30"></span>
          <span class="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform peer-checked:translate-x-4"></span>
        </label>
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-1.5">
            <h4 class="text-sm font-bold text-gray-800">请求签名</h4>
            <span class="rounded bg-gray-200 px-1.5 py-0.5 font-mono text-[10px] text-gray-600">HMAC-SHA256</span>
            <button
              type="button"
              class="inline-flex h-5 w-5 items-center justify-center rounded-full border border-gray-300 bg-white text-[11px] font-bold text-gray-500 transition-colors hover:border-primary hover:text-primary"
              title="查看请求签名接入说明"
              aria-label="查看请求签名接入说明"
              @click="showHelp = true"
            >?</button>
          </div>
          <p class="mt-0.5 text-xs leading-relaxed text-gray-500">
            开启后平台对最终请求签名，业务方可用共享密钥验证来源与完整性；关闭时完全忽略签名配置。
          </p>
        </div>
      </div>

      <div class="flex shrink-0 items-center gap-2">
        <span
          v-if="enabled"
          class="rounded-full px-2 py-0.5 text-[10px] font-semibold"
          :class="configured || hasNewSecret ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'"
        >{{ configured || hasNewSecret ? '已配置密钥' : '待生成密钥' }}</span>
        <button
          type="button"
          class="rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
          @click="requestGenerate"
        >{{ keyIdValue || configured ? '重新生成' : '生成密钥' }}</button>
      </div>
    </div>

    <div v-if="enabled" class="mt-3 space-y-3 border-t border-gray-200 pt-3">
      <div class="space-y-3">
        <div class="space-y-1">
          <label class="block text-xs font-medium text-gray-700">Key-Id</label>
          <div class="flex items-center gap-1.5">
            <input
              :value="keyIdValue"
              readonly
              class="min-w-0 flex-1 rounded-md border border-gray-300 bg-white px-2.5 py-1.5 font-mono text-xs text-gray-700 outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
              placeholder="点击「生成密钥」自动生成"
            />
            <CopyButton v-if="keyIdValue" :text="keyIdValue" label="Key-Id" />
          </div>
          <p v-if="keyIdError" class="text-[11px] text-red-600">Key-Id 必须是 16 位小写十六进制</p>
        </div>

        <div class="space-y-1">
          <label class="block text-xs font-medium text-gray-700">签名密钥</label>
          <div class="flex items-center gap-1.5">
            <input
              :value="secret"
              :type="showSecret ? 'text' : 'password'"
              class="min-w-0 flex-1 rounded-md border border-gray-300 bg-white px-2.5 py-1.5 font-mono text-xs text-gray-700 outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
              :placeholder="configured ? '留空沿用已保存密钥，填写则轮换' : '点击「生成密钥」自动生成'"
              autocomplete="new-password"
              @input="emit('update:secret', ($event.target as HTMLInputElement).value)"
              @change="ensureKeyIdForManualSecret"
            />
            <button
              v-if="secret"
              type="button"
              class="shrink-0 whitespace-nowrap rounded-md border border-gray-300 bg-white px-2 py-1.5 text-[11px] text-gray-600 hover:bg-gray-50"
              :aria-label="showSecret ? '隐藏签名密钥' : '显示签名密钥'"
              @click="showSecret = !showSecret"
            >{{ showSecret ? '隐藏' : '显示' }}</button>
            <CopyButton v-if="secret" :text="secret" label="签名密钥" />
          </div>
          <p v-if="secretError" class="text-[11px] text-red-600">密钥必须是 32-128 个可打印 ASCII 字符</p>
          <p v-else-if="configured && !secret" class="text-[11px] text-gray-400">已保存密钥，留空表示不轮换</p>
        </div>
      </div>

      <p v-if="generationError" class="rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-700">
        {{ generationError }}
      </p>
      <p class="text-[11px] leading-relaxed text-amber-700">
        密钥只在生成时可见，保存后不再回显；请立即复制并线下交付业务方。轮换时请同时更新业务方配置。
      </p>
    </div>
  </section>

  <RequestSigningHelpModal :show="showHelp" @close="showHelp = false" />
  <ConfirmModal
    v-if="confirmingRotate"
    title="重新生成签名密钥？"
    message="保存后平台会立即改用新 Key-Id 签名；请先确认业务方已准备好接收新密钥，再继续。"
    confirm-text="生成新密钥"
    cancel-text="取消"
    type="warning"
    @confirm="confirmGenerate"
    @cancel="confirmingRotate = false"
  />
</template>
