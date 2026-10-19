# @ 浮层「文件」tab 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让输入框里的 `@` 除了切换专家，还能浏览工作区文件并把选中的文件挂到当前消息上。

**Architecture:** 新增一个自包含的 `WorkspaceFilePicker.vue`（自己请求 `fs/list|search|recent-files`，负责搜索、面包屑、键盘与选中），`MentionList.vue` 只加 tab 壳与键盘分派，`ChatInput.vue` 负责 `@` 检测与附件入列（`pushLocalFile`），`EmbedChat.vue` 的抽屉选中改调同一方法，两条入口共用一份去重逻辑。

**Tech Stack:** Vue 3 `<script setup lang="ts">` + Tailwind 3 + axios；契约测试用 pytest 读源码断言（`pytest --confcutdir=tests/frontend`）。

**规范提醒：** 本项目禁止 Agent 自行 `git commit`。每个任务末尾的提交步骤由用户执行；Agent 只跑命令并汇报结果。

---

## 文件结构

| 文件 | 责任 |
| --- | --- |
| `frontend/src/components/embed/WorkspaceFilePicker.vue`（新增） | 文件 tab 的全部逻辑：请求、搜索防抖、面包屑、键盘、已挂载置灰 |
| `frontend/src/components/agent/MentionList.vue`（修改） | tab 头、`activeTab` 状态、`expertLocked` 提示、把键盘分派给 picker |
| `frontend/src/components/embed/ChatInput.vue`（修改） | `@` 检测（IME 守卫、放开门槛）、`initialTab`、`mountedFilePaths`、`pushLocalFile`、关闭后归还焦点 |
| `frontend/src/views/EmbedChat.vue`（修改） | `handleSelectLocalFs` 改调 `pushLocalFile`；接 `open-workspace` 打开抽屉 |
| `tests/frontend/test_at_file_picker_contract.py`（新增） | 九个契约断言 |
| `tests/CHECKLIST.md`（修改） | 追加交付记录 |

---

### Task 1: 新建 `WorkspaceFilePicker.vue`

**Files:**
- Create: `frontend/src/components/embed/WorkspaceFilePicker.vue`
- Test: `tests/frontend/test_at_file_picker_contract.py`

- [ ] **Step 1: 写失败测试（picker 基础契约）**

```python
"""Contract: @ 浮层的文件 tab（工作区文件选择器）。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PICKER = ROOT / "frontend" / "src" / "components" / "embed" / "WorkspaceFilePicker.vue"
MENTION_LIST = ROOT / "frontend" / "src" / "components" / "agent" / "MentionList.vue"
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "embed" / "ChatInput.vue"
EMBED_CHAT = ROOT / "frontend" / "src" / "views" / "EmbedChat.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_picker_hits_three_fs_endpoints_with_bounded_search():
    text = _read(PICKER)
    assert "/api/v1/chat/fs/list" in text
    assert "/api/v1/chat/fs/search" in text
    assert "/api/v1/chat/fs/recent-files" in text
    assert "max_results" in text, "搜索结果必须有上限，避免一次灌进几百行"
    assert "params" in text


def test_picker_debounces_search_and_drops_stale_responses():
    text = _read(PICKER)
    assert "setTimeout" in text and "clearTimeout" in text, "搜索必须防抖"
    assert "searchSeq" in text, "用自增序号丢弃过期响应"
    assert "seq !== searchSeq" in text


def test_picker_exposes_keyboard_handler():
    text = _read(PICKER)
    assert "handleKeydown" in text
    assert "defineExpose({ handleKeydown })" in text
    for key in ("ArrowUp", "ArrowDown", "Enter", "Escape"):
        assert key in text


def test_picker_marks_mounted_rows_and_blocks_click():
    text = _read(PICKER)
    assert "mountedPaths" in text
    assert "已挂载" in text
    assert "isMounted" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_at_file_picker_contract.py -q`
Expected: FAIL —— `FileNotFoundError`（组件还不存在）

- [ ] **Step 3: 实现组件**

创建 `frontend/src/components/embed/WorkspaceFilePicker.vue`：

```vue
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

export interface LocalFilePayload {
  type: "local_file";
  path: string;
  name: string;
  size: number;
  ext: string;
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
  (e: "select", payload: LocalFilePayload): void;
  (e: "open-workspace"): void;
  (e: "close"): void;
}>();

const FS_BASE = "/api/v1/chat/fs";
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
const isMobileViewport = () => window.innerWidth < 640;

const rows = computed<PickerRow[]>(() => {
  if (isSearching.value) {
    return [...items.value]
      .sort((a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name, "zh"))
      .map((item) => ({
        path: item.path,
        name: item.name,
        isDir: item.is_dir,
        size: item.size,
        section: "search" as const,
      }));
  }
  const dirRows = [...items.value]
    .sort((a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name, "zh"))
    .map((item) => ({
      path: item.path,
      name: item.name,
      isDir: item.is_dir,
      size: item.size,
      section: "dir" as const,
    }));
  const recentRows = breadcrumbs.value.length === 0 && !isSearching.value
    ? recent.value.slice(0, RECENT_LIMIT).map((item) => ({
        path: item.path,
        name: item.name,
        isDir: false,
        size: 0,
        section: "recent" as const,
      }))
    : [];
  return [...recentRows, ...dirRows];
});

const footerLabel = computed(() => {
  if (loading.value) return "加载中…";
  if (isSearching.value) return truncated.value ? "结果过多，仅显示前 30 项" : `搜索到 ${rows.value.length} 项`;
  if (breadcrumbs.value.length === 0 && recent.value.length > 0) {
    return `最近 ${Math.min(recent.value.length, RECENT_LIMIT)} 个文件 · 目录 ${items.value.length} 项`;
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
      axios.get(`${FS_BASE}/recent-files`),
      axios.get(`${FS_BASE}/list`),
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
    const res = await axios.get(`${FS_BASE}/list`, { params: { path } });
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
  // index = -1 表示回到根视图
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
    const res = await axios.get(`${FS_BASE}/search`, {
      params: { q: query, max_results: SEARCH_MAX },
    });
    if (seq !== searchSeq) return; // 过期响应直接丢弃
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

watch(keyword, (value) => {
  if (searchTimer) clearTimeout(searchTimer);
  const query = value.trim();
  if (!query) {
    searchSeq += 1; // 让在途搜索失效
    truncated.value = false;
    items.value = breadcrumbs.value.length === 0
      ? (dirCache.get("") ?? [])
      : (dirCache.get(breadcrumbs.value[breadcrumbs.value.length - 1].path) ?? []);
    activeIndex.value = 0;
    return;
  }
  searchTimer = setTimeout(() => void runSearch(query), SEARCH_DEBOUNCE_MS);
});

const handleReset = () => {
  failed.value = false;
  if (isSearching.value) void runSearch(keyword.value.trim());
  else if (breadcrumbs.value.length > 0) void loadDir(breadcrumbs.value[breadcrumbs.value.length - 1].path);
  else void loadRoot();
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
    const el = listEl.value.querySelector(`[data-picker-index="${activeIndex.value}"]`) as HTMLElement | null;
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

onMounted(() => {
  void loadRoot();
  if (!isMobileViewport()) {
    const query = keyword.value.trim();
    if (query) void runSearch(query);
    nextTick(() => searchInput.value?.focus());
  } else if (keyword.value.trim()) {
    void runSearch(keyword.value.trim());
  }
});

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer);
});

defineExpose({ handleKeydown });
</script>

<template>
  <div class="flex flex-col min-h-0 flex-1">
    <div class="px-2 pt-2 pb-1 shrink-0">
      <input
        ref="searchInput"
        v-model="keyword"
        type="text"
        placeholder="搜索文件名…"
        class="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-primary/40 focus:border-primary/40"
        @keydown="(e) => { if (!handleKeydown(e)) return; e.preventDefault(); }"
      />
    </div>

    <div v-if="breadcrumbs.length > 0 && !isSearching" class="px-2 pb-1 shrink-0 flex items-center gap-0.5 overflow-x-auto no-scrollbar">
      <button type="button" class="text-[10px] text-gray-500 dark:text-gray-400 hover:text-primary shrink-0" @click="goToCrumb(-1)">工作区</button>
      <template v-for="(crumb, index) in breadcrumbs" :key="crumb.path">
        <span class="text-[10px] text-gray-300 dark:text-gray-600 shrink-0">/</span>
        <button
          type="button"
          class="text-[10px] shrink-0 truncate max-w-[6rem]"
          :class="index === breadcrumbs.length - 1 ? 'text-gray-700 dark:text-gray-200 font-medium' : 'text-gray-500 dark:text-gray-400 hover:text-primary'"
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
        <div v-if="rows[0]?.section === 'recent'" class="px-2 pt-0.5 pb-1 text-[9px] font-semibold text-gray-400">最近使用</div>

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
          <span class="shrink-0 text-[13px]">{{ row.isDir ? "📁" : "📄" }}</span>
          <span class="flex-1 min-w-0">
            <span class="block text-[12px] font-medium truncate text-gray-900 dark:text-gray-100">{{ row.name }}</span>
            <span v-if="row.section === 'search'" class="block text-[9px] text-gray-400 truncate">{{ row.path }}</span>
          </span>
          <span v-if="isMounted(row.path)" class="shrink-0 text-[9px] text-emerald-600 dark:text-emerald-400">已挂载</span>
          <span v-else-if="!row.isDir && row.section !== 'recent' && row.size" class="shrink-0 text-[9px] text-gray-400">{{ formatSize(row.size) }}</span>
          <span v-if="row.isDir" class="shrink-0 text-[9px] text-gray-300 dark:text-gray-600">›</span>
        </button>
      </template>
    </div>

    <div class="px-2.5 py-1.5 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between gap-2 shrink-0 bg-gray-50 dark:bg-gray-900/80">
      <span class="text-[10px] text-gray-500 dark:text-gray-400 truncate">{{ footerLabel }}</span>
      <button type="button" class="text-[10px] text-primary hover:underline shrink-0" @click="emit('open-workspace')">打开文件面板 →</button>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 2px; }
</style>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_at_file_picker_contract.py -q`
Expected: 4 passed（其余断言在后续任务补）

- [ ] **Step 5: 提交（由用户执行）**

```bash
git add frontend/src/components/embed/WorkspaceFilePicker.vue tests/frontend/test_at_file_picker_contract.py
git commit -m "feat(chat): 新增工作区文件选择器（@ 文件 tab 的数据与交互层）"
```

---

### Task 2: `MentionList` 加 tab 头并集成 picker

**Files:**
- Modify: `frontend/src/components/agent/MentionList.vue:1-190`（script）、`:160-260`（template）
- Test: `tests/frontend/test_at_file_picker_contract.py`

- [ ] **Step 1: 追加失败测试**

在 `test_at_file_picker_contract.py` 末尾追加：

```python
def test_mention_list_switches_between_expert_and_file_tabs():
    text = _read(MENTION_LIST)
    assert "activeTab" in text, "tab 状态必须由 MentionList 持有"
    assert "'expert'" in text and "'file'" in text
    assert "initialTab" in text
    assert "WorkspaceFilePicker" in text
    assert "select-file" in text
    assert "open-workspace" in text
    assert "专家" in text and "文件" in text


def test_mention_list_resets_tab_when_reopened():
    text = _read(MENTION_LIST)
    assert "watch(" in text
    assert "activeTab.value = props.initialTab" in text, "每次 @ 重新打开都应回到 initialTab，不能记住上次停留"


def test_mention_list_routes_keyboard_to_active_tab():
    text = _read(MENTION_LIST)
    assert "activeTab.value === 'file'" in text
    assert "pickerRef" in text


def test_mention_list_shows_locked_hint_instead_of_experts():
    text = _read(MENTION_LIST)
    assert "expertLocked" in text
    assert "已锁定指定智能体" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_at_file_picker_contract.py -q`
Expected: 新增 4 项 FAIL（`activeTab` 等尚未出现）

- [ ] **Step 3: 改 `MentionList.vue`**

script 顶部 import 与 props/emits 扩展：

```ts
import WorkspaceFilePicker from "@/components/embed/WorkspaceFilePicker.vue";

const props = withDefaults(
  defineProps<{
    visible: boolean;
    keyword: string;
    agents: AgentOption[];
    position: { top: number; left: number };
    routingMode?: string;
    expertAgentId?: string;
    /** 每次打开时停留的 tab；没有可切换专家时由父组件传 'file' */
    initialTab?: "expert" | "file";
    /** 已挂到输入框的文件路径，用于置灰 */
    mountedFilePaths?: string[];
    /** 链接锁定了指定智能体时，专家 tab 只显示提示 */
    expertLocked?: boolean;
  }>(),
  {
    routingMode: "auto",
    expertAgentId: "",
    initialTab: "expert",
    mountedFilePaths: () => [],
    expertLocked: false,
  },
);

const emit = defineEmits<{
  (e: "select", agent: AgentOption): void;
  (e: "select-auto"): void;
  (e: "close"): void;
  (e: "select-file", payload: {
    type: "local_file"; path: string; name: string; size: number; ext: string;
  }): void;
  (e: "open-workspace"): void;
}>();

const activeTab = ref<"expert" | "file">(props.initialTab);
const pickerRef = ref<any>(null);

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      activeTab.value = props.initialTab;
      selectedIndex.value = 0;
    }
  },
);
```

键盘分派（保留原有 `defineExpose({ handleKeydown })`，现有契约测试依赖它）：

```ts
const handleKeydown = (e: KeyboardEvent) => {
  if (!props.visible) return false;
  if (activeTab.value === "file") {
    return pickerRef.value?.handleKeydown?.(e) ?? false;
  }
  if (rows.value.length === 0) return false;
  // …原有 ArrowDown / ArrowUp / Enter / Tab / Escape 逻辑保持不变
};
```

template 头部加 tab 条（替换现有标题行）：

```vue
<div class="px-2.5 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between shrink-0 bg-white dark:bg-gray-800">
  <div class="flex items-center gap-1.5 min-w-0">
    <span class="w-1 h-3.5 bg-primary rounded-full shrink-0" />
    <span class="text-xs font-semibold text-gray-900 dark:text-gray-100 truncate">选择专家</span>
    <div class="flex items-center gap-0.5 ml-1 p-0.5 rounded-lg bg-gray-100 dark:bg-gray-700/60">
      <button
        type="button"
        class="px-1.5 py-0.5 text-[10px] rounded-md transition-colors"
        :class="activeTab === 'expert' ? 'bg-white dark:bg-gray-800 text-primary shadow-sm' : 'text-gray-500 dark:text-gray-400'"
        @click="activeTab = 'expert'"
      >专家</button>
      <button
        type="button"
        class="px-1.5 py-0.5 text-[10px] rounded-md transition-colors"
        :class="activeTab === 'file' ? 'bg-white dark:bg-gray-800 text-primary shadow-sm' : 'text-gray-500 dark:text-gray-400'"
        @click="activeTab = 'file'"
      >文件</button>
    </div>
  </div>
  <span class="text-[9px] text-gray-400 hidden sm:inline shrink-0">
    {{ activeTab === 'expert' ? 'Enter 选择 · Esc 关闭' : 'Esc 关闭' }}
  </span>
</div>
```

列表区按 tab 二选一（专家 tab 用 `v-show` 保留既有 DOM 与样式断言）：

```vue
<div v-show="activeTab === 'expert'" ref="listContainer" class="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-1.5 space-y-0.5 bg-white dark:bg-gray-800">
  <!-- 原有内容整体保留，头部插入锁定提示 -->
  <div v-if="expertLocked" class="px-2.5 py-6 text-center text-[11px] text-gray-500 dark:text-gray-400">
    当前链接已锁定指定智能体，无法切换其他专家
  </div>
  <template v-else>
    <!-- 原有 rows 渲染 -->
  </template>
</div>

<WorkspaceFilePicker
  v-if="activeTab === 'file'"
  ref="pickerRef"
  :initial-keyword="keyword"
  :mounted-paths="mountedFilePaths"
  @select="(payload) => emit('select-file', payload)"
  @open-workspace="emit('open-workspace')"
  @close="emit('close')"
/>
```

底部条按 tab 切换文案：

```vue
<div class="px-2.5 py-1.5 border-t border-gray-200 dark:border-gray-700 text-center shrink-0 bg-gray-50 dark:bg-gray-900/80">
  <span v-if="activeTab === 'expert'" class="text-[10px] text-gray-500 dark:text-gray-400">共 {{ filteredAgents.length }} 个专家</span>
  <span v-else class="text-[10px] text-gray-400">工作区文件</span>
</div>
```

注意：`WorkspaceFilePicker` 自己已带底部条（含计数与「打开文件面板」），所以文件 tab 下应隐藏 MentionList 的底部条，改为 `v-if="activeTab === 'expert'"`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_at_file_picker_contract.py tests/frontend/test_mention_list_keyboard_scroll_contract.py tests/frontend/test_smart_delegation_copy_contract.py tests/frontend/test_embed_chat_agent_avatar_contract.py -q`
Expected: 全部 passed（既有 4 个契约文件不得被改坏）

- [ ] **Step 5: 提交（由用户执行）**

```bash
git add frontend/src/components/agent/MentionList.vue tests/frontend/test_at_file_picker_contract.py
git commit -m "feat(chat): @ 浮层增加专家/文件两个 tab"
```

---

### Task 3: `ChatInput` 接入（检测、初始 tab、挂载方法）

**Files:**
- Modify: `frontend/src/components/embed/ChatInput.vue:537-540`（状态）、`:758-800`（`handleInput`）、`:3656-3668`（`<MentionList>`）、`:1860`（`defineExpose` 区块）
- Test: `tests/frontend/test_at_file_picker_contract.py`

- [ ] **Step 1: 追加失败测试**

```python
def test_chat_input_mention_detection_guards_ime_and_drops_expert_gate():
    text = _read(CHAT_INPUT)
    assert "!isComposing.value" in text, "@ 检测必须避开输入法组合态"
    assert "mentionInitialTab" in text
    # 旧门槛：@ 只在「未锁定专家且有可切换专家」时才响应，现在文件 tab 不受此限
    assert "atMatch && !props.lockExpertAgent" not in text
    assert "isStartOfWord && props.allowedAgents.length > 0" not in text


def test_chat_input_exposes_push_local_file():
    text = _read(CHAT_INPUT)
    assert "pushLocalFile" in text
    assert "local_file" in text
    assert "mountedFilePaths" in text


def test_chat_input_returns_focus_after_closing_mention_list():
    text = _read(CHAT_INPUT)
    assert "handleMentionClose" in text
    assert "inputRef.value?.focus()" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_at_file_picker_contract.py -q`
Expected: 新增 3 项 FAIL

- [ ] **Step 3: 改 `ChatInput.vue`**

新增状态：

```ts
const mentionInitialTab = ref<"expert" | "file">("expert");
```

改写 `handleInput` 里的 `@` 分支（现在的两层门全部放开，只保留词首判断与 IME 守卫）：

```ts
  const atMatch = val.slice(0, cursor).match(/[@＠]([^\s@＠]*)$/);
  if (atMatch && !isComposing.value) {
    const atIndex = val.slice(0, cursor).lastIndexOf(atMatch[0][0] || "@");
    const isStartOfWord = atIndex === 0 || val[atIndex - 1] === " " || val[atIndex - 1] === "\n";
    if (isStartOfWord) {
      mentionKeyword.value = atMatch[1] || "";
      // 没有可切换的专家时直接落在文件 tab，避免弹出一个只有空列表的壳
      mentionInitialTab.value = props.allowedAgents.length > 0 ? "expert" : "file";
      showMentionList.value = true;
      const rect = target.getBoundingClientRect();
      mentionPosition.top = rect.top - 220;
      mentionPosition.left = rect.left + 20;
      return;
    }
  }
  showMentionList.value = false;
```

新增挂载方法（放在 `handleMentionSelectAuto` 之后）：

```ts
/** 把一个「服务器本地文件」挂到输入框附件区；抽屉与 @ 文件 tab 共用这一处去重。 */
const pushLocalFile = (payload: {
  type: "local_file" | "local_dir";
  path: string;
  name: string;
  size: number;
  ext: string;
}) => {
  if (!payload?.path) return false;
  const exists = uploadedFiles.value.some(
    (file: any) => file.type === payload.type && file.url === payload.path,
  );
  if (exists) return false;
  uploadedFiles.value.push({
    type: payload.type,
    url: payload.path,
    filename: payload.name,
    size: payload.size,
    ext: payload.ext,
  });
  return true;
};

const mountedFilePaths = computed(() =>
  uploadedFiles.value
    .filter((file: any) => file.type === "local_file" || file.type === "local_dir")
    .map((file: any) => String(file.url || "")),
);

const handleMentionFileSelect = (payload: {
  type: "local_file";
  path: string;
  name: string;
  size: number;
  ext: string;
}) => {
  clearMentionTrigger();
  pushLocalFile(payload);
  showMentionList.value = false;
  nextTick(() => inputRef.value?.focus());
};

const handleMentionClose = () => {
  showMentionList.value = false;
  nextTick(() => inputRef.value?.focus());
};

const handleOpenWorkspaceFromMention = () => {
  showMentionList.value = false;
  emit("open-workspace");
};
```

`defineExpose` 增加三个成员（现在的 expose 在 `:1860` 附近）：

```ts
defineExpose({
  uploadedFiles,
  pushLocalFile,
  mountedFilePaths,
});
```

模板 `<MentionList>` 增加 props 与事件：

```vue
<MentionList
  ref="mentionListRef"
  :visible="showMentionList"
  :keyword="mentionKeyword"
  :agents="allowedAgents"
  :position="mentionPosition"
  :routing-mode="routingMode"
  :expert-agent-id="expertAgentId"
  :initial-tab="mentionInitialTab"
  :mounted-file-paths="mountedFilePaths"
  :expert-locked="lockExpertAgent"
  @select="handleMentionSelect"
  @select-auto="handleMentionSelectAuto"
  @select-file="handleMentionFileSelect"
  @open-workspace="handleOpenWorkspaceFromMention"
  @close="handleMentionClose"
/>
```

`emits` 增加 `(e: 'open-workspace'): void;`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_at_file_picker_contract.py tests/frontend/test_chat_surface_extraction_contract.py tests/frontend/test_chat_input_icon_contract.py -q`
Expected: 全部 passed

- [ ] **Step 5: 提交（由用户执行）**

```bash
git add frontend/src/components/embed/ChatInput.vue tests/frontend/test_at_file_picker_contract.py
git commit -m "feat(chat): @ 可直接选工作区文件并挂到输入框"
```

---

### Task 4: `EmbedChat` 统一挂载链路并接 `open-workspace`

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue:3325-3338`（`handleSelectLocalFs`）、`:1497-1552`（`<ChatInput>` 绑定）、`:1712-1726`（抽屉）
- Test: `tests/frontend/test_at_file_picker_contract.py`

- [ ] **Step 1: 追加失败测试**

```python
def test_embed_chat_reuses_push_local_file_for_drawer_selection():
    text = _read(EMBED_CHAT)
    assert "chatInputRef.value.pushLocalFile" in text, "抽屉选中必须走同一个入列方法，避免两份去重逻辑"
    assert "chatInputRef.value.uploadedFiles.push" not in text


def test_embed_chat_opens_workspace_drawer_from_mention():
    text = _read(EMBED_CHAT)
    assert "@open-workspace" in text
    assert "showWorkspaceDrawer.value = true" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_at_file_picker_contract.py -q`
Expected: 新增 2 项 FAIL

- [ ] **Step 3: 改 `EmbedChat.vue`**

`handleSelectLocalFs` 收敛为一行委托：

```ts
const handleSelectLocalFs = (payload: { type: 'local_file' | 'local_dir'; path: string; name: string; size: number; ext: string }) => {
  chatInputRef.value?.pushLocalFile?.(payload);
};
```

`<ChatInput>` 绑定处新增：

```vue
@open-workspace="showWorkspaceDrawer = true"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_at_file_picker_contract.py tests/frontend/test_workspace_browser_view_contract.py tests/frontend/test_chat_sandbox_workspace_contract.py -q`
Expected: 全部 passed

- [ ] **Step 5: 提交（由用户执行）**

```bash
git add frontend/src/views/EmbedChat.vue tests/frontend/test_at_file_picker_contract.py
git commit -m "refactor(chat): 抽屉与 @ 文件 tab 共用同一个附件入列方法"
```

---

### Task 5: 全量回归与清单更新

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 跑前端契约测试全量**

Run: `pytest --confcutdir=tests/frontend tests/frontend -q`
Expected: 全绿（历史基线约 2205 passed / 1 skipped，本次 +11 项）

- [ ] **Step 2: 跑类型检查**

Run: `cd frontend && node --max-old-space-size=8192 ./node_modules/.bin/vue-tsc --noEmit`
Expected: exit 0，0 错误（注意：`-p tsconfig.app.json` 会报 82 条既有无关错误，不要用那个口径）

- [ ] **Step 3: 更新 `tests/CHECKLIST.md`**

在表格表头下追加一行：

```markdown
| @ 浮层新增「文件」tab：从工作区选文件挂载到输入框 (At File Picker) | `frontend/src/components/embed/WorkspaceFilePicker.vue`（新增）、`frontend/src/components/agent/MentionList.vue`、`frontend/src/components/embed/ChatInput.vue`、`frontend/src/views/EmbedChat.vue`、`tests/frontend/test_at_file_picker_contract.py`（新增）、`tests/CHECKLIST.md` | **用户诉求（原文）**：「我想 @ 后，再支持 文件浏览指定到文件……这个浮层加个 tab？保留现在智能体，加个文件浏览的 tab，然后方便浏览用户工作区的文件，选定文件后挂载到输入框」。**已有能力**：抽屉 `WorkspaceBrowserDrawer.vue` 与 `handleSelectLocalFs` 已能把文件挂成 `local_file` 附件，后端 `/chat/fs/list\|search\|recent-files` 也已限定在公共目录与本人 `agent_workspaces`；缺的只是把入口搬进 `@`。**实现**：① 新增自包含的 `WorkspaceFilePicker.vue`（搜索防抖 250ms、自增序号丢弃过期响应、目录结果按 path 缓存、面包屑由下钻栈生成不暴露绝对路径、已挂载置灰）；② `MentionList` 加「专家 / 文件」tab 头，`visible` 变 true 时重置到 `initialTab`，键盘按 tab 分派；③ `ChatInput` 的 `@` 检测放开「必须未锁定专家且有可切换专家」两层门并补 `!isComposing.value` 守卫（此前中文输入法打全角 `＠` 会提前弹层），锁定链接下专家 tab 改为提示、文件 tab 照常；④ 挂载链路收敛到 `ChatInput.pushLocalFile`，抽屉选中改为调用它。 | ✅ 新增 11 项契约测试；`tests/frontend` 全量通过；`vue-tsc --noEmit` 0 错误。未做真机实测，交互效果由用户重启前端后确认。 | 2026-10-19 |
```

- [ ] **Step 4: 提交（由用户执行）**

```bash
git add tests/CHECKLIST.md
git commit -m "docs(checklist): 记录 @ 文件 tab 的交付与测试"
```

---

## 自检记录

**规格覆盖**：tab 结构（Task 2）、文件三视图与请求（Task 1）、选中与去重（Task 1+3）、焦点键盘（Task 1+2+3）、移动端不自动聚焦（Task 1）、锁定链接（Task 2+3）、`@` 检测与 IME（Task 3）、挂载链路统一（Task 3+4）、清单（Task 5）。

**类型一致性**：`pushLocalFile` 在 Task 3 定义、Task 4 调用；`mountedFilePaths`（ChatInput）→ `mountedFilePaths`（MentionList prop）→ `mountedPaths`（picker prop）；`ActiveTab` 字面量 `'expert' | 'file'` 全程一致；picker 的 `InitialKeyword` 由 MentionList 的 `keyword` 透传。

**已知取舍**：picker 只选文件、不挂载目录（目录点击 = 进入）；`local_dir` 仍只从抽屉产生。
