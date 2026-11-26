<script setup lang="ts">
import { useRouter } from 'vue-router'
import FlowGuideStrip from '../common/FlowGuideStrip.vue'

const props = defineProps<{
  engineStatus?: 'checking' | 'connected' | 'disconnected'
  isEngineReady?: boolean
  ragflowApiUrl?: string
}>()

const emit = defineEmits<{
  (e: 'action', type: 'create' | 'sync'): void
  (e: 'close'): void
  (e: 'dismiss'): void
}>()

const router = useRouter()

type ActionType =
  | 'system_config'
  | 'create'
  | 'retrieval_test'
  | 'metrics'
  | 'roles'
  | 'agents'

interface FlowGuideStep {
  step: number
  title: string
  subtitle: string
  desc: string
  tag: string
  tagClass: string
  iconBg: string
  actionText: string
  actionType: ActionType | null
  hintText: string
  secondaryActionText?: string | null
  secondaryActionType?: ActionType | null
}

const steps: FlowGuideStep[] = [
  {
    step: 1,
    title: '环境连通与库创建',
    subtitle: '部署 RAGFlow 并定义切分',
    desc: '在系统配置中配置 RAGFlow API 地址与密钥确保连通；选择通用、QA问答、表格等切分模板创建知识库。',
    tag: '基础环境',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '系统配置',
    actionType: 'system_config',
    hintText: '',
    secondaryActionText: '新建知识库',
    secondaryActionType: 'create'
  },
  {
    step: 2,
    title: '文档上传与解析',
    subtitle: 'OCR识别与向量分块',
    desc: '支持批量上传 PDF、Word、Excel、Markdown 等多格式；自动提取版面与生成 Chunks 切片，支持人工审校修正。',
    tag: '数据入库',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '左侧树选择知识库上传解析',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 3,
    title: '召回测试与调优',
    subtitle: '混合检索与命中率评测',
    desc: '输入真实业务提问验证 Top-K 向量相似度、全文检索与 Rerank 重排准确率；前往监控中心查看检索频次与消耗。',
    tag: '效果评测',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '召回测试',
    actionType: 'retrieval_test',
    hintText: '',
    secondaryActionText: '指标监控',
    secondaryActionType: 'metrics'
  },
  {
    step: 4,
    title: '权限授权与角色分配',
    subtitle: '配置可见性与角色访问',
    desc: '设置知识库公开/私有范围与协作者读写权限；在角色管理中授予对应角色访问权限，确保业务人员可正常检索。',
    tag: '数据安全',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '前往角色管理',
    actionType: 'roles',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 5,
    title: '智能体挂载与问答',
    subtitle: '多智能体 RAG 协同溯源',
    desc: '在智能体中心为目标助手绑定该知识库；智能体在对话中自主调用知识检索工具，生成带切片溯源的高质量回答。',
    tag: '落地消费',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '前往智能体中心',
    actionType: 'agents',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  }
]

const handleAction = (type: string | null) => {
  if (!type) return
  if (type === 'system_config') {
    router.push('/dashboard/system?tab=configs')
  } else if (type === 'create') {
    emit('action', 'create')
  } else if (type === 'retrieval_test') {
    router.push('/dashboard/knowledge-retrieval-test')
  } else if (type === 'metrics') {
    router.push('/dashboard/knowledge-metrics')
  } else if (type === 'roles') {
    router.push('/dashboard/roles')
  } else if (type === 'agents') {
    router.push('/dashboard/agent-management')
  }
}

const handleClose = () => {
  emit('close')
}

const handleDismiss = () => {
  emit('dismiss')
}
</script>

<template>
  <FlowGuideStrip
    ariaLabel="知识库管理全流程指引"
    title="知识库全生命周期构建指引"
    badge="5 步实施标准"
    subtitle="从基础设施连通、切分策略定义、多模态文档解析，到召回测试评测、角色权限下发与智能体挂载"
    :steps="steps"
    :columns="5"
    icon-path="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"
    collapse-button-class="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-gray-600 shadow-sm hover:bg-white hover:text-gray-900 transition-colors cursor-pointer"
    dismiss-button-class="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-gray-500 shadow-sm hover:bg-white hover:text-amber-600 transition-colors cursor-pointer"
    card-class="group relative flex flex-col justify-between rounded-xl border border-white/80 bg-white/85 p-3 shadow-sm backdrop-blur-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-emerald-200 hover:bg-white hover:shadow-md"
    step-index-class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold shadow-sm"
    action-button-class="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-emerald-200 bg-emerald-50/60 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-600 hover:text-white transition-colors cursor-pointer"
    @action="handleAction"
    @close="handleClose"
    @dismiss="handleDismiss"
  >
    <!-- RAGFlow 引擎状态徽章 -->
    <template #badges>
      <span
        v-if="props.engineStatus === 'connected'"
        class="hidden sm:inline-flex items-center gap-1 rounded-full bg-green-50 border border-green-200 px-2 py-0.5 text-[11px] font-medium text-green-700"
      >
        <span class="h-1.5 w-1.5 rounded-full bg-green-500"></span>
        <span>RAGFlow 引擎已就绪</span>
      </span>
      <span
        v-else
        class="hidden sm:inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200 px-2 py-0.5 text-[11px] font-medium text-amber-700"
      >
        <span class="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse"></span>
        <span>前置：请先在系统配置中连通 RAGFlow</span>
      </span>
    </template>
  </FlowGuideStrip>
</template>
