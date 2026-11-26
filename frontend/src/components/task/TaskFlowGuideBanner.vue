<script setup lang="ts">
import { useRouter } from 'vue-router'
import FlowGuideStrip from '../common/FlowGuideStrip.vue'

const emit = defineEmits<{
  (e: 'action', type: 'create' | 'history'): void
  (e: 'close'): void
  (e: 'dismiss'): void
}>()

const router = useRouter()

type ActionType = 'create' | 'agents' | 'history'

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
    title: '任务创建与周期编排',
    subtitle: 'Cron 定时与智能体绑定',
    desc: '设定任务名称并绑定负责执行的目标智能体；设置每日/每周/每月定时周期与提示词，支持 AI 智能优化 Prompt。',
    tag: '触发编排',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '新建任务',
    actionType: 'create',
    hintText: '',
    secondaryActionText: '智能体中心',
    secondaryActionType: 'agents'
  },
  {
    step: 2,
    title: '资源限定与安全审批',
    subtitle: 'Scope 沙箱与审批策略',
    desc: '限定任务仅可调用的数据集、知识库、Skills 与 MCP 工具；设置 Allow 放行、Ask 人工授权或 Deny 拦截高危操作。',
    tag: '安全沙箱',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '编辑中设置资源与审批模式',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 3,
    title: '渠道分发与触达订阅',
    subtitle: '站内信/钉钉/企微/邮件',
    desc: '配置结果分发渠道，任务执行完毕后自动生成 Markdown 结构化报表并推送至群机器人、邮箱或沉淀至报告中心。',
    tag: '消息触达',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '通知配置中勾选推送渠道',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 4,
    title: '手动试跑与时序观测',
    subtitle: '立即执行与 Trace 观测',
    desc: '卡片上点击「立即执行」快速试跑验证；通过 Session Trace 时序链路全景观测大模型思考耗时与工具调用明细。',
    tag: '沙箱验证',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '卡片点击「立即执行」或 Trace',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 5,
    title: '健康监控与异常处置',
    subtitle: '执行记录与审计告警',
    desc: '切换至「执行记录」查看运行日志、耗时分布与健康状态（健康/需关注/异常），支持一键重试与异常告警排查。',
    tag: '监控治理',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '执行记录',
    actionType: 'history',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  }
]

const handleAction = (type: string | null) => {
  if (!type) return
  if (type === 'create') {
    emit('action', 'create')
  } else if (type === 'agents') {
    router.push('/dashboard/agent-management')
  } else if (type === 'history') {
    emit('action', 'history')
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
    ariaLabel="任务调度台全流程指引"
    title="任务调度台全流程构建指引"
    badge="5 步自动化闭环"
    subtitle="从 Cron 定时周期编排、资源与安全审批限定，到多渠道分发触达、Session Trace 时序观测与健康监控"
    :steps="steps"
    icon-path="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
    action-button-class="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-orange-200 bg-orange-50/60 py-1 text-xs font-medium text-orange-700 hover:bg-orange-600 hover:text-white transition-colors cursor-pointer"
    @action="handleAction"
    @close="handleClose"
    @dismiss="handleDismiss"
  />
</template>
