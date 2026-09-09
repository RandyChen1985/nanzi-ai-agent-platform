<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  env: 'host' | 'docker' | 'e2b' | 'ssh' | 'k8s'
}>()

const emit = defineEmits<{
  (e: 'dismiss'): void
  (e: 'ignore'): void
}>()

// 每次发射的 env 对应一个「Bash 实际执行环境」提示块。
// 配色类务必写成字面量，保证 Tailwind JIT 能静态提取到每个类名。
const CONTENT = {
  docker: {
    icon: '🟢',
    title: 'Bash 运行在 Docker 沙箱',
    hint: '命令在隔离的 Docker 容器内执行，仍需遵守命令安全规则',
    box: 'border-emerald-200 bg-emerald-50/90 text-emerald-900 shadow-sm dark:border-emerald-500/30 dark:bg-emerald-950/40 dark:text-emerald-100',
    hintTone: 'text-emerald-700/80 dark:text-emerald-200/70',
    btn: 'text-emerald-700/60 hover:bg-emerald-100/80 dark:text-emerald-200/60 dark:hover:bg-emerald-900/60',
    btnClose: 'text-emerald-700/70 hover:bg-emerald-100/80 dark:text-emerald-200/70 dark:hover:bg-emerald-900/60',
  },
  k8s: {
    icon: '🟢',
    title: 'Bash 运行在 Kubernetes 沙箱',
    hint: '命令在隔离的 Kubernetes 沙箱 Pod 内执行，仍需遵守命令安全规则',
    box: 'border-emerald-200 bg-emerald-50/90 text-emerald-900 shadow-sm dark:border-emerald-500/30 dark:bg-emerald-950/40 dark:text-emerald-100',
    hintTone: 'text-emerald-700/80 dark:text-emerald-200/70',
    btn: 'text-emerald-700/60 hover:bg-emerald-100/80 dark:text-emerald-200/60 dark:hover:bg-emerald-900/60',
    btnClose: 'text-emerald-700/70 hover:bg-emerald-100/80 dark:text-emerald-200/70 dark:hover:bg-emerald-900/60',
  },
  host: {
    icon: '⚠️',
    title: 'Bash 运行在宿主机上',
    hint: 'Bash 直接在后端进程所在环境执行，存在命令风险，建议 Docker 部署或改用 sandbox',
    box: 'border-amber-200 bg-amber-50/90 text-amber-900 shadow-sm dark:border-amber-500/30 dark:bg-amber-950/40 dark:text-amber-100',
    hintTone: 'text-amber-700/80 dark:text-amber-200/70',
    btn: 'text-amber-700/60 hover:bg-amber-100/80 dark:text-amber-200/60 dark:hover:bg-amber-900/60',
    btnClose: 'text-amber-700/70 hover:bg-amber-100/80 dark:text-amber-200/70 dark:hover:bg-amber-900/60',
  },
  e2b: {
    icon: '🟣',
    title: 'Bash 运行在 E2B 沙箱',
    hint: '命令在 E2B 云沙箱中执行，环境按需创建，执行后随会话释放',
    box: 'border-violet-200 bg-violet-50/90 text-violet-900 shadow-sm dark:border-violet-500/30 dark:bg-violet-950/40 dark:text-violet-100',
    hintTone: 'text-violet-700/80 dark:text-violet-200/70',
    btn: 'text-violet-700/60 hover:bg-violet-100/80 dark:text-violet-200/60 dark:hover:bg-violet-900/60',
    btnClose: 'text-violet-700/70 hover:bg-violet-100/80 dark:text-violet-200/70 dark:hover:bg-violet-900/60',
  },
  ssh: {
    icon: '🔵',
    title: 'Bash 运行在远端 SSH 主机',
    hint: '命令经 SSH 在远端主机执行，请确认该主机受控、符合安全基线',
    box: 'border-sky-200 bg-sky-50/90 text-sky-900 shadow-sm dark:border-sky-500/30 dark:bg-sky-950/40 dark:text-sky-100',
    hintTone: 'text-sky-700/80 dark:text-sky-200/70',
    btn: 'text-sky-700/60 hover:bg-sky-100/80 dark:text-sky-200/60 dark:hover:bg-sky-900/60',
    btnClose: 'text-sky-700/70 hover:bg-sky-100/80 dark:text-sky-200/70 dark:hover:bg-sky-900/60',
  },
} as const

const active = computed(() => CONTENT[props.env])
</script>

<template>
  <div
    role="status"
    :class="
      `mb-2 flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2 text-xs ${active.box}`
    "
  >
    <span class="font-semibold">{{ active.icon }} {{ active.title }}</span>
    <span :class="active.hintTone">
      {{ active.hint }}
    </span>
    <div class="ml-auto flex items-center gap-2">
      <button
        type="button"
        :class="`rounded-lg px-2 py-1 ${active.btn}`"
        aria-label="忽略提示"
        @click="emit('ignore')"
      >
        忽略提示
      </button>
      <button
        type="button"
        :class="`rounded-lg px-2 py-1 ${active.btnClose}`"
        aria-label="关闭"
        @click="emit('dismiss')"
      >
        ✕
      </button>
    </div>
  </div>
</template>