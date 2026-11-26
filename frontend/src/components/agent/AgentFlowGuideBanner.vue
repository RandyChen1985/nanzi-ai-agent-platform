<script setup lang="ts">
import { useRouter } from 'vue-router'
import FlowGuideStrip from '../common/FlowGuideStrip.vue'

const emit = defineEmits<{
  (e: 'action', type: 'create' | 'template' | 'skills'): void
  (e: 'close'): void
  (e: 'dismiss'): void
}>()

const router = useRouter()

type ActionType = 'create' | 'template' | 'skills' | 'roles' | 'debug'

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
    title: '定义与系统标识',
    subtitle: '设定业务定位与委派依据',
    desc: '填写名称与业务描述。提示：仅勾选「系统智能体」才能进入主专家的智能委派目录；支持从场景模板克隆。',
    tag: '基础准备',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '新建智能体',
    actionType: 'create',
    hintText: '',
    secondaryActionText: '场景模板',
    secondaryActionType: 'template'
  },
  {
    step: 2,
    title: '模型与能力装配',
    subtitle: '整合 Prompt、工具与技能',
    desc: '配置模型底座与提示词，按需装配内置 API 工具、MCP 外部服务、动态 Skills 以及挂载数据集/知识库。',
    tag: '资源整合',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '技能工作台',
    actionType: 'skills',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 3,
    title: '版本管理与发布',
    subtitle: '多版本隔离与就绪度校验',
    desc: '草稿 (DRAFT) 与生产发布隔离。系统自动校验主类型、工具链与数据绑定就绪度，一键发布即刻生效。',
    tag: '质量基线',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '点击卡片管理版本',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 4,
    title: '角色授权与权限',
    subtitle: '下发角色权限确保可访问',
    desc: '在角色管理中为目标业务角色授予该智能体的交互权限。未授权角色的普通用户将无法在工作台查看或调用。',
    tag: '权限受控',
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
    title: '调试与渠道消费',
    subtitle: '时序跟踪与全渠道落地',
    desc: '在调试台观察思考链与工具调用；系统智能体由主专家按任务需要自动委派；支持生成 Embed 对话或对接钉钉/企微。',
    tag: '消费应用',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '去智能体调试',
    actionType: 'debug',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  }
]

const handleAction = (type: string | null) => {
  if (!type) return
  if (type === 'roles') {
    router.push('/dashboard/roles')
  } else if (type === 'debug') {
    router.push('/dashboard/agent-debug')
  } else if (type === 'template') {
    router.push('/dashboard/scenario-templates')
  } else if (type === 'skills') {
    router.push('/dashboard/skills')
  } else if (type === 'create') {
    emit('action', 'create')
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
    ariaLabel="智能体中心全流程指引"
    title="智能体全流程构建指引"
    badge="5 步生命周期"
    subtitle="从角色定位、大模型与技能装配，到版本发布、权限分配与全渠道消费"
    :steps="steps"
    icon-path="M13 10V3L4 14h7v7l9-11h-7z"
    action-button-class="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-gray-200 bg-white py-1 text-xs font-medium text-primary hover:border-primary/40 hover:bg-primary/5 transition-colors cursor-pointer"
    @action="handleAction"
    @close="handleClose"
    @dismiss="handleDismiss"
  />
</template>
