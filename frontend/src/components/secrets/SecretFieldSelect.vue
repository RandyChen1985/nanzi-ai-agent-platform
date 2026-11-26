<script setup lang="ts">
/**
 * 字段选择器：绑定结构化凭据时，指定「取哪一部分」。
 *
 * 为什么必须有这一步：一条 SMTP 凭据存进去是
 * `{"host":…,"port":465,"username":…,"password":…}`。绑到「邮件密码」参数上却
 * 整段注入，调用方拿到的是那段 JSON，必然失败——而失败点是几小时后的运行日志，
 * 离配置的人很远。所以这里逼着选字段。
 *
 * 数据来自 `GET /secrets/{id}/fields`：**敏感字段只有名字，没有值**。选择器本来
 * 也不需要值，后端也就不该给——一给，「凭据不可读」这条边界就开了个旁路。
 *
 * 单值凭据（`format !== 'json'`）没有字段可选，组件会自己收起来只留一句说明，
 * 调用方无需判断。
 */
import { computed, ref, watch } from 'vue'
import axios from '../../utils/axios'

interface SecretField {
  /** 点分隔的完整路径，嵌套字段如 auth.token、数组元素如 hosts.0 */
  name: string
  sensitive: boolean
  preview: string
  /** scalar | object | array —— 对象/数组本身也可选（整段取） */
  kind?: 'scalar' | 'object' | 'array'
}

const props = withDefaults(
  defineProps<{
    /** 已选凭据 id；为空时不请求 */
    secretId?: string | null
    /** 字段路径（点分隔），空串 = 整段 */
    modelValue?: string
    disabled?: boolean
    /** 紧凑模式：绑定点位空间小，去掉外框留白 */
    compact?: boolean
  }>(),
  {
    secretId: null,
    modelValue: '',
    disabled: false,
    compact: false,
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  /** 字段清单加载完：调用方据此决定「是否需要强制选字段」 */
  (e: 'loaded', info: { format: string; fields: SecretField[] }): void
}>()

const loading = ref(false)
const loaded = ref(false)
const format = ref('text')
const fields = ref<SecretField[]>([])
/** 后端读取失败（解不开 / 不是 JSON 对象）时给出可读的说明 */
const errorText = ref('')

const isStructured = computed(() => format.value === 'json')
const hasFields = computed(() => fields.value.length > 0)
const missing = computed(() => isStructured.value && hasFields.value && !props.modelValue)

/**
 * 请求序号：快速连点两个凭据时，先发的那个可能后到。没有序号保护时，
 * 晚到的旧结果会盖掉新凭据的字段，还会顺手把刚选好的路径清空。
 */
let loadSeq = 0

async function load(id: string | null) {
  const seq = ++loadSeq
  loaded.value = false
  errorText.value = ''
  fields.value = []
  format.value = 'text'
  if (!id) {
    loaded.value = true
    emit('loaded', { format: 'text', fields: [] })
    return
  }
  loading.value = true
  let failed = false
  try {
    const res = await axios.get(`/api/portal/secrets/${encodeURIComponent(id)}/fields`)
    if (seq !== loadSeq) return
    const data = res.data?.data || {}
    format.value = String(data.format || 'text')
    fields.value = Array.isArray(data.fields) ? data.fields : []
    // 换到单值凭据时把残留的字段路径清掉，否则绑定会带着一个不存在的字段
    if (format.value !== 'json' && props.modelValue) emit('update:modelValue', '')
  } catch {
    if (seq !== loadSeq) return
    failed = true
    errorText.value = '读取字段失败，可稍后重试'
  } finally {
    if (seq === loadSeq) {
      loading.value = false
      loaded.value = !failed
      // format 仍报 text：消费方只拿它算「要不要选字段」，报别的会让界面上
      // 出现一个没有字段可选的状态。读失败时由后端兜底——结构化凭据 + 空
      // value_path 在 bind_secret 里会被拒（「请选择要取用的字段」），
      // 所以这里不会静默存下坏数据，只是保存时会收到一句明确的报错。
      emit('loaded', { format: format.value, fields: fields.value })
    }
  }
}

watch(() => props.secretId, (id) => { void load(id) }, { immediate: true })

function fieldLabel(field: SecretField): string {
  if (field.sensitive) return `${field.name}（敏感）`
  // 对象/数组没有标量预览，标出形态才知道「选它等于整段取」
  if (field.kind === 'object') return `${field.name}（整个对象）`
  if (field.kind === 'array') return `${field.name}（整个数组）`
  return field.preview ? `${field.name} · ${field.preview}` : field.name
}

function onChange(event: Event) {
  emit('update:modelValue', (event.target as HTMLSelectElement).value)
}
</script>

<template>
  <div :class="compact ? '' : 'space-y-1'">
    <div v-if="!secretId" class="text-[11px] text-gray-400">先选凭据，再选字段</div>

    <div v-else-if="loading" class="text-[11px] text-gray-400">正在读取字段…</div>

    <div v-else-if="errorText" class="text-[11px] text-rose-600">{{ errorText }}</div>

    <!-- 结构化：选一个字段 -->
    <template v-else-if="isStructured && hasFields">
      <select
        class="w-full rounded-md border px-2 py-1.5 text-xs focus:outline-none"
        :class="missing ? 'border-amber-400' : 'border-gray-300 focus:border-primary'"
        :value="modelValue"
        :disabled="disabled"
        @change="onChange"
      >
        <option value="">请选择取用的字段</option>
        <option v-for="field in fields" :key="field.name" :value="field.name">
          {{ fieldLabel(field) }}
        </option>
      </select>
      <p v-if="missing" class="mt-1 text-[11px] leading-snug text-amber-600">
        这条凭据是结构化的：不选字段就会把整段 JSON 注进去，调用方一定收不对。
      </p>
    </template>

    <!-- 结构化但没有可读字段（解不开 / 不是对象）：只能整段，且要说明白 -->
    <div v-else-if="isStructured" class="text-[11px] leading-snug text-amber-600">
      读不出这条凭据的字段，只能整段注入；确认内容格式与使用方一致再保存。
    </div>

    <!-- 单值：没有可选的，直接说清楚会注入什么 -->
    <div v-else class="text-[11px] leading-snug text-gray-500">
      单值凭据，整条值直接注入
    </div>
  </div>
</template>
