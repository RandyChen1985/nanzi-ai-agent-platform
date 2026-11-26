<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import FlowGuideStrip from '../common/FlowGuideStrip.vue'

/**
 * 平台**管理分区**是否可用（待审核 / 已发布 / 智能体中心），即 `canAccessPlatformWorkspace`。
 *
 * 「平台技能」列表对所有登录用户开放（没有写权限时只读），因此它不再受这个开关限制。
 * 第 3、5 步指向「待审核 / 智能体中心」，这些视图在个人技能页根本不渲染
 * （tab bar 整体 `v-if="!personalOnly"`），无写权限时也不可达；点进去只会让技能列表
 * 整块消失，页面看起来像坏了。因此这两个入口在不可用时一律隐藏，改为文案说明。
 */
const props = withDefaults(defineProps<{ platformWorkspaceAvailable?: boolean }>(), {
  platformWorkspaceAvailable: true,
})

const emit = defineEmits<{
  (e: 'action', type: 'create' | 'review' | 'global'): void
  (e: 'close'): void
  (e: 'dismiss'): void
}>()

const router = useRouter()

type ActionType = 'create' | 'review' | 'global' | 'agents'

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
  /** 该步的目标视图属平台管理分区：只有 `element:skills:admin` 可达（个人技能页不渲染） */
  requiresPlatformAdmin?: boolean
}

const steps: FlowGuideStep[] = [
  {
    step: 1,
    title: '技能创建与目录规划',
    subtitle: 'SKILL.md 与 YAML 声明',
    desc: '设定英文 ID、中文名称与业务描述（Description 是大模型识别调用意图的关键）；自动生成 SKILL.md 入口。',
    tag: '规范创建',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '新建技能',
    actionType: 'create',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 2,
    title: '脚本编写与沙箱调试',
    subtitle: '多文件目录与代码实现',
    desc: '在内置工作台编写多级文件树（scripts/ 脚本、resources/ 模板、references/ 规范）；支持 Markdown 在线预览。',
    tag: '开发沉淀',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '',
    actionType: null,
    hintText: '点击卡片进入文件树编辑',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 3,
    title: '提审申请与平台合规',
    subtitle: '申请发布与管理员审核',
    desc: '个人技能成熟后在抽屉中点击「申请发布至平台」；管理员在「待审核」Tab 下审查文件 Diff 与代码合规性。',
    tag: '提审合规',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '前往待审核',
    actionType: 'review',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null,
    requiresPlatformAdmin: true
  },
  {
    step: 4,
    title: '平台发布与版本隔离',
    subtitle: '全员共享与多版本基线',
    desc: '审批通过后晋升为平台公共技能，对全体业务角色公开；严格实现生产发布版本与开发草稿隔离，互不干扰。',
    tag: '生产就绪',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '平台技能',
    actionType: 'global',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  },
  {
    step: 5,
    title: '智能体绑定与动态激活',
    subtitle: '多智能体装配与意图调度',
    desc: '在智能体中心为目标助手显式勾选挂载该技能，或由大模型意图感知动态加载；支持查看智能体使用分布。',
    tag: '生产消费',
    tagClass: 'bg-gray-50 text-gray-600 border-gray-200',
    iconBg: 'bg-primary/10 text-primary font-semibold',
    actionText: '前往智能体中心',
    actionType: 'agents',
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null,
    requiresPlatformAdmin: true
  }
]

/** 仅平台管理分区入口：个人技能页 / 无权限时既不显示按钮，也不允许触发跳转。 */
const isAdminOnlyStep = (item: { requiresPlatformAdmin?: boolean }) =>
  Boolean(item.requiresPlatformAdmin) && !props.platformWorkspaceAvailable

/** 不可用的管理分区入口降级为纯文案提示，按钮整块不渲染 */
const visibleSteps = computed(() =>
  steps.map((item) =>
    isAdminOnlyStep(item)
      ? { ...item, actionType: null, hintText: '仅平台技能管理员可用' }
      : item,
  ),
)

const handleAction = (type: string | null) => {
  if (!type) return
  // 兜底守卫：即使调用方漏了 v-if，也不把个人技能页切到不可见的平台视图 / 越界跳转。
  // 「新建技能」「平台技能」对所有能进页面的用户合法（后者只读也能看），
  // 只有「待审核」「智能体中心」这两个管理分区受闸。
  if (type !== 'create' && type !== 'global' && !props.platformWorkspaceAvailable) return
  if (type === 'create') {
    emit('action', 'create')
  } else if (type === 'review') {
    emit('action', 'review')
  } else if (type === 'global') {
    emit('action', 'global')
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
    ariaLabel="技能工作台全流程指引"
    title="技能工作台全生命周期研发指引"
    badge="5 步研发体系"
    subtitle="从 SKILL.md 规范定义、代码脚本沉淀，到发布审批、平台多版本隔离与智能体动态装配激活"
    :steps="visibleSteps"
    icon-path="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"
    action-button-class="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-emerald-200 bg-emerald-50/60 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-600 hover:text-white transition-colors cursor-pointer"
    @action="handleAction"
    @close="handleClose"
    @dismiss="handleDismiss"
  />
</template>
