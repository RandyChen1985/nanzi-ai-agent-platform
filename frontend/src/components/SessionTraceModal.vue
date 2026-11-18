<script setup lang="ts">
import { ref, computed } from 'vue'
import MessageRenderer from "@/components/MessageRenderer.vue";
import { SparklesIcon, ArrowPathIcon } from '@heroicons/vue/24/outline'

const props = defineProps<{
  visible: boolean
  loading: boolean
  turns: any[]
  traceId?: string
  activeTraceId?: string // Highlights a specific trace within the session
  showContinue?: boolean
  showDelete?: boolean
}>()

const emit = defineEmits(['close', 'continue', 'delete', 'toggle-steps', 'open-trace'])

const formatDate = (dateStr: string) => {
  if (!dateStr) return "-"
  return new Date(dateStr).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  })
}

// Window width detection logic if needed, or assume desktop for now in TaskCenter
const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 640)

</script>

<template>
  <Teleport to="body">
  <div
    v-if="visible"
    class="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 backdrop-blur-sm sm:p-4"
    @click.self="emit('close')"
  >
    <div 
        class="bg-white w-full flex flex-col overflow-hidden animate-fade-in-up border border-gray-200 shadow-2xl transition-all duration-300"
        :class="isMobile ? 'h-full rounded-none' : 'max-w-3xl h-[80vh] rounded-xl'"
    >
        <!-- Header -->
        <div 
            class="px-4 py-3 sm:px-6 sm:py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50 flex-shrink-0"
        >
            <div class="flex items-center gap-2 sm:gap-3 min-w-0">
                <div class="p-2 bg-primary/10 rounded-lg text-primary flex-shrink-0">
                    <SparklesIcon class="w-5 h-5" />
                </div>
                <div>
                    <h3 class="text-sm sm:text-lg font-black text-gray-800 truncate">会话回溯详情</h3>
                    <p class="text-[10px] text-gray-400 font-mono" v-if="traceId">Trace: {{ traceId }}</p>
                </div>
            </div>
            <div class="flex items-center gap-1 sm:gap-2 flex-shrink-0">
                <button 
                    v-if="showContinue"
                    @click="emit('continue')"
                    class="flex items-center space-x-1.5 px-3 py-1.5 bg-primary/10 text-primary hover:bg-primary hover:text-white rounded-lg transition-all text-xs font-black border border-primary/20"
                >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
                    <span>继续聊天</span>
                </button>

                <div v-if="showDelete" class="w-px h-4 bg-gray-300 mx-1"></div>

                <button 
                    v-if="showDelete"
                    @click="emit('delete')"
                    class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                    title="删除此记录"
                >
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                </button>
                
                <div class="w-px h-4 bg-gray-300 mx-1"></div>

                <button 
                        @click="emit('close')" 
                        class="p-2 rounded-full transition-colors flex items-center justify-center bg-gray-100 hover:bg-gray-200 text-gray-500"
                >
                    <svg class="w-5 h-5 sm:w-6 sm:h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
            </div>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-4 sm:p-6 bg-gray-50 custom-scrollbar">
            <div v-if="loading" class="flex flex-col items-center justify-center h-full text-gray-400 py-20">
                <ArrowPathIcon class="w-10 h-10 animate-spin mb-3 text-primary" />
                <p class="text-xs font-bold uppercase tracking-widest">正在加载会话记录...</p>
            </div>
            <div v-else-if="turns && turns.length > 0" class="space-y-4 sm:space-y-6 pb-10">
                <!-- Conversation Thread -->
                <div v-for="(turn, tIdx) in turns" :key="turn.id || tIdx" 
                        class="bg-white p-4 rounded-3xl border border-gray-200 shadow-sm relative overflow-hidden"
                        :class="{'ring-2 ring-primary/20': activeTraceId && turn.trace_id === activeTraceId}"
                >
                    <!-- Turn Header -->
                    <div class="flex justify-between items-center mb-4">
                        <span class="text-[10px] font-black text-gray-400 uppercase tracking-widest flex items-center gap-1.5">
                            <span class="w-2 h-2 rounded-full bg-blue-500 shadow-sm shadow-blue-500/20"></span>
                            对话回合 #{{ tIdx + 1 }}
                        </span>
                        <span class="text-[9px] text-gray-400 font-mono bg-gray-50 px-2 py-0.5 rounded-full">{{ formatDate(turn.created_at) }}</span>
                    </div>

                    <!-- Q&A Content -->
                    <div class="space-y-4">
                        <div>
                            <div class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-2 opacity-70">提问 · Query</div>
                            <div class="text-gray-800 text-sm font-bold leading-relaxed whitespace-pre-wrap bg-gray-50/50 p-3 rounded-xl border border-gray-100">
                                {{ turn.query || 'N/A' }}
                            </div>
                        </div>
                        <div>
                            <div class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-2 opacity-70">回答 · Response</div>
                            <div class="text-gray-600 text-xs sm:text-sm leading-relaxed">
                                <MessageRenderer :content="turn.summary || 'N/A'" />
                            </div>
                        </div>
                    </div>

                    <!-- Embedded Thinking Chain (Steps) -->
                    <div class="mt-6 pt-4 border-t border-gray-50">
                        <button 
                            @click="emit('toggle-steps', turn)"
                            class="flex items-center justify-between w-full p-2.5 rounded-xl hover:bg-gray-50 transition-all group/btn"
                        >
                            <div class="flex items-center space-x-2 text-[11px] font-black text-gray-500 group-hover/btn:text-primary transition-colors uppercase tracking-widest">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                                <span>执行全链路 (Steps)</span>
                                <span v-if="turn.steps?.length" class="bg-gray-100 px-1.5 py-0.5 rounded text-[9px] font-mono">{{ turn.steps.length }}</span>
                            </div>
                            <div class="flex items-center">
                                <div v-if="turn.loading" class="w-3.5 h-3.5 border-2 border-primary/30 border-t-primary rounded-full animate-spin mr-2"></div>
                                <svg 
                                    class="w-4 h-4 text-gray-400 transform transition-transform duration-300"
                                    :class="{ 'rotate-180': turn.isExpanded }"
                                    fill="none" stroke="currentColor" viewBox="0 0 24 24"
                                >
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" />
                                </svg>
                            </div>
                        </button>

                        <!-- Steps List -->
                        <div v-show="turn.isExpanded" class="mt-4 space-y-4 pl-4 border-l-2 border-gray-100 animate-fade-in">
                            <div v-if="turn.steps && turn.steps.length > 0" class="space-y-4">
                                <div v-for="(step, sIdx) in turn.steps" :key="sIdx" class="relative group/step">
                                    <!-- Step Dot -->
                                    <div class="absolute -left-[26px] top-1 w-5 h-5 rounded-full border border-gray-200 shadow-sm flex items-center justify-center text-[9px] font-black text-white z-10"
                                        :class="step.status === 'error' ? 'bg-amber-500' : 'bg-blue-500'">
                                        {{ Number(sIdx) + 1 }}
                                    </div>
                                    <!-- Step Card -->
                                    <div class="bg-white rounded-xl border border-gray-200 p-3 shadow-sm hover:shadow-md transition-shadow">
                                        <div class="flex justify-between items-center mb-2">
                                            <div class="flex items-center gap-2">
                                                <span class="text-[9px] font-black px-1.5 py-0.5 rounded uppercase tracking-tighter"
                                                    :class="{
                                                        'bg-blue-100 text-blue-700': step.event_type === 'thought',
                                                        'bg-purple-100 text-purple-700': step.event_type === 'router',
                                                        'bg-amber-100 text-amber-700': step.event_type === 'tool_call',
                                                        'bg-green-100 text-green-700': step.event_type === 'synthesis' || step.event_type === 'final_answer',
                                                        'bg-red-100 text-red-700': step.event_type === 'error'
                                                    }">
                                                    {{ step.event_type }}
                                                </span>
                                                <span v-if="step.tool_name" class="text-[9px] font-black text-purple-600 bg-purple-50 px-1.5 py-0.5 rounded font-mono">
                                                    {{ step.tool_name }}
                                                </span>
                                            </div>
                                            <span class="text-[8px] text-gray-400 font-mono">{{ step.execution_time_ms ? `${step.execution_time_ms.toFixed(0)}ms` : '' }}</span>
                                        </div>
                                        <div class="space-y-2">
                                            <!-- Input -->
                                            <div v-if="step.tool_input" class="bg-gray-50 p-2 rounded-lg border border-gray-100">
                                                <pre class="text-[9px] text-gray-500 overflow-x-auto font-mono whitespace-pre-wrap break-all">{{ typeof step.tool_input === 'string' ? step.tool_input : JSON.stringify(step.tool_input, null, 2) }}</pre>
                                            </div>
                                            <!-- Output -->
                                            <div v-if="step.tool_output && step.tool_output.content" class="text-[11px] text-gray-600 leading-relaxed bg-blue-50/20 p-2 rounded-lg border border-blue-100/10">
                                                {{ step.tool_output.content }}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div v-else-if="!turn.loading" class="py-4 text-center text-[10px] text-gray-400 italic">
                                暂无详细执行记录
                            </div>
                        </div>
                    </div>

                    <!-- Indicator for current trace -->
                    <div v-if="activeTraceId && turn.trace_id === activeTraceId" class="absolute top-0 right-0">
                        <div class="bg-primary text-white text-[8px] font-black px-2.5 py-1 rounded-bl-xl uppercase tracking-tighter shadow-sm animate-pulse">
                            Current Turn
                        </div>
                    </div>
                </div>
            </div>
            <div v-else class="text-center py-16 text-gray-400 bg-white rounded-2xl border border-dashed border-gray-200">
                <div class="mb-2 opacity-20"><svg class="w-12 h-12 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" /></svg></div>
                <p class="text-xs font-bold uppercase tracking-tighter">暂无详细执行日志回溯</p>
            </div>
        </div>
    </div>
  </div>
  </Teleport>
</template>
