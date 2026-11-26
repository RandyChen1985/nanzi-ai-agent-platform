<script setup lang="ts">
import { useRouter } from 'vue-router'
import FlowGuideStrip from '../common/FlowGuideStrip.vue'

const emit = defineEmits<{
  (e: 'action', type: 'sync_all'): void
  (e: 'close'): void
  (e: 'dismiss'): void
}>()

const router = useRouter()

type ActionType = 'chat' | 'sync_all' | 'agents'

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
    title: '样本沉淀与录入',
    subtitle: '会话采纳点赞与专家录入',
    desc: '用户在 Chat 对话中对满意答案/SQL 点击「采纳点赞」，系统自动提取意图与 SQL 沉淀为待审样本；专家亦可后台手动录入。',
    tag: '数据来源',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '前往对话',
    actionType: 'chat',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 2,
    title: '专家审核与打标',
    subtitle: '清洗意图与黄金 SQL 确认',
    desc: '管理员进入「待审核」列表，校验自然语言意图（Refined Query）与 SQL 标准性；通过审批后正式纳入经验案例库。',
    tag: '质量把关',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '待审核 Tab 下逐条审批',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 3,
    title: '向量同步与索引',
    subtitle: 'RAGFlow/Redis 向量化',
    desc: '审核通过的案例需同步至向量索引库；支持单个案例同步或顶部一键全量同步，构建语义密集检索向量。',
    tag: '索引构建',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '一键全量同步',
    actionType: 'sync_all',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 4,
    title: '动态 Few-Shot 召回',
    subtitle: '问答上下文动态注入',
    desc: '智能体接收到用户新提问时，后台通过向量检索秒级召回最相似的 1~3 条案例注入 Prompt，辅助大模型生成高精度 SQL。',
    tag: '模型消费',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '前往智能体中心',
    actionType: 'agents',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 5,
    title: '引用统计与持续迭代',
    subtitle: 'use_count 统计与飞轮进化',
    desc: '系统自动统计每条案例的真实引用频次（use_count），对过时或低质案例标记「废弃」，持续维持高水准业务经验库。',
    tag: '飞轮迭代',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '卡片查看引用次数与点赞',
    secondaryActionText: null,
    secondaryActionType: null
  }
]

const handleAction = (type: string | null) => {
  if (!type) return
  if (type === 'chat') {
    router.push('/dashboard/chat')
  } else if (type === 'agents') {
    router.push('/dashboard/agent-management')
  } else if (type === 'sync_all') {
    emit('action', 'sync_all')
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
    ariaLabel="案例集管理全流程指引"
    title="案例集全生命周期沉淀与应用指引"
    badge="Few-Shot 学习闭环"
    subtitle="理解案例「从哪里来、如何审核向量化、如何在智能体问答中秒级 Few-Shot 召回注入提升准确率」"
    :steps="steps"
    icon-path="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
    action-button-class="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-cyan-200 bg-cyan-50/60 py-1 text-xs font-medium text-cyan-700 dark:text-cyan-300 hover:bg-primary hover:text-white transition-colors cursor-pointer"
    @action="handleAction"
    @close="handleClose"
    @dismiss="handleDismiss"
  />
</template>
