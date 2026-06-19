<script setup lang="ts">
interface Config {
  routingMode: string;
  expertAgentId: string;
}

defineProps<{
  config: Config;
  currentExpertAgent: any;
  showAutoRoutingHint: boolean;
  showMultiAgentHint?: boolean;
  multiAgentHintMessage?: string;
  isMobile?: boolean;
}>();

const emit = defineEmits(['switch-to-auto']);
</script>

<template>
  <div class="z-20 w-full">
    <transition 
      mode="out-in"
      enter-active-class="transition-all duration-500 ease-out"
      enter-from-class="opacity-0 -translate-y-4"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition-all duration-300 ease-in"
      leave-to-class="opacity-0 -translate-y-2"
    >
        <!-- Auto Mode Hint (Temporary bar) -->
        <div v-if="showAutoRoutingHint" class="w-full bg-green-50/80 dark:bg-green-900/20 backdrop-blur-md border-b border-green-100 dark:border-green-800 px-4 py-2 flex items-center justify-center shadow-sm">
            <div class="flex items-center space-x-2">
                <svg class="w-3.5 h-3.5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                <span class="text-[10px] font-bold text-green-700 dark:text-green-300 uppercase tracking-widest">已切换为自动路由模式</span>
            </div>
        </div>

        <!-- Multi-Agent Mode Hint -->
        <div v-else-if="showMultiAgentHint" class="w-full bg-blue-50/80 dark:bg-blue-900/20 backdrop-blur-md border-b border-blue-100 dark:border-blue-800 px-4 py-2 flex items-center justify-center shadow-sm">
            <div class="flex items-center space-x-2">
                <svg class="w-3.5 h-3.5 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                <span class="text-[10px] font-bold text-blue-700 dark:text-blue-300 uppercase tracking-widest">{{ multiAgentHintMessage }}</span>
            </div>
        </div>

        <!-- Expert Mode Header Bar (desktop only; mobile uses main header) -->
        <div v-else-if="!isMobile && config.routingMode === 'expert' && currentExpertAgent"
             class="w-full bg-blue-50/90 dark:bg-blue-900/20 backdrop-blur-md border-b border-blue-100 dark:border-blue-800 px-4 py-2 flex items-center justify-center relative shadow-sm"
        >
            <!-- 1. Avatar (Absolute Left) -->
            <div class="absolute left-4 flex-shrink-0">
                <div class="relative flex-shrink-0">
                    <div class="w-7 h-7 rounded-full bg-gradient-to-tr from-blue-500 to-violet-600 p-[1px] shadow-sm">
                        <div class="w-full h-full rounded-full bg-white dark:bg-gray-800 overflow-hidden">
                             <img v-if="currentExpertAgent.avatar_url" :src="currentExpertAgent.avatar_url" class="w-full h-full object-cover" />
                             <div v-else class="w-full h-full flex items-center justify-center text-[10px] font-black text-blue-600 dark:text-blue-400">
                                {{ Array.from(currentExpertAgent.display_name || currentExpertAgent.name || 'E')[0] }}
                             </div>
                        </div>
                    </div>
                    <div class="absolute -bottom-0.5 -right-0.5 bg-blue-600 text-white rounded-full p-[1.5px] border border-white dark:border-gray-800 z-10">
                        <svg class="w-1.5 h-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg>
                    </div>
                </div>
            </div>

            <!-- 2. Info (Strictly Centered) -->
            <div class="flex flex-col items-center min-w-0">
                <div class="flex items-center space-x-1.5">
                    <span class="text-[9px] text-blue-500 dark:text-blue-400 font-black uppercase tracking-widest leading-none">Expert Mode</span>
                    <span class="w-1 h-1 rounded-full bg-green-400 animate-pulse"></span>
                </div>
                <span class="text-xs font-bold text-gray-800 dark:text-gray-100 truncate">
                    当前专家：{{ currentExpertAgent.display_name || currentExpertAgent.name }}
                </span>
            </div>

            <!-- 3. Action (Absolute Right) -->
            <button 
                @click="emit('switch-to-auto')"
                class="absolute right-4 px-2 py-1 bg-gray-50 dark:bg-gray-700 hover:bg-red-500 hover:text-white border border-gray-100 dark:border-gray-600 rounded text-[10px] font-bold text-gray-600 dark:text-gray-300 transition-all flex items-center space-x-1"
            >
                <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                <span>退出</span>
            </button>
        </div>
    </transition>
  </div>
</template>

<style scoped>
.slide-fade-enter-active,
.slide-fade-leave-active {
  transition: all 0.2s ease-out;
}
.slide-fade-enter-from {
  opacity: 0;
  transform: translateY(5px);
}
.slide-fade-leave-to {
  opacity: 0;
  transform: translateY(-5px);
}
</style>