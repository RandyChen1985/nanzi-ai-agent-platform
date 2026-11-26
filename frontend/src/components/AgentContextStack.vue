<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  context: Record<string, any>
}>()

const emit = defineEmits(['clear'])

const entities = computed(() => {
  const result = []
  if (props.context.room) result.push({ key: 'room', label: '机房', value: props.context.room, icon: '🏢' })
  if (props.context.metric) result.push({ key: 'metric', label: '指标', value: props.context.metric, icon: '📊' })
  if (props.context.time) result.push({ key: 'time', label: '时间范围', value: props.context.time, icon: '🕒' })
  
  // Add other generic keys if any
  Object.keys(props.context).forEach(key => {
    if (!['room', 'metric', 'time'].includes(key)) {
      result.push({ key, label: key, value: props.context[key], icon: '📍' })
    }
  })
  
  return result
})

const clearEntity = (key: string) => {
  emit('clear', key)
}
</script>

<template>
  <div class="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden flex flex-col h-full">
    <div class="px-4 py-3 border-b border-gray-50 bg-gray-50/50 flex items-center justify-between">
      <h3 class="text-xs font-bold text-gray-700 flex items-center uppercase tracking-wider">
        <svg class="w-4 h-4 mr-2 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
        </svg>
        当前上下文 (Context)
      </h3>
      <span class="px-1.5 py-0.5 bg-primary/10 text-primary-dark text-[10px] rounded font-bold">MEMORY</span>
    </div>
    
    <div class="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar">
      <div v-if="entities.length === 0" class="flex flex-col items-center justify-center h-32 text-gray-400 space-y-2">
        <svg class="w-8 h-8 opacity-20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
        </svg>
        <span class="text-xs">暂无提取的实体信息</span>
      </div>
      
      <transition-group name="list">
        <div v-for="entity in entities" :key="entity.key" class="group relative bg-white border border-gray-200 rounded-lg p-3 hover:border-primary/30 hover:shadow-sm transition-all duration-200">
          <div class="flex items-start justify-between">
            <div class="flex items-center space-x-2">
              <span class="text-lg" aria-hidden="true">{{ entity.icon }}</span>
              <div class="min-w-0">
                <p class="text-[10px] font-bold text-gray-400 uppercase tracking-widest leading-none mb-1">{{ entity.label }}</p>
                <p class="text-sm font-semibold text-gray-800 truncate">{{ entity.value }}</p>
              </div>
            </div>
            <button 
              @click="clearEntity(entity.key)"
              class="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded transition-all"
              title="清除此记忆"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      </transition-group>
    </div>
    
    <div class="p-4 bg-gray-50 border-t border-gray-100">
      <p class="text-[10px] text-gray-400 leading-relaxed">
         智能体会根据对话自动更新上下文。如果查询结果不符合预期，尝试清除特定实体后再提问。
      </p>
    </div>
  </div>
</template>

<style scoped>
.list-enter-active,
.list-leave-active {
  transition: all 0.3s ease;
}
.list-enter-from,
.list-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
</style>
