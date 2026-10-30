<script setup lang="ts">
/**
 * 工具包导入弹窗：选文件 → 服务端预检 → 逐条决定 → 单事务落库 → 结果摘要。
 *
 * 刻意不在前端做字段校验与查重：那会是第二份会漂移的规则。这里只负责
 * 「把预检结果摆清楚、把用户的选择原样交给服务端」。
 */
import { computed, ref, watch } from 'vue'
import Modal from '../Modal.vue'
import {
  toolBundleApi,
  type ToolBundle,
  type ToolBundleImportAction,
  type ToolBundleImportDecision,
  type ToolBundleImportResult,
  type ToolBundlePrecheckItem,
  type ToolBundlePrecheckResult,
} from '@/api/tool'
import { bundleKindLabel, parseBundleText } from '@/utils/toolBundle'
import { buildScriptToolPrompt, SCRIPT_PROMPT_EXAMPLES, type ScriptPromptExample } from '@/constants/toolBundlePrompt'
import { copyToClipboard } from '@/utils/clipboard'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'imported', result: ToolBundleImportResult): void
}>()

interface RowDecision {
  action: ToolBundleImportAction
  renameTo: string
}

const bundle = ref<ToolBundle | null>(null)
const precheck = ref<ToolBundlePrecheckResult | null>(null)
const result = ref<ToolBundleImportResult | null>(null)
const fileName = ref('')
const fileSize = ref(0)
const decisions = ref<Record<number, RowDecision>>({})
const errorMessage = ref('')
const prechecking = ref(false)
const importing = ref(false)
const dragActive = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

function reset() {
  bundle.value = null
  precheck.value = null
  result.value = null
  fileName.value = ''
  fileSize.value = 0
  decisions.value = {}
  errorMessage.value = ''
  prechecking.value = false
  importing.value = false
  dragActive.value = false
  if (fileInput.value) fileInput.value.value = ''
}

watch(
  () => props.show,
  (visible) => {
    if (visible) reset()
  },
)

function close() {
  if (importing.value) return
  emit('close')
}

function extractError(error: any): string {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((item: any) => item?.msg || JSON.stringify(item)).join('；')
  }
  return error?.message || '操作失败'
}

function decideDefault(item: ToolBundlePrecheckItem): RowDecision {
  if (item.status === 'new') return { action: 'create', renameTo: item.name }
  // 冲突与不合法一律默认跳过：默认覆盖别人的同名工具太危险
  return { action: 'skip', renameTo: item.suggested_name || `${item.name}_2` }
}

async function runPrecheck() {
  if (!bundle.value) return
  prechecking.value = true
  errorMessage.value = ''
  try {
    const res = await toolBundleApi.precheck(bundle.value)
    precheck.value = res.data
    const next: Record<number, RowDecision> = {}
    for (const item of res.data.items) next[item.index] = decideDefault(item)
    decisions.value = next
  } catch (error: any) {
    errorMessage.value = extractError(error)
  } finally {
    prechecking.value = false
  }
}

/** 文件与粘贴共用的入口：解析文本 → 预检。抛错交给调用方展示。 */
async function applyBundleText(text: string, name: string, size: number) {
  reset()
  fileName.value = name
  fileSize.value = size
  bundle.value = parseBundleText(text)
  await runPrecheck()
}

async function handleFile(file: File) {
  try {
    await applyBundleText(await file.text(), file.name, file.size)
  } catch (error: any) {
    errorMessage.value = error?.message || '读取文件失败'
  }
}

function onFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (file) void handleFile(file)
}

function onDrop(event: DragEvent) {
  dragActive.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) void handleFile(file)
}

function openPicker() {
  fileInput.value?.click()
}

function decisionFor(index: number): RowDecision {
  return decisions.value[index] || { action: 'skip', renameTo: '' }
}

/** 模板里直接 decisions[index].renameTo 会被 TS 判成 possibly undefined */
function renameValue(index: number): string {
  return decisionFor(index).renameTo
}

function setRenameTo(index: number, event: Event) {
  const value = (event.target as HTMLInputElement).value
  decisions.value[index] = { ...decisionFor(index), renameTo: value }
}

function pendingCredentialText(name: string): string {
  return (result.value?.pending_credentials?.[name] || []).join('、')
}

function onActionChange(item: ToolBundlePrecheckItem, event: Event) {
  const action = (event.target as HTMLSelectElement).value as ToolBundleImportAction
  decisions.value[item.index] = { ...decisionFor(item.index), action }
}

const counts = computed(() => {
  let create = 0
  let overwrite = 0
  let rename = 0
  for (const item of precheck.value?.items || []) {
    const action = decisionFor(item.index).action
    if (action === 'create') create += 1
    else if (action === 'overwrite') overwrite += 1
    else if (action === 'rename') rename += 1
  }
  return { create, overwrite, rename, total: create + overwrite + rename }
})

const renameProblem = computed(() => {
  for (const item of precheck.value?.items || []) {
    const decision = decisionFor(item.index)
    if (decision.action !== 'rename') continue
    if (!decision.renameTo.trim()) return '另存为的工具名不能为空'
    if (decision.renameTo.trim() === item.name) return '另存为的工具名必须与原名称不同'
  }
  return ''
})

const canSubmit = computed(
  () => !importing.value && !prechecking.value && counts.value.total > 0 && !renameProblem.value,
)

function buildDecisions(): ToolBundleImportDecision[] {
  return Object.entries(decisions.value).map(([index, decision]) => ({
    index: Number(index),
    action: decision.action,
    rename_to: decision.action === 'rename' ? decision.renameTo.trim() : undefined,
  }))
}

async function confirmImport() {
  if (!bundle.value || !canSubmit.value) return
  importing.value = true
  errorMessage.value = ''
  try {
    const res = await toolBundleApi.importBundle(bundle.value, buildDecisions())
    result.value = res.data
    emit('imported', res.data)
  } catch (error: any) {
    errorMessage.value = extractError(error)
  } finally {
    importing.value = false
  }
}

const statusLabel: Record<string, string> = {
  new: '新建',
  conflict: '同名冲突',
  invalid: '校验未通过',
}

function statusClass(item: ToolBundlePrecheckItem): string {
  if (item.status === 'new') return 'bg-emerald-50 text-emerald-700 border-emerald-200'
  if (item.status === 'invalid') return 'bg-red-50 text-red-700 border-red-200'
  return 'bg-amber-50 text-amber-700 border-amber-200'
}

const pendingCredentialNames = computed(() => Object.keys(result.value?.pending_credentials || {}))

// ------------------------------------------------------------------ AI 生成
// 两种导入入口：手上已有 JSON，或让 AI 按平台规范现写一个。
// 提示词正文在 constants/toolBundlePrompt.ts，这里只负责「填需求、复制」。
type ImportTab = 'file' | 'paste' | 'ai'

const activeTab = ref<ImportTab>('file')
const aiRequirement = ref('')
const copied = ref(false)
const copyFailed = ref(false)

const promptText = computed(() => buildScriptToolPrompt(aiRequirement.value))

// ------------------------------------------------------------------ 粘贴导入
// 从聊天窗口复制来的 JSON、AI 直接吐出来的代码块，都走这条路：
// 不落盘、不找文件，解析后与上传文件走同一套预检。
/** 与后端 MAX_BUNDLE_BYTES 保持一致 */
const MAX_BUNDLE_BYTES = 8 * 1024 * 1024

const pastedText = ref('')
const pasteError = ref('')

async function precheckPastedText() {
  const text = pastedText.value.trim()
  if (!text) {
    pasteError.value = '请先把 JSON 内容粘贴进来'
    return
  }
  pasteError.value = ''
  // 后端上限 8MB（MAX_BUNDLE_BYTES），先拦一道，省得贴完才看到 413
  if (new Blob([text]).size > MAX_BUNDLE_BYTES) {
    pasteError.value = `内容超过 ${MAX_BUNDLE_BYTES / 1024 / 1024}MB，请改用文件导入，或只粘贴需要的工具`
    return
  }
  try {
    await applyBundleText(text, '粘贴的 JSON', new Blob([text]).size)
    // 预检表挂在「文件」页签下，切过去才能看到结果（页签头在预检态会隐藏）
    activeTab.value = 'file'
  } catch (error: any) {
    pasteError.value = error?.message || '解析失败，请检查 JSON 是否完整'
  }
}

function applyExample(example: ScriptPromptExample) {
  aiRequirement.value = example.requirement
}

async function copyPrompt() {
  copyFailed.value = false
  const ok = await copyToClipboard(promptText.value)
  if (!ok) {
    copyFailed.value = true
    return
  }
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 2000)
}
</script>

<template>
  <Modal title="导入工具 JSON" size="max-w-3xl" :show="show" @close="close">
    <div class="space-y-4">
      <!-- 结果视图：导入完成后不再展示预检表，避免用户误以为还能再点一次 -->
      <template v-if="result">
        <div class="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          导入完成：新建 <strong>{{ result.created }}</strong> 个，覆盖
          <strong>{{ result.updated }}</strong> 个，跳过 <strong>{{ result.skipped }}</strong> 个。
        </div>
        <div v-if="result.created_items.length || result.updated_items.length" class="max-h-64 overflow-y-auto rounded-lg border border-gray-200">
          <table class="min-w-full text-xs">
            <thead class="bg-gray-50 text-left text-gray-500">
              <tr>
                <th class="px-3 py-2 font-medium">结果</th>
                <th class="px-3 py-2 font-medium">类型</th>
                <th class="px-3 py-2 font-medium">工具名</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <tr v-for="item in result.created_items" :key="`c-${item.index}`">
                <td class="px-3 py-1.5 text-emerald-700">新建</td>
                <td class="px-3 py-1.5 text-gray-600">{{ bundleKindLabel(item.kind) }}</td>
                <td class="px-3 py-1.5 font-mono text-gray-800">{{ item.name }}</td>
              </tr>
              <tr v-for="item in result.updated_items" :key="`u-${item.index}`">
                <td class="px-3 py-1.5 text-sky-700">覆盖</td>
                <td class="px-3 py-1.5 text-gray-600">{{ bundleKindLabel(item.kind) }}</td>
                <td class="px-3 py-1.5 font-mono text-gray-800">{{ item.name }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div
          v-if="pendingCredentialNames.length"
          class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800"
        >
          <p class="mb-1 font-semibold">以下工具还缺凭据，启用前请先在工具管理里补上：</p>
          <ul class="list-inside list-disc space-y-0.5">
            <li v-for="name in pendingCredentialNames" :key="name">
              <span class="font-mono">{{ name }}</span>：{{ pendingCredentialText(name) }}
            </li>
          </ul>
        </div>
      </template>

      <!-- 两个入口：直接导入已有 JSON，或让 AI 按规范生成一个 -->
      <template v-else>
        <div v-if="!bundle" class="flex items-center gap-1 border-b border-gray-100" role="tablist" aria-label="导入方式">
          <button
            type="button"
            role="tab"
            :aria-selected="activeTab === 'file'"
            class="relative px-3 pb-2 text-sm font-medium transition-colors"
            :class="activeTab === 'file' ? 'text-primary' : 'text-gray-500 hover:text-gray-700'"
            @click="activeTab = 'file'"
          >
            导入 JSON 文件
            <span v-if="activeTab === 'file'" class="absolute inset-x-2 -bottom-px h-0.5 rounded-t bg-primary"></span>
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="activeTab === 'paste'"
            class="relative px-3 pb-2 text-sm font-medium transition-colors"
            :class="activeTab === 'paste' ? 'text-primary' : 'text-gray-500 hover:text-gray-700'"
            @click="activeTab = 'paste'"
          >
            粘贴 JSON
            <span v-if="activeTab === 'paste'" class="absolute inset-x-2 -bottom-px h-0.5 rounded-t bg-primary"></span>
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="activeTab === 'ai'"
            class="relative px-3 pb-2 text-sm font-medium transition-colors"
            :class="activeTab === 'ai' ? 'text-primary' : 'text-gray-500 hover:text-gray-700'"
            @click="activeTab = 'ai'"
          >
            让 AI 生成
            <span v-if="activeTab === 'ai'" class="absolute inset-x-2 -bottom-px h-0.5 rounded-t bg-primary"></span>
          </button>
        </div>

        <template v-if="activeTab === 'file'">
          <!-- 选择文件 -->
          <template v-if="!bundle">
          <div
            class="flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors"
            :class="dragActive ? 'border-primary bg-primary/5' : 'border-gray-300 hover:border-primary/60 hover:bg-gray-50'"
            @click="openPicker"
            @dragover.prevent="dragActive = true"
            @dragleave.prevent="dragActive = false"
            @drop.prevent="onDrop"
          >
            <div class="text-3xl">📦</div>
            <p class="mt-2 text-sm font-medium text-gray-700">把导出的工具 JSON 拖到这里，或点击选择文件</p>
            <p class="mt-1 text-xs text-gray-500">
              文件里的凭据是占位符，导入后需要你补自己的值；签名密钥不会随文件传递。
            </p>
            <input ref="fileInput" type="file" accept="application/json,.json" class="hidden" @change="onFileChange" />
          </div>
          <p v-if="prechecking" class="text-xs text-gray-500">正在预检…</p>
        </template>

        <!-- 预检结果 -->
        <template v-else>
          <div class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
            <span class="truncate">
              <span class="font-mono">{{ fileName }}</span>
              <span class="ml-2 text-gray-400">{{ (fileSize / 1024).toFixed(1) }} KB</span>
            </span>
            <span v-if="precheck">
              共 {{ precheck.total }} 条：新建 {{ precheck.summary.new }}、冲突
              {{ precheck.summary.conflict }}、不合法 {{ precheck.summary.invalid }}
            </span>
            <button type="button" class="text-primary hover:underline" @click="reset">重新选择</button>
          </div>

          <div v-if="precheck" class="max-h-[22rem] overflow-y-auto rounded-lg border border-gray-200">
            <table class="min-w-full text-xs">
              <thead class="sticky top-0 bg-gray-50 text-left text-gray-500">
                <tr>
                  <th class="px-3 py-2 font-medium">工具名</th>
                  <th class="px-3 py-2 font-medium">类型</th>
                  <th class="px-3 py-2 font-medium">状态</th>
                  <th class="px-3 py-2 font-medium">处理方式</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100">
                <tr v-for="item in precheck.items" :key="item.index">
                  <td class="px-3 py-2">
                    <div class="font-mono text-gray-800">{{ item.name || '（缺少名称）' }}</div>
                    <div v-if="item.reason" class="mt-0.5 text-[11px] text-gray-400">{{ item.reason }}</div>
                    <div v-if="item.requires_credentials.length" class="mt-0.5 text-[11px] text-amber-700">
                      需补凭据：{{ item.requires_credentials.join('、') }}
                    </div>
                  </td>
                  <td class="px-3 py-2 text-gray-600">{{ bundleKindLabel(item.kind) }}</td>
                  <td class="px-3 py-2">
                    <span class="inline-flex rounded border px-1.5 py-0.5 text-[11px]" :class="statusClass(item)">
                      {{ statusLabel[item.status] || item.status }}
                    </span>
                  </td>
                  <td class="px-3 py-2">
                    <select
                      v-if="item.status !== 'invalid'"
                      class="w-full rounded border border-gray-300 bg-white px-2 py-1 text-xs"
                      :value="decisionFor(item.index).action"
                      @change="onActionChange(item, $event)"
                    >
                      <option v-if="item.status === 'new'" value="create">新建</option>
                      <option value="skip">跳过</option>
                      <option v-if="item.can_overwrite" value="overwrite">覆盖已有</option>
                      <option value="rename">另存为新工具</option>
                    </select>
                    <span v-else class="text-gray-400">不导入</span>
                    <input
                      v-if="decisionFor(item.index).action === 'rename'"
                      :value="renameValue(item.index)"
                      type="text"
                      class="mt-1 w-full rounded border border-gray-300 px-2 py-1 font-mono text-xs"
                      placeholder="新的工具名"
                      @input="setRenameTo(item.index, $event)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <p v-if="renameProblem" class="text-xs text-red-600">{{ renameProblem }}</p>
        </template>
        </template>

        <!-- 粘贴导入：从聊天/文档里复制来的 JSON 直接贴进去，与上传文件同一套校验 -->
        <template v-else-if="activeTab === 'paste'">
          <div class="space-y-3">
            <div>
              <label for="tool-paste-json" class="mb-1 block text-xs font-medium text-gray-600">把 JSON 内容粘贴到这里</label>
              <textarea
                id="tool-paste-json"
                v-model="pastedText"
                rows="12"
                spellcheck="false"
                class="w-full rounded-lg border border-gray-300 bg-white p-2 font-mono text-[11px] leading-relaxed text-gray-700 outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                placeholder="粘贴整份导出的工具包，或 AI 返回的 JSON 代码块内容（从第一个左花括号到最后一个右花括号）"
              ></textarea>
            </div>
            <div class="flex items-center justify-between gap-2">
              <p class="text-[11px] text-gray-500">
                与上传文件走同一套预检与校验；AI 多说了几句时，只保留 JSON 那一段即可。
              </p>
              <button
                type="button"
                class="shrink-0 rounded-lg bg-primary px-3 py-2 text-sm text-white transition-colors hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="prechecking"
                @click="precheckPastedText"
              >{{ prechecking ? '预检中…' : '预检' }}</button>
            </div>
            <p v-if="pasteError" class="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {{ pasteError }}
            </p>
          </div>
        </template>

        <!-- 让 AI 生成：需求 → 提示词 → 复制给任意 AI → 存成 .json 再回来导入 -->
        <template v-else>
          <div class="space-y-3 text-left">
            <div>
              <div class="mb-1 flex items-center justify-between">
                <label for="tool-ai-requirement" class="text-xs font-medium text-gray-600">1. 描述你要的工具</label>
                <span class="text-[11px] text-gray-400">按「功能 / 输入参数 / 输出 / 特殊要求」写</span>
              </div>
              <textarea
                id="tool-ai-requirement"
                v-model="aiRequirement"
                rows="3"
                class="w-full rounded-lg border border-gray-300 p-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                placeholder="功能：把一段 CSV 按某列分组统计，输出 JSON 摘要。
输入参数：csv（字符串，必填）、group_by（字符串，必填）、sum_column（字符串，可选）
输出：{groups: [{key, count, sum}], total_rows}
特殊要求：纯标准库；列名不存在要给中文错误"
              ></textarea>
              <div class="mt-1.5 flex flex-wrap gap-1.5">
                <button
                  v-for="example in SCRIPT_PROMPT_EXAMPLES"
                  :key="example.label"
                  type="button"
                  class="rounded-full border border-gray-300 bg-white px-2 py-0.5 text-[11px] text-gray-600 transition-colors hover:border-primary/50 hover:text-primary"
                  @click="applyExample(example)"
                >{{ example.label }}</button>
              </div>
            </div>

            <div>
              <div class="mb-1 flex items-center justify-between">
                <label for="tool-ai-prompt" class="text-xs font-medium text-gray-600">2. 复制提示词，发给任意 AI</label>
                <button
                  type="button"
                  class="rounded-md border border-primary/40 bg-white px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/10"
                  @click="copyPrompt"
                >{{ copied ? '已复制' : '复制提示词' }}</button>
              </div>
              <textarea
                id="tool-ai-prompt"
                readonly
                :value="promptText"
                rows="11"
                spellcheck="false"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 p-2 font-mono text-[11px] leading-relaxed text-gray-700 outline-none"
              ></textarea>
              <p class="mt-1 text-[11px] leading-relaxed text-gray-500">
                AI 会返回一段 JSON：存成 <span class="font-mono">.json</span> 文件，切到「导入 JSON 文件」页签导入即可。
                若它多说了几句，只保留 JSON 代码块里的内容。
              </p>
              <p v-if="copyFailed" class="mt-1 text-[11px] text-red-600">
                浏览器拦截了自动复制，请点击上面的文本框后全选复制。
              </p>
            </div>
          </div>
        </template>
      </template>

      <p v-if="errorMessage" class="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
        {{ errorMessage }}
      </p>
    </div>

    <template #footer>
      <div class="flex flex-wrap items-center justify-between gap-2">
        <span v-if="precheck && !result && activeTab === 'file'" class="text-xs text-gray-500">
          将新建 {{ counts.create }} 个、覆盖 {{ counts.overwrite }} 个、另存 {{ counts.rename }} 个
        </span>
        <span v-else></span>
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-600"
            @click="close"
          >{{ result ? '完成' : (activeTab === 'ai' ? '关闭' : '取消') }}</button>
          <button
            v-if="!result && activeTab === 'file'"
            type="button"
            class="rounded-lg bg-primary px-3 py-2 text-sm text-white disabled:opacity-50"
            :disabled="!canSubmit"
            @click="confirmImport"
          >{{ importing ? '导入中…' : '确认导入' }}</button>
        </div>
      </div>
    </template>
  </Modal>
</template>
