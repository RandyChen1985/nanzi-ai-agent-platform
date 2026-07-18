<template>
  <div class="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
    <div class="px-5 py-3.5 border-b border-gray-100">
      <h3 class="text-sm font-bold text-gray-900 flex items-center">
        <span class="w-1.5 h-1.5 rounded-full bg-primary mr-2"></span>
        {{ title || '最近调用' }}
      </h3>
    </div>
    <div class="p-5">
      <!-- Detailed Mode (User) -->
      <div v-if="mode === 'detailed'">
        <div v-if="calls?.length > 0" class="space-y-3">
          <div
            v-for="call in calls"
            :key="call.id"
            class="flex items-center justify-between py-2 border-b border-gray-50 last:border-0"
          >
            <div class="flex items-center flex-1 min-w-0">
              <span
                class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium mr-2.5"
                :class="{
                  'bg-emerald-50 text-emerald-700':
                    call.status_code >= 200 && call.status_code < 300,
                  'bg-amber-50 text-amber-700':
                    call.status_code >= 300 && call.status_code < 400,
                  'bg-red-50 text-red-700': call.status_code >= 400,
                }"
              >
                {{ call.status_code }}
              </span>
              <span
                class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700 font-mono mr-2.5"
              >
                {{ call.method }}
              </span>
              <span
                class="text-sm text-gray-600 font-mono flex-1 truncate"
                :title="call.endpoint"
              >
                {{ call.endpoint }}
              </span>
            </div>
            <div class="flex items-center space-x-3 ml-3 flex-shrink-0">
              <span class="text-sm text-gray-500 tabular-nums"
                >{{ call.process_time_ms?.toFixed(2) }} ms</span
              >
              <span class="text-xs text-gray-400">{{
                formatDate(call.created_at)
              }}</span>
            </div>
          </div>
        </div>
        <div v-else class="text-center py-10 text-gray-400">
          <p class="text-sm">暂无调用记录</p>
          <p class="text-xs text-gray-400 mt-1">
            开始使用 API 后，这里会显示最近的调用记录
          </p>
        </div>
      </div>

      <!-- Compact Mode (Admin) -->
      <div v-else>
        <div v-if="calls?.length > 0" class="space-y-3">
          <div
            v-for="call in calls.slice(0, 5)"
            :key="call.id"
            class="flex items-center text-sm"
          >
            <span
              class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium mr-2"
              :class="{
                'bg-emerald-50 text-emerald-700':
                  call.status_code >= 200 && call.status_code < 300,
                'bg-red-50 text-red-700': call.status_code >= 400,
              }"
            >
              {{ call.status_code }}
            </span>
            <span
              class="text-gray-600 font-mono text-xs flex-1 truncate"
              :title="call.endpoint"
            >
              {{ call.endpoint }}
            </span>
            <span class="text-xs text-gray-400 ml-2 shrink-0">
              {{ formatDate(call.created_at) }}
            </span>
          </div>
        </div>
        <div v-else class="text-center py-10 text-gray-400 text-sm">
          暂无数据
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { formatDate } from '../../utils/date';

defineProps<{
  title?: string;
  calls: Array<any>;
  mode?: 'compact' | 'detailed';
}>();
</script>
