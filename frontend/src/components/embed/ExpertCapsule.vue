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
  showExpertSwitchHint?: boolean;
  expertSwitchHintName?: string;
  isMobile?: boolean;
}>();

defineEmits(['switch-to-auto']);
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
        <div v-if="showAutoRoutingHint" class="w-full bg-green-50 dark:bg-green-900/20 border-b border-green-100 dark:border-green-800 px-4 py-2 flex items-center justify-center shadow-sm">
            <div class="flex items-center space-x-2">
                <svg class="w-3.5 h-3.5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                <span class="text-[10px] font-bold text-green-700 dark:text-green-300 tracking-wide">已切换为自动路由模式</span>
            </div>
        </div>

        <!-- Expert Switch Hint -->
        <div v-else-if="showExpertSwitchHint" class="w-full bg-blue-50 dark:bg-blue-900/20 border-b border-blue-100 dark:border-blue-800 px-4 py-2 flex items-center justify-center shadow-sm">
            <div class="flex items-center space-x-2 min-w-0">
                <svg class="w-3.5 h-3.5 text-blue-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" /></svg>
                <span class="text-[10px] font-bold text-blue-700 dark:text-blue-300 tracking-wide truncate">
                  已切换至专家：{{ expertSwitchHintName || '专家模式' }}
                </span>
            </div>
        </div>

        <!-- Multi-Agent Mode Hint -->
        <div v-else-if="showMultiAgentHint" class="w-full bg-blue-50 dark:bg-blue-900/20 border-b border-blue-100 dark:border-blue-800 px-4 py-2 flex items-center justify-center shadow-sm">
            <div class="flex items-center space-x-2">
                <svg class="w-3.5 h-3.5 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                <span class="text-[10px] font-bold text-blue-700 dark:text-blue-300 tracking-wide">{{ multiAgentHintMessage }}</span>
            </div>
        </div>
    </transition>
  </div>
</template>
