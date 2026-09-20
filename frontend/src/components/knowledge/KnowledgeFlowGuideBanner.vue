<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

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
const isCollapsed = ref(true)

const steps = [
  {
    step: 1,
    title: '环境连通与库创建',
    subtitle: '部署 RAGFlow 并定义切分',
    desc: '在系统配置中配置 RAGFlow API 地址与密钥确保连通；选择通用、QA问答、表格等切分模板创建知识库。',
    tag: '基础环境',
    tagClass: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    iconBg: 'bg-emerald-600 text-white font-bold',
    actionText: '系统配置',
    actionType: 'system_config' as const,
    hintText: '',
    secondaryActionText: '新建知识库',
    secondaryActionType: 'create' as const
  },
  {
    step: 2,
    title: '文档上传与解析',
    subtitle: 'OCR识别与向量分块',
    desc: '支持批量上传 PDF、Word、Excel、Markdown 等多格式；自动提取版面与生成 Chunks 切片，支持人工审校修正。',
    tag: '数据入库',
    tagClass: 'bg-blue-50 text-blue-700 border-blue-200',
    iconBg: 'bg-blue-600 text-white font-bold',
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
    tagClass: 'bg-indigo-50 text-indigo-700 border-indigo-200',
    iconBg: 'bg-indigo-600 text-white font-bold',
    actionText: '召回测试',
    actionType: 'retrieval_test' as const,
    hintText: '',
    secondaryActionText: '指标监控',
    secondaryActionType: 'metrics' as const
  },
  {
    step: 4,
    title: '权限授权与角色分配',
    subtitle: '配置可见性与角色访问',
    desc: '设置知识库公开/私有范围与协作者读写权限；在角色管理中授予对应角色访问权限，确保业务人员可正常检索。',
    tag: '数据安全',
    tagClass: 'bg-purple-50 text-purple-700 border-purple-200',
    iconBg: 'bg-purple-600 text-white font-bold',
    actionText: '前往角色管理',
    actionType: 'roles' as const,
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
    tagClass: 'bg-amber-50 text-amber-700 border-amber-200',
    iconBg: 'bg-amber-600 text-white font-bold',
    actionText: '前往智能体中心',
    actionType: 'agents' as const,
    hintText: '',
    secondaryActionText: null,
    secondaryActionType: null
  }
]

const handleAction = (type: 'system_config' | 'create' | 'retrieval_test' | 'metrics' | 'roles' | 'agents' | null) => {
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
  <section
    aria-label="知识库管理全流程指引"
    class="relative overflow-hidden rounded-2xl border border-emerald-100 bg-gradient-to-r from-emerald-50/70 via-teal-50/40 to-slate-50/60 p-4 shadow-sm transition-all sm:p-5"
  >
    <!-- 背景光晕装饰 -->
    <div
      class="pointer-events-none absolute -right-16 -top-16 h-48 w-48 rounded-full bg-emerald-200/30 blur-3xl"
      aria-hidden="true"
    ></div>
    <div
      class="pointer-events-none absolute left-1/3 -bottom-20 h-40 w-40 rounded-full bg-teal-200/25 blur-2xl"
      aria-hidden="true"
    ></div>

    <!-- 顶部标题与控制栏 -->
    <div class="relative z-10 flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <div
          class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-emerald-600 text-white shadow-md shadow-emerald-600/30"
          style="background-color: #059669; color: #ffffff;"
        >
          <svg class="h-5 w-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
          </svg>
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h3 class="text-sm font-bold text-gray-900 sm:text-base">
              知识库全生命周期构建指引
            </h3>
            <span
              class="rounded-full bg-emerald-100/80 px-2 py-0.5 text-[11px] font-semibold text-emerald-700"
            >
              5 步实施标准
            </span>

            <!-- RAGFlow 引擎状态徽章 -->
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
          </div>
          <p class="mt-0.5 text-xs text-gray-500">
            从基础设施连通、切分策略定义、多模态文档解析，到召回测试评测、角色权限下发与智能体挂载
          </p>
        </div>
      </div>

      <!-- 右侧快捷控制按钮 -->
      <div class="flex items-center gap-1.5 sm:gap-2">
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-gray-600 shadow-sm hover:bg-white hover:text-gray-900 transition-colors cursor-pointer"
          @click="isCollapsed = !isCollapsed"
        >
          <span>{{ isCollapsed ? '展开流程' : '收起' }}</span>
          <svg
            class="h-3.5 w-3.5 transition-transform duration-200"
            :class="{ 'rotate-180': !isCollapsed }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-gray-500 shadow-sm hover:bg-white hover:text-amber-600 transition-colors cursor-pointer"
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

    <!-- 5 步骤流程卡片网格 -->
    <div
      v-show="!isCollapsed"
      class="relative z-10 mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5"
    >
      <div
        v-for="(item, idx) in steps"
        :key="item.step"
        class="group relative flex flex-col justify-between rounded-xl border border-white/80 bg-white/85 p-3 shadow-sm backdrop-blur-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-emerald-200 hover:bg-white hover:shadow-md"
      >
        <!-- 连接箭头（仅在桌面端第 1~4 步显示） -->
        <div
          v-if="idx < steps.length - 1"
          class="pointer-events-none absolute -right-2 top-1/2 z-20 hidden -translate-y-1/2 text-emerald-300 lg:block"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
          </svg>
        </div>

        <div>
          <!-- 卡片头部：序号与标签 -->
          <div class="flex items-start justify-between gap-1.5">
            <div class="flex items-center gap-1.5 min-w-0">
              <span
                class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold shadow-sm"
                :class="item.iconBg"
              >
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
            <p class="text-[11px] font-medium text-emerald-900/80">{{ item.subtitle }}</p>
            <p class="mt-1 text-[11px] leading-relaxed text-gray-500">{{ item.desc }}</p>
          </div>
        </div>

        <!-- 底部快捷动作 -->
        <div class="mt-3.5 border-t border-gray-100/80 pt-2.5">
          <div v-if="item.actionType" class="flex items-center gap-1.5">
            <button
              type="button"
              class="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-emerald-200 bg-emerald-50/60 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-600 hover:text-white transition-colors cursor-pointer"
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
