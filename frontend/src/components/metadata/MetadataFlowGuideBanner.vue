<script setup lang="ts">
import { useRouter } from 'vue-router'
import FlowGuideStrip from '../common/FlowGuideStrip.vue'

const props = withDefaults(
  defineProps<{
    collapsible?: boolean
  }>(),
  {
    collapsible: true
  }
)

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'dismiss'): void
  (e: 'action', type: 'datasource' | 'import' | 'create'): void
}>()

const router = useRouter()

type ActionType = 'datasource' | 'import' | 'create'

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
}

const steps: FlowGuideStep[] = [
  {
    step: 1,
    title: '连接与摸排数据源',
    subtitle: '配置物理连接与表画像',
    desc: '配置 MySQL、PostgreSQL、ClickHouse 等外部库连接，支持连通性测试与一键智能摸排表结构画像。',
    tag: '基础准备',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '去管理数据源',
    actionType: 'datasource',
    hintText: ''
  },
  {
    step: 2,
    title: '智能导入与 AI 解析',
    subtitle: '自动提取业务语义与枚举',
    desc: '通过画像免推理导入、库表直读或 DDL 粘贴，AI 自动提取中文业务名、字段描述、字典枚举及主键。',
    tag: '语义生成',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '向导新建数据集',
    actionType: 'import',
    hintText: ''
  },
  {
    step: 3,
    title: '建模指标与实体关系',
    subtitle: '定义计算口径与关联拓扑',
    desc: '在数据集中维护业务指标计算公式（Metrics），定义 1:1、1:N 实体关联与跨库 Join 图谱。',
    tag: '语义建模',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '点击下方数据集进入配置'
  },
  {
    step: 4,
    title: '向量同步与智能体挂载',
    subtitle: '供 ChatBI 与 Agent 精准检索',
    desc: '一键将数据集导出为紧凑 YAML 并同步至向量库，供 ChatBI 与 Agent 智能体精准召回并生成 SQL。',
    tag: '消费应用',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '在智能体编排中生效'
  }
]

const handleAction = (type: string | null) => {
  if (!type) return
  if (type === 'datasource') {
    router.push('/dashboard/data-sources')
  } else if (type === 'import' || type === 'create') {
    emit('action', type)
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
    ariaLabel="元数据管理流程指引"
    title="元数据管理全流程指引"
    badge="4 步构建语义大脑"
    subtitle="物理数据库到 AI 语义模型的生命周期管理，助力 Text-to-SQL 准确率提升"
    :steps="steps"
    :columns="4"
    :collapsible="props.collapsible"
    title-tag="h2"
    icon-path="M13 10V3L4 14h7v7l9-11h-7z"
    header-class="relative z-10 flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 pb-3"
    header-left-class="flex items-center gap-2.5"
    title-class="text-base font-bold text-gray-900"
    badge-class="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary"
    subtitle-class="text-xs text-gray-500"
    controls-class="flex items-center gap-2"
    collapse-button-class="inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-medium text-gray-600 hover:bg-primary/10 hover:text-gray-900 transition-colors cursor-pointer"
    dismiss-button-class="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-gray-600 hover:border-gray-300 hover:bg-white hover:text-gray-900 transition-colors shadow-2xs cursor-pointer"
    action-button-class="inline-flex w-full items-center justify-center gap-1 rounded-lg border border-gray-200 bg-white py-1 text-xs font-medium text-primary hover:border-primary/40 hover:bg-primary/5 transition-colors cursor-pointer"
    collapse-chevron-inverted
    @action="handleAction"
    @close="handleClose"
    @dismiss="handleDismiss"
  />
</template>
