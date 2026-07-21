<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, computed } from "vue";

const props = defineProps<{
  visible: boolean;
  loading: boolean;
  loadingMore?: boolean;
  hasMore?: boolean;
  historyList: any[];
  activeTraceId: string;
  modelValue: string; // keyword
}>();

const emit = defineEmits<{
  (e: "update:visible", value: boolean): void;
  (e: "update:modelValue", value: string): void;
  (e: "fetch-history"): void;
  (e: "load-more"): void;
  (e: "load-chat", item: any): void;
  (e: "open-full-logs", traceId: string): void;
  (e: "delete-group", group: any): void;
}>();

const windowWidth = ref(window.innerWidth);
const isMobile = computed(() => windowWidth.value < 640);

const handleResize = () => {
  windowWidth.value = window.innerWidth;
};

onMounted(() => {
  window.addEventListener("resize", handleResize);
});

onUnmounted(() => {
  window.removeEventListener("resize", handleResize);
});

const keyword = ref(props.modelValue);

watch(
  () => props.modelValue,
  (val) => {
    keyword.value = val;
  }
);

watch(keyword, (val) => {
  emit("update:modelValue", val);
});

const formatDate = (dateStr: string) => {
  if (!dateStr) return "-";
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - date.getTime();

  // Less than 1 minute
  if (diff < 60000) {
    return "刚刚";
  }
  // Less than 1 hour
  if (diff < 3600000) {
    return `${Math.floor(diff / 60000)} 分钟前`;
  }
  // Less than 1 day
  if (diff < 86400000) {
    return `${Math.floor(diff / 3600000)} 小时前`;
  }
  // Less than 7 days
  if (diff < 604800000) {
    return `${Math.floor(diff / 86400000)} 天前`;
  }

  // Otherwise show date
  return date.toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const handleScroll = (e: Event) => {
  const target = e.target as HTMLElement;
  if (!target) return;
  // If scrolled to bottom with 10px threshold
  if (target.scrollHeight - target.scrollTop <= target.clientHeight + 10) {
    if (props.hasMore && !props.loading && !props.loadingMore) {
      emit("load-more");
    }
  }
};
</script>

<template>
  <div v-if="visible && isMobile" class="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 transition-opacity" @click="emit('update:visible', false)"></div>
  <transition :name="isMobile ? 'slide-up' : 'slide-fade-left'">
    <div
      v-if="visible"
      class="bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 flex flex-col flex-shrink-0 shadow-xl transition-all duration-300"
      :class="[
        isMobile 
          ? 'fixed inset-x-0 bottom-0 top-0 w-full z-50 rounded-none h-full' 
          : 'relative w-64 h-full z-10'
      ]"
    >
      <div
        class="h-14 px-4 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between bg-gray-50/50 dark:bg-gray-800/50 flex-shrink-0"
      >
        <div class="flex items-center gap-2">
          <button
            @click="emit('update:visible', false)"
            class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition-colors text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            title="关闭侧边栏"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
          <h3 class="font-medium text-gray-800 dark:text-gray-100 text-sm tracking-normal flex items-center">
            会话历史
          </h3>
        </div>
        <button
          @click="emit('fetch-history')"
          class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition-colors"
          title="刷新历史"
        >
          <svg
            class="w-4 h-4 text-gray-500"
            :class="{ 'animate-spin': loading }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
        </button>
      </div>

      <!-- Search Bar -->
      <div class="px-4 py-3 border-b border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 flex-shrink-0">
        <div class="relative">
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索历史记录..."
            class="w-full pl-9 pr-3 py-2 text-xs bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl focus:outline-none focus:ring-2 focus:ring-[rgb(var(--primary-color-rgb)/0.2)] focus:border-[var(--primary-color)] transition-all placeholder-gray-400 text-gray-600 dark:text-gray-300"
          />
          <svg
            class="w-4 h-4 text-gray-400 absolute left-3 top-2.5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
        </div>
      </div>

      <!-- History List -->
      <div class="flex-1 overflow-y-auto custom-scrollbar bg-gray-50/30 dark:bg-gray-900/30" @scroll="handleScroll">
        <div
          v-if="loading && !historyList.length"
          class="p-12 flex flex-col items-center justify-center space-y-3 text-gray-400"
        >
          <div
            class="animate-spin rounded-full h-8 w-8 border-2 border-[var(--primary-color)] border-t-transparent"
          ></div>
          <span class="text-xs font-normal text-gray-500 tracking-normal">正在安全回溯</span>
        </div>

        <div v-else-if="!historyList.length" class="p-12 text-center">
          <div class="w-16 h-16 bg-gray-100 dark:bg-gray-800 rounded-3xl flex items-center justify-center mx-auto mb-4 opacity-50">
            <svg
                class="w-8 h-8 text-gray-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
            >
                <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
                />
            </svg>
          </div>
          <p class="text-xs text-gray-400 font-normal tracking-normal">时光机暂时空着</p>
        </div>

        <div v-else class="space-y-3 p-2">
          <!-- 遍历日期分组 -->
          <div v-for="group in historyList" :key="group.id" class="mb-4">
            
            <!-- 分组 Header：TDesign 风格，去背景色块 -->
            <div class="px-3 py-1.5 flex items-center justify-between mb-2">
              <span class="text-xs font-medium text-gray-500 dark:text-gray-400 tracking-normal">{{ group.title }}</span>
              <button 
                @click.stop="emit('delete-group', group)"
                class="p-1 hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-950/30 rounded-lg text-gray-400 transition-colors"
                title="一键删除此组所有会话"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </div>

            <!-- 遍历组内的会话 Card：TDesign 风格，圆角 9px + 无重阴影 + hover 靠背景 -->
            <div
              v-for="(item, index) in group.items"
              :key="item.trace_id"
              @click="emit('load-chat', item)"
              class="p-4 rounded-td-xl transition-colors border border-transparent group relative mb-1.5 overflow-hidden"
              :class="[
                activeTraceId === item.trace_id ? 'bg-tdSecondaryContainer dark:bg-tdSecondaryContainer-dark border-[rgb(var(--primary-color-rgb)/0.3)]' : '',
                isMobile ? 'cursor-default' : 'cursor-pointer hover:bg-tdSecondaryContainer dark:hover:bg-tdSecondaryContainer-dark'
              ]"
            >
              <!-- Watermark Number -->
              <div 
                  class="absolute bottom-2 right-2 w-7 h-7 rounded-full border-[2px] flex items-center justify-center text-[12px] font-normal select-none pointer-events-none z-0 -rotate-12 opacity-20 dark:opacity-30"
                  :style="{ color: `hsl(${((Number(index) || 0) * 137.5) % 360}, 70%, 50%)`, borderColor: `hsl(${((Number(index) || 0) * 137.5) % 360}, 70%, 50%, 0.3)` }"
              >
                  {{ (Number(index) || 0) + 1 }}
              </div>
              
              <div class="relative z-10">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-[11px] font-normal text-gray-400 tracking-normal">{{
                    formatDate(item.created_at)
                  }}</span>
                  <span
                    v-if="item.turn_count !== undefined"
                    class="px-1.5 py-0.5 rounded-md bg-gray-100 dark:bg-gray-800 text-[11px] text-gray-500 dark:text-gray-400 font-normal border border-gray-200 dark:border-gray-700"
                    >{{ item.turn_count }} 轮对话</span
                  >
                </div>
                <p
                  class="text-sm font-medium text-gray-800 dark:text-gray-100 truncate mb-1.5 group-hover:text-[var(--primary-color)] transition-colors"
                  :title="item.query"
                >
                  {{ item.query }}
                </p>
                <p
                  class="text-[11px] text-gray-500 dark:text-gray-400 line-clamp-2 leading-relaxed opacity-80"
                  :title="item.summary"
                >
                  {{ item.summary }}
                </p>

                <div
                  class="mt-3 flex items-center justify-between transition-all"
                  :class="isMobile ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'"
                >
                  <div class="flex items-center gap-1.5">
                    <span
                        class="w-1.5 h-1.5 rounded-full"
                        :class="item.status === 'success' ? 'bg-green-500' : 'bg-red-500'"
                    ></span>
                    <span class="text-[11px] font-normal tracking-normal text-gray-400">{{ item.status === "success" ? "Done" : "Error" }}</span>
                  </div>
                  <button
                    @click.stop="emit('open-full-logs', item.trace_id)"
                    class="mr-8 text-[11px] text-[var(--primary-color)] font-medium flex items-center hover:opacity-80 transition-opacity bg-[rgb(var(--primary-color-rgb)/0.05)] rounded-md border border-[rgb(var(--primary-color-rgb)/0.1)]"
                    :class="isMobile ? 'px-3 py-2' : 'px-2 py-1'"
                  >
                    回溯日志
                    <svg
                      class="w-3 h-3 ml-1"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="3"
                        d="M9 5l7 7-7 7"
                      />
                    </svg>
                  </button>
                </div>
              </div>
            </div>
          </div>
          
          <!-- Loading More Indicator -->
          <div v-if="loadingMore" class="py-4 flex justify-center items-center text-gray-400">
            <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="ml-2 text-xs font-normal text-gray-500 tracking-normal">加载更多...</span>
          </div>
          <!-- No More History Indicator -->
          <div v-if="!hasMore && historyList.length > 0" class="py-4 text-center text-[11px] text-gray-400 tracking-normal opacity-60">
             - 没有更多记录了 -
          </div>
        </div>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.slide-fade-left-enter-active,
.slide-fade-left-leave-active {
  transition: all 0.3s ease;
}

.slide-fade-left-enter-from,
.slide-fade-left-leave-to {
  transform: translateX(-20px);
  opacity: 0;
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

.slide-up-enter-from,
.slide-up-leave-to {
  transform: translateY(100%);
}
</style>

<style scoped>
.slide-fade-left-enter-active,
.slide-fade-left-leave-active {
  transition: all 0.3s ease;
}

.slide-fade-left-enter-from,
.slide-fade-left-leave-to {
  transform: translateX(-20px);
  opacity: 0;
}
</style>
