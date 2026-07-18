<template>
  <div
    v-if="errors?.length > 0"
    class="bg-white rounded-2xl shadow-sm border border-red-100 overflow-hidden"
  >
    <div
      class="px-5 py-3.5 border-b border-red-50 bg-red-50/30 flex items-center justify-between"
    >
      <h3 class="text-sm font-bold text-red-800 flex items-center">
        <span class="w-1.5 h-1.5 rounded-full bg-red-500 mr-2"></span>
        智能体异常链路
      </h3>
      <span class="text-[11px] text-red-400 font-medium">最近 5 条</span>
    </div>
    <div class="overflow-x-auto">
      <table class="min-w-full divide-y divide-gray-100">
        <thead class="bg-gray-50/60">
          <tr>
            <th class="px-5 py-2.5 text-left text-[11px] font-semibold text-gray-400">
              时间
            </th>
            <th class="px-5 py-2.5 text-left text-[11px] font-semibold text-gray-400">
              工具
            </th>
            <th class="px-5 py-2.5 text-left text-[11px] font-semibold text-gray-400">
              错误详情
            </th>
            <th class="px-5 py-2.5 text-right text-[11px] font-semibold text-gray-400">
              操作
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-50 bg-white">
          <tr
            v-for="err in errors"
            :key="err.trace_id"
            class="hover:bg-red-50/20 transition-colors"
          >
            <td class="px-5 py-3.5 whitespace-nowrap text-xs text-gray-500">
              {{ formatDate(err.time) }}
            </td>
            <td class="px-5 py-3.5 whitespace-nowrap">
              <span class="text-xs font-mono font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-md">
                {{ err.tool }}
              </span>
            </td>
            <td
              class="px-5 py-3.5 text-xs text-gray-600 max-w-md truncate"
              :title="err.message"
            >
              {{ err.message }}
            </td>
            <td class="px-5 py-3.5 whitespace-nowrap text-right text-xs">
              <button
                @click="$emit('view-trace', err.trace_id)"
                class="text-blue-600 font-semibold hover:text-blue-800 transition-colors"
              >
                回溯详情
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { formatDate } from '../../utils/date';

defineProps<{
  errors: Array<any>;
}>();

defineEmits<{
  (e: 'view-trace', traceId: string): void;
}>();
</script>
