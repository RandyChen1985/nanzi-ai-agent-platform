<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from "vue";

export type DockerWorkspaceStatus = "idle" | "starting" | "stopping" | "running" | "error";

const props = defineProps<{
  workspaceStatus: DockerWorkspaceStatus;
  workspaceError?: string;
  containerId?: string | null;
  /** 沙箱后端术语维度：docker（默认）| k8s */
  backend?: "docker" | "k8s";
}>();

const emit = defineEmits<{
  (event: "start"): void;
  (event: "refresh"): void;
  (event: "close"): void;
}>();

const AUTO_DISMISS_SECONDS = 3;
const remainingSeconds = ref(AUTO_DISMISS_SECONDS);
/** 倒计时结束后折叠为浮标模式（不完全关闭） */
const collapsed = ref(false);
let countdownTimer: ReturnType<typeof setInterval> | null = null;

const clearCountdown = () => {
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
};

const startCountdown = () => {
  clearCountdown();
  if (props.workspaceStatus !== "idle") return;
  remainingSeconds.value = AUTO_DISMISS_SECONDS;
  countdownTimer = setInterval(() => {
    if (remainingSeconds.value > 1) {
      remainingSeconds.value -= 1;
    } else {
      clearCountdown();
      // 倒计时结束：折叠为浮标，而非完全关闭
      collapsed.value = true;
    }
  }, 1000);
};

const handleMouseEnter = () => {
  clearCountdown();
};

const handleMouseLeave = () => {
  if (props.workspaceStatus === "idle" && !collapsed.value) {
    clearCountdown();
    countdownTimer = setInterval(() => {
      if (remainingSeconds.value > 1) {
        remainingSeconds.value -= 1;
      } else {
        clearCountdown();
        collapsed.value = true;
      }
    }, 1000);
  }
};

const handleStart = () => {
  clearCountdown();
  collapsed.value = false;
  emit("start");
};

const expandBanner = () => {
  collapsed.value = false;
  clearCountdown();
};

watch(
  () => props.workspaceStatus,
  (status) => {
    // 状态非 idle 时（如 starting / error），强制展开 banner
    if (status !== "idle") {
      collapsed.value = false;
      clearCountdown();
    } else {
      // 重新进入 idle（如停止后），重置折叠状态并重新开始倒计时
      collapsed.value = false;
      startCountdown();
    }
  },
);

onMounted(() => {
  if (props.workspaceStatus === "idle") {
    startCountdown();
  }
});

onUnmounted(() => {
  clearCountdown();
});

const statusTexts = computed(() => {
  if (props.backend === "k8s") {
    return {
      startingTitle: "Kubernetes 沙箱 Pod 创建中",
      startingHint: "正在创建或复用当前用户的沙箱 Pod",
      stoppingTitle: "Kubernetes 沙箱 Pod 停止中",
      stoppingHint: "正在停止并清理当前用户的沙箱 Pod",
      runningTitle: "Kubernetes 沙箱 Pod 已运行",
      runningHint: props.containerId
        ? `当前用户 Pod：${props.containerId}`
        : "Bash 将绑定到当前用户的沙箱 Pod",
      errorTitle: "Kubernetes 沙箱 Pod 启动失败",
      errorHint: props.workspaceError || "请检查集群网络、RBAC 与镜像拉取状态",
      idleTitle: "Kubernetes 沙箱 Pod 未启动",
      idleHint: "启动后，Bash 命令将绑定到当前用户的沙箱 Pod",
      startLabel: "启动我的沙箱 Pod",
      retryLabel: "重试启动",
      closeLabel: "关闭沙箱提示",
      collapsedLabel: "K8s 沙箱未启动，点击查看",
    };
  }
  return {
    startingTitle: "Docker 沙箱容器启动中",
    startingHint: "正在创建或复用当前用户的 Docker 容器",
    stoppingTitle: "Docker 沙箱容器停止中",
    stoppingHint: "正在停止并清理当前用户的 Docker 容器",
    runningTitle: "Docker 沙箱容器已运行",
    runningHint: props.containerId
      ? `当前用户容器：${props.containerId}`
      : "Bash 将绑定到当前用户的 Docker 容器",
    errorTitle: "Docker 沙箱容器启动失败",
    errorHint: props.workspaceError || "请检查 Docker daemon、镜像和权限",
    idleTitle: "Docker 沙箱容器未启动",
    idleHint: "启动后，Bash 命令将绑定到当前用户的 Docker 容器",
    startLabel: "启动我的 Docker 沙箱",
    retryLabel: "重试启动",
    closeLabel: "关闭 Docker 沙箱提示",
    collapsedLabel: "Docker 沙箱未启动，点击查看",
  };
});

const statusCopy = computed(() => {
  const t = statusTexts.value;
  switch (props.workspaceStatus) {
    case "starting":
      return {
        icon: "🟡",
        title: t.startingTitle,
        hint: t.startingHint,
        box: "border-sky-200 bg-sky-50/90 text-sky-900 dark:border-sky-500/30 dark:bg-sky-950/40 dark:text-sky-100",
        hintTone: "text-sky-700/80 dark:text-sky-200/70",
      };
    case "stopping":
      return {
        icon: "🟡",
        title: t.stoppingTitle,
        hint: t.stoppingHint,
        box: "border-amber-200 bg-amber-50/90 text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/40 dark:text-amber-100",
        hintTone: "text-amber-700/80 dark:text-amber-200/70",
      };
    case "running":
      return {
        icon: "🟢",
        title: t.runningTitle,
        hint: t.runningHint,
        box: "border-emerald-200 bg-emerald-50/90 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-950/40 dark:text-emerald-100",
        hintTone: "text-emerald-700/80 dark:text-emerald-200/70",
      };
    case "error":
      return {
        icon: "🔴",
        title: t.errorTitle,
        hint: t.errorHint,
        box: "border-rose-200 bg-rose-50/90 text-rose-900 dark:border-rose-500/30 dark:bg-rose-950/40 dark:text-rose-100",
        hintTone: "text-rose-700/80 dark:text-rose-200/70",
      };
    default:
      return {
        icon: "⚪",
        title: t.idleTitle,
        hint: t.idleHint,
        box: "border-indigo-200 bg-indigo-50/90 text-indigo-900 dark:border-indigo-500/30 dark:bg-indigo-950/40 dark:text-indigo-100",
        hintTone: "text-indigo-700/80 dark:text-indigo-200/70",
      };
  }
});
</script>

<template>
  <!-- 折叠浮标模式：fixed 定位，右上角悬浮 -->
  <Teleport to="body">
    <Transition name="sandbox-chip-fade">
      <button
        v-if="collapsed && workspaceStatus === 'idle'"
        type="button"
        data-testid="docker-workspace-banner-chip"
        class="fixed right-4 top-14 z-40 inline-flex items-center gap-1.5 rounded-full border border-gray-200/70 bg-white/80 px-2.5 py-1 text-xs text-gray-400 backdrop-blur-sm transition-all hover:border-gray-300 hover:bg-white hover:text-gray-600 dark:border-white/10 dark:bg-gray-900/70 dark:text-gray-500 dark:hover:border-white/20 dark:hover:bg-gray-800/80 dark:hover:text-gray-400"
        :title="statusTexts.collapsedLabel"
        @click="expandBanner"
      >
        <span class="h-1.5 w-1.5 rounded-full bg-gray-300 dark:bg-gray-600" />
        <span>沙箱未启动</span>
        <span class="opacity-60">点击展开</span>
      </button>
    </Transition>
  </Teleport>

  <!-- 完整 banner 模式 -->
  <Transition name="sandbox-banner-expand">
    <div
      v-if="!collapsed"
      role="status"
      data-testid="docker-workspace-banner"
      :class="`mb-2 flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2 text-xs shadow-sm ${statusCopy.box} transition-opacity duration-300`"
      @mouseenter="handleMouseEnter"
      @mouseleave="handleMouseLeave"
    >
      <span class="font-semibold">{{ statusCopy.icon }} {{ statusCopy.title }}</span>
      <span :class="statusCopy.hintTone">{{ statusCopy.hint }}</span>
      <div class="ml-auto flex items-center gap-2">
        <button
          v-if="workspaceStatus === 'idle' || workspaceStatus === 'error'"
          type="button"
          class="rounded-lg border border-indigo-200 bg-white/70 px-2.5 py-1 font-medium text-indigo-700 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-indigo-500/40 dark:bg-indigo-950/30 dark:text-indigo-200 dark:hover:bg-indigo-900/50"
          :aria-label="workspaceStatus === 'error' ? statusTexts.retryLabel : statusTexts.startLabel"
          @click="handleStart"
        >
          {{ workspaceStatus === "error" ? statusTexts.retryLabel : statusTexts.startLabel }}
        </button>
        <button
          v-else-if="workspaceStatus === 'running'"
          type="button"
          class="rounded-lg px-2 py-1 text-emerald-700/80 hover:bg-emerald-100/80 dark:text-emerald-200/80 dark:hover:bg-emerald-900/60"
          aria-label="刷新沙箱状态"
          @click="emit('refresh')"
        >
          刷新状态
        </button>
        <span v-else class="rounded-lg px-2 py-1 text-sky-700/70 dark:text-sky-200/70">
          启动中...
        </span>
        <button
          type="button"
          class="rounded-lg px-2 py-1 text-gray-500/80 hover:bg-black/5 hover:text-gray-700 dark:text-gray-300/80 dark:hover:bg-white/10 dark:hover:text-gray-100 inline-flex items-center gap-1"
          :aria-label="statusTexts.closeLabel"
          :title="workspaceStatus === 'idle' ? `${remainingSeconds}秒后折叠` : statusTexts.closeLabel"
          @click="emit('close')"
        >
          <span>×</span>
          <span v-if="workspaceStatus === 'idle'" class="text-[10px] opacity-70 font-mono">({{ remainingSeconds }}s)</span>
        </button>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.sandbox-chip-fade-enter-active,
.sandbox-chip-fade-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.sandbox-chip-fade-enter-from,
.sandbox-chip-fade-leave-to {
  opacity: 0;
  transform: scale(0.85);
}

.sandbox-banner-expand-enter-active,
.sandbox-banner-expand-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.sandbox-banner-expand-enter-from,
.sandbox-banner-expand-leave-to {
  opacity: 0;
  transform: scaleY(0.9);
  transform-origin: top;
}
</style>
