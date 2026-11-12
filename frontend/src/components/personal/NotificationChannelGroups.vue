<template>
  <div class="space-y-4">
    <!-- 群列表：宽度够就一行两个。群卡片是「一屏内并排对比」的东西（谁默认、
         谁停用、webhook 填没填），纵向堆叠会让每个渠道白白多出半屏滚动。 -->
    <div v-if="groups.length > 0" class="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <div
        v-for="(group, index) in groups"
        :key="group.id ?? `new-${index}`"
        class="border rounded-xl p-4 transition-all duration-200"
        :class="[
          group.is_default && group.is_enabled
            ? 'border-blue-200 bg-gradient-to-b from-blue-50/40 to-white shadow-sm'
            : 'border-gray-200 bg-white',
          group.is_enabled ? '' : 'opacity-60 bg-gray-50',
        ]"
      >
        <!-- 第一行：群 ID / 名称 / 徽标 / 操作 -->
        <div class="flex items-center flex-wrap gap-2">
          <span
            class="shrink-0 inline-flex items-center px-1.5 py-0.5 rounded-md text-[10.5px] font-bold font-mono cursor-pointer select-all border"
            :class="group.id
              ? 'text-gray-600 bg-gray-100 border-gray-200 hover:text-blue-600 hover:bg-blue-50 hover:border-blue-200'
              : 'text-gray-400 bg-gray-50 border-dashed border-gray-200'"
            :title="group.id ? '点击复制群 ID：发送消息时用它指定群' : '保存后生成群 ID'"
            @click="copyGroupId(group)"
          >{{ group.id ? `ID ${group.id}` : 'ID 待保存' }}</span>

          <input
            v-model="group.name"
            type="text"
            maxlength="64"
            placeholder="群名称，如：运维告警群"
            class="min-w-[120px] flex-none text-sm font-semibold text-gray-800 px-2 py-1 rounded-md border border-transparent hover:border-gray-200 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/20"
          />

          <span
            v-if="group.is_default && group.is_enabled"
            class="shrink-0 px-1.5 py-0.5 text-[9.5px] font-black text-white bg-blue-600 rounded"
          >默认</span>
          <span
            v-if="!group.is_enabled"
            class="shrink-0 px-1.5 py-0.5 text-[9.5px] font-black text-gray-500 bg-gray-100 rounded"
          >已停用</span>

          <span class="flex-1" />

          <button
            type="button"
            class="px-2.5 py-1 text-[11px] font-medium text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-md transition-colors disabled:opacity-50"
            :disabled="testing === group || !group.webhook_url"
            :title="group.webhook_url ? '向该群发送一条测试消息' : '请先填写 Webhook 地址'"
            @click="testGroup(group)"
          >{{ testing === group ? '发送中…' : (tested === group ? '已发送 ✓' : '测试') }}</button>

          <button
            type="button"
            class="px-2.5 py-1 text-[11px] font-medium text-red-600 bg-red-50 hover:bg-red-100 rounded-md transition-colors"
            @click="removeGroup(index)"
          >删除</button>

          <label class="relative inline-flex items-center cursor-pointer">
            <input
              v-model="group.is_enabled"
              type="checkbox"
              class="sr-only peer"
              @change="onToggleEnabled(group)"
            >
            <div class="w-9 h-5 bg-gray-200 rounded-full peer peer-checked:after:translate-x-4 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600"></div>
          </label>
        </div>

        <!-- 第二行：Webhook / 加签 -->
        <div class="mt-3 flex flex-wrap gap-3">
          <div class="flex-1 min-w-[220px]">
            <label class="block text-[10px] font-black uppercase tracking-wide text-gray-400 mb-1">Webhook 地址</label>
            <input
              v-model="group.webhook_url"
              type="text"
              :placeholder="webhookPlaceholder"
              class="w-full text-xs px-2.5 py-1.5 border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 placeholder-gray-300"
            />
          </div>
          <div v-if="supportsSecret" class="flex-1 min-w-[180px]">
            <label class="block text-[10px] font-black uppercase tracking-wide text-gray-400 mb-1">加签密钥（可选）</label>
            <input
              v-model="group.secret"
              type="password"
              placeholder="SEC..."
              class="w-full text-xs px-2.5 py-1.5 border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 placeholder-gray-300"
            />
          </div>
        </div>

        <!-- 第三行：设为默认 -->
        <div class="mt-2.5 flex items-center justify-end">
          <label
            class="inline-flex items-center gap-1.5 text-[11px] font-bold"
            :class="group.is_enabled ? 'text-gray-500 cursor-pointer' : 'text-gray-300 cursor-not-allowed'"
            :title="group.is_enabled ? '不指定群时，消息默认发到这一个群' : '停用的群不能设为默认'"
          >
            <input
              type="radio"
              :name="`default-${channelType}`"
              :checked="group.is_default"
              :disabled="!group.is_enabled"
              class="accent-blue-600"
              @change="setDefault(index)"
            >
            设为默认
          </label>
        </div>
      </div>
    </div>

    <!-- 空态 -->
    <div v-else class="border border-dashed border-gray-200 rounded-xl py-8 text-center">
      <p class="text-xs text-gray-400">还没有配置任何{{ label }}群</p>
      <p class="text-[11px] text-gray-400 mt-1">点击下方「新增{{ label }}群」开始配置</p>
    </div>

    <!-- 操作区 -->
    <div class="flex flex-wrap items-center justify-between gap-3 pt-1">
      <div class="flex items-center gap-3">
        <button
          type="button"
          class="px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="groups.length >= maxGroups"
          :title="groups.length >= maxGroups ? `每个渠道最多 ${maxGroups} 个群` : ''"
          @click="addGroup"
        >+ 新增{{ label }}群</button>
        <span class="text-[11px] text-gray-400">
          {{ groups.length }} / {{ maxGroups }} 个 · {{ enabledCount }} 个启用
        </span>
      </div>
      <div class="flex items-center gap-2">
        <span v-if="dirty" class="text-[11px] text-amber-600">有未保存的改动</span>
        <button
          type="button"
          class="px-4 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors disabled:opacity-50 shadow-sm"
          :disabled="saving || !dirty || !!saveBlockedReason"
          :title="saveBlockedReason"
          @click="save"
        >{{ saving ? '保存中…' : '保存' }}</button>
      </div>
    </div>

    <p v-if="hint" class="text-[11px] text-gray-400 leading-relaxed">{{ hint }}</p>
    <p v-if="saveBlockedReason" class="text-[11px] text-amber-600 leading-relaxed">{{ saveBlockedReason }}</p>
    <p v-else-if="errorText" class="text-[11px] text-red-600 leading-relaxed">{{ errorText }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import axios from '../../utils/axios'

export interface NotificationGroup {
  id?: number
  name: string
  is_enabled: boolean
  is_default: boolean
  webhook_url: string
  secret?: string
}

const props = withDefaults(
  defineProps<{
    channelType: 'dingtalk' | 'feishu' | 'wechat_work'
    label: string
    webhookPlaceholder?: string
    hint?: string
    maxGroups?: number
    supportsSecret?: boolean
  }>(),
  {
    webhookPlaceholder: 'https://...',
    hint: '',
    maxGroups: 10,
    supportsSecret: true,
  }
)

const emit = defineEmits<{
  (e: 'show-toast', message: string, type?: 'success' | 'error'): void
  (e: 'changed', counts: { total: number; enabled: number }): void
  (e: 'saved'): void
  (e: 'dirty', value: boolean): void
}>()

const groups = ref<NotificationGroup[]>([])
const initial = ref('')
const saving = ref(false)
const testing = ref<NotificationGroup | null>(null)
const tested = ref<NotificationGroup | null>(null)
const errorText = ref('')

const enabledCount = computed(() => groups.value.filter((g) => g.is_enabled).length)
const dirty = computed(() => serialize(groups.value) !== initial.value)

// 保存前置校验：缺 Webhook 就点保存，只会存进一个发不出消息的群，
// 而用户会以为配好了 —— 所以置灰按钮，并把原因写到界面上。
// 只置灰不说话是另一种「界面说谎」：用户只会以为按钮坏了。
const saveBlockedReason = computed(() => {
  if (groups.value.length === 0) return ''
  for (const group of groups.value) {
    if (!group.name.trim()) return '每个群都要有名称，否则发送时无法识别。'
    if (!group.webhook_url.trim()) {
      return `「${group.name.trim()}」还没有填写 Webhook 地址后再保存。`
    }
  }
  const names = groups.value.map((g) => g.name.trim().toLowerCase())
  if (new Set(names).size !== names.length) {
    return '群名称不能重复，否则「按名称发送」时分不清是哪一个。'
  }
  return ''
})

function serialize(list: NotificationGroup[]): string {
  return JSON.stringify(
    list.map((g) => ({
      id: g.id ?? null,
      name: g.name,
      is_enabled: g.is_enabled,
      is_default: g.is_default,
      webhook_url: g.webhook_url,
      secret: g.secret ?? '',
    }))
  )
}

function applyLoaded(list: NotificationGroup[]) {
  groups.value = list.map((item) => ({
    id: item.id,
    name: item.name ?? '',
    // 兜底读一次旧键名 enabled：后端曾经返回这个键，而前端读 is_enabled 会得到
    // undefined → Boolean(undefined)=false → 整列显示「已停用」，用户一点保存
    // 就把停用状态真写回库里。部署窗口期内旧前端缓存 / 旧后端都可能存在，
    // 这里宁可多读一个键，也不能把「读不到」当成「已停用」。
    is_enabled: Boolean(item.is_enabled ?? (item as { enabled?: boolean }).enabled),
    is_default: Boolean(item.is_default),
    webhook_url: item.webhook_url ?? '',
    secret: item.secret ?? '',
  }))
  initial.value = serialize(groups.value)
  errorText.value = ''
}

async function load() {
  try {
    const res = await axios.get<Record<string, NotificationGroup[]>>('/api/portal/notifications/channels')
    const list = (res.data?.[props.channelType] ?? []) as NotificationGroup[]
    applyLoaded(list)
  } catch (error: any) {
    // 读不到就保持空列表，不做任何「自动创建默认群」的猜测
    errorText.value = error.response?.data?.detail || '加载群配置失败'
    applyLoaded([])
  }
}

function addGroup() {
  if (groups.value.length >= props.maxGroups) return
  groups.value.push({
    name: `${props.label}群 ${groups.value.length + 1}`,
    is_enabled: true,
    // 第一个群自动成为默认群：否则用户新增完还得再点一次单选
    is_default: groups.value.length === 0,
    webhook_url: '',
    secret: '',
  })
}

function removeGroup(index: number) {
  const [removed] = groups.value.splice(index, 1)
  // 删掉的是默认群时，交给最早的启用群接任——否则「不指定群」就发不出去了
  if (removed?.is_default) {
    const successor = groups.value.find((g) => g.is_enabled) ?? groups.value[0]
    if (successor) successor.is_default = true
  }
}

function setDefault(index: number) {
  groups.value.forEach((group, i) => {
    group.is_default = i === index
  })
}

function onToggleEnabled(group: NotificationGroup) {
  // 停用当前默认群时立刻把默认交给别的启用群，避免出现「默认群停用」的悬空状态
  if (!group.is_enabled && group.is_default) {
    group.is_default = false
    const successor = groups.value.find((g) => g.is_enabled)
    if (successor) successor.is_default = true
  }
  if (group.is_enabled && !groups.value.some((g) => g.is_default && g.is_enabled)) {
    // 先清空再顶：库里可能留着一条「已停用的默认群」，不清掉就会出现两个
    // is_default=true，同一组 radio 在保存前渲染成两个都选中。
    for (const item of groups.value) item.is_default = false
    group.is_default = true
  }
}

async function copyGroupId(group: NotificationGroup) {
  if (!group.id) return
  try {
    await navigator.clipboard.writeText(String(group.id))
    emit('show-toast', `已复制群 ID：${group.id}`, 'success')
  } catch {
    emit('show-toast', `群 ID 是 ${group.id}`, 'success')
  }
}

async function testGroup(group: NotificationGroup) {
  // 用条目引用而不是数组下标：测试请求进行中删掉前面的群，下标会整体前移，
  // 「发送中…」「已发送 ✓」就跑到别的群上去了。
  if (!group) return
  testing.value = group
  errorText.value = ''
  try {
    await axios.post('/api/portal/notifications/test', {
      channel_type: props.channelType,
      config_data: {
        is_enabled: true,
        webhook_url: group.webhook_url,
        ...(props.supportsSecret ? { secret: group.secret ?? '' } : {}),
      },
      channel_id: group.id ?? null,
    })
    tested.value = group
    emit('show-toast', `「${group.name}」测试消息已发送`, 'success')
    window.setTimeout(() => {
      if (tested.value === group) tested.value = null
    }, 2000)
  } catch (error: any) {
    const detail = error.response?.data?.detail || '连通测试失败'
    errorText.value = detail
    emit('show-toast', detail, 'error')
  } finally {
    testing.value = null
  }
}

async function save() {
  saving.value = true
  errorText.value = ''
  // 兜底：没有任何群标了默认时，把最早启用的一个顶上，
  // 否则「不指定群」就发不出去，而界面上看不出少了什么。
  if (groups.value.length > 0 && !groups.value.some((g) => g.is_default && g.is_enabled)) {
    const successor = groups.value.find((g) => g.is_enabled)
    if (successor) {
      // 先清空再顶：否则会同时存在「停用群 is_default」和「启用群 is_default」，
      // 后端按数组顺序可能仍选中停用的那条，保存完默认群还是发不出去。
      for (const group of groups.value) group.is_default = false
      successor.is_default = true
    }
  }
  try {
    const res = await axios.put('/api/portal/notifications/channels', {
      channel_type: props.channelType,
      channels: groups.value.map((g) => ({
        id: g.id ?? null,
        name: g.name,
        is_enabled: g.is_enabled,
        is_default: g.is_default,
        webhook_url: g.webhook_url,
        ...(props.supportsSecret ? { secret: g.secret ?? '' } : {}),
      })),
    })
    const saved = (res.data?.channels?.[props.channelType] ?? []) as NotificationGroup[]
    applyLoaded(saved)
    // 保存会改变「默认群是哪一个」，而状态徽标读的正是默认群，必须让它重新判定
    emit('saved')
    emit('show-toast', `${props.label}群配置已保存`, 'success')
  } catch (error: any) {
    const detail = error.response?.data?.detail || '保存群配置失败'
    errorText.value = detail
    emit('show-toast', detail, 'error')
  } finally {
    saving.value = false
  }
}

// 折叠之后看不到内容区，未保存的改动只能靠头部那行提示，所以状态要上报父组件
watch(dirty, (value) => emit('dirty', value), { immediate: true })

// 头部要显示「启用数/总数」：只上报总数看不出「配了但全停用」这种状态，
// 而这种状态恰恰是「消息发不出去」的常见原因。深度监听才能在开关、
// 删除、新增时实时同步 —— 只在加载时上报一次的话，改完保存前数字是错的。
watch(
  groups,
  () => emit('changed', {
    total: groups.value.length,
    enabled: groups.value.filter((group) => group.is_enabled).length,
  }),
  { deep: true, immediate: true }
)

// 渠道切换时重新加载（组件复用时 props 会变）
watch(() => props.channelType, load, { immediate: true })

defineExpose({ reload: load })
</script>
