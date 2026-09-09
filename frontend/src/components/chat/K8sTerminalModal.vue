<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from "vue";
import axios from "axios";
import {
  CommandLineIcon,
  XMarkIcon,
  ArrowPathIcon,
  TrashIcon,
  ClipboardDocumentIcon,
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
} from "@heroicons/vue/24/outline";

interface CommandRecord {
  id: string;
  kind?: "command" | "welcome";
  command: string;
  workdir: string;
  stdout: string;
  stderr: string;
  output: string;
  exitCode: number | null;
  durationMs: number;
  timestamp: string;
  loading?: boolean;
}

const props = defineProps<{
  show: boolean;
  podName?: string | null;
  namespace?: string | null;
  conversationId?: string;
  authToken?: string | null;
}>();

const emit = defineEmits<{
  (event: "close"): void;
}>();

const isMaximized = ref(true);
const inputCommand = ref("");
const isExecuting = ref(false);
const commandHistory = ref<string[]>([]);
const historyIndex = ref(-1);
const records = ref<CommandRecord[]>([]);
const terminalScrollRef = ref<HTMLDivElement | null>(null);
const commandInputRef = ref<HTMLInputElement | null>(null);
const currentWorkdir = ref("/workspace");
const copiedIndex = ref<number | null>(null);

const QUICK_COMMANDS = [
  { label: "查看目录 (ls -la)", cmd: "ls -la" },
  { label: "当前路径 (pwd)", cmd: "pwd" },
  { label: "Python 环境", cmd: "python3 --version || python --version" },
  { label: "系统信息", cmd: "cat /etc/os-release" },
  { label: "磁盘占用 (df -h)", cmd: "df -h /workspace" },
  { label: "内存概览 (/proc/meminfo)", cmd: "cat /proc/meminfo" },
];

const shortPodName = computed(() => props.podName || "nanzi-pod");

const createWelcomeRecord = (): CommandRecord => {
  const workdir = currentWorkdir.value || "/workspace";
  const output = `Kubernetes 沙箱终端\n已连接 Pod：${props.podName || "default"}\n命名空间：${props.namespace || "agent-sandboxes"}\n工作目录：${workdir}`;
  return {
    id: `welcome_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    kind: "welcome",
    command: "nanzi-welcome",
    workdir,
    stdout: "",
    stderr: "",
    output,
    exitCode: 0,
    durationMs: 0,
    timestamp: new Date().toLocaleTimeString(),
  };
};

const scrollToBottom = async () => {
  await nextTick();
  if (terminalScrollRef.value) {
    terminalScrollRef.value.scrollTop = terminalScrollRef.value.scrollHeight;
  }
};

const focusInput = async () => {
  await nextTick();
  commandInputRef.value?.focus();
};

const clearTerminal = () => {
  records.value = [];
  focusInput();
};

const copyOutput = async (text: string, index: number) => {
  try {
    await navigator.clipboard.writeText(text);
    copiedIndex.value = index;
    setTimeout(() => {
      if (copiedIndex.value === index) {
        copiedIndex.value = null;
      }
    }, 1500);
  } catch (err) {
    console.error("复制失败", err);
  }
};

const runCommand = async (cmdToRun?: string) => {
  const cmd = (cmdToRun !== undefined ? cmdToRun : inputCommand.value).trim();
  if (!cmd) return;

  if (cmd === "clear" || cmd === "cls") {
    inputCommand.value = "";
    clearTerminal();
    return;
  }

  if (!commandHistory.value.length || commandHistory.value[commandHistory.value.length - 1] !== cmd) {
    commandHistory.value.push(cmd);
  }
  historyIndex.value = -1;

  const recordId = `rec_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
  const nowStr = new Date().toLocaleTimeString();

  const newRecord: CommandRecord = {
    id: recordId,
    command: cmd,
    workdir: currentWorkdir.value,
    stdout: "",
    stderr: "",
    output: "",
    exitCode: null,
    durationMs: 0,
    timestamp: nowStr,
    loading: true,
  };

  records.value.push(newRecord);
  inputCommand.value = "";
  isExecuting.value = true;
  await scrollToBottom();

  const headers: Record<string, string> = {};
  if (props.authToken) {
    headers["Authorization"] = `Bearer ${props.authToken}`;
  }

  try {
    const resp = await axios.post(
      "/api/v1/sandbox/k8s/workspace/exec",
      {
        conversation_id: props.conversationId,
        command: cmd,
        workdir: currentWorkdir.value,
      },
      { headers },
    );
    const data = resp.data?.data ?? resp.data;
    newRecord.stdout = data.stdout || "";
    newRecord.stderr = data.stderr || "";
    newRecord.output = data.output || (data.stdout ? data.stdout : data.stderr);
    newRecord.exitCode = typeof data.exit_code === "number" ? data.exit_code : null;
    newRecord.durationMs = data.duration_ms || 0;
    if (data.workdir) {
      currentWorkdir.value = data.workdir;
    }
  } catch (err: any) {
    const detail = err?.response?.data?.detail;
    const msg = typeof detail === "string"
      ? detail
      : String(detail?.message || err?.message || "命令执行异常");
    newRecord.stderr = msg;
    newRecord.output = msg;
    newRecord.exitCode = -1;
  } finally {
    newRecord.loading = false;
    isExecuting.value = false;
    await scrollToBottom();
    focusInput();
  }
};

const handleKeyDown = (e: KeyboardEvent) => {
  if (e.key === "ArrowUp") {
    e.preventDefault();
    if (!commandHistory.value.length) return;
    if (historyIndex.value === -1) {
      historyIndex.value = commandHistory.value.length - 1;
    } else if (historyIndex.value > 0) {
      historyIndex.value--;
    }
    inputCommand.value = commandHistory.value[historyIndex.value] || "";
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    if (historyIndex.value === -1) return;
    if (historyIndex.value < commandHistory.value.length - 1) {
      historyIndex.value++;
      inputCommand.value = commandHistory.value[historyIndex.value] || "";
    } else {
      historyIndex.value = -1;
      inputCommand.value = "";
    }
  }
};

const handleKeyGlobal = (e: KeyboardEvent) => {
  if (props.show && e.key === "Escape") {
    emit("close");
  }
};

watch(
  () => props.show,
  (val) => {
    if (val) {
      records.value = [createWelcomeRecord()];
      focusInput();
      void scrollToBottom();
    }
  },
  { immediate: true },
);

onMounted(() => {
  window.addEventListener("keydown", handleKeyGlobal);
});

onUnmounted(() => {
  window.removeEventListener("keydown", handleKeyGlobal);
});
</script>

<template>
  <div
    v-if="show"
    class="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-black/60 backdrop-blur-sm animate-fade-in"
    @click.self="emit('close')"
  >
    <div
      class="flex flex-col w-full bg-[#1e1e24] text-gray-200 rounded-xl shadow-2xl border border-gray-700/80 overflow-hidden transition-all duration-200"
      :class="isMaximized ? 'h-full max-h-full max-w-full rounded-none' : 'max-w-4xl h-[85vh] max-h-[760px]'"
    >
      <!-- 终端顶栏 Header -->
      <div class="flex items-center justify-between px-4 py-2.5 bg-[#18181c] border-b border-gray-800 select-none">
        <div class="flex items-center gap-3">
          <div class="flex items-center gap-1.5">
            <button
              type="button"
              class="w-3 h-3 rounded-full bg-rose-500 hover:bg-rose-600 transition-colors flex items-center justify-center text-[8px] text-rose-950 font-bold opacity-80 hover:opacity-100"
              title="关闭终端"
              @click="emit('close')"
            >
              ×
            </button>
            <button
              type="button"
              class="w-3 h-3 rounded-full bg-amber-500 hover:bg-amber-600 transition-colors flex items-center justify-center text-[8px] text-amber-950 font-bold opacity-80 hover:opacity-100"
              title="清空终端"
              @click="clearTerminal"
            >
              -
            </button>
            <button
              type="button"
              class="w-3 h-3 rounded-full bg-emerald-500 hover:bg-emerald-600 transition-colors flex items-center justify-center text-[8px] text-emerald-950 font-bold opacity-80 hover:opacity-100"
              title="全屏切换"
              @click="isMaximized = !isMaximized"
            >
              +
            </button>
          </div>

          <div class="flex items-center gap-2 text-xs font-mono text-gray-300">
            <CommandLineIcon class="w-4 h-4 text-emerald-400" />
            <span class="font-semibold text-gray-100">Kubernetes 沙箱 Pod 终端</span>
            <span class="px-1.5 py-0.5 rounded bg-emerald-950/70 border border-emerald-700/50 text-[10px] text-emerald-400 font-mono">
              {{ shortPodName }}
            </span>
            <span class="text-gray-500 text-[11px] hidden sm:inline font-mono">
              {{ currentWorkdir }}
            </span>
          </div>
        </div>

        <div class="flex items-center gap-2">
          <button
            type="button"
            class="p-1 rounded text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors"
            title="清屏"
            @click="clearTerminal"
          >
            <TrashIcon class="w-4 h-4" />
          </button>
          <button
            type="button"
            class="p-1 rounded text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors"
            :title="isMaximized ? '还原窗口' : '最大化窗口'"
            @click="isMaximized = !isMaximized"
          >
            <ArrowsPointingInIcon v-if="isMaximized" class="w-4 h-4" />
            <ArrowsPointingOutIcon v-else class="w-4 h-4" />
          </button>
          <button
            type="button"
            class="p-1 rounded text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors"
            title="关闭 (Esc)"
            @click="emit('close')"
          >
            <XMarkIcon class="w-5 h-5" />
          </button>
        </div>
      </div>

      <!-- 快捷命令栏 -->
      <div class="flex items-center gap-1.5 px-3 py-1.5 bg-[#141417] border-b border-gray-800/80 overflow-x-auto text-[11px] no-scrollbar">
        <span class="text-gray-500 shrink-0 text-[10px] uppercase font-mono mr-1">常用命令:</span>
        <button
          v-for="item in QUICK_COMMANDS"
          :key="item.cmd"
          type="button"
          class="shrink-0 px-2 py-0.5 rounded bg-gray-800/80 hover:bg-indigo-900/40 hover:text-indigo-200 border border-gray-700/60 hover:border-indigo-600/50 text-gray-300 transition-colors font-mono"
          :disabled="isExecuting"
          @click="runCommand(item.cmd)"
        >
          {{ item.label }}
        </button>
      </div>

      <!-- 终端输出区 -->
      <div
        ref="terminalScrollRef"
        class="flex-1 p-4 overflow-y-auto font-mono text-[12px] leading-relaxed select-text space-y-4 bg-[#1e1e24]"
        @click="focusInput"
      >
        <div v-for="(rec, idx) in records" :key="rec.id" class="space-y-1.5 group">
          <div
            v-if="rec.kind === 'welcome'"
            class="rounded-lg border border-slate-700/80 bg-slate-900/70 p-3 text-[11px] leading-relaxed"
          >
            <div class="mb-2 flex items-center gap-2 text-emerald-300">
              <CommandLineIcon class="h-4 w-4" />
              <span class="font-semibold">Kubernetes 沙箱终端</span>
              <span class="text-slate-500">· 环境说明</span>
            </div>
            <div class="grid gap-1 sm:grid-cols-2">
              <div><span class="text-emerald-300">● 已进入</span><span class="text-slate-400"> Pod：</span><span class="text-slate-100">{{ props.podName || '—' }}</span></div>
              <div><span class="text-sky-300">命名空间</span><span class="text-slate-400">：</span><span class="text-sky-200">{{ props.namespace || 'agent-sandboxes' }}</span></div>
              <div><span class="text-sky-300">工作目录</span><span class="text-slate-400">：</span><span class="text-sky-200">{{ rec.workdir || '/workspace' }}</span></div>
            </div>
            <div class="mt-3 rounded-md border border-rose-500/30 bg-rose-950/20 p-2 text-rose-200">
              <div class="font-semibold">⚠ 安全提示</div>
              <div class="text-rose-200/80">命令在当前沙箱 Pod 内执行；删除文件、安装依赖、联网或启动后台进程前，请先确认路径与影响范围。Pod 停止/重建后容器内非挂载路径改动不保留。</div>
            </div>
          </div>
          <template v-else>
            <div class="flex items-center justify-between text-gray-400 text-[11px] pt-1">
              <div class="flex items-center gap-2">
                <span class="text-emerald-400 font-semibold">root@nanzi-k8s</span>
                <span class="text-gray-500">:</span>
                <span class="text-sky-400 font-medium">{{ rec.workdir }}</span>
                <span class="text-gray-400">$</span>
                <span class="text-gray-100 font-mono font-medium">{{ rec.command }}</span>
              </div>
              <div class="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <span v-if="rec.durationMs" class="text-[10px] text-gray-500">{{ rec.durationMs }}ms</span>
                <span
                  v-if="!rec.loading && rec.exitCode !== 0 && rec.exitCode !== null"
                  class="px-1 py-0.2 rounded bg-rose-950 border border-rose-800 text-rose-400 text-[9px]"
                >
                  code {{ rec.exitCode }}
                </span>
                <button
                  v-if="rec.output"
                  type="button"
                  class="p-0.5 rounded text-gray-400 hover:text-gray-200 hover:bg-gray-700/60"
                  :title="copiedIndex === idx ? '已复制' : '复制命令输出'"
                  @click.stop="copyOutput(rec.output, idx)"
                >
                  <ClipboardDocumentIcon class="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            <div v-if="rec.loading" class="flex items-center gap-2 text-indigo-400 py-1 text-xs">
              <ArrowPathIcon class="w-3.5 h-3.5 animate-spin" />
              <span>执行中...</span>
            </div>

            <div
              v-else-if="rec.output"
              class="pl-3 border-l-2 text-gray-300 whitespace-pre-wrap break-all font-mono py-0.5"
              :class="rec.exitCode !== 0 && rec.exitCode !== null ? 'border-rose-500/70 text-rose-300/90' : 'border-gray-700 text-gray-300'"
            >
              {{ rec.output }}
            </div>
          </template>
        </div>

        <div class="flex items-center gap-2 pt-2 text-[12px] font-mono">
          <span class="text-emerald-400 font-semibold shrink-0">root@nanzi-k8s</span>
          <span class="text-gray-500 shrink-0">:</span>
          <span class="text-sky-400 font-medium shrink-0">{{ currentWorkdir }}</span>
          <span class="text-gray-400 shrink-0">$</span>
          <input
            ref="commandInputRef"
            v-model="inputCommand"
            type="text"
            class="flex-1 bg-transparent text-gray-100 placeholder-gray-600 focus:outline-none font-mono caret-emerald-400 text-[12px] border-none p-0 focus:ring-0"
            placeholder="输入 shell 命令并回车 (按 ↑/↓ 切换历史命令)..."
            :disabled="isExecuting"
            @keydown.enter.prevent="runCommand()"
            @keydown="handleKeyDown"
          />
        </div>
      </div>

      <div class="flex items-center justify-between px-4 py-2 bg-[#18181c] border-t border-gray-800/90 text-[11px] text-gray-400 font-mono">
        <div class="flex items-center gap-3">
          <span class="inline-flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            <span>已连接 Kubernetes 沙箱 Pod</span>
          </span>
          <span class="text-gray-600">|</span>
          <span class="text-gray-400">会话隔离沙箱</span>
        </div>
        <div class="text-gray-500 text-[10px]">
          按 <kbd class="px-1 py-0.5 rounded bg-gray-800 border border-gray-700 text-gray-300 font-mono">Enter</kbd> 发送执行，<kbd class="px-1 py-0.5 rounded bg-gray-800 border border-gray-700 text-gray-300 font-mono">Esc</kbd> 退出
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.no-scrollbar::-webkit-scrollbar {
  display: none;
}
.no-scrollbar {
  -ms-overflow-style: none;
  scrollbar-width: none;
}
@keyframes fadeIn {
  from {
    opacity: 0;
    transform: scale(0.98);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}
.animate-fade-in {
  animation: fadeIn 0.15s ease-out forwards;
}
</style>
