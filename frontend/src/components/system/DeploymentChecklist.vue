<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import axios from '@/utils/axios'

const props = withDefaults(defineProps<{ compact?: boolean; hideWhenComplete?: boolean }>(), { compact: false, hideWhenComplete: false })
const router = useRouter()
const loading = ref(true)
const saving = ref<string | null>(null)
const visible = ref(true)
const isExpanded = ref(true)
const forceVisible = ref(false)
const showHelp = ref(false)
const activeHelp = ref<(typeof steps)[number]['id'] | null>(null)
const state = ref({
  version: 'post_install_v2',
  completed: { model_config: false, knowledge_environment: false, system_config: false, agent_config: false },
})

const steps = [
  { id: 'model_config', title: '配置模型管理', desc: '添加可用的 Chat、Embedding 等模型并完成连通性测试。', optional: false, path: '/dashboard/system', query: { tab: 'models' } },
  { id: 'knowledge_environment', title: '知识库环境', desc: '可选：使用知识库或让元数据走 RAGFlow 时，先独立部署并测试 RAGFlow。', optional: true, path: '/dashboard/system', query: { tab: 'configs' } },
  { id: 'system_config', title: '检查参数配置', desc: '根据部署环境检查模型、向量、下载地址和沙箱等关键参数。', optional: false, path: '/dashboard/system', query: { tab: 'configs' } },
  { id: 'agent_config', title: '发布智能体配置', desc: '为预置智能体换绑可用模型，保存并发布生效版本。', optional: false, path: '/dashboard/agent-management', query: undefined },
] as const

const helpContent = {
  model_config: {
    title: '配置模型管理',
    checks: [
      { key: 'Chat 模型', what: '至少有一个可用的对话模型，供平台对话和智能体推理使用。' },
      { key: 'Base URL / API Key', what: '地址、密钥和模型 ID 正确，连通性测试返回成功。' },
      { key: '模型能力', what: '按实际能力勾选 Chat、Embedding、Reasoning 或 Multimodal。' },
      { key: 'Embedding 维度', what: '确认向量模型输出维度，后续要与 Redis 索引一致。' },
    ],
    steps: ['进入“模型管理”，新增或编辑模型提供商', '填写 Base URL、API Key 和模型 ID，选择模型能力', '点击“测试连接”，确认返回成功'],
    done: '至少一个实际使用的模型测试通过，Embedding 模型信息完整。',
  },
  system_config: {
    title: '检查参数配置',
    checks: [
      { key: 'download_url_prefix', what: '生成文件、报表和工件下载链接的对外地址；忘记密码的重置邮件链接也用它，不能继续使用 localhost。' },
      { key: 'llm_model_name', what: '平台默认 Chat 模型名称，必须对应模型管理中测试通过的模型。' },
      { key: 'multimodal_model_name', what: '图片识别的视觉模型；使用图片能力时必须配置可用模型。' },
      { key: 'embed_api_url', what: 'Embedding 接口地址，用于元数据、案例和会话记忆向量化。' },
      { key: 'metadata_provider', what: '选择 Redis 或 RAGFlow，必须和实际部署的元数据服务一致。' },
      { key: 'knowledge_ragflow_api_url', what: '知识库功能启用时填写 RAGFlow 地址和 Key；不用知识库可留空。' },
      { key: 'sandbox_policy', what: '控制代码执行隔离方式，生产环境通常建议使用 docker。' },
      { key: 'TASK_SCHEDULER_ENABLED', what: '控制当前节点是否运行定时任务；多节点只保留一个节点开启。' },
    ],
    steps: ['进入“系统配置 → 参数配置”', '逐项核对模型、向量、元数据、下载地址和沙箱参数', '修改后点击页面顶部或底部的保存按钮'],
    done: '关键参数已按实际部署环境核对并保存，向量维度与 Redis 索引一致。',
  },
  knowledge_environment: {
    title: '知识库环境（可选）',
    checks: [
      { key: 'RAGFlow 部署', what: '如果使用知识库或 RAGFlow 元数据，先独立部署可访问的 RAGFlow 服务。' },
      { key: 'knowledge_ragflow_api_url', what: '在系统配置中填写 RAGFlow API 地址和 API Key。' },
      { key: 'metadata_provider', what: '需要 RAGFlow 元数据时选择 ragflow；使用 Redis 本地元数据时无需此项。' },
      { key: '连接测试', what: '保存地址后，在系统配置中测试 RAGFlow/相关能力，确认服务可用。' },
    ],
    steps: ['按需独立部署 RAGFlow，并确认平台容器可以访问', '进入“系统配置 → 参数配置”，填写 RAGFlow 地址和 Key', '按需将 metadata_provider 设置为 ragflow，并保存后测试', '不使用知识库或 RAGFlow 时，直接标记为完成/跳过'],
    done: '使用 RAGFlow 的环境已部署、地址和 Key 已配置并测试通过；不使用时已明确跳过。',
  },
  agent_config: {
    title: '发布智能体配置',
    checks: [
      { key: '模型换绑', what: '主助手、ChatBI、元数据等核心智能体使用已测试通过的模型。' },
      { key: '资源绑定', what: 'Prompt、工具、数据集和知识库绑定符合智能体职责。' },
      { key: '版本发布', what: '克隆草稿、保存后点击“立即发布并激活”。' },
      { key: '启用与验证', what: '智能体已启用，并在调试台完成一次实际调用。' },
    ],
    steps: ['进入“智能体中心”，打开主助手、ChatBI、元数据等核心智能体', '点击“配置与发布”，克隆版本并换绑已测试模型', '检查工具/数据集绑定，保存草稿后点击“立即发布并激活”', '进入调试台实际运行一次'],
    done: '核心智能体均有可用的已发布版本，并至少完成一次实际调用验证。',
  },
} as const

const completedCount = computed(() => steps.filter(step => state.value.completed[step.id]).length)
const isComplete = computed(() => completedCount.value === steps.length)

const load = async () => {
  try {
    const response = await axios.get('/api/portal/system/setup-checklist')
    state.value = response.data
    isExpanded.value = !isComplete.value
  } catch {
    visible.value = false
  } finally {
    loading.value = false
  }
}

const toggle = async (stepId: typeof steps[number]['id']) => {
  saving.value = stepId
  try {
    const response = await axios.put('/api/portal/system/setup-checklist', {
      item_id: stepId,
      completed: !state.value.completed[stepId],
    })
    state.value = response.data
    isExpanded.value = !isComplete.value
  } finally {
    saving.value = null
  }
}

const openStep = (step: typeof steps[number]) => {
  router.push({ path: step.path, query: step.query })
}

const openHelp = (stepId: typeof steps[number]['id']) => {
  activeHelp.value = stepId
  showHelp.value = true
}

const closeHelp = () => {
  showHelp.value = false
  activeHelp.value = null
}

const scrollToDetails = () => {
  window.dispatchEvent(new CustomEvent('deployment-checklist:open'))
  document.getElementById('deployment-checklist-details')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const openDetails = () => {
  forceVisible.value = true
  isExpanded.value = true
  window.setTimeout(() => document.getElementById('deployment-checklist-details')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0)
}

onMounted(() => {
  load()
  window.addEventListener('deployment-checklist:open', openDetails)
})

onUnmounted(() => window.removeEventListener('deployment-checklist:open', openDetails))
</script>

<template>
  <section v-if="!props.compact && !loading && visible && isExpanded && (!props.hideWhenComplete || !isComplete || forceVisible)" id="deployment-checklist-details" class="hidden lg:block rounded-2xl border border-blue-100 bg-gradient-to-r from-blue-50/70 via-indigo-50/40 to-slate-50/60 p-4 shadow-sm sm:p-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <div class="flex items-center gap-2">
          <h2 class="text-base font-bold text-gray-900">首次部署检查</h2>
          <span class="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-semibold text-blue-700">{{ completedCount }} / {{ steps.length }}</span>
        </div>
        <p class="mt-1 text-xs text-gray-500">完成模型、参数与智能体发布检查，确保平台可以正常使用。</p>
      </div>
      <button v-if="!props.compact" type="button" class="rounded-lg border border-blue-200 bg-white px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-50" @click="isExpanded = !isExpanded">{{ isExpanded ? '收起检查清单' : '重新展开' }}</button>
    </div>

    <div v-if="!props.compact && isExpanded" class="mt-4 flex flex-col gap-3 lg:flex-row lg:items-stretch lg:gap-0">
      <template v-for="(step, index) in steps" :key="step.id">
        <article class="flex-1 rounded-xl border border-white/80 bg-white/85 p-3 shadow-sm">
          <div class="flex items-start gap-2">
            <button type="button" class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-xs" :class="state.completed[step.id] ? 'border-emerald-500 bg-emerald-500 text-white' : 'border-gray-300 bg-white text-transparent'" :disabled="saving === step.id" :aria-label="state.completed[step.id] ? '取消完成' : (step.optional ? '标记完成或跳过' : '标记为完成')" @click="toggle(step.id)">✓</button>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5">
                <h3 class="text-sm font-semibold text-gray-900">{{ step.title }}</h3>
                <span v-if="step.optional" class="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">可选</span>
                <button type="button" class="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-gray-300 text-[10px] font-bold text-gray-500 hover:border-blue-400 hover:text-blue-600" :aria-label="`${step.title}检查说明`" @click.stop="openHelp(step.id)">?</button>
              </div>
              <p class="mt-1 text-xs leading-relaxed text-gray-500">{{ step.desc }}</p>
              <button type="button" class="mt-2 text-xs font-medium text-blue-600 hover:text-blue-800" @click="openStep(step)">前往配置 →</button>
            </div>
          </div>
        </article>
        <div v-if="index < steps.length - 1" class="deployment-checklist-connector flex h-5 shrink-0 items-center justify-center text-xl font-semibold text-blue-300 lg:h-auto lg:w-8">
          <span class="lg:hidden">↓</span>
          <span class="hidden lg:inline">→</span>
        </div>
      </template>
    </div>
    <p v-if="isComplete" class="mt-3 text-xs font-medium text-emerald-700">✓ 首次部署检查已完成；后续可随时重新打开查看。</p>
  </section>
  <button v-else-if="!loading && visible && props.compact" type="button" class="hidden lg:inline-flex items-center gap-1.5 rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-500 hover:bg-gray-100 hover:text-gray-700" @click="scrollToDetails">
    <span aria-hidden="true">✓</span>
    <span>部署检查</span>
  </button>

  <div v-if="showHelp && activeHelp" class="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4" role="dialog" aria-modal="true" :aria-label="`${helpContent[activeHelp].title}检查说明`" @click.self="closeHelp">
    <div class="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-5 shadow-2xl sm:p-6">
      <div class="flex items-start justify-between gap-4">
        <div>
          <h2 class="text-lg font-bold text-gray-900">{{ helpContent[activeHelp].title }}：如何检查</h2>
          <p class="mt-1 text-xs text-gray-500">按照下面的检查项目完成配置，最后再勾选清单。</p>
        </div>
        <button type="button" class="text-2xl leading-none text-gray-400 hover:text-gray-700" aria-label="关闭检查说明" @click="closeHelp">×</button>
      </div>
      <div class="mt-5 grid gap-5 sm:grid-cols-3">
        <div class="sm:col-span-2"><h3 class="text-sm font-semibold text-gray-900">检查项目</h3><ul class="mt-2 space-y-2 text-xs leading-relaxed text-gray-600"><li v-for="item in helpContent[activeHelp].checks" :key="item.key" class="flex gap-1.5"><span class="font-mono font-semibold text-blue-600">{{ item.key }}</span><span>{{ item.what }}</span></li></ul></div>
        <div><h3 class="text-sm font-semibold text-gray-900">操作步骤</h3><ol class="mt-2 space-y-2 text-xs leading-relaxed text-gray-600"><li v-for="(item, index) in helpContent[activeHelp].steps" :key="item" class="flex gap-1.5"><span class="font-semibold text-blue-600">{{ index + 1 }}.</span>{{ item }}</li></ol></div>
        <div><h3 class="text-sm font-semibold text-gray-900">完成标准</h3><p class="mt-2 rounded-lg bg-emerald-50 p-3 text-xs leading-relaxed text-emerald-800">{{ helpContent[activeHelp].done }}</p></div>
      </div>
      <div class="mt-6 flex justify-end"><button type="button" class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700" @click="closeHelp">知道了</button></div>
    </div>
  </div>
</template>
