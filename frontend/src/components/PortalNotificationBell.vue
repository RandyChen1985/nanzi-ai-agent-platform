<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import axios from "../utils/axios";

const router = useRouter();
const open = ref(false);
const loading = ref(false);
const unreadCount = ref(0);
const notifications = ref<any[]>([]);
let closeTimer: ReturnType<typeof setTimeout> | null = null;

const cancelScheduledClose = () => {
  if (closeTimer) clearTimeout(closeTimer);
  closeTimer = null;
};
const closeNotifications = () => {
  cancelScheduledClose();
  open.value = false;
};
const scheduleClose = () => {
  cancelScheduledClose();
  closeTimer = setTimeout(() => { open.value = false; }, 220);
};

const fetchUnreadCount = async () => {
  const res = await axios.get("/api/portal/inbox/unread-count");
  unreadCount.value = Number(res.data?.data?.count || 0);
};
const fetchNotifications = async () => {
  loading.value = true;
  try {
    const res = await axios.get("/api/portal/inbox", { params: { limit: 30 } });
    notifications.value = res.data?.data || [];
  } finally { loading.value = false; }
};
const toggle = async () => {
  cancelScheduledClose();
  open.value = !open.value;
  if (open.value) await fetchNotifications();
};
const markRead = async (item: any) => {
  if (!item.read_at) {
    await axios.post(`/api/portal/inbox/${item.id}/read`);
    item.read_at = new Date().toISOString();
    unreadCount.value = Math.max(0, unreadCount.value - 1);
  }
  const meta = item.metadata || {};
  if (meta.report_id) {
    open.value = false;
    await router.push({ path: "/dashboard/chat", query: { dataset_portal: "1", report_id: meta.report_id, run_id: item.resource_id || "" } });
  }
};
const markAllRead = async () => {
  await axios.post("/api/portal/inbox/read-all");
  notifications.value.forEach(item => { if (!item.read_at) item.read_at = new Date().toISOString(); });
  unreadCount.value = 0;
};
const formatDate = (value: string) => value ? new Date(value).toLocaleString("zh-CN") : "";

onMounted(() => { fetchUnreadCount().catch(() => undefined); });
onUnmounted(cancelScheduledClose);
</script>

<template>
  <div class="relative" @mouseenter="cancelScheduledClose" @mouseleave="scheduleClose">
    <button type="button" class="relative p-2 rounded-lg text-gray-500 hover:bg-gray-100" title="站内通知" @click="toggle">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 10-12 0v3.2c0 .5-.2 1-.6 1.4L4 17h5m6 0a3 3 0 01-6 0" /></svg>
      <span v-if="unreadCount" class="absolute -right-1 -top-1 min-w-4 h-4 px-1 rounded-full bg-red-500 text-white text-[9px] font-bold leading-4 text-center">{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
    </button>
    <div v-if="open" class="absolute right-0 mt-2 w-[22rem] max-w-[90vw] rounded-2xl bg-white border border-gray-100 shadow-2xl overflow-hidden z-50">
      <div class="px-4 py-3 flex items-center justify-between gap-3 border-b"><div><h3 class="text-sm font-black text-gray-800">站内通知</h3><p class="text-[10px] text-gray-400">报表运行与系统消息</p></div><div class="flex items-center gap-2"><button class="text-xs text-blue-600" @click="markAllRead">全部已读</button><button type="button" class="p-1.5 rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-600" title="关闭通知" aria-label="关闭通知" @click="closeNotifications"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg></button></div></div>
      <div class="max-h-[28rem] overflow-y-auto">
        <p v-if="loading" class="py-10 text-center text-xs text-gray-400">正在加载...</p>
        <p v-else-if="!notifications.length" class="py-10 text-center text-xs text-gray-400">暂无通知</p>
        <button v-for="item in notifications" v-else :key="item.id" class="w-full px-4 py-3 text-left border-b border-gray-50 hover:bg-gray-50" :class="!item.read_at ? 'bg-blue-50/40' : ''" @click="markRead(item)">
          <div class="flex gap-2"><span class="mt-1 w-2 h-2 rounded-full shrink-0" :class="item.level === 'error' ? 'bg-red-500' : item.level === 'success' ? 'bg-emerald-500' : 'bg-blue-500'"></span><div class="min-w-0"><p class="text-xs font-bold text-gray-700 truncate">{{ item.title }}</p><p class="text-[11px] text-gray-500 mt-1 line-clamp-2">{{ item.content }}</p><p class="text-[10px] text-gray-400 mt-1">{{ formatDate(item.created_at) }}</p></div></div>
        </button>
      </div>
    </div>
  </div>
</template>
