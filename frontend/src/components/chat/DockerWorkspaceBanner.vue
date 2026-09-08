<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from "vue";

export type DockerWorkspaceStatus = "idle" | "starting" | "stopping" | "running" | "error";

const props = defineProps<{
  workspaceStatus: DockerWorkspaceStatus;
  workspaceError?: string;
  containerId?: string | null;
}>();

const emit = defineEmits<{
  (event: "start"): void;
  (event: "refresh"): void;
  (event: "close"): void;
}>();

const AUTO_DISMISS_SECONDS = 3;
const remainingSeconds = ref(AUTO_DISMISS_SECONDS);
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
      emit("close");
    }
  }, 1000);
};

const handleMouseEnter = () => {
  clearCountdown();
};

const handleMouseLeave = () => {
  if (props.workspaceStatus === "idle") {
    clearCountdown();
    countdownTimer = setInterval(() => {
      if (remainingSeconds.value > 1) {
        remainingSeconds.value -= 1;
      } else {
        clearCountdown();
        emit("close");
      }
    }, 1000);
  }
};

const handleStart = () => {
  clearCountdown();
  emit("start");
};

watch(
  () => props.workspaceStatus,
  (status) => {
    if (status === "idle") {
      startCountdown();
    } else {
      clearCountdown();
    }
  },
  { immediate: true },
);

onMounted(() => {
  if (props.workspaceStatus === "idle") {
    startCountdown();
  }
});

onUnmounted(() => {
  clearCountdown();
});

const statusCopy = computed(() => {
  switch (props.workspaceStatus) {
    case "starting":
      return {
        icon: "🟡",
        title: "Docker 沙箱容器启动中",
        hint: "正在创建或复用当前用户的 Docker 容器",
        box: "border-sky-200 bg-sky-50/90 text-sky-900 dark:border-sky-500/30 dark:bg-sky-950/40 dark:text-sky-100",
        hintTone: "text-sky-700/80 dark:text-sky-200/70",
      };
    case "stopping":
      return {
        icon: "🟡",
        title: "Docker 沙箱容器停止中",
        hint: "正在停止并清理当前用户的 Docker 容器",
        box: "border-amber-200 bg-amber-50/90 text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/40 dark:text-amber-100",
        hintTone: "text-amber-700/80 dark:text-amber-200/70",
      };
    case "running":
      return {
        icon: "🟢",
        title: "Docker 沙箱容器已运行",
        hint: props.containerId
          ? `当前用户容器：${props.containerId}`
          : "Bash 将绑定到当前用户的 Docker 容器",
        box: "border-emerald-200 bg-emerald-50/90 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-950/40 dark:text-emerald-100",
        hintTone: "text-emerald-700/80 dark:text-emerald-200/70",
      };
    case "error":
      return {
        icon: "🔴",
        title: "Docker 沙箱容器启动失败",
        hint: props.workspaceError || "请检查 Docker daemon、镜像和权限",
        box: "border-rose-200 bg-rose-50/90 text-rose-900 dark:border-rose-500/30 dark:bg-rose-950/40 dark:text-rose-100",
        hintTone: "text-rose-700/80 dark:text-rose-200/70",
      };
    default:
      return {
        icon: "⚪",
        title: "Docker 沙箱容器未启动",
        hint: "启动后，Bash 命令将绑定到当前用户的 Docker 容器",
        box: "border-indigo-200 bg-indigo-50/90 text-indigo-900 dark:border-indigo-500/30 dark:bg-indigo-950/40 dark:text-indigo-100",
        hintTone: "text-indigo-700/80 dark:text-indigo-200/70",
      };
  }
});
</script>

<template>
  <div
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
        :aria-label="workspaceStatus === 'error' ? '重试启动 Docker 沙箱' : '启动我的 Docker 沙箱'"
        @click="handleStart"
      >
        {{ workspaceStatus === "error" ? "重试启动" : "启动我的 Docker 沙箱" }}
      </button>
      <button
        v-else-if="workspaceStatus === 'running'"
        type="button"
        class="rounded-lg px-2 py-1 text-emerald-700/80 hover:bg-emerald-100/80 dark:text-emerald-200/80 dark:hover:bg-emerald-900/60"
        aria-label="刷新 Docker 沙箱状态"
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
        aria-label="关闭 Docker 沙箱提示"
        :title="workspaceStatus === 'idle' ? `${remainingSeconds}秒后自动关闭` : '关闭提示'"
        @click="emit('close')"
      >
        <span>×</span>
        <span v-if="workspaceStatus === 'idle'" class="text-[10px] opacity-70 font-mono">({{ remainingSeconds }}s)</span>
      </button>
    </div>
  </div>
</template>
