<script setup lang="ts">
import { computed, ref } from 'vue'

/**
 * 流程指引横幅的公共外壳。
 *
 * 结构：顶部标题区（图标 + 标题 + 徽章 + 副标题）+ 展开/收起 + 不再提示 + 关闭按钮
 * + 步骤卡片网格（含步骤间箭头）+ 每步底部快捷动作 / 提示文案。
 *
 * 智能体、案例集、知识库、MCP、元数据、技能、任务 7 个页面共用这一套结构，
 * 差异只有文案、步数、列数、顶部图标，以及少数几处历史遗留的 Tailwind 类名 ——
 * 后者通过下面带默认值的类名 props 覆盖（默认值 = 「标准样式」，与多数页面一致），
 * 这样每条页面的视觉与交互都保持抽取前的样子。
 */
interface FlowGuideStep {
  /** 步骤序号，同时作为列表 key */
  step: number
  title: string
  subtitle: string
  desc: string
  tag: string
  /** 标签配色，如 'bg-gray-50 text-gray-600 border-gray-200' */
  tagClass: string
  /** 序号圆点配色，如 'bg-primary/10 text-primary font-semibold' */
  iconBg: string
  /** 主按钮文案；为空时该步只展示 hintText */
  actionText: string
  /** 主按钮动作标识，由父组件映射成路由跳转或向上 emit；null 表示该步没有按钮 */
  actionType: string | null
  /** 没有主按钮时展示的提示文案 */
  hintText: string
  /** 可选次要按钮（如「场景模板」「生态市场」） */
  secondaryActionText?: string | null
  secondaryActionType?: string | null
}

const props = withDefaults(
  defineProps<{
    /** 外层 section 的无障碍标签 */
    ariaLabel: string
    title: string
    /** 标题右侧徽章文案，如「5 步生命周期」 */
    badge: string
    subtitle: string
    steps: FlowGuideStep[]
    /** 桌面端列数，仅支持 4 / 5 / 6（Tailwind 需静态类名） */
    columns?: number
    /** 是否显示展开/收起按钮 */
    collapsible?: boolean
    /** 初始是否折叠（各页面原 isCollapsed 默认值均为 true） */
    initialCollapsed?: boolean
    /** 顶部 8x8 圆角方块内的 svg 路径 */
    iconPath?: string
    /** 标题标签，元数据页历史使用 h2 */
    titleTag?: 'h2' | 'h3'
    headerClass?: string
    headerLeftClass?: string
    badgeClass?: string
    titleClass?: string
    subtitleClass?: string
    controlsClass?: string
    collapseButtonClass?: string
    dismissButtonClass?: string
    cardClass?: string
    stepIndexClass?: string
    actionButtonClass?: string
    /** 元数据页历史写法：折叠时箭头反向（rotate-180） */
    collapseChevronInverted?: boolean
  }>(),
  {
    columns: 5,
    collapsible: true,
    initialCollapsed: true,
    iconPath: 'M13 10V3L4 14h7v7l9-11h-7z',
    titleTag: 'h3',
    headerClass: 'relative z-10 flex flex-wrap items-center justify-between gap-3',
    headerLeftClass: 'flex items-center gap-3',
    badgeClass: 'rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary',
    titleClass: 'text-sm font-bold text-gray-900 sm:text-base',
    subtitleClass: 'mt-0.5 text-xs text-gray-500',
    controlsClass: 'flex items-center gap-1.5 sm:gap-2',
    collapseButtonClass:
      'inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-gray-600 shadow-2xs hover:bg-white hover:text-gray-900 transition-colors cursor-pointer',
    dismissButtonClass:
      'inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-gray-500 shadow-2xs hover:bg-white hover:text-amber-600 transition-colors cursor-pointer',
    cardClass:
      'group relative flex flex-col justify-between rounded-lg border border-gray-200/80 bg-gray-50/50 p-3 transition-colors hover:border-gray-300 hover:bg-white',
    stepIndexClass:
      'flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold shadow-2xs',
    actionButtonClass:
      'inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-gray-200 bg-white py-1 text-xs font-medium text-primary hover:border-primary/40 hover:bg-primary/5 transition-colors cursor-pointer',
    collapseChevronInverted: false,
  },
)

const emit = defineEmits<{
  (e: 'action', type: string): void
  (e: 'close'): void
  (e: 'dismiss'): void
}>()

const isCollapsed = ref(props.initialCollapsed)

/** Tailwind 需要静态类名，这里不能用模板字符串拼接 */
const gridColumnsClass = computed(() =>
  props.columns === 4 ? 'lg:grid-cols-4' : props.columns === 6 ? 'lg:grid-cols-6' : 'lg:grid-cols-5',
)

/** 绝大多数页面「展开后箭头朝上」；元数据页历史写法相反，用 prop 保留 */
const collapseChevronRotated = computed(() =>
  props.collapseChevronInverted ? isCollapsed.value : !isCollapsed.value,
)

const handleAction = (type: string | null) => {
  if (!type) return
  emit('action', type)
}

const handleClose = () => {
  emit('close')
}

const handleDismiss = () => {
  emit('dismiss')
}
</script>

<template>
  <section
    :aria-label="ariaLabel"
    class="relative overflow-hidden rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-xs transition-all"
  >
    <!-- 顶部标题与控制栏 -->
    <div :class="headerClass">
      <div :class="headerLeftClass">
        <div
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
        >
          <svg class="h-4 w-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" :d="iconPath" />
          </svg>
        </div>
        <div>
          <div class="flex items-center gap-2">
            <component :is="titleTag" :class="titleClass">
              {{ title }}
            </component>
            <span :class="badgeClass">
              {{ badge }}
            </span>
            <!-- 页面自带的额外徽章（如知识库的 RAGFlow 引擎状态） -->
            <slot name="badges" />
          </div>
          <p :class="subtitleClass">
            {{ subtitle }}
          </p>
        </div>
      </div>

      <!-- 右侧快捷控制按钮 -->
      <div :class="controlsClass">
        <button
          v-if="collapsible"
          type="button"
          :class="collapseButtonClass"
          @click="isCollapsed = !isCollapsed"
        >
          <span>{{ isCollapsed ? '展开流程' : '收起' }}</span>
          <svg
            class="h-3.5 w-3.5 transition-transform duration-200"
            :class="{ 'rotate-180': collapseChevronRotated }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        <button
          type="button"
          :class="dismissButtonClass"
          title="下次进入不再主动弹出此引导"
          @click="handleDismiss"
        >
          <svg class="h-3.5 w-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l18 18" />
          </svg>
          <span>不再提示</span>
        </button>

        <button
          type="button"
          class="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-200/50 hover:text-gray-700 transition-colors cursor-pointer"
          title="关闭本次指引"
          @click="handleClose"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 步骤流程卡片网格 -->
    <div
      v-show="!isCollapsed"
      class="relative z-10 mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2"
      :class="gridColumnsClass"
    >
      <div
        v-for="(item, idx) in steps"
        :key="item.step"
        :class="cardClass"
      >
        <!-- 连接箭头（仅在桌面端、且不是最后一步时显示） -->
        <div
          v-if="idx < steps.length - 1"
          class="pointer-events-none absolute -right-2 top-1/2 z-20 hidden -translate-y-1/2 text-gray-300 lg:block"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
          </svg>
        </div>

        <div>
          <!-- 卡片头部：序号与标签 -->
          <div class="flex items-start justify-between gap-1.5">
            <div class="flex items-center gap-1.5 min-w-0">
              <span :class="[stepIndexClass, item.iconBg]">
                {{ item.step }}
              </span>
              <span class="text-xs font-bold text-gray-900 leading-tight truncate" :title="item.title">{{ item.title }}</span>
            </div>
            <span
              class="rounded-md border px-1.5 py-0.5 text-[10px] font-semibold shrink-0 whitespace-nowrap leading-none"
              :class="item.tagClass"
            >
              {{ item.tag }}
            </span>
          </div>

          <!-- 副标题与说明文案 -->
          <div class="mt-2.5">
            <p class="text-[11px] font-medium text-gray-700">{{ item.subtitle }}</p>
            <p class="mt-1 text-[11px] leading-relaxed text-gray-500">{{ item.desc }}</p>
          </div>
        </div>

        <!-- 底部快捷动作 -->
        <div class="mt-3.5 border-t border-gray-100/80 pt-2.5">
          <div v-if="item.actionType" class="flex items-center gap-1.5">
            <button
              type="button"
              :class="actionButtonClass"
              @click="handleAction(item.actionType)"
            >
              <span>{{ item.actionText }}</span>
              <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
              </svg>
            </button>
            <button
              v-if="item.secondaryActionType"
              type="button"
              class="inline-flex items-center justify-center rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-100 transition-colors cursor-pointer"
              :title="item.secondaryActionText || undefined"
              @click="handleAction(item.secondaryActionType)"
            >
              <span>{{ item.secondaryActionText }}</span>
            </button>
          </div>
          <div
            v-else
            class="flex items-center justify-center rounded-lg bg-gray-50/80 py-1 text-[11px] font-medium text-gray-400"
          >
            <span>{{ item.hintText }}</span>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
