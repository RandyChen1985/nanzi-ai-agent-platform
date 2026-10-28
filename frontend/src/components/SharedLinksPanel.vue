<template>
  <!-- 作为侧栏的 flex 子项占满剩余高度，内部自己滚动（不依赖父级固定高度） -->
  <div class="flex flex-1 min-h-0 flex-col">
    <div class="flex-1 overflow-y-auto custom-scrollbar px-2.5 py-3">
      <!-- 首屏骨架屏：翻页时保留旧列表，避免整块闪白 -->
      <div v-if="loading && items.length === 0" class="space-y-2">
        <div
          v-for="n in 3"
          :key="n"
          class="h-[92px] animate-pulse rounded-xl bg-gray-100 dark:bg-gray-800"
        ></div>
      </div>

      <div v-else-if="errorMessage" class="px-2 py-8 text-center">
        <p class="text-[11.5px] text-red-500">{{ errorMessage }}</p>
        <button class="mt-2 text-[11.5px] font-semibold text-primary" @click="load()">重试</button>
      </div>

      <div v-else-if="items.length === 0" class="px-4 py-10 text-center">
        <div class="mx-auto mb-2 grid h-10 w-10 place-items-center rounded-xl bg-gray-100 text-gray-400 dark:bg-gray-800">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M10 13a4.5 4.5 0 006.4 0l2.2-2.2a4.5 4.5 0 00-6.4-6.4L10.9 5.7" />
            <path d="M14 11a4.5 4.5 0 00-6.4 0l-2.2 2.2a4.5 4.5 0 006.4 6.4l1.3-1.3" />
          </svg>
        </div>
        <p class="text-[12px] font-semibold text-gray-500">还没有分享过会话</p>
        <p class="mt-1 text-[11px] leading-relaxed text-gray-400">
          在「会话历史」里点条目上的分享图标，<br />可选公开或密钥访问、设置有效期。
        </p>
      </div>

      <div v-else class="space-y-2">
        <div
          v-for="item in rows"
          :key="item.share_id"
          class="rounded-xl border bg-white p-3 dark:bg-gray-800/80"
          :class="item.state === 'active' ? 'border-gray-100 dark:border-gray-800' : 'border-dashed border-gray-200 opacity-80'"
        >
          <p
            class="line-clamp-2 text-[12.5px] font-bold leading-snug text-gray-800 dark:text-gray-100"
            :class="{ 'text-gray-400 line-through dark:text-gray-500': item.state !== 'active' }"
          >
            {{ item.title || '未命名会话' }}
          </p>

          <div class="mt-1.5 flex flex-wrap items-center gap-1">
            <span class="rounded-md bg-gray-100 px-1.5 py-0.5 text-[10px] font-bold text-gray-500 dark:bg-gray-700 dark:text-gray-300">
              {{ item.access_mode === 'secret' ? '🔑 密钥' : '🌐 公开' }}
            </span>
            <span class="rounded-md px-1.5 py-0.5 text-[10px] font-bold" :class="statusClass(item.state)">
              {{ statusLabel(item.state) }}
            </span>
          </div>

          <p class="mt-1.5 text-[10.5px] text-gray-500 dark:text-gray-400">
            已打开 <b class="text-gray-700 dark:text-gray-200">{{ item.view_count }}</b> 次
            <template v-if="item.last_viewed_at"> · 最近 {{ relativeTime(item.last_viewed_at) }}</template>
          </p>
          <p class="mt-0.5 text-[10.5px] text-gray-400">
            {{ expiryText(item) }} · {{ formatTime(item.created_at) }} 创建
          </p>

          <!-- 作废：行内二次确认，不弹新窗 -->
          <div
            v-if="confirmingId === item.share_id"
            class="mt-2 flex items-center justify-between gap-2 rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5"
          >
            <span class="text-[11px] font-bold text-red-600">作废后链接立即失效</span>
            <span class="flex gap-1.5">
              <button
                class="rounded-md border border-gray-200 bg-white px-2 py-1 text-[11px] font-semibold text-gray-600"
                @click="confirmingId = ''"
              >取消</button>
              <button
                class="rounded-md border border-red-200 bg-white px-2 py-1 text-[11px] font-bold text-red-600"
                @click="revoke(item.share_id)"
              >确认作废</button>
            </span>
          </div>

          <div v-else class="mt-2 flex gap-1.5">
            <button
              class="rounded-md border border-gray-200 px-2 py-1 text-[11px] font-semibold text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300"
              :disabled="item.state !== 'active'"
              @click="copy(buildShareCopyText({ title: item.title, url: item.url, secret: item.secret }), '分享信息')"
            >复制分享信息</button>
            <button
              v-if="item.access_mode === 'secret'"
              class="rounded-md border border-gray-200 px-2 py-1 text-[11px] font-semibold text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300"
              :disabled="item.state !== 'active' || !item.secret"
              @click="copy(item.secret || '', '密码')"
            >复制密码</button>
            <button
              v-if="item.state === 'active'"
              class="rounded-md border border-red-200 bg-red-50/60 px-2 py-1 text-[11px] font-bold text-red-600 dark:border-red-900/50 dark:bg-red-950/30"
              @click="confirmingId = item.share_id"
            >作废</button>
          </div>
        </div>

        <div v-if="hasMore" class="pt-1 text-center">
          <button class="text-[11.5px] font-semibold text-primary" :disabled="loading" @click="loadMore">
            {{ loading ? '加载中…' : '加载更多' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import {
  listConversationShares,
  revokeConversationShare,
  type AuthHeaders,
  type ShareListItem,
} from "@/api/chatShare";
import { buildShareCopyText } from "@/utils/shareCopy";
import { deriveShareStatus, formatRemaining, type ShareStatus } from "@/utils/shareExpiry";

/**
 * 「我分享的」面板（所有者视角）。
 *
 * 侧栏只负责 tab 切换与事件转发，鉴权头由宿主注入（门户页不传、嵌入页传 Bearer /
 * X-API-Key），请求在这里发起——鉴权只写一套，不在侧栏里再复制一遍。
 */
const props = withDefaults(defineProps<{ authHeaders?: AuthHeaders }>(), {
  authHeaders: undefined,
});

const emit = defineEmits<{
  (e: "toast", message: string, type: "success" | "error" | "info" | "warning"): void;
}>();

const PAGE_SIZE = 20;

const items = ref<ShareListItem[]>([]);
const total = ref(0);
const page = ref(1);
const loading = ref(false);
const errorMessage = ref("");
const confirmingId = ref("");

const hasMore = ref(false);

/** 列表行 + 推导出的展示状态：作废优先于过期。 */
type ShareRow = ShareListItem & { state: ShareStatus };

/**
 * 展示状态用本地时间兜底：服务端的 status 可能还没刷新，
 * 一个已经过期的链接不该继续显示「生效中」。
 */
const rows = computed<ShareRow[]>(() =>
  items.value.map((item) => ({ ...item, state: deriveShareStatus(item) })),
);

function statusLabel(status: ShareStatus): string {
  if (status === "revoked") return "已作废";
  if (status === "expired") return "已过期";
  return "生效中";
}

function statusClass(status: ShareStatus): string {
  if (status === "revoked") return "bg-gray-100 text-gray-400";
  if (status === "expired") return "bg-amber-50 text-amber-700";
  return "bg-emerald-50 text-emerald-700";
}

/** 有效期文案：生效中显示剩余时间；已失效给出具体时刻，永久有效直说。 */
function expiryText(item: ShareRow): string {
  if (item.state === "revoked") {
    return `${formatTime(item.revoked_at || item.created_at)} 作废`;
  }
  if (!item.expires_at) return "永久有效";
  if (item.state === "expired") return `${formatTime(item.expires_at)} 失效`;
  return formatRemaining(item.expires_at);
}

function formatTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function relativeTime(value: string): string {
  const date = new Date(value);
  const diff = Date.now() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

async function load(reset = true): Promise<void> {
  if (loading.value) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    const target = reset ? 1 : page.value + 1;
    const data = await listConversationShares(
      { page: target, page_size: PAGE_SIZE },
      props.authHeaders,
    );
    const incoming = data?.items || [];
    items.value = reset ? incoming : [...items.value, ...incoming];
    total.value = Number(data?.total || 0);
    page.value = target;
    hasMore.value = items.value.length < total.value;
  } catch (error: any) {
    errorMessage.value = String(error?.response?.data?.detail || "加载分享列表失败");
  } finally {
    loading.value = false;
  }
}

function loadMore(): void {
  void load(false);
}

async function copy(text: string, label: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    emit("toast", `${label}已复制`, "success");
  } catch {
    emit("toast", "复制失败，请手动选中复制", "error");
  }
}

async function revoke(shareId: string): Promise<void> {
  confirmingId.value = "";
  try {
    await revokeConversationShare(shareId, props.authHeaders);
    const target = items.value.find((item) => item.share_id === shareId);
    if (target) {
      target.status = "revoked";
      target.revoked_at = new Date().toISOString();
    }
    emit("toast", "分享已作废", "success");
  } catch (error: any) {
    emit("toast", String(error?.response?.data?.detail || "作废失败"), "error");
  }
}

onMounted(() => {
  void load();
});

defineExpose({ reload: () => load() });
</script>
