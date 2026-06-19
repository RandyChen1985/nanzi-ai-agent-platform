<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';

const props = defineProps<{
  visible: boolean;
  config: any;
  availableModels: any[];
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
    props.config.enableMultiAgent = enabled;
    saveAndClose();
};

const handleSetSqlPlan = (enabled: boolean) => {
    props.config.enableSqlPlan = enabled;
    saveAndClose();
};

const handleModelChange = () => {
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
      class="absolute inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm"
      @click.self="close"
    >
      <div
        class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-72 p-4 border border-gray-200 dark:border-gray-700 transform transition-all scale-100 animate-fade-in-up"
      >
        <div class="flex justify-between items-center mb-4">
          <h3 class="text-sm font-bold text-gray-800 dark:text-gray-200">
            界面设置
          </h3>
          <button
            @click="close"
            class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
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

        <!-- Theme Toggle -->
        <div class="mb-4">
          <label
            class="block text-xs font-semibold text-gray-500 uppercase mb-2"
            >主题模式</label
          >
          <div class="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-1">
            <button
              @click="handleSetTheme('light')"
              class="flex-1 py-1.5 text-xs rounded-md font-medium transition-all"
              :class="
                config.theme === 'light'
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-500 dark:text-gray-400 hover:text-gray-900'
              "
            >
              浅色
            </button>
            <button
              @click="handleSetTheme('dark')"
              class="flex-1 py-1.5 text-xs rounded-md font-medium transition-all"
              :class="
                config.theme === 'dark'
                  ? 'bg-gray-600 text-white shadow-sm'
                  : 'text-gray-500 dark:text-gray-400 hover:text-gray-900'
              "
            >
              深色
            </button>
          </div>
        </div>

        <!-- Color Picker -->
        <div class="mb-4">
          <label
            class="block text-xs font-semibold text-gray-500 uppercase mb-2"
            >主题颜色</label
          >
          <div class="grid grid-cols-5 gap-2">
            <button
              v-for="color in presetColors"
              :key="color"
              @click="handleSetColor(color)"
              class="w-8 h-8 rounded-full border-2 transition-transform hover:scale-110"
              :class="
                activeColor === color
                  ? 'border-gray-400 dark:border-gray-200'
                  : 'border-transparent'
              "
              :style="{ backgroundColor: color }"
            ></button>
            <!-- Custom Color (Basic Input) -->
            <div
              class="relative w-8 h-8 rounded-full overflow-hidden border-2 border-transparent hover:scale-110 transition-transform cursor-pointer group"
            >
              <input
                type="color"
                class="absolute -top-2 -left-2 w-16 h-16 cursor-pointer opacity-0"
                @change="handleColorInput"
              />
              <div
                class="w-full h-full bg-gradient-to-br from-red-500 via-green-500 to-blue-500"
              ></div>
            </div>
          </div>
        </div>

        <!-- Multi-Agent Collaboration Toggle -->
        <div class="mb-4">
          <label class="block text-xs font-semibold text-gray-500 uppercase mb-2">多智能体协同</label>
          <div class="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-1">
            <button
              @click="handleSetMultiAgent(true)"
              class="flex-1 py-1 text-[10px] rounded-md font-medium transition-all"
              :class="
                config.enableMultiAgent
                  ? 'bg-white text-primary shadow-sm'
                  : 'text-gray-500 dark:text-gray-400'
              "
            >
              开启
            </button>
            <button
              @click="handleSetMultiAgent(false)"
              class="flex-1 py-1 text-[10px] rounded-md font-medium transition-all"
              :class="
                !config.enableMultiAgent
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-500 dark:text-gray-400'
              "
            >
              关闭
            </button>
          </div>
          <p class="mt-1 text-[10px] text-gray-400">开启后支持跨领域任务并行执行</p>
        </div>

        <!-- SQL Plan Toggle -->
        <div class="mb-4">
          <label class="block text-xs font-semibold text-gray-500 uppercase mb-2">SQL Plan 中间层</label>
          <div class="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-1">
            <button
              @click="handleSetSqlPlan(true)"
              class="flex-1 py-1 text-[10px] rounded-md font-medium transition-all"
              :class="
                config.enableSqlPlan
                  ? 'bg-white text-primary shadow-sm'
                  : 'text-gray-500 dark:text-gray-400'
              "
            >
              开启
            </button>
            <button
              @click="handleSetSqlPlan(false)"
              class="flex-1 py-1 text-[10px] rounded-md font-medium transition-all"
              :class="
                !config.enableSqlPlan
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-500 dark:text-gray-400'
              "
            >
              关闭
            </button>
          </div>
          <p class="mt-1 text-[10px] text-gray-400">高风险查数先校验计划</p>
        </div>

        <!-- Model Selector -->
        <div>
          <label
            class="block text-xs font-semibold text-gray-500 uppercase mb-2"
            >模型选择</label
          >
          <select
            v-model="config.overrideModel"
            @change="handleModelChange"
            class="w-full text-xs bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg px-2 py-1.5 focus:ring-1 focus:ring-primary outline-none text-gray-700 dark:text-gray-300 transition-all"
          >
            <option value="">默认配置</option>
            <option
              v-for="model in availableModels"
              :key="model.id"
              :value="model.model_id"
            >
              {{ model.name }} ({{ model.model_id }})
            </option>
          </select>
          <p class="mt-1 text-[10px] text-gray-400">
            选择后将覆盖智能体默认模型配置
          </p>
        </div>

        <!-- Action Buttons -->
        <div class="mt-6 pt-4 border-t border-gray-100 dark:border-gray-700 space-y-2">
          <button
            @click="confirmReset"
            class="w-full py-2 text-xs font-bold text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg transition-colors flex items-center justify-center space-x-2 border border-blue-100 dark:border-blue-900/30"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" /></svg>
            <span>开启新会话</span>
          </button>
          <!-- Mobile Only Logout Button -->
          <button
            @click="handleLogout"
            class="sm:hidden w-full py-2 text-xs font-bold text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors flex items-center justify-center space-x-2 border border-red-100 dark:border-red-900/30"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" /></svg>
            <span>退出登录</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Confirm Modal -->
    <div v-if="showConfirmModal" class="absolute inset-0 z-[60] flex items-center justify-center bg-black/30 backdrop-blur-sm p-4">
        <div class="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-64 p-4 border border-gray-200 dark:border-gray-700 animate-fade-in-up">
            <h3 class="text-sm font-bold text-gray-800 dark:text-gray-200 mb-2">确认开启新会话？</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">当前页面将被清空以开始新对话。旧的对话记录仍可在“历史”中查阅。</p>
            <div class="flex space-x-2">
                <button @click="showConfirmModal = false" class="flex-1 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors">取消</button>
                <button @click="handleReset" class="flex-1 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors">确认开启</button>
            </div>
        </div>
    </div>
</template>
