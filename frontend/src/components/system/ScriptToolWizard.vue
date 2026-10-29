<script setup lang="ts">
/**
 * 脚本工具向导：新建 / 编辑一段 Python 脚本工具。
 *
 * 四步的顺序是有原因的：先让用户起名与描述（模型靠这两样选工具），再声明参数
 * （决定了 stdin 里有什么），然后才是代码，最后是依赖与超时这些「跑起来才疼」的配置。
 * 把代码放最后一步，是因为前一步的参数表就是写代码时要读的字段清单。
 *
 * 校验分两档：**硬校验**只拦「一定跑不通」的（名称格式、schema 不是合法 JSON、
 * 代码为空），挡住「下一步 / 保存」；**软提示**是经验判断（有没有读 stdin、有没有
 * 自己 pip install），只提醒、不拦人——脚本嘛，用户比我们更知道自己要干什么。
 */
import { computed, ref, watch } from 'vue'
import { CheckIcon } from '@heroicons/vue/24/outline'
import Modal from '../Modal.vue'
import ToolParamEditor from './ToolParamEditor.vue'
import CodeEditor from '../CodeEditor.vue'
import GroupSelect from './GroupSelect.vue'
import ConfirmModal from '../ConfirmModal.vue'

/** 与后端 ``sys_script_tools`` 一致；parameter_schema 兼容「对象」与「JSON 文本」两种来源 */
interface ScriptTool {
  id: string
  name: string
  display_name?: string | null
  description?: string | null
  source: 'builtin' | 'custom'
  language?: string | null
  code?: string | null
  parameter_schema?: Record<string, any> | string | null
  requirements?: string[] | null
  timeout_seconds?: number | null
  group_name?: string | null
  is_active: boolean
}

export interface ScriptToolPayload {
  name: string
  display_name?: string | null
  description?: string | null
  language: 'python'
  code: string
  parameter_schema: Record<string, any>
  requirements: string[]
  timeout_seconds?: number | null
  group_name?: string | null
  is_active: boolean
}

const props = defineProps<{
  show: boolean
  /** 编辑时传入；为空表示新建 */
  tool?: ScriptTool | null
  /** 沙箱位置提示（按部署策略生成）；原样渲染，不传则不显示 */
  sandboxNotice?: string
  /** 已有业务分组，供分组选择器给出候选（仍可直接输入新分组） */
  groupOptions?: { name: string; count?: number }[]
  saving?: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'submit', payload: ScriptToolPayload): void
}>()

const STEPS = [
  { id: 1, title: '基本信息', hint: '名称与描述' },
  { id: 2, title: '参数', hint: '模型能填什么' },
  { id: 3, title: '代码', hint: 'stdin 进、stdout 出' },
  { id: 4, title: '依赖与超时', hint: '跑起来才疼的配置' },
]

const NAME_RE = /^[a-z][a-z0-9_]{2,63}$/

/** 依赖列表里的一行；用对象包一层是为了拿到稳定的 key，避免删中间行时输入框错位 */
interface RequirementRow {
  key: string
  value: string
}

let requirementSeed = 0
const nextRequirementKey = () => {
  requirementSeed += 1
  return `req-${requirementSeed}`
}

const step = ref(1)
const name = ref('')
const displayName = ref('')
const description = ref('')
const groupName = ref('')
/** ToolParamEditor 是受控的 JSON 文本组件，这里也只持有文本，提交时才解析成对象 */
const schemaText = ref('')
const code = ref('')
const requirements = ref<RequirementRow[]>([])
const timeout = ref('')
const isActive = ref(true)

const isBuiltin = computed(() => props.tool?.source === 'builtin')
const isEditing = computed(() => Boolean(props.tool?.id))
const title = computed(() => {
  if (isBuiltin.value) return '编辑内置脚本'
  return isEditing.value ? '编辑脚本工具' : '新建脚本工具'
})

/** 内置脚本可以改（用户选择「可编辑内置」），但名称锁死：改名会失去与内置版本的对应关系 */
const nameReadonly = computed(() => isBuiltin.value)

const schemaToText = (value: ScriptTool['parameter_schema']): string => {
  if (!value) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return ''
  }
}

/** 打开弹窗时把工具内容灌进表单；切工具 / 重开都要重来一次，避免上一次的草稿串台 */
const resetForm = () => {
  const tool = props.tool
  step.value = 1
  name.value = tool?.name || ''
  displayName.value = tool?.display_name || ''
  description.value = tool?.description || ''
  groupName.value = tool?.group_name || ''
  schemaText.value = schemaToText(tool?.parameter_schema)
  code.value = tool?.code || ''
  timeout.value = tool?.timeout_seconds != null ? String(tool.timeout_seconds) : ''
  isActive.value = tool?.is_active ?? true
  requirements.value = (tool?.requirements || [])
    .filter((item) => String(item || '').trim())
    .map((item) => ({ key: nextRequirementKey(), value: String(item).trim() }))
}

watch(
  () => [props.show, props.tool?.id ?? ''] as const,
  ([show]) => {
    if (show) resetForm()
  },
  { immediate: true },
)

// ---------------------------------------------------------------------------
// 硬校验：只拦「一定跑不通」的
// ---------------------------------------------------------------------------
const nameError = computed(() => {
  const value = name.value.trim()
  if (!value) return '工具名称还没填——模型就是靠它来选择工具的'
  if (!NAME_RE.test(value)) {
    return '名称只能用小写字母、数字和下划线，且以字母开头，长度 3–64，例如 read_pdf_page'
  }
  return ''
})

const schemaError = computed(() => {
  const text = schemaText.value.trim()
  // 不声明参数是合法的：脚本自己从 stdin 读空对象
  if (!text) return ''
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return '参数定义不是合法 JSON：可以切到「可视化」编辑，或先修好再继续'
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return '参数定义必须是一个 JSON 对象，例如 { "path": { "type": "string" } }'
  }
  return ''
})

/** 内置脚本库里可能只存元数据（源码在平台代码里），此时不拦人，也不假装有代码 */
const codeMissing = computed(() => !code.value.trim() && !isBuiltin.value)

const timeoutError = computed(() => {
  const raw = timeout.value.trim()
  if (!raw) return ''
  const value = Number(raw)
  if (!Number.isFinite(value) || !Number.isInteger(value) || value < 1) {
    return '超时填正整数秒，例如 60；留空表示跟随平台的工具调用超时'
  }
  if (value > 3600) return '超时不要超过 3600 秒（1 小时）；再长请拆成多个工具'
  return ''
})

/** 每一步「还差什么」，空字符串代表这步已通过 */
const stepBlockers = computed<Record<number, string>>(() => {
  const blockers: Record<number, string> = {}
  if (nameError.value) blockers[1] = nameError.value
  if (schemaError.value) blockers[2] = schemaError.value
  if (codeMissing.value) blockers[3] = '代码还没写：脚本内容不能为空'
  if (timeoutError.value) blockers[4] = timeoutError.value
  return blockers
})

/** 能走到的最远步骤 = 第一个没通过的步骤 */
const maxStep = computed(() => {
  for (const id of [1, 2, 3]) {
    if (stepBlockers.value[id]) return id
  }
  return STEPS.length
})

const currentBlocker = computed(() => stepBlockers.value[step.value] || '')
const canGoTo = (target: number) => target >= 1 && target <= maxStep.value

const goToStep = (target: number) => {
  if (canGoTo(target)) step.value = target
}

const isLastStep = computed(() => step.value === STEPS.length)
const canSubmit = computed(() => !stepBlockers.value[1]
  && !stepBlockers.value[2]
  && !stepBlockers.value[3]
  && !stepBlockers.value[4])

// ---------------------------------------------------------------------------
// 软提示：提醒但不拦人
// ---------------------------------------------------------------------------
const codeHints = computed(() => {
  const hints: string[] = []
  const text = code.value
  if (!text.trim()) return hints
  if (!text.includes('json.load(sys.stdin)')) {
    hints.push('没看到 json.load(sys.stdin)：模型传进来的参数会读不到。确实不需要参数时可以忽略这条。')
  }
  if (!text.includes('json.dump') && !/(^|\n)\s*print\(/.test(text)) {
    hints.push('没看到向 stdout 输出内容（json.dump 或 print）：模型会收到空结果。')
  }
  if (text.includes('pip install') || text.includes('pip3 install')) {
    hints.push('脚本里不要自己 pip install：依赖写在「依赖」步骤，平台会在首次调用时装到工作区并注入 PYTHONPATH。')
  }
  return hints
})

const requirementHints = computed(() => {
  const hints: string[] = []
  const rows = requirements.value.map((row) => row.value.trim()).filter(Boolean)
  const withCommand = rows.filter((row) => /\bpip3?\s+install\b/.test(row))
  if (withCommand.length) {
    hints.push('依赖只填包名（如 pypdf>=4.0），不要写 pip install 命令。')
  }
  const duplicated = rows.filter((row, index) => rows.indexOf(row) !== index)
  if (duplicated.length) {
    hints.push(`有重复的依赖：${[...new Set(duplicated)].join('、')}`)
  }
  return hints
})

const requirementCount = computed(() => requirements.value.filter((row) => row.value.trim()).length)

const addRequirement = () => {
  requirements.value = [...requirements.value, { key: nextRequirementKey(), value: '' }]
}

const removeRequirement = (index: number) => {
  requirements.value = requirements.value.filter((_, i) => i !== index)
}

// ---------------------------------------------------------------------------
// 参数摘要：给用户一个「模型能看到几个参数」的直观数字
// ---------------------------------------------------------------------------
const paramNames = computed<string[]>(() => {
  const text = schemaText.value.trim()
  if (!text) return []
  let parsed: any
  try {
    parsed = JSON.parse(text)
  } catch {
    return []
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return []
  if (parsed.properties && typeof parsed.properties === 'object') {
    return Object.keys(parsed.properties)
  }
  const ignored = ['type', 'required', '$schema', 'properties', 'description', 'definitions']
  return Object.keys(parsed).filter((key) => !ignored.includes(key))
})

const requiredParamCount = computed(() => {
  const text = schemaText.value.trim()
  if (!text) return 0
  try {
    const parsed = JSON.parse(text)
    return Array.isArray(parsed?.required) ? parsed.required.length : 0
  } catch {
    return 0
  }
})

// ---------------------------------------------------------------------------
// 示例与提交
// ---------------------------------------------------------------------------
const CODE_TEMPLATE = `import json
import sys


def main() -> None:
    params = json.load(sys.stdin)
    path = str(params.get("path", "")).strip()
    if not path:
        print("参数 path 不能为空", file=sys.stderr)
        sys.exit(2)

    with open(path, "rb") as fh:
        size = len(fh.read())

    json.dump({"path": path, "size": size}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()`

const SCHEMA_TEMPLATE = `{
  "type": "object",
  "properties": {
    "path": {
      "type": "string",
      "description": "工作区内的文件路径，如 uploads/a.pdf"
    }
  },
  "required": ["path"]
}`

/** 覆盖已有内容是破坏性操作，必须问一句；用项目内的 ConfirmModal，不用原生 confirm */
const pendingOverwrite = ref<null | 'code' | 'schema'>(null)

const insertCodeTemplate = () => {
  if (code.value.trim() && code.value.trim() !== CODE_TEMPLATE.trim()) {
    pendingOverwrite.value = 'code'
    return
  }
  code.value = CODE_TEMPLATE
}

const insertSchemaTemplate = () => {
  if (schemaText.value.trim()) {
    pendingOverwrite.value = 'schema'
    return
  }
  schemaText.value = SCHEMA_TEMPLATE
}

const confirmOverwrite = () => {
  if (pendingOverwrite.value === 'code') code.value = CODE_TEMPLATE
  if (pendingOverwrite.value === 'schema') schemaText.value = SCHEMA_TEMPLATE
  pendingOverwrite.value = null
}

const parsedSchema = (): Record<string, any> => {
  const text = schemaText.value.trim()
  if (!text) return {}
  try {
    const parsed = JSON.parse(text)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

const submit = () => {
  if (!canSubmit.value || props.saving) return
  const finalName = name.value.trim()
  emit('submit', {
    name: finalName,
    display_name: displayName.value.trim() || null,
    description: description.value.trim() || null,
    language: 'python',
    code: code.value,
    parameter_schema: parsedSchema(),
    requirements: requirements.value
      .map((row) => row.value.trim())
      .filter(Boolean)
      // 保序去重：重复的包会让 pip 行为变得难以预测
      .filter((item, index, list) => list.indexOf(item) === index),
    timeout_seconds: timeout.value.trim() ? Number(timeout.value.trim()) : null,
    group_name: groupName.value.trim() || null,
    is_active: isActive.value,
  })
}

const onPrimary = () => {
  if (isLastStep.value) {
    submit()
    return
  }
  goToStep(step.value + 1)
}

const primaryLabel = computed(() => {
  if (isLastStep.value) return '保存'
  return '下一步'
})
</script>

<template>
  <Modal
    :title="title"
    :show="show"
    size="max-w-3xl"
    :z-index="12000"
    @close="emit('close')"
  >
    <div class="space-y-4">
      <!-- 沙箱位置：原样渲染调用方给的提示，避免各页面各写一份口径 -->
      <div
        v-if="sandboxNotice"
        class="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2"
        role="note"
      >
        <span class="mt-0.5 shrink-0 rounded bg-amber-200/70 px-1.5 py-0.5 text-[10px] font-bold text-amber-900">运行位置</span>
        <p class="text-xs leading-relaxed text-amber-800">{{ sandboxNotice }}</p>
      </div>

      <!-- 步骤条：四步一排不折行；可点击回退，也能跳到「前序都已通过」的位置 -->
      <ol class="grid grid-cols-4 items-stretch gap-2" aria-label="脚本工具向导步骤">
        <li v-for="(item, index) in STEPS" :key="item.id" class="flex min-w-0 items-center gap-1.5">
          <button
            type="button"
            class="flex h-full w-full min-w-0 items-start gap-2 rounded-lg border px-2.5 py-2 text-left transition-all"
            :class="[
              step === item.id ? 'border-primary bg-primary/5' : 'border-gray-200 hover:border-gray-300',
              canGoTo(item.id) ? '' : 'cursor-not-allowed opacity-60',
            ]"
            :aria-current="step === item.id ? 'step' : undefined"
            :aria-disabled="canGoTo(item.id) ? undefined : 'true'"
            @click="goToStep(item.id)"
          >
            <span
              class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold"
              :class="step > item.id ? 'bg-green-500 text-white' : step === item.id ? 'bg-primary text-white' : 'bg-gray-200 text-gray-500'"
            >
              <CheckIcon v-if="step > item.id" class="h-3 w-3" />
              <span v-else>{{ item.id }}</span>
            </span>
            <span class="min-w-0 leading-tight">
              <span class="block truncate text-xs font-semibold" :class="step === item.id ? 'text-primary' : 'text-gray-600'">{{ item.title }}</span>
              <span class="mt-0.5 text-[11px] leading-snug text-gray-400">{{ item.hint }}</span>
            </span>
          </button>
          <span v-if="index < STEPS.length - 1" class="shrink-0 text-gray-300">›</span>
        </li>
      </ol>

      <!-- ① 基本信息 -->
      <section v-if="step === 1" class="space-y-4">
        <div v-if="isBuiltin" class="rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2">
          <p class="text-xs leading-relaxed text-amber-900">
            这是平台内置脚本。<b>代码、参数、依赖与超时可以直接改</b>；只要这些运行配置有改动，
            保存后这个工具会被标记为
            <span class="rounded bg-amber-100 px-1 font-medium">已覆盖</span>：
            以你保存的版本为准，平台后续对内置脚本的修复<b>不再影响它</b>，
            随时可以点行尾的「恢复内置默认」退回平台版本。只改分组、展示名或描述
            不会标记覆盖。
          </p>
          <p class="mt-1 text-[11px] text-amber-800">
            名称固定为 <code class="font-mono">{{ tool?.name }}</code>（改名会失去与内置版本的对应关系）。
          </p>
        </div>

        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label class="block text-sm font-medium text-gray-700">工具名称 <span class="text-red-500">*</span></label>
            <input
              v-model="name"
              class="mt-1 block w-full rounded-md border-gray-300 font-mono text-sm shadow-sm focus:border-primary focus:ring-2 focus:ring-primary/20"
              :class="nameReadonly ? 'cursor-not-allowed bg-gray-100 text-gray-500' : ''"
              placeholder="e.g. read_pdf_page"
              :readonly="nameReadonly"
              :aria-invalid="nameError ? 'true' : undefined"
            />
            <p v-if="nameError" class="mt-1 text-xs text-red-600">{{ nameError }}</p>
            <p v-else-if="nameReadonly" class="mt-1 text-xs text-gray-500">内置脚本名称固定，如需新名字请用「复制为新工具」</p>
            <p v-else class="mt-1 text-xs text-gray-500">模型靠它选择工具；仅小写字母、数字、下划线，3–64 位</p>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700">展示名称</label>
            <input
              v-model="displayName"
              class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-primary focus:ring-2 focus:ring-primary/20"
              placeholder="e.g. 读取 PDF 指定页"
            />
            <p class="mt-1 text-xs text-gray-500">给业务方看的名字，留空则显示上面的工具名称</p>
          </div>
        </div>

        <div>
          <label class="block text-sm font-medium text-gray-700">描述</label>
          <textarea
            v-model="description"
            rows="3"
            class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-primary focus:ring-2 focus:ring-primary/20"
            placeholder="e.g. 从工作区的 PDF 中提取指定页的文本。当用户说「把这份 PDF 第 3 页内容摘出来」时使用。"
          ></textarea>
          <p class="mt-1 text-xs leading-relaxed text-gray-500">
            写清「<b>什么场景下该用它</b>」比写「它做什么」更重要——模型是按场景选工具的。把用户可能的问法也写进去，命中率会明显提高。
          </p>
        </div>

        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label class="block text-sm font-medium text-gray-700">业务分组</label>
            <GroupSelect
              v-model="groupName"
              class="mt-1"
              :options="groupOptions || []"
              placeholder="选择已有分组，或直接输入新分组（留空 = 未分组）"
            />
            <p class="mt-1 text-xs text-gray-500">智能体配置的「工具能力」步骤按它归组；与 API 工具共用同一套分组</p>
          </div>
          <div class="flex items-end">
            <label class="flex items-center gap-2 text-sm text-gray-700">
              <input
                v-model="isActive"
                type="checkbox"
                class="h-4 w-4 rounded border-gray-300 text-primary focus:ring-2 focus:ring-primary/20"
              />
              保存后立即启用
            </label>
          </div>
        </div>
      </section>

      <!-- ② 参数 -->
      <section v-else-if="step === 2" class="space-y-3">
        <div class="flex flex-wrap items-start justify-between gap-2">
          <p class="text-xs leading-relaxed text-gray-500">
            这里声明的字段会变成模型能填的入参，调用时以 <code class="font-mono">stdin</code> 的 JSON 对象交给脚本。
            支持 JSON 与可视化两种写法，描述务必写给模型看。
          </p>
          <button
            type="button"
            class="shrink-0 rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
            @click="insertSchemaTemplate"
          >插入示例参数</button>
        </div>

        <!-- 复用整站的参数编辑器：JSON / 可视化双模式与 API 工具完全一致，不另写一套 -->
        <ToolParamEditor v-model="schemaText" scope="script" />

        <p v-if="paramNames.length" class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs leading-relaxed text-gray-600">
          共 <b class="text-gray-800">{{ paramNames.length }}</b> 个参数<span v-if="requiredParamCount">，其中 <b class="text-gray-800">{{ requiredParamCount }}</b> 个必填</span>：
          <span class="font-mono text-gray-700">{{ paramNames.join('、') }}</span>
        </p>
        <p v-else class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs leading-relaxed text-gray-500">
          没有声明参数也可以：脚本会收到一个空对象 <code class="font-mono">{}</code>，适合「固定动作」这类工具。
        </p>

        <p v-if="schemaError" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{{ schemaError }}</p>
      </section>

      <!-- ③ 代码 -->
      <section v-else-if="step === 3" class="space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p class="text-sm font-medium text-gray-700">Python 脚本</p>
            <p class="mt-0.5 text-xs text-gray-500">
              从 <code class="font-mono">stdin</code> 读参数 JSON，结果写 <code class="font-mono">stdout</code>；失败时非 0 退出码。
            </p>
          </div>
          <button
            type="button"
            class="shrink-0 rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
            @click="insertCodeTemplate"
          >插入最小模板</button>
        </div>

        <!-- 代码区用 CodeMirror：行号 + Python 高亮 + 自动缩进，长脚本才找得到行 -->
        <CodeEditor
          v-model="code"
          min-height="16rem"
          max-height="30rem"
          aria-label="Python 脚本代码"
        />

        <p v-if="codeMissing" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          代码不能为空：至少要把参数读进来，再输出一段结果。
        </p>

        <ul v-if="codeHints.length" class="space-y-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
          <li v-for="(hint, index) in codeHints" :key="index" class="text-[11px] leading-relaxed text-amber-800">· {{ hint }}</li>
          <li class="text-[11px] leading-relaxed text-amber-700">以上只是提醒，确认无误可以直接继续。</li>
        </ul>

        <details class="rounded-lg border border-gray-200 px-3 py-2">
          <summary class="cursor-pointer text-xs font-medium text-gray-600">运行契约速查</summary>
          <ul class="mt-1.5 space-y-1 text-[11px] leading-relaxed text-gray-500">
            <li>· 入参：stdin 一个 JSON 对象，键为参数表里的字段名</li>
            <li>· 出参：stdout 文本或 JSON；能解析成 JSON 就结构化返回给模型</li>
            <li>· 失败：退出码非 0，stderr 会作为错误详情回给模型</li>
            <li>· 上限：单次输出 80KB，超出截断；大文件请写工作区并在 JSON 里给 <code class="font-mono">artifacts</code></li>
            <li>· 路径：默认只读写当前工作区，具体范围以「运行位置」提示为准，一律用相对路径</li>
          </ul>
        </details>
      </section>

      <!-- ④ 依赖与超时 -->
      <section v-else class="space-y-4">
        <div>
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p class="text-sm font-medium text-gray-700">依赖</p>
              <p class="mt-0.5 text-xs text-gray-500">
                一行一个包名，写法同 <code class="font-mono">requirements.txt</code>。首次调用时平台装到会话工作区的
                <code class="font-mono">.pylibs</code> 并注入 <code class="font-mono">PYTHONPATH</code>，脚本里直接 import 即可。
              </p>
            </div>
            <button
              type="button"
              class="shrink-0 rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
              @click="addRequirement"
            >+ 添加依赖</button>
          </div>

          <p v-if="requirements.length === 0" class="mt-2 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs text-gray-500">
            暂无依赖。只用标准库时留空即可，装包有独立的时间预算，留空能让首次调用快很多。
          </p>

          <div v-else class="mt-2 space-y-2">
            <div
              v-for="(row, index) in requirements"
              :key="row.key"
              class="flex items-center gap-2 rounded-lg border border-gray-200 bg-white p-2 shadow-sm"
            >
              <input
                v-model="row.value"
                class="min-w-0 flex-1 rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-primary focus:ring-2 focus:ring-primary/20"
                placeholder="pypdf>=4.0"
                :aria-label="`依赖 ${index + 1}`"
              />
              <button
                type="button"
                class="rounded-md p-1 text-gray-400 transition-colors hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/20"
                :title="`删除依赖 ${row.value || index + 1}`"
                :aria-label="`删除依赖 ${row.value || index + 1}`"
                @click="removeRequirement(index)"
              >
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </div>
          </div>

          <ul v-if="requirementHints.length" class="mt-2 space-y-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
            <li v-for="(hint, index) in requirementHints" :key="index" class="text-[11px] leading-relaxed text-amber-800">· {{ hint }}</li>
          </ul>
          <p class="mt-1.5 text-[11px] leading-relaxed text-gray-500">
            不要写 <code class="font-mono">pip install</code> 命令，也不要在脚本里自己装包：沙箱内一般没有网络，装包由平台负责。
          </p>
        </div>

        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label class="block text-sm font-medium text-gray-700">超时（秒）</label>
            <input
              v-model="timeout"
              type="number"
              min="1"
              max="3600"
              inputmode="numeric"
              class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-primary focus:ring-2 focus:ring-primary/20"
              placeholder="留空 = 跟随平台工具调用超时"
            />
            <p v-if="timeoutError" class="mt-1 text-xs text-red-600">{{ timeoutError }}</p>
            <p v-else class="mt-1 text-xs text-gray-500">
              超时后平台停止等待；沙箱内单条命令另有 600 秒硬上限，超出时容器会先杀掉进程
            </p>
          </div>
          <div class="flex items-end">
            <label class="flex items-center gap-2 text-sm text-gray-700">
              <input
                v-model="isActive"
                type="checkbox"
                class="h-4 w-4 rounded border-gray-300 text-primary focus:ring-2 focus:ring-primary/20"
              />
              保存后立即启用
            </label>
          </div>
        </div>

        <div class="rounded-lg border border-gray-200 bg-gray-50/60 px-3 py-2 text-xs leading-relaxed text-gray-600">
          <p class="font-semibold text-gray-800">保存前最后确认</p>
          <ul class="mt-1 space-y-0.5">
            <li>· 名称：<b class="font-mono text-gray-800">{{ name.trim() || '（未填）' }}</b></li>
            <li>· 参数：{{ paramNames.length }} 个<span v-if="requiredParamCount">（必填 {{ requiredParamCount }}）</span></li>
            <li>· 依赖：{{ requirementCount ? `${requirementCount} 个` : '无（仅标准库）' }}</li>
            <li>· 超时：{{ timeout.trim() ? `${timeout} 秒` : '跟随平台配置' }}</li>
          </ul>
          <p v-if="isBuiltin" class="mt-1.5 text-[11px] text-amber-700">
            改动代码、参数、依赖或超时后，保存会把该内置脚本标记为「已覆盖」，平台后续修复不再影响它；只改分组/展示信息不会。
          </p>
        </div>
      </section>
    </div>

    <!-- 底部导航：走不动时把「还差什么」直接摆出来，而不是让按钮默默灰着 -->
    <template #footer>
      <div class="space-y-2">
        <p v-if="currentBlocker" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
          {{ currentBlocker }}
        </p>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <button
            type="button"
            :disabled="step === 1 || saving"
            class="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
            @click="goToStep(step - 1)"
          >上一步</button>
          <span class="text-[11px] text-gray-400">第 {{ step }} / {{ STEPS.length }} 步</span>
          <div class="flex items-center gap-2">
            <button
              type="button"
              :disabled="saving"
              class="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
              @click="emit('close')"
            >取消</button>
            <button
              type="button"
              :disabled="saving || (isLastStep ? !canSubmit : !canGoTo(step + 1))"
              class="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-40"
              @click="onPrimary"
            >{{ saving ? '保存中…' : primaryLabel }}</button>
          </div>
        </div>
      </div>
    </template>
  </Modal>

  <ConfirmModal
    v-if="pendingOverwrite"
    :title="pendingOverwrite === 'code' ? '覆盖已有代码？' : '覆盖已有参数定义？'"
    :message="pendingOverwrite === 'code'
      ? '插入模板会覆盖代码区的全部内容，且无法撤销。'
      : '插入示例会覆盖当前的参数定义，且无法撤销。'"
    confirm-text="覆盖"
    @confirm="confirmOverwrite"
    @cancel="pendingOverwrite = null"
  />
</template>
