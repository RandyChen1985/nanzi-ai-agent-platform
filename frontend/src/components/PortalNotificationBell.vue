<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import axios from "../utils/axios";
import { renderMarkdown } from "../utils/markdown";
import {
  createSavedReportOpenRequest,
  publishSavedReportOpenRequest,
} from "../utils/savedReportOpenProtocol";

const router = useRouter();
const open = ref(false);
const loading = ref(false);
const refreshing = ref(false);
const unreadCount = ref(0);
const notifications = ref<any[]>([]);
const deleteMode = ref<"single" | "read" | null>(null);
const deleteTarget = ref<any | null>(null);
const deleting = ref(false);
const deleteError = ref("");
const detailItem = ref<any | null>(null);
let unreadTimer: ReturnType<typeof setInterval> | null = null;
let listTimer: ReturnType<typeof setInterval> | null = null;

const isSavedReportNotification = (item: any) => {
  const meta = item?.metadata || {};
  return Boolean(meta.report_id) || item?.resource_type === "saved_report_run";
};

const notificationKindLabel = (item: any) => {
  if (isSavedReportNotification(item)) return "黄金报表";
  if (item?.category === "agent") return "智能体";
  if (item?.category === "saved_report") return "报表";
  return "系统";
};

const detailHtml = computed(() => renderMarkdown(String(detailItem.value?.content || "")));

const detailCopied = ref(false);
const closeNotifications = () => {
  open.value = false;
  if (listTimer) clearInterval(listTimer);
  listTimer = null;
};

const closeDetail = () => {
  detailItem.value = null;
  detailCopied.value = false;
};

const copyDetailContent = async () => {
  if (!detailItem.value?.content) return;
  try {
    await navigator.clipboard.writeText(detailItem.value.content);
    detailCopied.value = true;
    setTimeout(() => {
      detailCopied.value = false;
    }, 2000);
  } catch (err) {
    console.warn("Failed to copy notification content", err);
  }
};

const fetchUnreadCount = async () => {
  const res = await axios.get("/api/portal/inbox/unread-count");
  unreadCount.value = Number(res.data?.data?.count || 0);
};
const fetchNotifications = async (silent = false) => {
  if (!silent) loading.value = true;
  try {
    const res = await axios.get("/api/portal/inbox", { params: { limit: 30 } });
    notifications.value = res.data?.data || [];
  } finally { if (!silent) loading.value = false; }
};
const refreshNotifications = async (silent = false) => {
  if (refreshing.value || document.hidden) return;
  refreshing.value = true;
  try {
    await Promise.all([fetchNotifications(silent), fetchUnreadCount()]);
  } finally {
    refreshing.value = false;
  }
};
const startUnreadPolling = () => {
  if (unreadTimer) clearInterval(unreadTimer);
  unreadTimer = null;
  if (document.hidden) return;
  unreadTimer = setInterval(() => {
    if (!document.hidden && !open.value) fetchUnreadCount().catch(() => undefined);
  }, 60_000);
};
const startListPolling = () => {
  if (listTimer) clearInterval(listTimer);
  listTimer = null;
  if (!open.value || document.hidden) return;
  listTimer = setInterval(() => {
    if (open.value && !document.hidden) refreshNotifications(true).catch(() => undefined);
  }, 30_000);
};
const refreshWhenActive = () => {
  if (document.hidden) return;
  if (open.value) refreshNotifications(true).catch(() => undefined);
  else fetchUnreadCount().catch(() => undefined);
  startUnreadPolling();
  startListPolling();
};
const handleVisibilityChange = () => {
  if (document.hidden) {
    if (unreadTimer) clearInterval(unreadTimer);
    if (listTimer) clearInterval(listTimer);
    unreadTimer = null;
    listTimer = null;
    return;
  }
  refreshWhenActive();
};
const toggle = async () => {
  open.value = !open.value;
  if (open.value) {
    await refreshNotifications();
    startListPolling();
  } else {
    closeNotifications();
  }
};
const markItemRead = async (item: any) => {
  if (item.read_at) return;
  const readAt = new Date().toISOString();
  // 先本地标记，保证列表/角标即时反馈；接口失败再回滚
  const applyLocal = (value: string | null) => {
    item.read_at = value;
    const idx = notifications.value.findIndex((n) => n.id === item.id);
    if (idx >= 0) {
      notifications.value[idx] = { ...notifications.value[idx], read_at: value };
    }
    if (detailItem.value?.id === item.id) {
      detailItem.value = { ...detailItem.value, read_at: value };
    }
  };
  applyLocal(readAt);
  unreadCount.value = Math.max(0, unreadCount.value - 1);
  try {
    await axios.post(`/api/portal/inbox/${item.id}/read`);
  } catch (error) {
    console.warn("Failed to mark portal notification as read", error);
    applyLocal(null);
    unreadCount.value += 1;
  }
};
const openNotification = async (item: any) => {
  const meta = item.metadata || {};
  const reportId = meta.report_id || (item.category === "saved_report" || item.resource_type === "saved_report" ? item.resource_id : null);
  const savedReportOpenRequest = isSavedReportNotification(item) && reportId
    ? createSavedReportOpenRequest({ report_id: reportId, run_id: item.resource_id || "" })
    : null;
  const notificationTarget = savedReportOpenRequest
    ? {
        path: "/dashboard/chat",
        query: {
          dataset_portal: "1",
          report_id: savedReportOpenRequest.report_id,
          run_id: savedReportOpenRequest.run_id,
          open_request_id: savedReportOpenRequest.request_id,
        },
      }
    : null;

  await markItemRead(item);

  if (notificationTarget && savedReportOpenRequest) {
    closeNotifications();
    closeDetail();
    await router.push(notificationTarget);
    publishSavedReportOpenRequest(savedReportOpenRequest);
    return;
  }

  // 智能体/系统站内消息：打开详情弹窗并自动关闭下拉弹框；黄金报表消息保持原样
  if (!isSavedReportNotification(item)) {
    closeNotifications();
  }
  detailItem.value = item;
};
const markAllRead = async () => {
  await axios.post("/api/portal/inbox/read-all");
  notifications.value.forEach(item => { if (!item.read_at) item.read_at = new Date().toISOString(); });
  unreadCount.value = 0;
};
const hasReadNotifications = computed(() => notifications.value.some(item => !!item.read_at));
const requestDeleteNotification = (item: any) => {
  deleteMode.value = "single";
  deleteTarget.value = item;
  deleteError.value = "";
};
const requestClearRead = () => {
  if (!hasReadNotifications.value) return;
  deleteMode.value = "read";
  deleteTarget.value = null;
  deleteError.value = "";
};
const closeDeleteConfirm = () => {
  if (deleting.value) return;
  deleteMode.value = null;
  deleteTarget.value = null;
  deleteError.value = "";
};
const confirmDeleteNotifications = async () => {
  if (deleting.value || !deleteMode.value) return;
  deleting.value = true;
  deleteError.value = "";
  try {
    if (deleteMode.value === "read") {
      await axios.delete("/api/portal/inbox/read");
      notifications.value = notifications.value.filter(item => !item.read_at);
      if (detailItem.value?.read_at) closeDetail();
    } else if (deleteTarget.value) {
      await axios.delete(`/api/portal/inbox/${deleteTarget.value.id}`);
      if (!deleteTarget.value.read_at) unreadCount.value = Math.max(0, unreadCount.value - 1);
      notifications.value = notifications.value.filter(item => item.id !== deleteTarget.value.id);
      if (detailItem.value?.id === deleteTarget.value.id) closeDetail();
    }
    closeDeleteConfirm();
  } catch (error: any) {
    deleteError.value = error.response?.data?.detail || "删除消息失败，请稍后重试";
  } finally {
    deleting.value = false;
    if (!deleteError.value) closeDeleteConfirm();
  }
};
const formatDate = (value: string) => value ? new Date(value).toLocaleString("zh-CN") : "";
const previewText = (content: string) =>
  String(content || "")
    .replace(/[#>*`_\-|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

onMounted(() => {
  fetchUnreadCount().catch(() => undefined);
  startUnreadPolling();
  document.addEventListener("visibilitychange", handleVisibilityChange);
  window.addEventListener("focus", refreshWhenActive);
});
onUnmounted(() => {
  if (unreadTimer) clearInterval(unreadTimer);
  if (listTimer) clearInterval(listTimer);
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  window.removeEventListener("focus", refreshWhenActive);
});
</script>

<template>
  <div class="relative">
    <button type="button" class="relative p-2 rounded-lg text-gray-500 hover:bg-gray-100" title="站内通知" @click="toggle">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 10-12 0v3.2c0 .5-.2 1-.6 1.4L4 17h5m6 0a3 3 0 01-6 0" /></svg>
      <span v-if="unreadCount" class="absolute -right-1 -top-1 min-w-4 h-4 px-1 rounded-full bg-red-500 text-white text-[9px] font-bold leading-4 text-center">{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
    </button>
    <div v-if="open" class="absolute right-0 mt-2 w-[22rem] max-w-[90vw] rounded-2xl bg-white border border-gray-100 shadow-2xl overflow-hidden z-50">
      <div class="border-b border-gray-100">
        <div class="notification-header-main flex items-start justify-between gap-3 px-4 pb-2 pt-3">
          <div class="min-w-0"><h3 class="text-sm font-black text-gray-800">站内通知</h3><p class="mt-0.5 truncate text-[10px] text-gray-400">报表运行与系统消息</p></div>
          <button type="button" class="shrink-0 rounded-full p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600" title="关闭通知" aria-label="关闭通知" @click="closeNotifications"><svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg></button>
        </div>
        <div class="notification-header-actions flex items-center gap-1.5 px-3 pb-3 whitespace-nowrap">
          <button type="button" class="inline-flex items-center gap-1 rounded-lg bg-gray-50 px-2.5 py-1.5 text-[11px] font-bold text-gray-500 hover:bg-gray-100 disabled:opacity-50" :disabled="refreshing" title="刷新消息" aria-label="刷新消息" @click="refreshNotifications()"><svg class="h-3.5 w-3.5" :class="refreshing ? 'animate-spin' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.6m14.8 2A8 8 0 004.6 9m0 0H9m11 11v-5h-.6m0 0A8 8 0 014.6 13m14.8 2H15" /></svg><span>{{ refreshing ? '刷新中' : '刷新' }}</span></button>
          <button class="rounded-lg bg-blue-50 px-2.5 py-1.5 text-[11px] font-bold text-blue-600 hover:bg-blue-100" @click="markAllRead">全部已读</button>
          <button type="button" class="rounded-lg px-2.5 py-1.5 text-[11px] font-bold text-red-500 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40" :disabled="!hasReadNotifications" @click="requestClearRead">清空已读</button>
        </div>
      </div>
      <div class="max-h-[28rem] overflow-y-auto">
        <p v-if="loading" class="py-10 text-center text-xs text-gray-400">正在加载...</p>
        <p v-else-if="!notifications.length" class="py-10 text-center text-xs text-gray-400">暂无通知</p>
        <div v-for="item in notifications" v-else :key="item.id" class="group flex border-b border-gray-50 hover:bg-gray-50" :class="!item.read_at ? 'bg-blue-50/50' : 'bg-white'">
          <button class="min-w-0 flex-1 px-4 py-3 text-left" @click="openNotification(item)">
            <div class="flex gap-2">
              <span
                class="mt-1.5 w-2 h-2 rounded-full shrink-0"
                :class="!item.read_at ? 'bg-blue-500' : 'bg-gray-200'"
                :title="item.read_at ? '已读' : '未读'"
              ></span>
              <div class="min-w-0">
                <div class="flex items-center gap-1.5 min-w-0">
                  <span
                    class="shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-black"
                    :class="isSavedReportNotification(item) ? 'bg-amber-50 text-amber-700 border border-amber-100' : 'bg-slate-100 text-slate-600 border border-slate-200'"
                  >{{ isSavedReportNotification(item) ? '⭐ 黄金报表' : notificationKindLabel(item) }}</span>
                  <span
                    v-if="!item.read_at"
                    class="shrink-0 rounded-full bg-blue-100 px-1.5 py-0.5 text-[9px] font-black text-blue-600"
                  >未读</span>
                  <p class="truncate text-xs" :class="!item.read_at ? 'font-black text-gray-800' : 'font-medium text-gray-500'">{{ item.title }}</p>
                </div>
                <p class="text-[11px] mt-1 line-clamp-2" :class="!item.read_at ? 'text-gray-600' : 'text-gray-400'">{{ previewText(item.content) }}</p>
                <p class="text-[10px] text-gray-400 mt-1">{{ formatDate(item.created_at) }}</p>
              </div>
            </div>
          </button>
          <button type="button" class="mr-2 self-center rounded-lg p-2 text-gray-300 opacity-70 transition-all hover:bg-red-50 hover:text-red-500 focus:opacity-100 sm:opacity-0 sm:group-hover:opacity-100" title="删除消息" aria-label="删除消息" @click.stop="requestDeleteNotification(item)"><svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 7h12m-10 0 1 13h6l1-13m-6 0V4h4v3"/></svg></button>
        </div>
      </div>
    </div>

    <teleport to="body">
      <div
        v-if="detailItem"
        class="fixed inset-0 z-[270] flex items-center justify-center bg-black/35 p-4 backdrop-blur-[1px]"
        @click.self="closeDetail"
      >
        <div class="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="flex items-start justify-between gap-3 border-b border-gray-100 px-5 py-4">
            <div class="min-w-0">
              <div class="mb-1.5 flex flex-wrap items-center gap-2">
                <span class="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-black text-slate-600">{{ notificationKindLabel(detailItem) }}</span>
                <span
                  class="rounded-full px-2 py-0.5 text-[10px] font-black"
                  :class="detailItem.level === 'error' ? 'bg-red-50 text-red-600' : detailItem.level === 'success' ? 'bg-emerald-50 text-emerald-700' : detailItem.level === 'warning' ? 'bg-amber-50 text-amber-700' : 'bg-blue-50 text-blue-600'"
                >{{ detailItem.level || 'info' }}</span>
              </div>
              <h3 class="text-base font-black text-gray-800">{{ detailItem.title }}</h3>
              <p class="mt-1 text-[11px] text-gray-400">{{ formatDate(detailItem.created_at) }}</p>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              <button type="button" class="shrink-0 rounded-full p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600" title="关闭详情" aria-label="关闭详情" @click="closeDetail">
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>
          </div>
          <div class="notification-detail-body group relative flex-1 overflow-y-auto px-5 py-4">
            <button
              type="button"
              class="absolute top-3 right-5 z-20 flex h-7 w-7 items-center justify-center rounded-lg border border-gray-200 bg-white/90 shadow-sm backdrop-blur-sm transition-all duration-200 opacity-0 group-hover:opacity-100 hover:scale-105 active:scale-95"
              :class="detailCopied ? 'border-emerald-300 bg-emerald-50 text-emerald-600 opacity-100' : 'text-gray-400 hover:border-blue-300 hover:bg-white hover:text-blue-600'"
              :title="detailCopied ? '已复制到剪贴板' : '复制内容'"
              aria-label="复制消息内容"
              @click="copyDetailContent"
            >
              <svg v-if="detailCopied" class="h-4 w-4 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
              </svg>
              <svg v-else class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
            </button>
            <div class="markdown-body text-sm text-gray-700 leading-relaxed" v-html="detailHtml"></div>
          </div>
          <div class="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
            <button type="button" class="rounded-lg border px-3 py-2 text-xs font-bold text-gray-600" @click="closeDetail">关闭</button>
          </div>
        </div>
      </div>
    </teleport>

    <teleport to="body"><div v-if="deleteMode" class="fixed inset-0 z-[280] flex items-center justify-center bg-black/35 p-4 backdrop-blur-[1px]" @click.self="closeDeleteConfirm">
      <div class="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
        <h3 class="text-sm font-black text-gray-800">{{ deleteMode === 'read' ? '确认清空已读消息？' : '确认删除这条消息？' }}</h3>
        <p class="mt-2 text-xs leading-relaxed text-gray-500">{{ deleteMode === 'read' ? '所有已读站内消息将被永久删除，未读消息不受影响。' : '只会删除这条站内消息，不会删除关联报表和运行历史。' }}</p>
        <p v-if="deleteTarget" class="mt-2 truncate rounded-lg bg-gray-50 px-3 py-2 text-xs font-bold text-gray-700">{{ deleteTarget.title }}</p>
        <p v-if="deleteError" class="mt-2 text-xs text-red-500">{{ deleteError }}</p>
        <div class="mt-5 flex justify-end gap-2"><button type="button" class="rounded-lg border px-3 py-2 text-xs font-bold text-gray-600 disabled:opacity-50" :disabled="deleting" @click="closeDeleteConfirm">取消</button><button type="button" class="rounded-lg bg-red-600 px-3 py-2 text-xs font-bold text-white disabled:opacity-60" :disabled="deleting" @click="confirmDeleteNotifications">{{ deleting ? '删除中...' : deleteMode === 'read' ? '确认清空' : '确认删除' }}</button></div>
      </div>
    </div></teleport>
  </div>
</template>

<style scoped>
.notification-detail-body :deep(.markdown-body) {
  overflow-wrap: break-word;
}
.notification-detail-body :deep(h1),
.notification-detail-body :deep(h2),
.notification-detail-body :deep(h3) {
  font-weight: 800;
  margin: 0.85em 0 0.4em;
  color: #1f2937;
}
.notification-detail-body :deep(h1) { font-size: 1.15rem; }
.notification-detail-body :deep(h2) { font-size: 1.05rem; }
.notification-detail-body :deep(h3) { font-size: 0.95rem; }
.notification-detail-body :deep(p) { margin: 0.45em 0; }
.notification-detail-body :deep(ul),
.notification-detail-body :deep(ol) { padding-left: 1.25em; margin: 0.5em 0; }
.notification-detail-body :deep(li) { margin: 0.25em 0; }
.notification-detail-body :deep(blockquote) {
  margin: 0.6em 0;
  padding: 0.4em 0.75em;
  border-left: 3px solid #93c5fd;
  background: #f8fafc;
  color: #475569;
}
.notification-detail-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.75em 0;
  font-size: 12px;
}
.notification-detail-body :deep(th),
.notification-detail-body :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 6px 8px;
  text-align: left;
}
.notification-detail-body :deep(th) {
  background: #f8fafc;
  font-weight: 800;
}
.notification-detail-body :deep(pre) {
  overflow-x: auto;
  border-radius: 10px;
  padding: 10px 12px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
}
.notification-detail-body :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
</style>
