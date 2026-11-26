<script setup lang="ts">
import { useRouter } from 'vue-router'
import FlowGuideStrip from '../common/FlowGuideStrip.vue'

const emit = defineEmits<{
  (e: 'action', type: 'add' | 'marketplace'): void
  (e: 'close'): void
  (e: 'dismiss'): void
}>()

const router = useRouter()

type ActionType = 'add' | 'marketplace' | 'roles' | 'agents'

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
    title: '服务登记与生态安装',
    subtitle: 'SSE / Streamable HTTP / 生态市场',
    desc: '支持接入 SSE 或 Streamable HTTP 远程服务，或粘贴包含 URL 的 JSON 配置；支持从生态市场一键安装官方精选服务。',
    tag: '服务接入',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '新增服务',
    actionType: 'add',
    hintText: '',
    secondaryActionText: '生态市场',
    secondaryActionType: 'marketplace'
  },
  {
    step: 2,
    title: '探活发现与工具同步',
    subtitle: 'tools/list 自动提取入参',
    desc: '系统自动发起 tools/list 协议握手与健康检查，自动解析工具名称、描述与 JSON Schema 入参定义；支持一键刷新同步。',
    tag: '协议自检',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '服务列表点击刷新即刻同步',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 3,
    title: '在线测试与参数调试',
    subtitle: '内置沙箱实时参数验证',
    desc: '点击服务卡片中各工具的「测试」按钮进入调试台，输入测试值实时触发 MCP 执行并查看原始返回，快速排查网络和鉴权。',
    tag: '沙箱验证',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '卡片中点击测试按钮验证',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 4,
    title: '范围隔离与权限分配',
    subtitle: '平台共享与个人私有隔离',
    desc: '区分「平台公开 MCP」（全员可用，需管理员维护）与「我的私有 MCP」（个人专属）；在角色管理中下发操作与维护权限。',
    tag: '安全治理',
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
    title: '智能体挂载与协同调用',
    subtitle: '多智能体生态生产落地',
    desc: '前往「智能体中心」在目标智能体版本装配中显式勾选挂载该 MCP 服务；智能体在对话中自主调度 MCP 执行复杂任务。',
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
  if (type === 'roles') {
    router.push('/dashboard/roles')
  } else if (type === 'agents') {
    router.push('/dashboard/agent-management')
  } else if (type === 'add') {
    emit('action', 'add')
  } else if (type === 'marketplace') {
    emit('action', 'marketplace')
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
    ariaLabel="MCP 工具集全流程指引"
    title="MCP 工具集全生命周期接入指引"
    badge="5 步实施标准"
    subtitle="从服务登记与生态市场安装、tools/list 探活自发现，到沙箱测试、权限范围隔离与智能体装配调用"
    :steps="steps"
    icon-path="M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a2 2 0 110-4h1a1 1 0 001-1V7a1 1 0 011-1h3a1 1 0 001-1V4z"
    action-button-class="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-primary/20 bg-primary/5 py-1 text-xs font-medium text-primary-dark hover:bg-primary hover:text-white transition-colors cursor-pointer"
    @action="handleAction"
    @close="handleClose"
    @dismiss="handleDismiss"
  />
</template>
