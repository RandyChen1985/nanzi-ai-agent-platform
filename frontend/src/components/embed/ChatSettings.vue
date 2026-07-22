<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import GroundingHelpPopover from '@/components/GroundingHelpPopover.vue';
import Switch from '@/components/Switch.vue';
import { useToast } from '@/composables/useToast';
import axios from '@/utils/axios';

const props = defineProps<{
  visible: boolean;
  config: any;
  allowedAgents: any[];
}>();

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void;
  (e: 'reset-session'): void;
  (e: 'fetch-agents'): void;
  (e: 'save-settings'): void;
  (e: 'set-theme', theme: string): void;
  (e: 'set-color', color: string): void;
  (e: 'mode-change', mode: string): void;
}>();

const router = useRouter();
const { showToast } = useToast();
const activeColor = ref("#1677ff");
const presetColors = [
  "#1677ff",
  "#f97316",
  "#10b981",
  "#8b5cf6",
  "#ec4899",
  "#06b6d4",
  "#eab308",
  "#ef4444",
  "#64748b",
];

const close = () => emit('update:visible', false);

const saveAndClose = () => {
  emit('save-settings');
  close();
};

const handleSetTheme = (theme: string) => {
  emit('set-theme', theme);
  saveAndClose();
};

const handleSetColor = (color: string) => {
  activeColor.value = color;
  emit('set-color', color);
  saveAndClose();
};

const handleColorInput = (e: any) => {
    handleSetColor(e.target.value);
};

const handleSetMultiAgent = (enabled: boolean) => {
    if (props.config.enableMultiAgent === enabled) {
        saveAndClose();
        return;
    }
    props.config.enableMultiAgent = enabled;
    showToast(
        enabled ? "多智能体协同已开启" : "多智能体协同已关闭",
        enabled ? "success" : "info"
    );
    saveAndClose();
};

const handleSetSqlPlan = (enabled: boolean) => {
    if (props.config.enableSqlPlan === enabled) {
        saveAndClose();
        return;
    }
    props.config.enableSqlPlan = enabled;
    showToast(
        enabled ? "SQL PLAN 中间层已开启" : "SQL PLAN 中间层已关闭",
        enabled ? "success" : "info"
    );
    saveAndClose();
};

const handleSetExpandThoughts = (enabled: boolean) => {
    if (props.config.expandThoughts === enabled) {
        saveAndClose();
        return;
    }
    props.config.expandThoughts = enabled;
    showToast(
        enabled ? "思考过程默认展开已开启" : "思考过程默认展开已关闭",
        enabled ? "success" : "info"
    );
    saveAndClose();
};

const handleSetMarkdownTheme = (theme: string) => {
    if (props.config.markdownTheme === theme) {
        saveAndClose();
        return;
    }
    props.config.markdownTheme = theme;
    localStorage.setItem("user_has_custom_theme", "true");
    localStorage.setItem("yovole_markdown_theme", theme);
    // 异步同步到后端 Redis 持久化，无需阻塞前端 UI
    void axios.put("/api/portal/portal-prefs/markdown-theme", { theme }).catch((err) => {
        console.error("Failed to sync markdown theme preference to Redis", err);
    });

    const themeNames: Record<string, string> = {
        default: "现代",
        minimal: "极简",
        academic: "学术",
        apple: "苹果",
        warm: "护眼",
        compact: "紧凑",
        bauhaus: "包豪斯",
        editorial: "日报",
        zen: "禅意",
    };
    const name = themeNames[theme] || theme;
    showToast(`排版样式已切换为: ${name}`, "success");

    saveAndClose();
};

const handleSetGrounding = (enabled: boolean) => {
    if (props.config.enableGrounding === enabled) {
      saveAndClose();
      return;
    }

    props.config.enableGrounding = enabled;
    showToast(
      enabled ? "反幻觉校验已开启" : "反幻觉校验已关闭",
      enabled ? "success" : "info",
    );
    saveAndClose();
};

const showConfirmModal = ref(false);

const confirmReset = () => {
    showConfirmModal.value = true;
};

const handleReset = () => {
    emit('reset-session');
    showConfirmModal.value = false;
    close();
};

const handleLogout = () => {
    localStorage.removeItem('user_info');
    localStorage.removeItem('token');
    localStorage.removeItem('yovole_embed_token');
    router.push('/login');
};
</script>

<template>
    <!-- Settings Modal -->
    <div
      v-if="visible"
      class="absolute inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      @click.self="close"
    >
      <div
        class="bg-white/95 dark:bg-gray-800/95 backdrop-blur-md rounded-2xl shadow-2xl w-[90vw] sm:w-[460px] max-h-[85vh] p-5 sm:p-6 border border-gray-200/80 dark:border-gray-700/80 transform transition-all scale-100 animate-fade-in-up flex flex-col"
      >
        <!-- Header -->
        <div class="flex justify-between items-center mb-5 flex-shrink-0">
          <h3 class="text-sm font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest flex items-center gap-1.5">
            <svg class="w-4 h-4 text-blue-600 dark:text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
            界面设置
          </h3>
          <button
            @click="close"
            class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
          >
            <svg
              class="w-4 h-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        <!-- Scrollable Content -->
        <div class="flex-1 overflow-y-auto pr-1 custom-scrollbar space-y-5">
          
          <!-- Group 1: 个性视觉 (Visual Styles) -->
          <div class="bg-gray-50/50 dark:bg-gray-900/20 border border-gray-100 dark:border-gray-700/40 rounded-xl p-3.5 space-y-4">
            <div class="flex items-center space-x-1.5 pb-1.5 border-b border-gray-100 dark:border-gray-700/50">
              <svg class="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
              <h4 class="text-[10px] font-black text-gray-400 uppercase tracking-widest">个性视觉 / Styles</h4>
            </div>

            <!-- Theme Mode -->
            <div>
              <label class="block text-[10px] font-black text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wider">主题模式</label>
              <div class="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-1">
                <button
                  @click="handleSetTheme('light')"
                  class="flex-1 py-1 text-xs rounded-md font-medium transition-all"
                  :class="
                    config.theme === 'light'
                      ? 'bg-white text-gray-900 shadow-sm'
                      : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                  "
                >
                  浅色
                </button>
                <button
                  @click="handleSetTheme('dark')"
                  class="flex-1 py-1 text-xs rounded-md font-medium transition-all"
                  :class="
                    config.theme === 'dark'
                      ? 'bg-gray-600 text-white shadow-sm'
                      : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                  "
                >
                  深色
                </button>
              </div>
            </div>

            <!-- Theme Color -->
            <div>
              <label class="block text-[10px] font-black text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wider">主题颜色</label>
              <div class="grid grid-cols-5 sm:grid-cols-10 gap-2">
                <button
                  v-for="color in presetColors"
                  :key="color"
                  @click="handleSetColor(color)"
                  class="w-7 h-7 rounded-full transition-all duration-300 hover:scale-110 flex items-center justify-center relative active:scale-90"
                  :class="
                    activeColor === color
                      ? 'ring-2 ring-offset-2 ring-blue-500 border-transparent scale-110 shadow-md'
                      : 'border-transparent hover:shadow'
                  "
                  :style="{ backgroundColor: color, '--tw-ring-color': color }"
                >
                  <!-- Tick mark for selected color -->
                  <svg v-if="activeColor === color" class="w-3.5 h-3.5 text-white filter drop-shadow-sm" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" /></svg>
                </button>
                <!-- Custom Color (Basic Input) -->
                <div
                  class="relative w-7 h-7 rounded-full overflow-hidden border-2 border-transparent hover:scale-110 transition-transform cursor-pointer group flex items-center justify-center bg-gradient-to-br from-red-500 via-green-500 to-blue-500 shadow-sm active:scale-90"
                >
                  <input
                    type="color"
                    class="absolute -top-2 -left-2 w-16 h-16 cursor-pointer opacity-0"
                    @change="handleColorInput"
                  />
                  <!-- Custom color indicator icon -->
                  <svg class="w-3.5 h-3.5 text-white filter drop-shadow-sm pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" /></svg>
                </div>
              </div>
            </div>

            <!-- Markdown Theme (6 styles in 3x2 Grid) -->
            <div>
              <label class="block text-[10px] font-black text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wider">AI消息排版样式</label>
              <div class="grid grid-cols-3 gap-1.5">
                <button
                  @click="handleSetMarkdownTheme('default')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'default' || !config.markdownTheme
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>✨</span>
                  <span>现代</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('minimal')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'minimal'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>🍃</span>
                  <span>极简</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('academic')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'academic'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>📖</span>
                  <span>学术</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('apple')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'apple'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>🍎</span>
                  <span>苹果</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('warm')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'warm'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>🍂</span>
                  <span>护眼</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('compact')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'compact'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>🔎</span>
                  <span>紧凑</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('bauhaus')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'bauhaus'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>📐</span>
                  <span>包豪斯</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('editorial')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'editorial'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>📰</span>
                  <span>日报</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('zen')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'zen'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>🍃</span>
                  <span>禅意</span>
                </button>
              </div>
            </div>
          </div>

          <!-- Group 2: 智能特性 (Intelligent Features with Switches) -->
          <div class="bg-gray-50/50 dark:bg-gray-900/20 border border-gray-100 dark:border-gray-700/40 rounded-xl p-3.5 space-y-4">
            <div class="flex items-center space-x-1.5 pb-1.5 border-b border-gray-100 dark:border-gray-700/50">
              <svg class="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
              <h4 class="text-[10px] font-black text-gray-400 uppercase tracking-widest">智能特性 / Features</h4>
            </div>

            <!-- Switch Row 1: Multi-Agent Collaboration -->
            <div class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20H7v-2C7 15 5 13 3 13m4 7v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-700 dark:text-gray-200">多智能体协同</h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">开启后支持跨领域任务并行执行</p>
                </div>
              </div>
              <Switch :modelValue="config.enableMultiAgent" @update:modelValue="handleSetMultiAgent" class="scale-[0.8] origin-right" />
            </div>

            <!-- Switch Row 2: SQL Plan -->
            <div class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-700 dark:text-gray-200">SQL PLAN 中间层</h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">高风险查数先校验执行计划</p>
                </div>
              </div>
              <Switch :modelValue="config.enableSqlPlan" @update:modelValue="handleSetSqlPlan" class="scale-[0.8] origin-right" />
            </div>

            <!-- Switch Row 3: Thoughts Expand -->
            <div class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-700 dark:text-gray-200">思考过程</h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">智能体推理思维链默认展开展示</p>
                </div>
              </div>
              <Switch :modelValue="config.expandThoughts" @update:modelValue="handleSetExpandThoughts" class="scale-[0.8] origin-right" />
            </div>

            <!-- Switch Row 4: Grounding Toggle -->
            <div class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-750 dark:text-gray-250 flex items-center gap-1.5">
                    反幻觉校验
                    <GroundingHelpPopover />
                  </h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">开启后校验回答的事实来源并提示风险</p>
                </div>
              </div>
              <Switch :modelValue="config.enableGrounding" @update:modelValue="handleSetGrounding" class="scale-[0.8] origin-right" />
            </div>

          </div>

          <!-- Bottom Action Buttons -->
          <div class="mt-6 pt-4 border-t border-gray-150 dark:border-gray-700/50 space-y-2">
            <button
              @click="confirmReset"
              class="w-full py-2 text-xs font-bold text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 active:scale-98 rounded-xl transition-all flex items-center justify-center space-x-2 border border-blue-100 dark:border-blue-900/30 shadow-sm"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" /></svg>
              <span>开启新会话</span>
            </button>
            <!-- Mobile Only Logout Button -->
            <button
              @click="handleLogout"
              class="sm:hidden w-full py-2 text-xs font-bold text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 active:scale-98 rounded-xl transition-all flex items-center justify-center space-x-2 border border-red-100 dark:border-red-900/30"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" /></svg>
              <span>退出登录</span>
            </button>
          </div>

        </div>
      </div>
    </div>

    <!-- Confirm Modal -->
    <div v-if="showConfirmModal" class="absolute inset-0 z-[60] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
        <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-64 p-4 border border-gray-200 dark:border-gray-700 animate-fade-in-up">
            <h3 class="text-sm font-black text-gray-850 dark:text-gray-150 mb-2 flex items-center gap-1.5">
                <svg class="w-4 h-4 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
                确认开启新会话？
            </h3>
            <p class="text-[10.5px] text-gray-500 dark:text-gray-400 leading-normal mb-4">当前页面将被清空以开始新对话。旧的对话记录仍可在“历史”中查阅。</p>
            <div class="flex space-x-2">
                <button @click="showConfirmModal = false" class="flex-1 py-1.5 text-xs font-bold text-gray-500 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600 rounded-xl transition-colors">取消</button>
                <button @click="handleReset" class="flex-1 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-xl transition-colors">确认开启</button>
            </div>
        </div>
    </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 4px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 2px;
}
.dark .custom-scrollbar::-webkit-scrollbar-thumb {
  background: #4b5563;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}
</style>
