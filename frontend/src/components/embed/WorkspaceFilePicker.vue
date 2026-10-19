<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import axios from "@/utils/axios";

interface FileItem {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  mtime: number;
  is_public?: boolean;
  readonly?: boolean;
}

interface RecentItem {
  path: string;
  name: string;
  mtime: number;
}

interface PickerRow {
  path: string;
  name: string;
  isDir: boolean;
  size: number;
  section: "recent" | "dir" | "search";
}

const props = withDefaults(
  defineProps<{
    /** 切到文件 tab 时 @ 后面的词，作为搜索框初值并触发一次搜索 */
    initialKeyword?: string;
    /** 已挂到输入框的本地路径，用于置灰 */
    mountedPaths?: string[];
  }>(),
  { initialKeyword: "", mountedPaths: () => [] },
);

const emit = defineEmits<{
  (
    e: "select",
    payload: { type: "local_file"; path: string; name: string; size: number; ext: string },
  ): void;
  (e: "open-workspace"): void;
  (e: "close"): void;
  /** 搜索框把左右键上抛：由浮层决定切 tab，picker 只负责判断能不能让出按键 */
  (e: "switch-tab", direction: "left" | "right"): void;
}>();

const RECENT_LIMIT = 5;
const SEARCH_MAX = 30;
const SEARCH_DEBOUNCE_MS = 250;

const keyword = ref(props.initialKeyword);
const loading = ref(false);
const failed = ref(false);
const truncated = ref(false);
const items = ref<FileItem[]>([]);
const recent = ref<RecentItem[]>([]);
const activeIndex = ref(0);
const breadcrumbs = ref<{ name: string; path: string }[]>([]);
const searchInput = ref<HTMLInputElement | null>(null);
const listEl = ref<HTMLElement | null>(null);

const dirCache = new Map<string, FileItem[]>();
let searchTimer: ReturnType<typeof setTimeout> | null = null;
let searchSeq = 0;

const isSearching = computed(() => keyword.value.trim().length > 0);
/** 搜索框空着时左右键没有「移动光标」的意义，可以让给浮层切 tab */
const canLeaveByArrow = computed(() => keyword.value.trim().length === 0);
const isMobileViewport = () => window.innerWidth < 640;

const sortItems = (list: FileItem[]) =>
  [...list].sort(
    (a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name, "zh"),
  );

const rows = computed<PickerRow[]>(() => {
  if (isSearching.value) {
    return sortItems(items.value).map((item) => ({
      path: item.path,
      name: item.name,
      isDir: item.is_dir,
      size: item.size,
      section: "search" as const,
    }));
  }
  const recentRows: PickerRow[] =
    breadcrumbs.value.length === 0
      ? recent.value.slice(0, RECENT_LIMIT).map((item) => ({
          path: item.path,
          name: item.name,
          isDir: false,
          size: 0,
          section: "recent" as const,
        }))
      : [];
  const dirRows: PickerRow[] = sortItems(items.value).map((item) => ({
    path: item.path,
    name: item.name,
    isDir: item.is_dir,
    size: item.size,
    section: "dir" as const,
  }));
  return [...recentRows, ...dirRows];
});

const footerLabel = computed(() => {
  if (loading.value) return "加载中…";
  if (isSearching.value) {
    return truncated.value ? "结果过多，仅显示前 30 项" : `搜索到 ${rows.value.length} 项`;
  }
  if (breadcrumbs.value.length === 0 && recent.value.length > 0) {
    return `最近 ${Math.min(recent.value.length, RECENT_LIMIT)} 个 · 目录 ${items.value.length} 项`;
  }
  return `目录 ${items.value.length} 项`;
});

const isMounted = (path: string) => props.mountedPaths.includes(path);

const extOf = (name: string) => {
  const idx = name.lastIndexOf(".");
  return idx > 0 ? name.slice(idx).toLowerCase() : "";
};

const formatSize = (bytes: number) => {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
};

const loadRoot = async () => {
  loading.value = true;
  failed.value = false;
  try {
    const [recentRes, listRes] = await Promise.all([
      axios.get("/api/v1/chat/fs/recent-files"),
      axios.get("/api/v1/chat/fs/list"),
    ]);
    recent.value = recentRes.data?.data?.items ?? [];
    items.value = listRes.data?.data?.items ?? [];
    dirCache.set("", items.value);
    activeIndex.value = 0;
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
};

const loadDir = async (path: string) => {
  const cached = dirCache.get(path);
  if (cached) {
    items.value = cached;
    activeIndex.value = 0;
    return;
  }
  loading.value = true;
  failed.value = false;
  try {
    const res = await axios.get("/api/v1/chat/fs/list", { params: { path } });
    items.value = res.data?.data?.items ?? [];
    dirCache.set(path, items.value);
    activeIndex.value = 0;
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
};

const enterDir = (row: PickerRow) => {
  breadcrumbs.value = [...breadcrumbs.value, { name: row.name, path: row.path }];
  keyword.value = "";
  void loadDir(row.path);
};

const goToCrumb = (index: number) => {
  // index < 0 表示回到根视图（虚拟根：公共目录 + 本人工作区）
  breadcrumbs.value = index < 0 ? [] : breadcrumbs.value.slice(0, index + 1);
  keyword.value = "";
  if (index < 0) {
    items.value = dirCache.get("") ?? [];
    activeIndex.value = 0;
    return;
  }
  void loadDir(breadcrumbs.value[index].path);
};

const runSearch = async (query: string) => {
  const seq = ++searchSeq;
  loading.value = true;
  failed.value = false;
  try {
    const res = await axios.get("/api/v1/chat/fs/search", {
      params: { q: query, max_results: SEARCH_MAX },
    });
    if (seq !== searchSeq) return; // 过期响应直接丢弃，避免旧结果盖掉新结果
    items.value = res.data?.data?.items ?? [];
    truncated.value = res.data?.data?.truncated === true;
    activeIndex.value = 0;
  } catch {
    if (seq !== searchSeq) return;
    failed.value = true;
  } finally {
    if (seq === searchSeq) loading.value = false;
  }
};

const currentDirItems = () =>
  breadcrumbs.value.length === 0
    ? (dirCache.get("") ?? [])
    : (dirCache.get(breadcrumbs.value[breadcrumbs.value.length - 1].path) ?? []);

watch(keyword, (value) => {
  if (searchTimer) clearTimeout(searchTimer);
  const query = value.trim();
  if (!query) {
    searchSeq += 1; // 让在途搜索失效，回到目录视图
    truncated.value = false;
    items.value = currentDirItems();
    activeIndex.value = 0;
    return;
  }
  searchTimer = setTimeout(() => void runSearch(query), SEARCH_DEBOUNCE_MS);
});

const handleReset = () => {
  failed.value = false;
  if (isSearching.value) {
    void runSearch(keyword.value.trim());
    return;
  }
  if (breadcrumbs.value.length > 0) {
    void loadDir(breadcrumbs.value[breadcrumbs.value.length - 1].path);
    return;
  }
  void loadRoot();
};

const pickRow = (row: PickerRow) => {
  if (row.isDir) {
    enterDir(row);
    return;
  }
  if (isMounted(row.path)) return;
  emit("select", {
    type: "local_file",
    path: row.path,
    name: row.name,
    size: row.size,
    ext: extOf(row.name),
  });
};

const scrollActiveIntoView = () => {
  nextTick(() => {
    if (!listEl.value) return;
    const el = listEl.value.querySelector(
      `[data-picker-index="${activeIndex.value}"]`,
    ) as HTMLElement | null;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ block: "nearest" });
    }
  });
};

const handleKeydown = (e: KeyboardEvent): boolean => {
  if (e.key === "Escape") {
    emit("close");
    return true;
  }
  if (!rows.value.length) return false;
  if (e.key === "ArrowDown") {
    activeIndex.value = (activeIndex.value + 1) % rows.value.length;
    scrollActiveIntoView();
    return true;
  }
  if (e.key === "ArrowUp") {
    activeIndex.value = (activeIndex.value - 1 + rows.value.length) % rows.value.length;
    scrollActiveIntoView();
    return true;
  }
  if (e.key === "Enter" || e.key === "Tab") {
    const row = rows.value[activeIndex.value];
    if (row) pickRow(row);
    return true;
  }
  return false;
};

const focusSearch = () => {
  if (isMobileViewport()) return;
  nextTick(() => searchInput.value?.focus());
};

const onSearchKeydown = (e: KeyboardEvent) => {
  if (handleKeydown(e)) {
    e.preventDefault();
    return;
  }
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  // 搜索框有字时左右键留给光标，需要强制切走就带 ⌘/Ctrl/Alt
  const force = e.metaKey || e.ctrlKey || e.altKey;
  if (!force && !canLeaveByArrow.value) return;
  e.preventDefault();
  emit("switch-tab", e.key === "ArrowLeft" ? "left" : "right");
};

onMounted(() => {
  void loadRoot();
  const query = keyword.value.trim();
  if (query) void runSearch(query);
  focusSearch();
});

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer);
});

defineExpose({ handleKeydown, canLeaveByArrow, focusSearch });
</script>

<template>
  <div class="flex flex-col min-h-0 flex-1">
    <div class="px-2 pt-2 pb-1 shrink-0">
      <input
        ref="searchInput"
        v-model="keyword"
        type="search"
        placeholder="搜索文件名…"
        class="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-primary/40 focus:border-primary/40"
        @keydown="onSearchKeydown"
      />
    </div>

    <div
      v-if="breadcrumbs.length > 0 && !isSearching"
      class="px-2 pb-1 shrink-0 flex items-center gap-0.5 overflow-x-auto no-scrollbar"
    >
      <button
        type="button"
        class="text-[10px] text-gray-500 dark:text-gray-400 hover:text-primary shrink-0"
        @click="goToCrumb(-1)"
      >工作区</button>
      <template v-for="(crumb, index) in breadcrumbs" :key="crumb.path">
        <span class="text-[10px] text-gray-300 dark:text-gray-600 shrink-0">/</span>
        <button
          type="button"
          class="text-[10px] shrink-0 truncate max-w-[6rem]"
          :class="index === breadcrumbs.length - 1
            ? 'text-gray-700 dark:text-gray-200 font-medium'
            : 'text-gray-500 dark:text-gray-400 hover:text-primary'"
          :title="crumb.name"
          @click="goToCrumb(index)"
        >{{ crumb.name }}</button>
      </template>
    </div>

    <div ref="listEl" class="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-1.5 space-y-0.5">
      <div v-if="loading && !rows.length" class="px-2 py-6 text-center text-[11px] text-gray-400">加载中…</div>

      <button
        v-else-if="failed"
        type="button"
        class="w-full px-2 py-6 text-center text-[11px] text-rose-500 hover:underline"
        @click="handleReset"
      >加载失败，点击重试</button>

      <div v-else-if="!rows.length" class="px-2 py-6 text-center text-[11px] text-gray-400">
        <template v-if="isSearching">没有匹配「{{ keyword.trim() }}」的文件</template>
        <template v-else>工作区还没有文件</template>
      </div>

      <template v-else>
        <div
          v-if="rows[0]?.section === 'recent'"
          class="px-2 pt-0.5 pb-1 text-[9px] font-semibold text-gray-400"
        >最近使用</div>

        <button
          v-for="(row, index) in rows"
          :key="`${row.section}_${row.path}`"
          type="button"
          :data-picker-index="index"
          class="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-left transition-all"
          :class="[
            index === activeIndex
              ? 'bg-primary/10 border-primary/30 ring-1 ring-primary/20 dark:bg-primary/20 dark:border-primary/40'
              : 'border-transparent hover:bg-gray-50 dark:hover:bg-gray-700/60',
            isMounted(row.path) ? 'opacity-50 cursor-default' : 'cursor-pointer',
          ]"
          :title="row.path"
          @mouseenter="activeIndex = index"
          @click="pickRow(row)"
        >
          <span v-if="row.isDir" class="shrink-0 text-amber-500">
            <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path d="M2 5.5A1.5 1.5 0 013.5 4h3.6l1.4 1.6h8A1.5 1.5 0 0118 7.1v7.4A1.5 1.5 0 0116.5 16h-13A1.5 1.5 0 012 14.5v-9z" />
            </svg>
          </span>
          <span v-else class="shrink-0 text-slate-400 dark:text-slate-500">
            <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path d="M5 2.5A1.5 1.5 0 016.5 1h4.8L17 6.7v10.8A1.5 1.5 0 0115.5 19h-9A1.5 1.5 0 015 17.5v-15z" />
            </svg>
          </span>
          <span class="flex-1 min-w-0">
            <span
              class="block text-[12px] font-medium truncate text-gray-900 dark:text-gray-100"
            >{{ row.name }}</span>
            <span
              v-if="row.section === 'search'"
              class="block text-[9px] text-gray-400 truncate"
              :title="row.path"
            >{{ row.path }}</span>
          </span>
          <span
            v-if="isMounted(row.path)"
            class="shrink-0 text-[9px] text-emerald-600 dark:text-emerald-400"
          >已挂载</span>
          <span
            v-else-if="!row.isDir && row.section !== 'recent' && row.size"
            class="shrink-0 text-[9px] text-gray-400"
          >{{ formatSize(row.size) }}</span>
          <span v-if="row.isDir" class="shrink-0 text-[10px] text-gray-300 dark:text-gray-600">›</span>
        </button>
      </template>
    </div>

    <div
      class="px-2.5 py-1.5 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between gap-2 shrink-0 bg-gray-50 dark:bg-gray-900/80"
    >
      <span class="text-[10px] text-gray-500 dark:text-gray-400 truncate">{{ footerLabel }}</span>
      <button
        type="button"
        class="text-[10px] text-primary hover:underline shrink-0"
        @click="emit('open-workspace')"
      >打开文件面板 →</button>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 2px; }
</style>
