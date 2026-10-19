<script setup lang="ts">
/**
 * API 工具「向导模式」：把一条真实请求变成可用的工具。
 *
 * **它没有自己的数据模型**——一切写回父组件传进来的 ``toolForm``（同一份对象）。
 * 另起一套表单状态意味着两套字段、两次校验、以及「切 tab 丢内容」，这是本次设计里
 * 最不能碰的红线（契约测试会直接断言向导源码里不存在第二个表单对象）。
 *
 * 四步的由来：懂得写 curl 的人不需要向导；需要向导的人卡在「URL 里 {param} 是什么」、
 * 「参数必填还是可选」、「我配的对不对」。所以第 1 步只让他粘贴，第 3 步把判断讲清楚，
 * 第 4 步让他**先试跑再保存**。
 */
import { computed, ref, watch } from 'vue'
import { CheckIcon } from '@heroicons/vue/24/outline'
import { isParsedRequest, parseRequestInput, type ParsedRequest } from '../../utils/toolImport'
import { buildLocationMap, buildDraftParams, buildDraftSchema, type DraftParam } from '../../utils/toolDraft'
import { parseToolSchema } from '../../utils/toolSchema'
import ToolParamEditor from './ToolParamEditor.vue'
import ApiToolTester from './ApiToolTester.vue'

const props = defineProps<{
    toolForm: {
        name: string
        description: string
        method: string
        url_template: string
        headers_str: string
        parameter_schema_str: string
        is_active: boolean
        group_name: string
    }
}>()

const STEPS = [
    { id: 1, title: '粘贴请求', hint: '从接口文档复制一条 curl 或 URL' },
    { id: 2, title: '起名与描述', hint: '让模型知道什么场景该用它' },
    { id: 3, title: '确认参数', hint: '已帮你判好必填与落位' },
    { id: 4, title: '试跑一次', hint: '真调一次，确认能通' },
]

const step = ref(1)
const pasteText = ref('')
const parseError = ref('')
const parsed = ref<ParsedRequest | null>(null)
const warnings = ref<string[]>([])
const draftParams = ref<DraftParam[]>([])
/** 第 1 步的粘贴内容在解析之后又被改过：参数可能已过期，但**不自动覆盖** */
const staleParams = ref(false)
/** 解析成功时所依据的那段文本：用来区分「真的改了」与「只是被程序重新赋值」 */
const parsedFromText = ref('')

/** 一键填示例：接口文档里最常见的那种 curl，路径参数 / 请求头 / 请求体都占全了。 */
const EXAMPLE_CURL = `curl -X POST 'https://api.example.com/v1/orders/{order_id}/refund' \\
  -H 'Authorization: Bearer sk-xxx' \\
  -H 'Content-Type: application/json' \\
  -d '{"amount": 100, "reason": "客户申请退款"}'`

const existingSystemParams = () =>
    parseToolSchema(props.toolForm.parameter_schema_str)?.systemParams ?? []

const writeDraftParams = (params: DraftParam[]) => {
    draftParams.value = params
    // 重新生成普通参数时不能丢掉用户已配置的系统级参数（它们不在 properties 里）
    props.toolForm.parameter_schema_str = buildDraftSchema(params, existingSystemParams())
    staleParams.value = false
}

/** 唯一会覆盖参数的操作：由按钮点击触发（解析成功后的首次生成走 writeDraftParams）。 */
const regenerateParams = () => {
    if (!parsed.value) return
    writeDraftParams(buildDraftParams(parsed.value))
}

const applyParsed = (result: ParsedRequest) => {
    parsed.value = result
    warnings.value = result.warnings
    parseError.value = ''
    props.toolForm.method = result.method
    props.toolForm.url_template = result.urlTemplate
    props.toolForm.headers_str = JSON.stringify(result.headers, null, 2)
    writeDraftParams(buildDraftParams(result))
    parsedFromText.value = pasteText.value
    step.value = 2
}

const parsePasted = () => {
    const result = parseRequestInput(pasteText.value)
    if (!isParsedRequest(result)) {
        parseError.value = result.error
        parsed.value = null
        warnings.value = []
        return
    }
    applyParsed(result)
}

/** 插入示例后顺手解析掉——这是给「想立刻看看效果」的人用的 */
const insertExample = () => {
    pasteText.value = EXAMPLE_CURL
    parsePasted()
}

watch(pasteText, (value) => {
    // 只标记「可能过期」，且只在内容**真的变了**时标记：这里若自动重算，
    // 用户在第 3 步手改过的描述/默认值就被悄悄抹掉了。
    if (parsed.value && value !== parsedFromText.value) staleParams.value = true
})

const locationMap = computed(() => buildLocationMap(draftParams.value))
const requiredCount = computed(() => draftParams.value.filter((param) => param.required).length)
const optionalCount = computed(() => draftParams.value.length - requiredCount.value)
const methodWarning = computed(() => props.toolForm.method !== 'GET')

// ---------------------------------------------------------------------------
// 步骤门禁：这步没填完 / 没解析成功，就不许往下走
// ---------------------------------------------------------------------------
/** 第 3 步要看**编辑器里的实时内容**，而不是解析时的快照——用户可能刚补了描述 */
const liveSchema = computed(() => parseToolSchema(props.toolForm.parameter_schema_str))
const paramsMissingDescription = computed(() => {
    const schema = liveSchema.value
    if (!schema) return [] as string[]   // JSON 还没写合法，交给编辑器自己提示
    return schema.params
        .filter((param) => param.required && !String(param.description || '').trim())
        .map((param) => param.name)
})

/** 每一步「还差什么」，空字符串代表这步已通过 */
const stepBlockers = computed<Record<number, string>>(() => {
    const blockers: Record<number, string> = {}
    if (!parsed.value) {
        blockers[1] = '还差一步：粘贴一条 curl 或 URL，然后点「解析这条请求」'
    }
    if (!props.toolForm.name.trim()) {
        blockers[2] = '工具名称还没填——模型就是靠它来选择工具的'
    }
    if (paramsMissingDescription.value.length) {
        blockers[3] = `还有 ${paramsMissingDescription.value.length} 个必填参数没写描述：${paramsMissingDescription.value.join('、')}`
    }
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
/** 回退与停留永远允许；向前只能走到「全部前序都已通过」的位置 */
const canGoTo = (target: number) => target <= maxStep.value

const goToStep = (target: number) => {
    if (canGoTo(target)) step.value = target
}
</script>

<template>
  <div class="space-y-5">
    <!-- 步骤条：四步一排不折行；可点击回退，也能跳到「前序都已通过」的位置 -->
    <ol class="grid grid-cols-4 items-stretch gap-2" aria-label="向导步骤">
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

    <!-- ① 粘贴请求 -->
    <section v-if="step === 1" class="space-y-3">
      <div>
        <label class="block text-sm font-medium text-gray-700">粘贴一条请求</label>
        <p class="mt-0.5 text-xs text-gray-500">
          直接把接口文档里的 curl 命令整段复制进来，或者粘贴一条带参数的完整 URL。我们会读出方法、地址、请求头和参数。
        </p>
        <textarea
          v-model="pasteText"
          rows="4"
          class="mt-1 block w-full rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-primary focus:ring-primary"
          placeholder="curl -X POST 'https://api.example.com/v1/orders/{order_id}/refund' -H 'Authorization: Bearer sk-xxx' -d '{&quot;amount&quot;: 100}'"
        ></textarea>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="rounded-md bg-primary px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-primary-dark"
          @click="parsePasted"
        >解析这条请求</button>
        <button
          type="button"
          class="rounded-md border border-gray-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
          @click="insertExample"
        >插入示例</button>
        <span class="text-xs text-gray-400">不会发起请求，只是先读懂它</span>
      </div>

      <p v-if="parseError" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
        {{ parseError }}
      </p>

      <div v-if="parsed" class="space-y-2 rounded-lg border border-gray-200 bg-gray-50/60 p-3">
        <div class="grid grid-cols-1 gap-1.5 text-xs sm:grid-cols-2">
          <div><span class="text-gray-400">方法：</span><span class="font-mono font-semibold text-gray-800">{{ parsed.method }}</span></div>
          <div class="sm:col-span-2"><span class="text-gray-400">地址：</span><span class="font-mono break-all text-gray-800">{{ parsed.urlTemplate }}</span></div>
          <div v-if="parsed.pathParams.length"><span class="text-gray-400">路径参数：</span><span class="font-mono text-gray-800">{{ parsed.pathParams.join('、') }}</span></div>
          <div v-if="parsed.queryParams.length"><span class="text-gray-400">查询参数：</span><span class="font-mono text-gray-800">{{ parsed.queryParams.map((p) => p.name).join('、') }}</span></div>
          <div v-if="parsed.bodyParams.length"><span class="text-gray-400">请求体参数：</span><span class="font-mono text-gray-800">{{ parsed.bodyParams.map((p) => p.name).join('、') }}</span></div>
          <div v-if="Object.keys(parsed.headers).length"><span class="text-gray-400">请求头：</span><span class="font-mono text-gray-800">{{ Object.keys(parsed.headers).join('、') }}</span></div>
        </div>
      </div>

      <div v-if="methodWarning && parsed" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
        这是 <b class="font-mono">{{ toolForm.method }}</b> 请求：平台把这里登记的工具当作「只读能力」交给智能体，但<b>请求会真实发出</b>。
        请确认它确实是查询类接口，不会产生下单、扣款之类的副作用。
      </div>

      <ul v-if="warnings.length" class="space-y-1 rounded-lg border border-amber-100 bg-amber-50/60 px-3 py-2">
        <li v-for="(item, index) in warnings" :key="index" class="text-[11px] leading-relaxed text-amber-800">· {{ item }}</li>
      </ul>

      <details class="rounded-lg border border-gray-200 px-3 py-2">
        <summary class="cursor-pointer text-xs font-medium text-gray-600">没有 curl，只有接口文档？</summary>
        <p class="mt-1.5 text-[11px] leading-relaxed text-gray-500">
          照着文档拼一条 URL 直接粘进来也行，例如 <code class="font-mono">https://api.example.com/v1/weather?city=&#123;city&#125;&amp;days=3</code>。
          需要让模型填值的地方写成 <code class="font-mono">&#123;参数名&#125;</code>；写死固定值就直接写值。
        </p>
      </details>
    </section>

    <!-- ② 起名与描述 -->
    <section v-else-if="step === 2" class="space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700">工具名称 <span class="text-red-500">*</span></label>
        <input
          v-model="toolForm.name"
          class="mt-1 block w-full rounded-md border-gray-300 font-mono text-sm shadow-sm focus:border-primary focus:ring-primary"
          placeholder="e.g. query_weather"
        />
        <p class="mt-1 text-xs text-gray-500">模型靠它选择工具，用英文小写加下划线最稳妥</p>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700">描述</label>
        <textarea
          v-model="toolForm.description"
          rows="3"
          class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-primary focus:ring-primary"
          placeholder="e.g. 查询指定城市的实时天气。当用户问「今天天气怎么样」时使用。"
        ></textarea>
        <p class="mt-1 text-xs leading-relaxed text-gray-500">
          写清「<b>什么场景下该用它</b>」比写「它做什么」更重要——模型是按场景选工具的。把用户可以怎么问也写进去，命中率会明显提高。
        </p>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700">业务分组</label>
        <input
          v-model="toolForm.group_name"
          list="tool-group-options"
          class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-primary focus:ring-primary"
          placeholder="e.g. 履约罚款（留空表示未分组）"
        />
        <p class="mt-1 text-xs text-gray-500">智能体配置的「工具能力」步骤会按它归组；留空则该工具落在「其他扩展工具」</p>
      </div>
    </section>

    <!-- ③ 确认参数 -->
    <section v-else-if="step === 3" class="space-y-3">
      <div v-if="staleParams" class="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
        <p class="flex-1 text-xs leading-relaxed text-amber-800">
          你在第 1 步改过请求地址：这里的参数可能已过期。<b>不会自动覆盖</b>你手改过的内容。
        </p>
        <button
          type="button"
          class="rounded-md border border-amber-300 bg-white px-2.5 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100"
          @click="regenerateParams"
        >重新生成参数</button>
      </div>

      <p class="text-xs text-gray-500">
        从你的请求里识别出 <b class="text-gray-700">{{ draftParams.length }}</b> 个参数：
        <b class="text-gray-700">{{ requiredCount }}</b> 个必填、<b class="text-gray-700">{{ optionalCount }}</b> 个可选。
        原始请求里已经带了值的参数，我们把它设成「可选 + 默认值」——模型不填就走那个值。
      </p>

      <ToolParamEditor
        v-model="toolForm.parameter_schema_str"
        mode="wizard"
        :locations="locationMap"
      />

      <details v-if="draftParams.length" class="rounded-lg border border-gray-200 px-3 py-2">
        <summary class="cursor-pointer text-xs font-medium text-gray-600">为什么这么判？</summary>
        <ul class="mt-1.5 space-y-1">
          <li v-for="param in draftParams" :key="param.name" class="text-[11px] leading-relaxed text-gray-500">
            · <code class="font-mono text-gray-700">{{ param.name }}</code>：{{ param.reason }}
          </li>
        </ul>
      </details>
    </section>

    <!-- ④ 试跑一次 -->
    <section v-else class="space-y-3">
      <ApiToolTester
        :method="toolForm.method"
        :url-template="toolForm.url_template"
        :headers-str="toolForm.headers_str"
        :schema-str="toolForm.parameter_schema_str"
      />
      <p class="rounded-lg border border-gray-100 bg-white px-3 py-2 text-[11px] leading-relaxed text-gray-500">
        试跑通过后，点弹窗右下角的「保存」正式创建。保存后还需要到智能体配置里把这个工具挂给对应的智能体，它才会生效。
      </p>
    </section>

    <!-- 底部导航：走不动时把「还差什么」直接摆出来，而不是让按钮默默灰着 -->
    <div class="space-y-2 border-t border-gray-100 pt-3">
      <p v-if="currentBlocker" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
        {{ currentBlocker }}
      </p>
      <div class="flex items-center justify-between">
        <button
          type="button"
          :disabled="step === 1"
          class="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
          @click="goToStep(step - 1)"
        >上一步</button>
        <span class="text-[11px] text-gray-400">第 {{ step }} / {{ STEPS.length }} 步</span>
        <button
          type="button"
          :disabled="!canGoTo(step + 1)"
          class="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-40"
          @click="goToStep(step + 1)"
        >下一步</button>
      </div>
    </div>
  </div>
</template>
