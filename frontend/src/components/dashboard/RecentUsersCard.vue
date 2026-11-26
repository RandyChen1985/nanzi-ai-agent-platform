<template>
  <div class="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
    <div class="px-5 py-3.5 border-b border-gray-100">
      <h3 class="text-sm font-bold text-gray-900 flex items-center">
        <span class="w-1.5 h-1.5 rounded-full bg-primary mr-2"></span>
        活跃用户
      </h3>
    </div>
    <div class="p-5">
      <div v-if="users?.length > 0" class="space-y-3.5">
        <div
          v-for="user in users"
          :key="user.user_name"
          class="flex items-center"
        >
          <div
            class="h-9 w-9 rounded-full bg-primary/5 text-primary flex items-center justify-center font-bold text-sm"
          >
            {{ user.user_name.charAt(0).toUpperCase() }}
          </div>
          <div class="ml-3 flex-1 min-w-0">
            <p class="text-sm font-medium text-gray-900 truncate">
              {{ user.user_name }}
            </p>
            <p class="text-xs text-gray-500">
              {{ user.role === "admin" ? "管理员" : "普通用户" }}
            </p>
          </div>
          <div class="text-xs text-gray-400 shrink-0">
            {{ formatDate(user.last_active) }}
          </div>
        </div>
      </div>
      <div v-else class="text-center py-10 text-gray-400 text-sm">
        暂无数据
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { formatDate } from '../../utils/date';

defineProps<{
  users: Array<any>;
}>();
</script>
