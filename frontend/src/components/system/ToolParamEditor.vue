<script setup lang="ts">
/**
 * 参数定义编辑器：JSON 模式 / 可视化模式。
 *
 * 从 ``ToolRegistry.vue`` 整块搬出来的，做成共享组件的原因是「添加工具」现在有
 * 表单模式与向导模式两个 tab——参数编辑 UI 各写一份必然逐渐漂移，而参数定义是
 * 运行时真正消费的东西，两边不一致会直接导致「界面上能填、运行时调不通」。
 *
 * 数据逻辑全部在 ``utils/toolSchema.ts``（纯函数，可用 Node 直接跑往返测试）；
 * 本组件只维护 UI 状态：当前模式、可视化行数据，以及「非法 JSON 不切走」这类交互约定。
 *
 * 组件对 ``modelValue`` 是受控的：JSON 模式直接写回文本，可视化模式经
 * ``serializeToolSchema`` 写回——**绝不自己拼 JSON**（顶层扩展键与嵌套子结构会被抹掉）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
    PARAM_TYPE_OPTIONS,
    parseToolSchema,
    serializeToolSchema,
    validateSystemParams,
    type ParsedToolSchema,
    type SchemaParam,
    type SystemParam,
} from '../../utils/toolSchema'
import { resolveParamLocation, type ParamLocation } from '../../utils/toolDraft'
import PermissionVariableMenu from '../metadata/PermissionVariableMenu.vue'
import ToolSystemParamEditor from './ToolSystemParamEditor.vue'
import { BUILTIN_USER_VARIABLES, collectExtraDataVariables, type UserVariable } from '../../utils/userVariables'

const props = withDefaults(defineProps<{
    modelValue: string
    /** ``wizard`` 时每行额外显示参数落位标签（向导第 3 步用） */
    mode?: 'full' | 'wizard'
    /** 参数名 → 落位；向导模式显式传入，表单模式可由 method + urlTemplate 推导 */
    locations?: Record<string, ParamLocation>
    method?: string
    urlTemplate?: string
    /**
     * 参数落位口径。``script`` 表示参数走 stdin，没有路径/查询/请求体之分，
     * 也没有「系统级参数」——脚本运行时不注入它们，配了也不会生效。
     */
    scope?: 'api' | 'script'
}>(), {
    mode: 'full',
    locations: () => ({}),
    method: 'GET',
    urlTemplate: '',
    scope: 'api',
})

const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()

type VisualParam = SchemaParam & { key: string }

const schemaEditorMode = ref<'json' | 'visual'>('json')
const visualParams = ref<VisualParam[]>([])
const systemParams = ref<SystemParam[]>([])
const parsedSchema = ref<ParsedToolSchema>({
    shape: 'flat',
    hadRequiredList: false,
    rest: {},
    params: [],
    systemParams: [],
})
const jsonHint = ref('')
let visualParamSeed = 0

const nextVisualParamKey = () => {
    visualParamSeed += 1
    return `param-${visualParamSeed}`
}

// 用户变量候选：内置字段 + 当前登录用户 extra_data 里的扩展键。运行时取值与
// 元数据行级权限共用同一套口径，见 utils/userVariables.ts。
const extraDataVariables = ref<UserVariable[]>(
    collectExtraDataVariables([], { excludeSensitive: true }),
)
const openVariableMenuKey = ref<string | null>(null)
const showParamHelp = ref(false)

const closeVariableMenu = () => {
    openVariableMenuKey.value = null
}

const toggleVariableMenu = (paramKey: string) => {
    openVariableMenuKey.value = openVariableMenuKey.value === paramKey ? null : paramKey
    if (openVariableMenuKey.value) showParamHelp.value = false
}

const toggleParamHelp = () => {
    showParamHelp.value = !showParamHelp.value
    if (showParamHelp.value) closeVariableMenu()
}

const applyUserVariable = (param: VisualParam, value: string) => {
    param.defaultValue = value
    closeVariableMenu()
}

const onDocumentClick = () => {
    closeVariableMenu()
    showParamHelp.value = false
}
onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))

// 嵌套参数（带 items / properties 子结构）在可视化里只能改类型、描述与必填，
// 子结构本身请回 JSON 模式编辑——这里只做提示。
const visualNestedCount = computed(() => visualParams.value.filter(
    (param) => param.extra.items || param.extra.properties
).length)

const systemParamErrors = computed(() => validateSystemParams(systemParams.value, visualParams.value))

const buildSchemaJson = (): string => serializeToolSchema({
    shape: parsedSchema.value.shape,
    hadRequiredList: parsedSchema.value.hadRequiredList,
    rest: parsedSchema.value.rest,
    // 剥掉仅用于 v-for 的 key：它不属于 schema 数据
    params: visualParams.value.map(({ key, ...param }) => param),
    systemParams: systemParams.value,
})

const loadVisualFromJson = (): boolean => {
    const parsed = parseToolSchema(props.modelValue)
    if (!parsed) {
        // 留在原地：既不丢用户输入，也能让他看到错在哪
        jsonHint.value = '参数定义不是合法 JSON，请先修正后再切到可视化编辑'
        return false
    }
    jsonHint.value = ''
    parsedSchema.value = parsed
    visualParams.value = parsed.params.map((param) => ({ ...param, key: nextVisualParamKey() }))
    systemParams.value = parsed.systemParams.map((param) => ({ ...param }))
    return true
}

const switchSchemaMode = (mode: 'json' | 'visual') => {
    if (mode === schemaEditorMode.value) return
    if (mode === 'visual') {
        if (!loadVisualFromJson()) return
        schemaEditorMode.value = 'visual'
        return
    }
    emit('update:modelValue', buildSchemaJson())
    schemaEditorMode.value = 'json'
}

const addVisualParam = () => {
    visualParams.value = [...visualParams.value, {
        key: nextVisualParamKey(),
        name: '',
        type: 'string',
        required: true,
        description: '',
        defaultValue: '',
        extra: {},
    }]
}

const removeVisualParam = (index: number) => {
    closeVariableMenu()
    visualParams.value = visualParams.value.filter((_, i) => i !== index)
}

const onJsonInput = (event: Event) => {
    jsonHint.value = ''
    emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

// 可视化行改动 → 写回受控值。比较一次是为了避免「自己写出去的又触发回解析」的死循环。
watch(visualParams, () => {
    if (schemaEditorMode.value !== 'visual') return
    const next = buildSchemaJson()
    if (next !== props.modelValue) emit('update:modelValue', next)
}, { deep: true })

watch(systemParams, () => {
    if (schemaEditorMode.value !== 'visual') return
    const next = buildSchemaJson()
    if (next !== props.modelValue) emit('update:modelValue', next)
}, { deep: true })

// 外部改写文本（换工具、向导重新生成参数、编辑已有工具）时，可视化行必须跟着重解析，
// 否则下一次行内改动会把外部刚写进来的内容整个覆盖掉。
watch(() => props.modelValue, (next) => {
    if (schemaEditorMode.value !== 'visual') return
    if (next === buildSchemaJson()) return
    loadVisualFromJson()
})

const locationOf = (name: string): ParamLocation | undefined => {
    if (!name) return undefined
    return props.locations?.[name] || resolveParamLocation(props.method, props.urlTemplate, name)
}

const locationLabel = (name: string): string => {
    if (props.scope === 'script') return name ? 'stdin' : ''
    const location = locationOf(name)
    if (location === 'path') return '路径参数'
    if (location === 'query') return '查询参数'
    if (location === 'body') return '请求体'
    return ''
}
</script>

<template>
  <div>
    <div class="flex flex-wrap items-center gap-2">
      <label class="block text-sm font-medium text-gray-700">参数定义 (JSON Schema)</label>
      <div class="relative">
        <button
          type="button"
          class="flex h-5 w-5 items-center justify-center rounded-full border border-gray-300 bg-white text-[11px] font-semibold text-gray-500 hover:border-primary hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
          title="必填与默认值说明"
          aria-label="必填与默认值说明"
          :aria-expanded="showParamHelp"
          @click.stop="toggleParamHelp"
        >?</button>
        <div
          v-if="showParamHelp"
          class="absolute left-0 top-full z-50 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-gray-200 bg-white p-3 text-left shadow-xl"
          role="dialog"
          aria-label="必填与默认值说明"
          @click.stop
        >
          <p class="text-xs font-semibold text-gray-800">必填与默认值怎么配合？</p>
          <ul class="mt-2 space-y-1.5 text-[11px] leading-relaxed text-gray-600">
            <li>
              <span class="font-medium text-gray-800">必填</span>：告诉模型「这个参数必须提供」，并写进工具 schema 的
              <code class="font-mono">required</code>；所以可视化模式下必填参数不允许配默认值。
            </li>
            <li>
              <span class="font-medium text-gray-800">非必填 + 默认值</span>：模型不传、传 <code class="font-mono">null</code> 或空串时，自动使用默认值。
            </li>
            <li>
              <span class="font-medium text-gray-800">默认值写 <code class="font-mono">{user.user_name}</code></span>：调用时按当前登录用户替换，适合申请人、部门这类由平台注入的值。
            </li>
          </ul>
          <p class="mt-2 rounded bg-amber-50 px-2 py-1.5 text-[11px] leading-relaxed text-amber-700">
            需要模型绝对不能改的值（申请人、操作人），请使用下方「系统级参数」；
            普通参数的默认值只是兜底，模型显式传值时仍会覆盖。必填目前主要用于约束模型和试跑提示，不是运行时的硬校验。
          </p>
        </div>
      </div>
      <div class="flex bg-gray-100 p-0.5 rounded-lg text-xs" role="group" aria-label="参数定义编辑模式">
        <button
          type="button"
          class="px-2.5 py-1 rounded-md transition-all font-medium"
          :class="schemaEditorMode === 'json' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
          @click="switchSchemaMode('json')"
        >JSON</button>
        <button
          type="button"
          class="px-2.5 py-1 rounded-md transition-all font-medium"
          :class="schemaEditorMode === 'visual' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
          @click="switchSchemaMode('visual')"
        >可视化</button>
      </div>
      <span v-if="schemaEditorMode === 'visual' && visualNestedCount > 0" class="text-xs text-amber-600">
        有 {{ visualNestedCount }} 个嵌套参数：可改类型/描述/必填，子结构请用 JSON 模式编辑
      </span>
    </div>

    <textarea
      v-if="schemaEditorMode === 'json'"
      :value="modelValue"
      rows="5"
      class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm font-mono text-xs bg-white disabled:bg-gray-100"
      placeholder='{ "city": { "type": "string", "description": "城市名" } }'
      @input="onJsonInput"
    ></textarea>

    <div v-else class="mt-1 space-y-2 rounded-md border border-gray-200 bg-gray-50/60 p-2">
      <!-- 系统级参数优先级高于普通参数：它们由平台强制注入、模型不可见，放在最前面让配置者先看到。 -->
      <ToolSystemParamEditor
        v-if="scope !== 'script'"
        v-model="systemParams"
        :builtin-variables="BUILTIN_USER_VARIABLES"
        :extra-data-variables="extraDataVariables"
      />
      <p v-if="systemParamErrors.length" class="rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">
        {{ systemParamErrors[0] }}
      </p>
      <p v-if="visualParams.length === 0" class="px-1 py-1 text-xs text-gray-400">还没有普通参数，点下面的「+ 添加参数」开始</p>
      <div v-for="(p, index) in visualParams" :key="p.key" class="flex flex-wrap items-center gap-2 rounded-md bg-white p-2 shadow-sm">
        <span
          v-if="locationOf(p.name)"
          class="rounded px-1.5 py-0.5 text-[11px] font-medium"
          :class="{
            'bg-purple-50 text-purple-700': locationOf(p.name) === 'path',
            'bg-blue-50 text-blue-700': locationOf(p.name) === 'query',
            'bg-green-50 text-green-700': locationOf(p.name) === 'body',
          }"
        >{{ locationLabel(p.name) }}</span>
        <input v-model="p.name" class="w-40 border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-xs font-mono" placeholder="参数名" />
        <select v-model="p.type" class="w-24 border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-xs font-mono">
          <option v-for="option in PARAM_TYPE_OPTIONS" :key="option" :value="option">{{ option }}</option>
        </select>
        <label class="flex items-center gap-1 text-xs text-gray-600">
          <input type="checkbox" v-model="p.required" class="h-3.5 w-3.5 text-primary focus:ring-primary border-gray-300 rounded" />
          必填
        </label>
        <input v-model="p.description" class="min-w-[10rem] flex-1 border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-xs" placeholder="描述（给 LLM 看）" />
        <div class="relative flex items-center gap-1">
          <input v-model="p.defaultValue" :disabled="p.required" class="w-32 border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-xs font-mono disabled:bg-gray-100" :placeholder="p.required ? '默认值（需非必填）' : '默认值'" />
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-md border border-gray-300 bg-white font-mono text-[11px] font-bold text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
            :disabled="p.required"
            :title="p.required ? '默认值仅在非必填时生效' : '插入用户变量'"
            :aria-label="`为参数 ${p.name || index + 1} 插入用户变量`"
            :aria-expanded="openVariableMenuKey === p.key"
            @click.stop="toggleVariableMenu(p.key)"
          >{{ '{}' }}</button>
          <PermissionVariableMenu
            v-if="openVariableMenuKey === p.key"
            :builtin-variables="BUILTIN_USER_VARIABLES"
            :extra-data-variables="extraDataVariables"
            accent="slate"
            @click.stop
            @select="applyUserVariable(p, $event)"
          />
        </div>
        <span v-if="p.extra.items || p.extra.properties" class="rounded-full bg-amber-50 px-2 text-xs leading-5 text-amber-700" title="嵌套子结构请用 JSON 模式编辑">嵌套</span>
        <button
          type="button"
          class="rounded-md p-1 text-gray-400 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
          :title="`删除参数 ${p.name || index + 1}`"
          :aria-label="`删除参数 ${p.name || index + 1}`"
          @click="removeVisualParam(index)"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
        </button>
      </div>
      <button
        type="button"
        class="rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        @click="addVisualParam"
      >+ 添加参数</button>
      <p class="text-xs text-gray-500">
        普通参数的 <code class="font-mono">default</code> 只是兜底，模型显式给值时会覆盖；
        申请人、操作人等不能由模型决定的值，请用上方「系统级参数」。
      </p>
    </div>

    <p v-if="jsonHint" class="mt-1 rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">{{ jsonHint }}</p>
    <p class="text-xs text-gray-500 mt-1">定义参数类型和描述，用于 LLM 理解；参数值支持 <code class="font-mono">{user.user_name}</code>、<code class="font-mono">{user.dept_code}</code> 等用户变量。</p>
  </div>
</template>
