<!--
  工作空间（独立页）。

  与聊天页右侧「浏览工作空间」抽屉共用同一套文件接口（`/api/v1/chat/fs/*`）与工具函数
  （`utils/workspaceFilePreview`、`useWorkspaceUploadQueue`、`utils/workspaceBatchSelection`）。能力对齐清单：

  - 浏览：面包屑 / 返回上级 / 快捷导航 / 最近文件（读写同步） / 列表-卡片切换 / 排序 / 类型筛选
  - 落点：默认直接进入本人的「我的目录」（后端 `prefer=user_root`），公共目录走「公共资源」入口
  - 搜索：默认过滤当前目录，输入 ≥2 字可切「全目录」走服务端 `/fs/search`
  - 文件：画布浮层预览（复用 `ChatCanvas`）/ 下载 / 分享（公共文件支持复制并分享）/ 重命名 / 复制路径 / 解压到指定目录 / 删除（移入回收站）
  - 目录：打开 / 新建文件夹 / 新建文件 / 上传（点选 + 拖拽，支持取消/重试与进度展示）
  - 回收站：还原 / 永久删除 / 清空
  - 批量：多选 / 半选与全选 / 批量删除（支持当前目录与全目录搜索结果）

  刻意不提供（与抽屉的差异）：
  1. 「挂载到会话」——需要目标会话，只属于聊天；
  2. 「本会话目录」快捷入口——同上；
  3. 钉住 / 拖拽宽度——独立页以浮层预览，避免与主工作区争抢宽度。

  也没有直接复用抽屉组件：它是 teleport 到 body 的抽屉，带拖拽宽度与 pinned 停靠，
  和聊天布局强耦合，强行页面化会破坏现有功能（抽屉文件保持零改动）。
-->
<template>
  <!-- 宽度口径与知识库一致：两边都只由 Dashboard 的 main 给内外边距，页面自身铺满。
       之前这里带着 `max-w-[1200px]`，在 1440+ 的宽屏上右侧会空出一大片，
       和知识库页并排看就是「一个铺满一个缩着」。 -->
  <div
    class="relative flex h-full w-full flex-col gap-3"
    @dragenter.prevent="onDragEnter"
    @dragover.prevent="onDragOver"
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <!-- 拖拽上传高亮 -->
    <div
      v-if="dragging"
      class="pointer-events-none absolute inset-2 z-20 flex items-center justify-center rounded-2xl border-2 border-dashed border-primary bg-primary/5"
    >
      <p class="text-sm font-semibold text-primary">松开即可上传到「{{ currentLabel }}」</p>
    </div>

    <!-- 标题卡：沿用知识库/数据源的 hero 配方（右上淡蓝光晕 + 底部剪影 + 两端渐隐） -->
    <!-- 不写 overflow-hidden：HeroSilhouette 自己处理底部圆角裁切，
         父级一裁就会切断「最近 / 新建」这两个下拉（面板看起来像被削平）。
         知识库、数据源的同类卡片也都没有这层裁切。 -->
      <div class="hero-surface relative z-20 rounded-2xl border border-gray-200/80 p-4 shadow-sm sm:p-5">
      <HeroSilhouette variant="cards" :height="44" />
      <div class="relative z-10 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <h1 class="text-xl font-bold tracking-tight text-gray-900 dark:text-gray-100 sm:text-2xl">工作空间</h1>
            <button
              v-if="usageStats"
              type="button"
              class="inline-flex items-center gap-1 rounded-full border border-gray-200/80 bg-white/80 px-2.5 py-0.5 text-[11px] text-gray-500 transition-colors hover:border-primary/40 hover:text-primary dark:border-gray-700 dark:bg-gray-800/80 dark:text-gray-400"
              :title="`已用 ${formatSize(usageStats.total_bytes)}（${usageStats.file_count} 个文件），回收站 ${formatSize(usageStats.trash_bytes)} · 点击刷新统计`"
              @click="refreshUsage(true)"
            >
              <span>已用 {{ usageStats.total_bytes > 0 ? formatSize(usageStats.total_bytes) : "0 B" }}</span>
              <span class="text-gray-300 dark:text-gray-600">·</span>
              <span>{{ usageStats.file_count }} 文件</span>
            </button>
          </div>
          <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
            AI 干活时的文件都放这里：上传的资料、生成的报告、会话中间产物，都能直接打开看。
          </p>
        </div>

        <div class="flex flex-shrink-0 items-center gap-2">
          <!-- 手动刷新 -->
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-sm text-gray-700 transition-colors hover:border-gray-300 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
            :disabled="loading || searching"
            title="刷新当前目录"
            @click="refreshCurrentView(true)"
          >
            <ArrowPathIcon class="h-4 w-4" :class="loading || searching ? 'animate-spin' : ''" aria-hidden="true" />
            <span class="hidden sm:inline">刷新</span>
          </button>

          <!-- 最近文件 -->
          <div ref="recentMenuRef" class="relative">
            <button
              type="button"
              class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-sm text-gray-700 transition-colors hover:border-gray-300 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
              title="最近打开的文件"
              @click="toggleRecentMenu"
            >
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <circle cx="12" cy="12" r="8.5" />
                <path stroke-linecap="round" d="M12 7.4V12l3.1 1.9" />
              </svg>
              最近
            </button>
            <div
              v-if="recentOpen"
              class="absolute right-0 z-30 mt-1 w-64 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-xl dark:border-gray-700 dark:bg-gray-800"
            >
              <p v-if="!recentFiles.length" class="px-3 py-3 text-xs text-gray-400">还没有打开过文件</p>
              <button
                v-for="recent in recentFiles.slice(0, 8)"
                :key="recent.path"
                type="button"
                class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700/60"
                :title="recent.path"
                @click="openRecent(recent)"
              >
                <component :is="fileIcon(recent.name)" class="h-3.5 w-3.5 shrink-0" :class="fileIconTone(recent.name)" aria-hidden="true" />
                <span class="min-w-0 flex-1 truncate">{{ displayFileName(recent.name) }}</span>
              </button>
            </div>
          </div>

          <!-- 新建：文件夹 / 文件 -->
          <div ref="createMenuRef" class="relative">
            <button
              type="button"
              class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
              :disabled="!canCreateHere"
              @click="toggleCreateMenu"
            >
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path stroke-linecap="round" d="M12 5v14M5 12h14" />
              </svg>
              新建
            </button>
            <div
              v-if="createOpen"
              class="absolute right-0 z-30 mt-1 w-40 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-xl dark:border-gray-700 dark:bg-gray-800"
            >
              <button type="button" class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700/60" @click="openCreate('dir')">
                <FolderPlusIcon class="h-3.5 w-3.5 shrink-0 text-amber-500" aria-hidden="true" />
                新建文件夹
              </button>
              <button type="button" class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700/60" @click="openCreate('file')">
                <DocumentPlusIcon class="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
                新建文件
              </button>
            </div>
          </div>

          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:opacity-50"
            :disabled="!canCreateHere || uploading"
            @click="fileInputRef?.click()"
          >
            <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 16V4M7.5 8.5 12 4l4.5 4.5" />
              <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 16v3.5h15V16" />
            </svg>
            {{ uploading ? "上传中…" : "上传文件" }}
          </button>
          <input ref="fileInputRef" type="file" multiple class="hidden" @change="onPickFiles" />
        </div>
      </div>
    </div>

    <!-- 路径 + 搜索 -->
    <div class="flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-gray-800"
        :disabled="!parentPath || isRoot || searchMode === 'all'"
        title="返回上级目录"
        @click="navigate(parentPath)"
      >
        <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 6l-6 6 6 6" />
        </svg>
        上一级
      </button>

      <nav class="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto text-xs text-gray-400">
        <span v-for="(seg, index) in pathSegments" :key="`${index}:${seg.path}`" class="flex flex-shrink-0 items-center gap-1">
          <span v-if="index > 0" class="text-gray-300 dark:text-gray-600">/</span>
          <button
            type="button"
            class="max-w-[160px] truncate rounded px-1 py-0.5 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            :class="index === pathSegments.length - 1 ? 'font-semibold text-gray-700 dark:text-gray-200' : ''"
            :title="seg.path || seg.label"
            @click="navigate(seg.path)"
          >{{ seg.label }}</button>
        </span>
      </nav>

      <label class="relative flex w-52 flex-shrink-0 items-center">
        <svg class="pointer-events-none absolute left-2.5 h-4 w-4 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="7" />
          <path d="M16.5 16.5L21 21" stroke-linecap="round" />
        </svg>
        <input
          v-model="keyword"
          type="search"
          placeholder="搜索文件"
          class="w-full rounded-lg border border-gray-200 py-1.5 pl-8 pr-2 text-xs outline-none transition-colors focus:border-primary dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
          @input="onKeywordInput"
        />
      </label>
      <label class="flex flex-shrink-0 cursor-pointer items-center gap-1 text-[11px] text-gray-500 dark:text-gray-400" title="勾选后搜索当前目录及全部子目录（至少输入 2 个字符）">
        <input v-model="searchAll" type="checkbox" class="rounded border-gray-300 text-primary focus:ring-primary/30" @change="onKeywordInput" />
        含子目录
      </label>
    </div>

    <!-- 搜索辅助提示：单字提示 / 截断提示 -->
    <div
      v-if="searchTooShortHint || (searchMode === 'all' && searchTruncated)"
      class="flex flex-wrap items-center gap-2 text-[11px]"
    >
      <span v-if="searchTooShortHint" class="text-amber-600 dark:text-amber-400">
        提示：输入至少 2 个字符才能搜索子目录，当前仅过滤本级列表。
      </span>
      <span v-if="searchMode === 'all' && searchTruncated" class="text-amber-600 dark:text-amber-400">
        搜索结果已达上限被截断，请输入更精确的关键词或缩小目录范围。
      </span>
    </div>

    <!-- 快捷导航 + 排序/筛选/视图 -->
    <div class="flex flex-wrap items-center gap-2">
      <button
        v-for="link in quickNavLinks"
        :key="link.key"
        type="button"
        class="rounded-full border px-3 py-1 text-xs transition-colors"
        :class="link.active
          ? 'border-primary bg-primary text-white'
          : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300'"
        @click="navigate(link.path)"
      >{{ link.label }}</button>

      <button
        v-if="inTrash"
        type="button"
        class="rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-100 dark:border-red-800/60 dark:bg-red-950/40 dark:text-red-400 dark:hover:bg-red-950/60"
        @click="askEmptyTrash"
      >清空回收站</button>

      <span
        v-if="scope === 'admin_all' && !isInsideOwnWorkspace"
        class="rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
      >
        管理员视图 · 全部用户
      </span>

      <div class="ml-auto flex items-center gap-1.5">
        <div ref="typeFilterRef" class="relative">
          <button
            type="button"
            class="inline-flex h-7 items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2 text-xs text-gray-600 transition-colors hover:border-gray-300 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:border-gray-600"
            :aria-expanded="typeFilterOpen ? 'true' : 'false'"
            aria-haspopup="listbox"
            title="按文件类型筛选"
            @click="typeFilterOpen = !typeFilterOpen"
          >
            <component :is="activeTypeFilter.icon" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{{ activeTypeFilter.label }}</span>
            <ChevronDownIcon class="h-3 w-3 shrink-0 text-gray-400" aria-hidden="true" />
          </button>
          <div
            v-if="typeFilterOpen"
            role="listbox"
            class="absolute left-0 z-30 mt-1 w-32 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-xl dark:border-gray-700 dark:bg-gray-800"
          >
            <button
              v-for="opt in TYPE_FILTER_OPTIONS"
              :key="opt.key"
              type="button"
              role="option"
              :aria-selected="typeFilter === opt.key ? 'true' : 'false'"
              class="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs transition-colors"
              :class="typeFilter === opt.key
                ? 'bg-primary/5 font-bold text-primary dark:bg-primary/10'
                : 'text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700/60'"
              @click="selectTypeFilter(opt.key)"
            >
              <component :is="opt.icon" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span class="flex-1 truncate">{{ opt.label }}</span>
              <CheckIcon v-if="typeFilter === opt.key" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            </button>
          </div>
        </div>
        <select
          v-model="sortBy"
          class="h-7 rounded-lg border border-gray-200 bg-white px-2 py-0 text-xs text-gray-600 outline-none dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
        >
          <option value="mtime">按时间</option>
          <option value="name">按名称</option>
          <option value="size">按大小</option>
        </select>
        <button
          type="button"
          class="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-gray-200 bg-white text-xs text-gray-600 transition-colors hover:border-gray-300 hover:text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
          :title="sortDir === 'asc' ? '当前升序，点击切换降序' : '当前降序，点击切换升序'"
          :aria-label="sortDir === 'asc' ? '切换为降序' : '切换为升序'"
          @click="toggleSortDir"
        >
          {{ sortDir === "asc" ? "↑" : "↓" }}
        </button>
        <div class="flex overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700">
          <button
            type="button"
            class="px-2 py-1 text-xs"
            :class="viewMode === 'list' ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white' : 'text-gray-500'"
            title="列表视图"
            @click="viewMode = 'list'"
          >
            <Bars3Icon class="h-4 w-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="px-2 py-1 text-xs"
            :class="viewMode === 'grid' ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white' : 'text-gray-500'"
            title="卡片视图"
            @click="viewMode = 'grid'"
          >
            <Squares2X2Icon class="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>

    <!-- 批量操作条（常规目录） -->
    <div
      v-if="selectedPaths.size && !inTrash"
      class="flex flex-wrap items-center gap-3 rounded-xl border border-primary/10 bg-primary/5 px-3 py-2 text-xs text-primary-dark dark:border-primary-dark/50 dark:bg-primary-dark/30 dark:text-primary-hover"
    >
      <span>已选 {{ selectedPaths.size }} 项</span>
      <button type="button" class="font-semibold hover:underline" :disabled="batchDownloading" @click="handleBatchDownload">
        {{ batchDownloading ? "打包中…" : "打包下载" }}
      </button>
      <button v-if="canBatchMove" type="button" class="font-semibold hover:underline" @click="askBatchMove">移动到…</button>
      <button type="button" class="font-semibold text-red-600 hover:underline dark:text-red-400" @click="askBatchDelete">批量删除</button>
      <button type="button" class="text-primary hover:underline dark:text-primary" @click="clearSelection">取消选择</button>
    </div>

    <!-- 批量操作条（回收站专用：只允许还原或彻底删除，绝不走普通删除） -->
    <div
      v-if="selectedTrashPaths.size && inTrash"
      class="flex flex-wrap items-center gap-3 rounded-xl border border-amber-200 bg-amber-50/80 px-3 py-2 text-xs text-amber-800 dark:border-amber-800/50 dark:bg-amber-950/30 dark:text-amber-300"
    >
      <span>回收站已选 {{ selectedTrashPaths.size }} 项</span>
      <button type="button" class="font-semibold text-primary hover:underline" @click="askBatchRestore">批量还原</button>
      <button type="button" class="font-semibold text-red-600 hover:underline dark:text-red-400" @click="askBatchPurge">批量彻底删除</button>
      <button type="button" class="hover:underline" @click="clearTrashSelection">取消选择</button>
    </div>

    <!-- 列表视图 -->
    <div
      v-if="viewMode === 'list'"
      class="min-h-0 flex-1 overflow-auto rounded-xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900"
      @contextmenu.prevent="openEmptyContextMenu($event)"
    >
      <table class="w-full border-collapse text-sm">
        <thead class="sticky top-0 z-10 bg-gray-50/90 backdrop-blur dark:bg-gray-800/90">
          <tr class="text-left text-xs text-gray-500 dark:text-gray-400">
            <th class="w-9 px-2 py-2">
              <input
                v-if="!inTrash"
                type="checkbox"
                class="rounded border-gray-300 text-primary focus:ring-primary/30"
                :checked="selectionState === 'all'"
                :indeterminate="selectionState === 'partial'"
                :disabled="!selectableItems.length"
                @change="toggleSelectAll"
              />
              <input
                v-else
                type="checkbox"
                class="rounded border-gray-300 text-primary focus:ring-primary/30"
                :checked="trashSelectionState === 'all'"
                :indeterminate="trashSelectionState === 'partial'"
                :disabled="!selectableTrashItems.length"
                @change="toggleSelectAllTrash"
              />
            </th>
            <th class="px-3 py-2 font-medium">
              <button type="button" class="inline-flex items-center gap-1 hover:text-gray-800 dark:hover:text-gray-200" @click="toggleHeaderSort('name')">
                <span>名称</span>
                <span v-if="sortBy === 'name'" class="text-[10px] text-primary">{{ sortDir === "asc" ? "↑" : "↓" }}</span>
              </button>
            </th>
            <th class="w-24 px-3 py-2 font-medium">
              <button type="button" class="inline-flex items-center gap-1 hover:text-gray-800 dark:hover:text-gray-200" @click="toggleHeaderSort('size')">
                <span>大小</span>
                <span v-if="sortBy === 'size'" class="text-[10px] text-primary">{{ sortDir === "asc" ? "↑" : "↓" }}</span>
              </button>
            </th>
            <th class="w-40 px-3 py-2 font-medium">
              <button type="button" class="inline-flex items-center gap-1 hover:text-gray-800 dark:hover:text-gray-200" @click="toggleHeaderSort('mtime')">
                <span>{{ inTrash ? "删除时间" : "修改时间" }}</span>
                <span v-if="sortBy === 'mtime'" class="text-[10px] text-primary">{{ sortDir === "asc" ? "↑" : "↓" }}</span>
              </button>
            </th>
            <th class="w-20 px-3 py-2 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading || searching">
            <td colspan="5" class="px-3 py-10 text-center text-xs text-gray-400">
              {{ searching ? "正在搜索…" : "正在加载…" }}
            </td>
          </tr>
          <tr v-else-if="!visibleItems.length">
            <td colspan="5" class="px-3 py-10 text-center text-xs text-gray-400">
              <div class="flex flex-col items-center gap-2">
                <span>
                  {{ loadFailed ? "目录读取失败，请重试" : keyword ? "没有匹配的文件" : typeFilter !== "all" ? `当前目录没有「${activeTypeFilter.label}」类型的文件` : inTrash ? "回收站是空的" : "这个目录还是空的，上传一个文件试试" }}
                </span>
                <button
                  v-if="loadFailed"
                  type="button"
                  class="rounded-lg border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-primary hover:text-primary dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
                  @click="refreshCurrentView(true)"
                >重试</button>
                <button
                  v-else-if="!keyword && typeFilter !== 'all'"
                  type="button"
                  class="rounded-lg border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary hover:bg-primary/10"
                  @click="selectTypeFilter('all')"
                >显示全部类型</button>
              </div>
            </td>
          </tr>
          <tr
            v-for="item in visibleItems"
            v-else
            :key="item.path"
            class="cursor-default select-none border-t border-gray-100 transition-colors dark:border-gray-800"
            :class="(inTrash ? selectedTrashPaths.has(item.path) : selectedPaths.has(item.path))
              ? 'bg-primary/10 hover:bg-primary/15 dark:bg-primary/20 dark:hover:bg-primary/25'
              : 'hover:bg-gray-100 dark:hover:bg-gray-800/70'"
            :title="item.is_dir ? '双击进入目录' : '双击打开预览'"
            @dblclick="openItem(item)"
            @contextmenu.prevent.stop="openItemContextMenu($event, item)"
          >
            <td class="px-2 py-2" @dblclick.stop>
              <input
                v-if="canSelect(item)"
                type="checkbox"
                class="rounded border-gray-300 text-primary focus:ring-primary/30"
                :checked="selectedPaths.has(item.path)"
                @change="toggleSelect(item.path)"
              />
              <input
                v-else-if="canSelectTrash(item)"
                type="checkbox"
                class="rounded border-gray-300 text-primary focus:ring-primary/30"
                :checked="selectedTrashPaths.has(item.path)"
                @change="toggleSelectTrash(item.path)"
              />
            </td>
            <td class="min-w-0 px-3 py-2">
              <div class="min-w-0">
                <button
                  v-if="item.is_dir"
                  type="button"
                  class="flex min-w-0 items-center gap-2 text-left"
                  @click.stop="navigate(item.path)"
                  @dblclick.stop
                >
                  <component :is="FolderIcon" class="h-4 w-4 shrink-0 text-amber-500" aria-hidden="true" />
                  <span v-if="inTrash" class="truncate font-medium text-gray-800 hover:text-primary dark:text-gray-200">{{ parseTrashedFileName(item.name).displayName }}</span>
                  <span v-else-if="isTrashRootItem(item)" class="truncate font-medium text-gray-800 hover:text-primary dark:text-gray-200">回收站</span>
                  <span v-else class="truncate font-medium text-gray-800 hover:text-primary dark:text-gray-200">{{ displayFileName(item.name) }}</span>
                </button>
                <div v-else class="flex min-w-0 items-center gap-2">
                  <component :is="fileIcon(item.name)" class="h-4 w-4 shrink-0" :class="fileIconTone(item.name)" aria-hidden="true" />
                  <span v-if="inTrash" class="truncate text-gray-800 dark:text-gray-200" :title="parseTrashedFileName(item.name).originalName">{{ parseTrashedFileName(item.name).displayName }}</span>
                  <span v-else class="truncate text-gray-800 dark:text-gray-200" :title="item.path">{{ displayFileName(item.name) }}</span>
                </div>
                <p
                  v-if="searchMode === 'all'"
                  class="mt-0.5 truncate pl-6 text-[10px] text-gray-400"
                  :title="relativeParentLabel(item.path)"
                >
                  {{ relativeParentLabel(item.path) }}
                </p>
              </div>
            </td>
            <td class="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
              {{ item.is_dir ? "—" : formatSize(item.size) }}
            </td>
            <td class="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">{{ formatItemTime(item) }}</td>
            <td class="px-3 py-2" @dblclick.stop>
              <div class="flex justify-end">
                <WorkspaceRowActions
                  v-if="!isVirtualRoot"
                  :item="item"
                  :in-trash="inTrash"
                  :is-trash-root="isTrashRootItem(item)"
                  :is-admin-scope="scope === 'admin_all'"
                  :is-system-protected="isSystemProtectedItem(item)"
                  :can-manage="canManageItem(item)"
                  :user-workspace-root="userWorkspaceRoot"
                  @open-dir="handleOpenDir"
                  @preview="handlePreview"
                  @download="handleDownload"
                  @download-zip="handleDownloadZip"
                  @share="openShare"
                  @copy-and-share="handleCopyAndShare"
                  @copy-to-workspace="askCopyToWorkspace"
                  @ask-ai="handleAskAi"
                  @move="askMove"
                  @rename="openRename"
                  @copy-path="handleCopyPath"
                  @extract="askExtract"
                  @delete="askDelete"
                  @restore="handleRestore"
                  @purge="askPurge"
                  @empty-trash="askEmptyTrash"
                />
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 卡片视图 -->
    <div
      v-else
      class="min-h-0 flex-1 overflow-auto rounded-xl bg-white p-2 dark:bg-gray-900"
      @contextmenu.prevent="openEmptyContextMenu($event)"
    >
      <p v-if="loading || searching" class="py-10 text-center text-xs text-gray-400">
        {{ searching ? "正在搜索…" : "正在加载…" }}
      </p>
      <div v-else-if="!visibleItems.length" class="flex flex-col items-center gap-2 py-10 text-center text-xs text-gray-400">
        <span>
          {{ loadFailed ? "目录读取失败，请重试" : keyword ? "没有匹配的文件" : typeFilter !== "all" ? `当前目录没有「${activeTypeFilter.label}」类型的文件` : inTrash ? "回收站是空的" : "这个目录还是空的，上传一个文件试试" }}
        </span>
        <button
          v-if="loadFailed"
          type="button"
          class="rounded-lg border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-primary hover:text-primary dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
          @click="refreshCurrentView(true)"
        >重试</button>
        <button
          v-else-if="!keyword && typeFilter !== 'all'"
          type="button"
          class="rounded-lg border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary hover:bg-primary/10"
          @click="selectTypeFilter('all')"
        >显示全部类型</button>
      </div>
      <!-- 大图相册风：aspect-[4/3] 满宽媒体区 + 左上悬浮勾选 + 紧凑信息底栏（文件名与 ⋮ 并排） -->
      <div v-else class="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
        <div
          v-for="item in visibleItems"
          :key="item.path"
          class="group flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white transition-all hover:border-primary/40 hover:shadow-sm dark:border-gray-800 dark:bg-gray-800/60"
          :class="(inTrash ? selectedTrashPaths.has(item.path) : selectedPaths.has(item.path)) ? 'border-primary/50 ring-2 ring-primary/70' : ''"
          :title="item.is_dir ? '点击进入目录' : '双击打开预览'"
          @dblclick.stop="item.is_dir ? undefined : openItem(item)"
          @contextmenu.prevent.stop="openItemContextMenu($event, item)"
        >
          <div
            class="relative flex aspect-[4/3] w-full items-center justify-center overflow-hidden bg-gray-50 dark:bg-gray-800/80"
            :class="item.is_dir ? 'cursor-pointer' : ''"
            :title="item.path"
            @click="item.is_dir ? navigate(item.path) : undefined"
          >
            <img
              v-if="isImageItem(item) && !thumbFailed[item.path]"
              :src="resolveFsPreviewUrl(item.path)"
              :alt="displayFileName(item.name)"
              loading="lazy"
              decoding="async"
              class="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.02]"
              @error="onThumbError(item.path)"
            />
            <component
              :is="fileIcon(item.name, item.is_dir)"
              v-else
              class="h-10 w-10 select-none"
              :class="fileIconTone(item.name, item.is_dir)"
              aria-hidden="true"
            />

            <label
              v-if="canSelect(item) || canSelectTrash(item)"
              class="absolute left-2 top-2 z-10 flex h-6 w-6 cursor-pointer items-center justify-center rounded-md bg-white/85 shadow-sm backdrop-blur-sm transition-opacity dark:bg-gray-900/80"
              :class="(inTrash ? selectedTrashPaths.has(item.path) : selectedPaths.has(item.path)) ? 'opacity-100' : 'opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100'"
              @click.stop
              @dblclick.stop
            >
              <input
                type="checkbox"
                class="rounded border-gray-300 text-primary focus:ring-primary/30"
                :checked="inTrash ? selectedTrashPaths.has(item.path) : selectedPaths.has(item.path)"
                @change="inTrash ? toggleSelectTrash(item.path) : toggleSelect(item.path)"
              />
            </label>
          </div>

          <div class="flex items-center justify-between gap-1.5 border-t border-gray-100 px-2.5 py-2 dark:border-gray-700/60">
            <div
              class="min-w-0 flex-1"
              :class="item.is_dir ? 'cursor-pointer' : ''"
              :title="item.path"
              @click="item.is_dir ? navigate(item.path) : undefined"
            >
              <p v-if="inTrash" class="truncate text-xs font-medium text-gray-800 dark:text-gray-100">{{ parseTrashedFileName(item.name).displayName }}</p>
              <p v-else-if="isTrashRootItem(item)" class="truncate text-xs font-medium text-gray-800 dark:text-gray-100">回收站</p>
              <p v-else class="truncate text-xs font-medium text-gray-800 dark:text-gray-100">{{ displayFileName(item.name) }}</p>
              <p class="mt-0.5 truncate text-[10px] text-gray-400">
                <template v-if="searchMode === 'all'">{{ relativeParentLabel(item.path) }}</template>
                <template v-else>{{ item.is_dir ? "文件夹" : formatSize(item.size) }}</template>
              </p>
            </div>
            <div
              class="flex-shrink-0 transition-opacity"
              :class="openActionPath === item.path ? 'opacity-100' : 'opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100'"
              @dblclick.stop
            >
              <WorkspaceRowActions
                v-if="!isVirtualRoot"
                :item="item"
                :in-trash="inTrash"
                :is-trash-root="isTrashRootItem(item)"
                :is-admin-scope="scope === 'admin_all'"
                :is-system-protected="isSystemProtectedItem(item)"
                :can-manage="canManageItem(item)"
                :user-workspace-root="userWorkspaceRoot"
                @open-dir="handleOpenDir"
                @preview="handlePreview"
                @download="handleDownload"
                @download-zip="handleDownloadZip"
                @share="openShare"
                @copy-and-share="handleCopyAndShare"
                @copy-to-workspace="askCopyToWorkspace"
                @ask-ai="handleAskAi"
                @move="askMove"
                @rename="openRename"
                @copy-path="handleCopyPath"
                @extract="askExtract"
                @delete="askDelete"
                @restore="handleRestore"
                @purge="askPurge"
                @empty-trash="askEmptyTrash"
                @open-change="(isOpen) => onCardMenuOpenChange(item.path, isOpen)"
              />
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部统计状态栏 -->
    <div class="flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] text-gray-400">
      <div class="flex flex-wrap items-center gap-2">
        <span>
          <template v-if="visibleItems.length !== sourceItems.length">
            显示 {{ visibleItems.length }} / {{ sourceItems.length }} 项
          </template>
          <template v-else>
            共 {{ visibleItems.length }} 项
          </template>
        </span>
        <span v-if="visibleFileTotalBytes > 0">· 文件合计 {{ formatSize(visibleFileTotalBytes) }}</span>
        <button
          v-if="typeFilter !== 'all' || keyword.trim()"
          type="button"
          class="text-primary hover:underline"
          @click="resetFilters"
        >重置筛选</button>
      </div>
      <span class="hidden sm:inline text-[10px] text-gray-400/80">
        支持右键菜单 · 快捷键：F2 重命名 · Delete 删除 · Ctrl/⌘+A 全选
      </span>
    </div>

    <!-- 上传进度 -->
    <div v-if="showUploadPanel" class="rounded-xl border border-gray-200 bg-gray-50/70 px-3 py-2 dark:border-gray-800 dark:bg-gray-800/50">
      <div class="mb-1 flex items-center justify-between gap-2 text-xs text-gray-600 dark:text-gray-300">
        <span>上传进度 {{ snapshot.aggregate.done }} / {{ snapshot.aggregate.total }}</span>
        <div class="flex items-center gap-3">
          <button
            v-if="snapshot.aggregate.done > 0"
            type="button"
            class="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            @click="clearCompletedUploads"
          >清除已完成</button>
          <button
            v-if="snapshot.aggregate.failed > 0 || snapshot.aggregate.canceled > 0"
            type="button"
            class="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            @click="clearFinishedUploads"
          >全部清空</button>
        </div>
      </div>
      <ul class="max-h-28 space-y-1 overflow-y-auto">
        <li v-for="task in snapshot.tasks" :key="task.id" class="flex items-center gap-2 text-[11px]">
          <span class="min-w-0 flex-1 truncate text-gray-600 dark:text-gray-300" :title="task.storedName || task.name">
            {{ task.name }}
            <span v-if="task.storedName && task.storedName !== task.name" class="text-gray-400">
              → {{ task.storedName }}
            </span>
          </span>
          <span :class="task.status === 'failed' ? 'text-red-600 dark:text-red-400' : 'text-gray-400'">
            {{ task.status === 'failed' ? (task.error || '失败') : task.status === 'done' ? '完成' : task.status === 'canceled' ? '已取消' : task.status === 'committing' ? '写入中…' : task.status === 'queued' ? '排队中' : `${Math.round(task.percent)}%` }}
          </span>
          <button
            v-if="task.status === 'queued' || task.status === 'transferring'"
            type="button"
            class="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            @click="cancelUpload(task.id)"
          >取消</button>
          <button
            v-else-if="task.status === 'failed' || task.status === 'canceled'"
            type="button"
            class="text-primary hover:underline"
            @click="retryUpload(task.id)"
          >重试</button>
        </li>
      </ul>
    </div>

    <InputModal
      v-if="dialog"
      v-model:model-value="dialogDraft"
      :title="dialogTitle"
      :message="dialogMessage"
      :placeholder="dialogPlaceholder"
      :confirm-text="dialog === 'rename' ? '保存' : '创建'"
      :loading="dialogSubmitting"
      @confirm="submitDialog"
      @cancel="closeDialog"
    />

    <!-- 解压目标目录选择器：与聊天抽屉复用同一套目录树，公共压缩包也能解到本人工作区 -->
    <WorkspaceDirectorySaveDialog
      :visible="!!extractDialog"
      :default-filename="extractDialog?.defaultName || ''"
      :default-path="extractDialog?.parentPath || null"
      :submitting="extractSubmitting"
      :allow-empty-name="true"
      title="解压到目录"
      subtitle="选择解压目标目录（仅可解压到当前用户的 AI 工作区）"
      name-label="子文件夹名（留空直接解到所选目录）"
      submit-label="解压"
      @close="extractDialog = null"
      @save="submitExtract"
    />

    <!-- 移动 / 复制到目录选择器（复用同一套目录树） -->
    <WorkspaceDirectorySaveDialog
      :visible="!!dirActionDialog"
      :default-filename="''"
      :default-path="dirActionDialog?.defaultPath || null"
      :submitting="dirActionSubmitting"
      :hide-name-input="true"
      :title="dirActionDialog?.mode === 'copy' ? '复制到我的目录' : '移动到目录'"
      :subtitle="dirActionDialog?.subtitle || '选择目标目录（仅限当前用户的 AI 工作区）'"
      :submit-label="dirActionDialog?.mode === 'copy' ? '复制到此目录' : '移动到此目录'"
      @close="dirActionDialog = null"
      @save="submitDirAction"
    />

    <!-- 右键上下文菜单（列表行 / 卡片 / 空白区） -->
    <Teleport to="body">
      <div
        v-if="contextMenu"
        ref="contextMenuRef"
        :style="{ top: `${contextMenu.top}px`, left: `${contextMenu.left}px` }"
        class="fixed z-[1250] w-48 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-xl dark:border-gray-700 dark:bg-gray-800"
        role="menu"
        @click.stop
        @contextmenu.prevent
      >
        <template v-for="entry in contextMenuEntries" :key="entry.key">
          <div
            v-if="entry.key === 'header'"
            class="select-none border-b border-gray-100 px-3 py-1.5 text-[10px] font-bold text-primary/80 dark:border-gray-700/80 dark:text-primary/70"
          >
            <span aria-hidden="true">{{ entry.icon }}</span>
            <span class="ml-1">{{ entry.label }}</span>
          </div>
          <template v-else>
            <div
              v-if="entry.separatorBefore"
              class="my-1 border-t border-gray-100 dark:border-gray-700/80"
              role="separator"
            />
            <button
              type="button"
              role="menuitem"
              class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40"
              :class="entry.danger
                ? 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40'
                : 'text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700/60'"
              :disabled="entry.disabled"
              :title="entry.disabledHint || entry.label"
              @click="runContextMenuAction(entry.key)"
            >
              <span aria-hidden="true">{{ entry.icon }}</span>
              <span class="truncate">{{ entry.label }}</span>
            </button>
          </template>
        </template>
      </div>
    </Teleport>

    <!-- 预览复用聊天页的 Canvas：markdown 渲染 / 代码高亮 / CSV 透视 / 文档查看器全都在这里，
         自己再写一套预览只会出现「同一个文件两种样子」 -->
    <ChatCanvas
      v-model:pinned="canvasPinned"
      :visible="canvasVisible"
      :data="canvasData"
      dock-side="right"
      @close="closeCanvas"
      @content-saved="handleCanvasSaved"
      @analyze-output="handleCanvasAnalyzeOutput"
    />

    <ShareFileDialog
      :visible="!!shareTarget"
      :file-path="shareTarget?.path || ''"
      :file-name="shareTarget?.name || ''"
      :file-size="shareTarget?.size || 0"
      @close="shareTarget = null"
      @created="handleShareCreated"
      @toast="showToast"
    />

    <ConfirmModal
      v-if="confirmAction"
      :title="confirmAction.title"
      :message="confirmAction.message"
      :confirm-text="confirmAction.confirmText"
      :type="confirmAction.type || 'danger'"
      :loading="confirmSubmitting"
      @confirm="runConfirmAction"
      @cancel="confirmAction = null"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue"
import type { Component } from "vue"
import { useRoute, useRouter } from "vue-router"
import axios from "@/utils/axios"
import InputModal from "@/components/InputModal.vue"
import ConfirmModal from "@/components/ConfirmModal.vue"
import WorkspaceRowActions from "@/components/workspace/WorkspaceRowActions.vue"
import type { ManageRowActionEntry, ManageRowActionKey } from "@/components/workspace/WorkspaceRowActions.vue"
import WorkspaceDirectorySaveDialog from "@/components/embed/WorkspaceDirectorySaveDialog.vue"
import HeroSilhouette from "@/components/common/HeroSilhouette.vue"
import {
  ArchiveBoxIcon,
  ArrowPathIcon,
  Bars3Icon,
  CheckIcon,
  ChevronDownIcon,
  CodeBracketIcon,
  DocumentPlusIcon,
  DocumentTextIcon,
  FolderIcon,
  FolderPlusIcon,
  PhotoIcon,
  Squares2X2Icon,
  TableCellsIcon,
} from "@heroicons/vue/24/outline"
import ChatCanvas from "@/components/embed/ChatCanvas.vue"
import { useWorkspaceCanvas } from "@/composables/chat/useWorkspaceCanvas"
import ShareFileDialog from "@/components/ShareFileDialog.vue"
import { useToast } from "@/composables/useToast"
import { useWorkspaceUploadQueue } from "@/composables/useWorkspaceUploadQueue"
import { shouldAutoDismissUploads } from "@/utils/workspaceUploadQueue"
import {
  buildWorkspaceActionEntries,
  buildWorkspaceItemMenuContext,
  isExtractableArchive,
  isPublicWorkspaceItem,
} from "@/utils/workspaceActionEntries"
import { workspaceFileIcon, workspaceFileTone } from "@/utils/workspaceFileIcon"
import { planBatchDelete, selectAllState, toggleSelectAllPaths } from "@/utils/workspaceBatchSelection"
import { copyFileToWorkspace } from "@/utils/workspaceFilePreview"
import {
  WORKSPACE_ROOT_PROTECTED_NAMES,
  buildWorkspaceBreadcrumbs,
  formatRelativeWorkspacePath,
  isProtectedWorkspaceEntry,
  isWithinDirectory,
  normalizeWorkspacePath,
  parentDirectoryOf,
} from "@/utils/workspacePath"
import { displayFileName } from "@/utils/workspaceFileName"
import { parseTrashedFileName } from "@/utils/workspaceFileName"
import {
  IMAGE_EXTENSIONS,
  batchDownloadWorkspaceEntries,
  canPreviewWorkspaceFile,
  copyTextToClipboard,
  createWorkspaceEntry,
  deleteWorkspaceEntry,
  downloadWorkspaceFile,
  emptyWorkspaceTrash,
  extractWorkspaceArchive,
  fetchWorkspaceUsage,
  moveWorkspaceEntry,
  purgeWorkspaceEntry,
  renameWorkspaceEntry,
  buildWorkspaceCanvasPayload,
  resolveFsPreviewUrl,
  restoreWorkspaceEntry,
  type WorkspaceUsageStats,
} from "@/utils/workspaceFilePreview"

interface FsItem {
  name: string
  path: string
  is_dir: boolean
  size: number
  mtime: number
  is_public?: boolean
  is_user_workspace?: boolean
  readonly?: boolean
}

const PREFS_STORAGE_KEY = "workspace_manage_prefs_v1"
const MAX_RECENT_FILES = 20

type ViewMode = "list" | "grid"
type SortKey = "mtime" | "name" | "size"
type SortDir = "asc" | "desc"
type TypeFilterKey = "all" | "doc" | "sheet" | "image" | "archive" | "code"

const SORT_KEYS: readonly SortKey[] = ["mtime", "name", "size"]
const TYPE_FILTER_KEYS: readonly TypeFilterKey[] = ["all", "doc", "sheet", "image", "archive", "code"]

/**
 * 类型筛选的展示项。
 *
 * 图标顺序与聊天侧「浏览工作空间」抽屉保持一致：同一个筛选在两个入口长得不一样，
 * 用户会以为筛的不是同一件事。选中态用 CheckIcon 标出，而不是靠颜色深浅去猜。
 */
const TYPE_FILTER_OPTIONS: Array<{ key: TypeFilterKey; label: string; icon: Component }> = [
  { key: "all", label: "全部类型", icon: Squares2X2Icon },
  { key: "doc", label: "文档", icon: DocumentTextIcon },
  { key: "sheet", label: "表格", icon: TableCellsIcon },
  { key: "image", label: "图片", icon: PhotoIcon },
  { key: "archive", label: "压缩包", icon: ArchiveBoxIcon },
  { key: "code", label: "代码", icon: CodeBracketIcon },
]

/**
 * 视图偏好：视图模式 / 排序 / 排序方向 / 类型筛选。
 *
 * 均存 localStorage，刷新后原样恢复。没有改用后端 `/fs/browser-prefs`：
 * 那份存的抽屉的偏好（是否含子目录、类型筛选），语义与独立页的表格/卡片取舍并不等价，
 * 硬接会让两个界面的偏好互相覆盖。
 */
const readStoredPrefs = (): { viewMode?: ViewMode; sortBy?: SortKey; sortDir?: SortDir; typeFilter?: TypeFilterKey } => {
  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return {
      viewMode: parsed?.viewMode === "grid" ? "grid" : parsed?.viewMode === "list" ? "list" : undefined,
      sortBy: SORT_KEYS.find((key) => key === parsed?.sortBy),
      sortDir: parsed?.sortDir === "asc" ? "asc" : parsed?.sortDir === "desc" ? "desc" : undefined,
      typeFilter: TYPE_FILTER_KEYS.find((key) => key === parsed?.typeFilter),
    }
  } catch {
    return {}
  }
}

const storedPrefs = readStoredPrefs()

const route = useRoute()
const router = useRouter()
const { showToast } = useToast()
const {
  snapshot,
  enqueue,
  cancel: cancelUpload,
  retry: retryUpload,
  clearCompleted,
  clearFinished,
  setMaxBytes,
} = useWorkspaceUploadQueue()

const items = ref<FsItem[]>([])
const currentPath = ref("")
const parentPath = ref("")
const userWorkspaceRoot = ref("")
const isRoot = ref(true)
const isVirtualRoot = ref(false)
const writable = ref(false)
const scope = ref<"user_scoped" | "admin_all">("user_scoped")
const loading = ref(false)
const searching = ref(false)
const searchTruncated = ref(false)
/** 最近一次目录读取失败：路径保持旧值，列表不能被清空伪装成「空目录」。 */
const loadFailed = ref(false)
const keyword = ref("")
const searchAll = ref(false)
const recursiveItems = ref<FsItem[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)
const viewMode = ref<ViewMode>(storedPrefs.viewMode || "list")
const sortBy = ref<SortKey>(storedPrefs.sortBy || "mtime")
const sortDir = ref<SortDir>(storedPrefs.sortDir || (sortBy.value === "name" ? "asc" : "desc"))
const typeFilter = ref<TypeFilterKey>(storedPrefs.typeFilter || "all")
const typeFilterOpen = ref(false)
const typeFilterRef = ref<HTMLElement | null>(null)
const usageStats = ref<WorkspaceUsageStats | null>(null)
const batchDownloading = ref(false)
/** 当前筛选的展示项；`all` 必定存在，取不到时退回第一项，避免模板里出现空图标位。 */
const activeTypeFilter = computed(
  () => TYPE_FILTER_OPTIONS.find((opt) => opt.key === typeFilter.value) ?? TYPE_FILTER_OPTIONS[0]!,
)
const selectTypeFilter = (key: TypeFilterKey) => {
  typeFilter.value = key
  typeFilterOpen.value = false
}
const toggleSortDir = () => {
  sortDir.value = sortDir.value === "asc" ? "desc" : "asc"
}
const toggleHeaderSort = (key: SortKey) => {
  // 换字段时方向统一交给 watch(sortBy) 重置，保证「点表头」与「用下拉选排序方式」
  // 两种入口行为一致（此前下拉换字段会沿用旧方向，切到按名称会变成 Z→A）。
  if (sortBy.value === key) toggleSortDir()
  else sortBy.value = key
}
const selectedPaths = reactive(new Set<string>())
const selectedTrashPaths = reactive(new Set<string>())
const dragging = ref(false)
const createOpen = ref(false)
const recentOpen = ref(false)
const openActionPath = ref("")
const recentFiles = ref<Array<{ path: string; name: string; mtime: number }>>([])
const recentMenuRef = ref<HTMLElement | null>(null)
const createMenuRef = ref<HTMLElement | null>(null)

const SYSTEM_PROTECTED = WORKSPACE_ROOT_PROTECTED_NAMES

const persistPrefs = () => {
  try {
    localStorage.setItem(
      PREFS_STORAGE_KEY,
      JSON.stringify({
        viewMode: viewMode.value,
        sortBy: sortBy.value,
        sortDir: sortDir.value,
        typeFilter: typeFilter.value,
      }),
    )
  } catch {
    /* ignore storage quota or private mode errors */
  }
}

/** 切换排序字段时重置为该字段的自然方向（名称升序、大小/时间降序）。 */
watch(sortBy, (next) => {
  sortDir.value = next === "name" ? "asc" : "desc"
})
// 四个偏好项统一走一个 watch 落盘，避免多个 watch 互相覆盖同一个 localStorage 键
watch([viewMode, sortBy, sortDir, typeFilter], persistPrefs)

const uploading = computed(() => snapshot.value.aggregate.active)
const showUploadPanel = computed(
  () =>
    snapshot.value.tasks.length > 0 &&
    (uploading.value || snapshot.value.aggregate.failed > 0 || snapshot.value.aggregate.canceled > 0),
)
/**
 * 回收站按「子树」判定。
 *
 * 删掉的是文件夹时，它会整个躺在 `.trash/` 里；用户点进去之后如果还按普通目录处理，
 * 行菜单会退化成「重命名 / 删除」，后端也只拦 `.trash` 根，等于能把条目再套一层移进回收站。
 * 覆盖整棵子树后，里面的条目仍然是「恢复 / 永久删除」，且不允许再勾选批量删除。
 */
const inTrash = computed(
  () =>
    Boolean(userWorkspaceRoot.value) &&
    isWithinDirectory(currentPath.value, `${userWorkspaceRoot.value}/.trash`),
)
/**
 * 当前所在回收站根目录（`.../.trash`）。
 *
 * `inTrash` 只认本人回收站；管理员浏览他人工作区回收站时（URL 直达）必须按实际路径取根，
 * 否则「清空回收站」清的是管理员自己的，对方条目原封不动。
 */
const trashRootOfCurrentPath = computed(() => {
  const norm = normalizeWorkspacePath(currentPath.value)
  const marker = "/.trash"
  const idx = norm.indexOf(marker)
  if (idx >= 0) {
    const after = norm.slice(idx + marker.length)
    if (!after || after.startsWith("/")) return norm.slice(0, idx + marker.length)
  }
  return ""
})
const activeTrashRoot = computed(
  () =>
    trashRootOfCurrentPath.value ||
    (userWorkspaceRoot.value ? `${normalizeWorkspacePath(userWorkspaceRoot.value)}/.trash` : ""),
)
// 管理员默认落在自己的私有工作区：那里不是「全部用户」视图，别让徽标误导。
const isInsideOwnWorkspace = computed(() => isWithinDirectory(currentPath.value, userWorkspaceRoot.value))
const searchMode = computed<"current" | "all">(() => (searchAll.value && keyword.value.trim().length >= 2 ? "all" : "current"))
const searchTooShortHint = computed(() => searchAll.value && keyword.value.trim().length === 1)
// 虚拟根（「公共资源」）是授权根汇总，不是真实可写目录：新建/上传/勾选都必须关掉，
// 否则按钮点下去只会吃后端 403。
const canCreateHere = computed(
  () => !inTrash.value && !loading.value && Boolean(currentPath.value) && searchMode.value !== "all" && writable.value,
)

const sourceItems = computed(() => (searchMode.value === "all" ? recursiveItems.value : items.value))

const TYPE_GROUPS: Record<string, string[]> = {
  doc: ["md", "markdown", "txt", "doc", "docx", "pdf", "rtf", "epub", "html", "htm", "ppt", "pptx", "log"],
  sheet: ["xls", "xlsx", "xlsm", "csv", "tsv"],
  image: ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"],
  archive: ["zip", "tar", "gz", "tgz", "7z", "rar", "bz2", "xz", "zst"],
  code: [
    "py", "sh", "bash", "js", "ts", "jsx", "tsx", "mjs", "cjs", "vue",
    "json", "yaml", "yml", "toml", "ini", "conf", "xml", "env",
    "sql", "go", "java", "rs", "rb", "php", "c", "cpp", "h", "hpp", "css", "diff", "patch",
  ],
}

/** 取文件扩展名（不含点号）。无后缀文件或以点开头的纯隐藏名返回空串，避免把文件名本身误当扩展名。 */
const extOf = (name: string) => {
  const idx = name.lastIndexOf(".")
  if (idx <= 0 || idx === name.length - 1) return ""
  return name.slice(idx + 1).toLowerCase()
}

/**
 * 卡片缩略图只对后端 preview 真正放行的图片类型出图。
 *
 * 用 `IMAGE_EXTENSIONS` 而不是图标映射那份更宽的表：那份表含 svg，
 * 但 `/fs/preview` 的白名单（`IMAGE_PREVIEW_EXTENSIONS`）里没有它，给 svg 出图
 * 只会换来一串注定失败的请求。
 */
const isImageItem = (item: FsItem) => !item.is_dir && IMAGE_EXTENSIONS.has(`.${extOf(item.name)}`)

/**
 * 读取失败的缩略图退回 emoji。
 *
 * 坏文件、被移走的文件、以及不属于当前用户的路径都会让 preview 返回错误；
 * 不兜这一下，卡片里就会留一块空白，用户不知道那里到底有没有文件。
 */
const thumbFailed = ref<Record<string, boolean>>({})
const onThumbError = (path: string) => {
  thumbFailed.value = { ...thumbFailed.value, [path]: true }
}

const isTrashRootItem = (item: FsItem) =>
  Boolean(userWorkspaceRoot.value) &&
  normalizeWorkspacePath(item.path) === normalizeWorkspacePath(`${userWorkspaceRoot.value}/.trash`)

/**
 * 系统保护目录判定：结合目录层级（只保护根级保留目录与用户工作区根目录），
 * 避免误伤用户在项目子目录下自建的同名 `docs` / `uploads` 目录。
 * `is_user_workspace`（本人或其他用户的工作区根）由 `isProtectedWorkspaceEntry` 内部覆盖。
 */
const isSystemProtectedItem = (item: FsItem) =>
  isProtectedWorkspaceEntry(item, userWorkspaceRoot.value, SYSTEM_PROTECTED)

const canManageItem = (item: FsItem) => {
  if (inTrash.value || isVirtualRoot.value) return false
  if (isTrashRootItem(item) || isSystemProtectedItem(item) || item.is_user_workspace) return false
  if (isPublicWorkspaceItem(item, userWorkspaceRoot.value)) {
    return scope.value === "admin_all"
  }
  if (item.readonly && scope.value !== "admin_all") return false
  if (!userWorkspaceRoot.value) return false
  return scope.value === "admin_all" || isWithinDirectory(item.path, userWorkspaceRoot.value)
}

const canSelect = (item: FsItem) => !inTrash.value && !isVirtualRoot.value && canManageItem(item)
/** 回收站独立勾选判定：只允许回收站内条目（非 .trash 根），与常规批量删除物理隔离。 */
const canSelectTrash = (item: FsItem) => inTrash.value && !isTrashRootItem(item)

const canMoveItem = (item: FsItem) =>
  !inTrash.value &&
  !isVirtualRoot.value &&
  canManageItem(item) &&
  !isPublicWorkspaceItem(item, userWorkspaceRoot.value)

const visibleItems = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  let list = [...sourceItems.value]

  if (searchMode.value === "current" && kw) {
    list = list.filter((item) => item.name.toLowerCase().includes(kw))
  }
  if (typeFilter.value !== "all") {
    const exts = TYPE_GROUPS[typeFilter.value] || []
    list = list.filter((item) => item.is_dir || exts.includes(extOf(item.name)))
  }
  const dirFactor = sortDir.value === "asc" ? 1 : -1
  list.sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
    if (sortBy.value === "name") {
      return displayFileName(a.name).localeCompare(displayFileName(b.name), "zh-CN") * dirFactor
    }
    if (sortBy.value === "size") return ((a.size || 0) - (b.size || 0)) * dirFactor
    return ((a.mtime || 0) - (b.mtime || 0)) * dirFactor
  })
  return list
})

const visibleFileTotalBytes = computed(() =>
  visibleItems.value.reduce((sum, item) => sum + (item.is_dir ? 0 : item.size || 0), 0),
)

const selectableItems = computed(() => visibleItems.value.filter((item) => canSelect(item)))
const selectablePaths = computed(() => selectableItems.value.map((item) => item.path))
const selectionState = computed(() => selectAllState(selectablePaths.value, selectedPaths))

const selectableTrashItems = computed(() => visibleItems.value.filter((item) => canSelectTrash(item)))
const selectableTrashPaths = computed(() => selectableTrashItems.value.map((item) => item.path))
const trashSelectionState = computed(() => selectAllState(selectableTrashPaths.value, selectedTrashPaths))

const canBatchMove = computed(() => {
  if (!selectedPaths.size || inTrash.value) return false
  const byPath = new Map(sourceItems.value.map((entry) => [entry.path, entry]))
  for (const path of selectedPaths) {
    const item = byPath.get(path)
    if (item && canMoveItem(item)) return true
  }
  return false
})

// 筛选/搜索变化导致部分已选项不再可见时，自动清理隐藏勾选，防止批量删除误删看不见的文件。
watch(selectablePaths, (nextPaths) => {
  const allowed = new Set(nextPaths)
  for (const path of [...selectedPaths]) {
    if (!allowed.has(path)) selectedPaths.delete(path)
  }
})
watch(selectableTrashPaths, (nextPaths) => {
  const allowed = new Set(nextPaths)
  for (const path of [...selectedTrashPaths]) {
    if (!allowed.has(path)) selectedTrashPaths.delete(path)
  }
})

const pathSegments = computed(() => {
  if (searchMode.value === "all") return [{ label: `全目录搜索：${keyword.value.trim()}`, path: currentPath.value }]
  // 虚拟根不是真实目录，面包屑只留一级「公共资源」（空 path 即回到汇总）。
  if (isVirtualRoot.value) return [{ label: "公共资源", path: "" }]
  return buildWorkspaceBreadcrumbs({
    currentPath: currentPath.value,
    userWorkspaceRoot: userWorkspaceRoot.value,
    isVirtualRoot: isVirtualRoot.value,
    searchMode: searchMode.value,
    keyword: keyword.value,
  })
})

const currentLabel = computed(() => pathSegments.value[pathSegments.value.length - 1]?.label || "我的目录")

const quickNavLinks = computed(() => {
  const root = userWorkspaceRoot.value
  if (!root) return []
  const normCurrent = normalizeWorkspacePath(currentPath.value)
  const isAtPublicRoot = isVirtualRoot.value || (scope.value === "admin_all" && isRoot.value)
  return [
    { key: "home", label: "我的目录", path: root },
    { key: "docs", label: "我的文档", path: `${root}/docs` },
    { key: "uploads", label: "上传目录", path: `${root}/uploads` },
    { key: "trash", label: "回收站", path: `${root}/.trash` },
    { key: "public", label: "公共资源", path: "" },
  ].map((link) => ({
    ...link,
    active:
      searchMode.value !== "all" &&
      (link.key === "public"
        ? isAtPublicRoot
        : !isAtPublicRoot && normCurrent === normalizeWorkspacePath(link.path)),
  }))
})

const formatSize = (bytes: number) => {
  if (!bytes) return "-"
  const units = ["B", "KB", "MB", "GB"]
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${parseFloat((bytes / Math.pow(1024, index)).toFixed(1))} ${units[index]}`
}

const formatTime = (timestamp: number) => {
  if (!timestamp) return "-"
  return new Date(timestamp * 1000).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

const formatItemTime = (item: FsItem) => {
  if (inTrash.value) {
    const parsed = parseTrashedFileName(item.name)
    return formatTime(parsed.deletedAt || item.mtime)
  }
  return formatTime(item.mtime)
}

const relativeParentLabel = (path: string) =>
  formatRelativeWorkspacePath(parentDirectoryOf(path), userWorkspaceRoot.value)

const resetFilters = () => {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  searchSeq += 1
  searching.value = false
  keyword.value = ""
  searchAll.value = false
  recursiveItems.value = []
  searchTruncated.value = false
  typeFilter.value = "all"
}

const refreshUsage = async (force = false) => {
  try {
    const stats = await fetchWorkspaceUsage(force)
    if (!stats) return
    usageStats.value = stats
    if (stats.upload_max_bytes > 0) {
      setMaxBytes(stats.upload_max_bytes)
    }
  } catch {
    /* 容量统计失败不影响主文件浏览 */
  }
}

/**
 * 文件类型图标（SVG）。
 *
 * 早先这里返回 emoji（📄📊🖼️🗜️📜），列表里靠它区分「文档 / 表格 / 图片 / 压缩包 / 代码」。
 * emoji 清理时函数体被换成了空返回，列表就只剩文件名了——所以改成按
 * `resolveFileTypeVisual` 的分类挑图标：形状负责区分类型，颜色只做弱提示。
 */
const fileIcon = workspaceFileIcon
const fileIconTone = workspaceFileTone

let loadSeq = 0
/**
 * 正在进行的目录读取请求。
 *
 * 同一目标重复触发（批量操作后刷新 + 用户手动刷新）时复用同一个 Promise：
 * 此前用「loading 且同 path 就直接 return」的写法，调用方 await 会立即 resolve，
 * `refreshCurrentViewAsync()` 便可能在实际刷新完成前返回，导致它刚补回的失败项勾选
 * 被随后那次请求开头的 `clearSelection()` 清掉。
 */
let inflightLoad: { key: string; promise: Promise<void> } | null = null
let searchSeq = 0
let searchTimer: ReturnType<typeof setTimeout> | null = null
let syncingUrlPath = false
const VIRTUAL_ROOT_URL_TOKEN = "__public__"

const syncPathToUrl = (targetPath: string, virtualRoot: boolean, mode: "push" | "replace" = "push") => {
  const desired = virtualRoot ? VIRTUAL_ROOT_URL_TOKEN : targetPath
  const existing = typeof route.query.path === "string" ? route.query.path : ""
  if (existing === desired) return
  const nextQuery: Record<string, any> = { ...route.query }
  if (desired) nextQuery.path = desired
  else delete nextQuery.path
  syncingUrlPath = true
  const nav = mode === "push" ? router.push({ query: nextQuery }) : router.replace({ query: nextQuery })
  void nav.finally(() => {
    syncingUrlPath = false
  })
}

/**
 * 读取目录。
 *
 * `preferUserRoot` 只用于首屏：把空 path 交给后端解析成「本人私有工作区」
 * （`GET /chat/fs/list?prefer=user_root`），不必先拿虚拟根再二次跳转。
 * 「公共资源」入口走默认空 path，拿到的就是授权根汇总。
 */
type LoadOptions = {
  preferUserRoot?: boolean
  preserveSearch?: boolean
  syncUrl?: boolean
  urlMode?: "push" | "replace"
  fallbackToHomeOnError?: boolean
}

const loadInner = async (path: string, options: LoadOptions) => {
  const seq = ++loadSeq
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  searchSeq += 1
  searching.value = false
  loading.value = true
  loadFailed.value = false
  clearSelection()
  clearTrashSelection()
  try {
    const params: Record<string, string> = { path }
    if (options.preferUserRoot) params.prefer = "user_root"
    const res = await axios.get("/api/v1/chat/fs/list", { params })
    if (seq !== loadSeq) return
    const data = res.data?.data
    if (!data) return
    currentPath.value = data.current_path || ""
    parentPath.value = data.parent_path || ""
    isRoot.value = Boolean(data.is_root)
    isVirtualRoot.value = Boolean(data.is_virtual_root)
    writable.value = Boolean(data.writable)
    scope.value = data.scope === "admin_all" ? "admin_all" : "user_scoped"
    items.value = Array.isArray(data.items) ? data.items : []
    thumbFailed.value = {}
    if (data.user_workspace_root) userWorkspaceRoot.value = data.user_workspace_root
    if (!options.preserveSearch) {
      keyword.value = ""
      searchAll.value = false
      recursiveItems.value = []
      searchTruncated.value = false
    }
    if (options.syncUrl !== false) {
      const mode = options.urlMode || (options.preferUserRoot ? "replace" : "push")
      syncPathToUrl(currentPath.value, isVirtualRoot.value, mode)
    }
  } catch {
    if (seq !== loadSeq) return
    // 首屏通过 URL `?path=` 进入已被删除或无权访问的目录时，不能把用户困死在无快捷导航的报错页，自动降级回我的目录
    if (options.fallbackToHomeOnError && !userWorkspaceRoot.value) {
      showToast("原目录不存在或无权访问，已回到我的目录", "warning")
      void load("", { preferUserRoot: true, urlMode: "replace" })
      return
    }
    loadFailed.value = true
    showToast("读取目录失败，请稍后重试", "error")
    // 刻意不清空 items：currentPath 仍是旧目录，清空会让旧目录看起来「空了」。
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

/**
 * 读取目录（并发去重版）。
 *
 * 同一目标已有请求在飞时直接返回同一个 Promise，保证 `await load(...)` 的语义是
 * 「等到这次读取真的完成」，而不是「这次调用被丢弃」。
 */
const load = (path = "", options: LoadOptions = {}): Promise<void> => {
  const normTargetKey = `${options.preferUserRoot ? "prefer:" : ""}${normalizeWorkspacePath(path)}`
  if (inflightLoad && inflightLoad.key === normTargetKey) return inflightLoad.promise
  const promise = loadInner(path, options)
  inflightLoad = { key: normTargetKey, promise }
  const release = () => {
    if (inflightLoad?.promise === promise) inflightLoad = null
  }
  promise.then(release, release)
  return promise
}

const navigate = (path: string | null | undefined) => {
  // 空字符串是「公共资源」虚拟根的合法目标，只有 null/undefined 才忽略。
  if (path === null || path === undefined) return
  closeContextMenu()
  void load(path)
}

/* ---------------- 搜索 ---------------- */
const runRecursiveSearch = async (kw: string) => {
  const seq = ++searchSeq
  searching.value = true
  try {
    const res = await axios.get("/api/v1/chat/fs/search", {
      params: { q: kw, path: currentPath.value, max_results: 100 },
    })
    if (seq !== searchSeq) return
    const payload = res.data?.data
    const found = payload?.items
    recursiveItems.value = Array.isArray(found) ? found : []
    searchTruncated.value = Boolean(payload?.truncated)
  } catch {
    if (seq !== searchSeq) return
    recursiveItems.value = []
    searchTruncated.value = false
    showToast("搜索失败，请稍后重试", "error")
  } finally {
    if (seq === searchSeq) searching.value = false
  }
}

const onKeywordInput = () => {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  const kw = keyword.value.trim()
  if (!searchAll.value || kw.length < 2) {
    searchSeq += 1
    searching.value = false
    recursiveItems.value = []
    searchTruncated.value = false
    return
  }
  searchTimer = setTimeout(() => {
    void runRecursiveSearch(kw)
  }, 300)
}

/** 保持当前视图模式刷新：含子目录搜索态下重跑搜索，普通态下重读当前目录并保留当前筛选词。 */
const refreshCurrentView = (forceUsage = false) => {
  if (forceUsage) void refreshUsage(true)
  if (searchMode.value === "all") {
    void runRecursiveSearch(keyword.value.trim())
    return
  }
  void load(currentPath.value, { preserveSearch: true, syncUrl: false })
}

/**
 * 等刷新完成再返回（批量操作用）。
 *
 * 失败的条目还留在原处，刷新后要重新勾上，用户点一次就能重试；
 * 而 `load()` 自己会清空选择，所以必须拿到「刷新已完成」的时点。
 */
const refreshCurrentViewAsync = async () => {
  if (searchMode.value === "all") {
    await runRecursiveSearch(keyword.value.trim())
    return
  }
  await load(currentPath.value, { preserveSearch: true, syncUrl: false })
}

/* ---------------- 选择 ---------------- */
const toggleSelect = (path: string) => {
  if (selectedPaths.has(path)) selectedPaths.delete(path)
  else selectedPaths.add(path)
}
const toggleSelectAll = () => {
  const next = toggleSelectAllPaths(selectablePaths.value, selectedPaths)
  selectedPaths.clear()
  for (const path of next) selectedPaths.add(path)
}
const clearSelection = () => selectedPaths.clear()

const toggleSelectTrash = (path: string) => {
  if (selectedTrashPaths.has(path)) selectedTrashPaths.delete(path)
  else selectedTrashPaths.add(path)
}
const toggleSelectAllTrash = () => {
  const next = toggleSelectAllPaths(selectableTrashPaths.value, selectedTrashPaths)
  selectedTrashPaths.clear()
  for (const path of next) selectedTrashPaths.add(path)
}
const clearTrashSelection = () => selectedTrashPaths.clear()

/* ---------------- 上传 ---------------- */
const onPickFiles = (event: Event) => {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ""
  if (!files.length) return
  enqueue(files, currentPath.value)
}

const clearCompletedUploads = () => {
  clearCompleted()
  refreshCurrentView()
}

const clearFinishedUploads = () => {
  clearFinished()
  refreshCurrentView()
}

watch(uploading, (now, before) => {
  if (before && !now) {
    const doneTasks = snapshot.value.tasks.filter((t) => t.status === "done")
    const renamed = doneTasks.filter((t) => t.storedName && t.storedName !== t.name)
    refreshCurrentView(true)
    if (shouldAutoDismissUploads(snapshot.value.aggregate)) {
      if (renamed.length === 1) {
        showToast(`已上传（重名自动存为 ${renamed[0]!.storedName}）`, "success")
      } else if (renamed.length > 1) {
        showToast(`已上传 ${doneTasks.length} 个文件（其中 ${renamed.length} 个重名已自动加后缀）`, "success")
      } else if (doneTasks.length > 0) {
        showToast(`已上传 ${doneTasks.length} 个文件`, "success")
      }
      clearFinished()
    }
  }
})

let dragDepth = 0
const onDragEnter = (event: DragEvent) => {
  if (!canCreateHere.value || !event.dataTransfer?.types.includes("Files")) return
  dragDepth += 1
  dragging.value = true
}
const onDragOver = (event: DragEvent) => {
  if (canCreateHere.value && event.dataTransfer?.types.includes("Files")) {
    event.dataTransfer.dropEffect = "copy"
  }
}
const onDragLeave = (event: DragEvent) => {
  if (!event.dataTransfer?.types.includes("Files")) return
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragging.value = false
}
const onDrop = (event: DragEvent) => {
  dragDepth = 0
  dragging.value = false
  if (!event.dataTransfer?.types.includes("Files")) return
  if (!canCreateHere.value) {
    showToast("当前目录不支持上传", "warning")
    return
  }
  const dtItems = Array.from(event.dataTransfer?.items || [])
  let skippedDirs = 0
  const files: File[] = []
  if (dtItems.length && typeof (dtItems[0] as any)?.webkitGetAsEntry === "function") {
    for (const item of dtItems) {
      const entry = (item as any).webkitGetAsEntry?.()
      if (entry?.isDirectory) {
        skippedDirs += 1
        continue
      }
      const file = item.getAsFile()
      if (file) files.push(file)
    }
  } else {
    files.push(...Array.from(event.dataTransfer?.files || []))
  }
  if (skippedDirs > 0) {
    showToast("暂不支持直接拖拽上传文件夹，请压缩为 ZIP 后上传或先新建文件夹", "warning")
  }
  if (files.length) enqueue(files, currentPath.value)
}

/* ---------------- 最近文件 ---------------- */
const loadRecent = async () => {
  try {
    const res = await axios.get("/api/v1/chat/fs/recent-files")
    recentFiles.value = res.data?.data?.items || []
  } catch {
    recentFiles.value = []
  }
}

const recordRecentFile = async (item: Pick<FsItem, "path" | "name">) => {
  const norm = normalizeWorkspacePath(item.path)
  if (!norm || norm.includes("/.trash/") || norm.endsWith("/.trash")) return
  const nextEntry = { path: norm, name: item.name, mtime: Date.now() / 1000 }
  const merged = [
    nextEntry,
    ...recentFiles.value.filter((entry) => normalizeWorkspacePath(entry.path) !== norm),
  ].slice(0, MAX_RECENT_FILES)
  recentFiles.value = merged
  try {
    await axios.put("/api/v1/chat/fs/recent-files", { items: merged })
  } catch {
    /* 最近文件记录失败不影响主预览/下载流程 */
  }
}

const toggleRecentMenu = async () => {
  createOpen.value = false
  closeContextMenu()
  recentOpen.value = !recentOpen.value
  if (recentOpen.value) await loadRecent()
}

const toggleCreateMenu = () => {
  recentOpen.value = false
  closeContextMenu()
  createOpen.value = !createOpen.value
}

const openRecent = (recent: { path: string; name: string }) => {
  recentOpen.value = false
  handlePreview({ path: recent.path, name: recent.name })
}

const onCardMenuOpenChange = (path: string, isOpen: boolean) => {
  if (isOpen) openActionPath.value = path
  else if (openActionPath.value === path) openActionPath.value = ""
}

const onDocumentClick = (event: MouseEvent) => {
  const target = event.target as Node
  if (recentMenuRef.value && !recentMenuRef.value.contains(target)) recentOpen.value = false
  if (createMenuRef.value && !createMenuRef.value.contains(target)) createOpen.value = false
  if (typeFilterRef.value && !typeFilterRef.value.contains(target)) typeFilterOpen.value = false
  if (contextMenuRef.value && !contextMenuRef.value.contains(target)) closeContextMenu()
}

const onRowActionsOpen = () => {
  recentOpen.value = false
  createOpen.value = false
  closeContextMenu()
}

/* ---------------- 右键菜单 ---------------- */
const contextMenu = ref<null | { top: number; left: number; item: FsItem | null }>(null)
const contextMenuRef = ref<HTMLElement | null>(null)

const closeContextMenu = () => {
  contextMenu.value = null
}

const buildContextEntriesForItem = (item: FsItem): ManageRowActionEntry[] => {
  const target = {
    name: item.name,
    path: item.path,
    isDir: item.is_dir,
    isPreviewable: !item.is_dir && canPreviewWorkspaceFile(item.name),
  }
  const isProtected = isSystemProtectedItem(item)
  const canManage = canManageItem(item) && !isProtected
  const raw = buildWorkspaceActionEntries(
    buildWorkspaceItemMenuContext({
      item,
      target,
      userWorkspaceRoot: userWorkspaceRoot.value,
      isAdminScope: scope.value === "admin_all",
      inTrash: inTrash.value,
      isTrashRoot: isTrashRootItem(item),
      isSystemProtected: isProtected,
      canManage,
    }),
  )
  const base: ManageRowActionEntry[] = raw
    .filter((entry) => entry.key !== "mount")
    .map((entry) => ({
      ...entry,
      label: entry.key === "preview" ? "预览" : entry.key === "download" ? "下载" : entry.label,
    }))
  const result: ManageRowActionEntry[] = []
  const isPublic = isPublicWorkspaceItem(item, userWorkspaceRoot.value)
  const canMove = !inTrash.value && !isTrashRootItem(item) && !isProtected && !isPublic && canManage
  for (const entry of base) {
    result.push(entry)
    if (entry.key === "open-dir" && item.is_dir && !inTrash.value && !isTrashRootItem(item)) {
      result.push({ key: "download-zip", label: "打包下载", icon: "📦" })
    }
    if (entry.key === "copy-and-share" && !item.is_dir) {
      result.push({ key: "copy-to-workspace", label: "复制到我的目录", icon: "📥" })
    }
    if (entry.key === "copy-path" && !item.is_dir && !inTrash.value) {
      result.push({ key: "ask-ai", label: "用 AI 分析", icon: "✨" })
    }
    if (entry.key === "rename" && canMove) {
      result.push({ key: "move", label: "移动到…", icon: "📁" })
    }
  }
  return result
}

const contextMenuEntries = computed<ManageRowActionEntry[]>(() => {
  if (!contextMenu.value) return []
  if (contextMenu.value.item) {
    return buildContextEntriesForItem(contextMenu.value.item)
  }
  const emptyEntries: ManageRowActionEntry[] = []
  if (canCreateHere.value) {
    emptyEntries.push(
      { key: "create-dir", label: "新建文件夹", icon: "📁" },
      { key: "create-file", label: "新建文件", icon: "📄" },
      { key: "upload", label: "上传文件到此目录", icon: "⬆️" },
    )
  }
  if (inTrash.value) {
    emptyEntries.push({ key: "empty-trash", label: "清空回收站", icon: "🗑️", danger: true })
  }
  return emptyEntries
})

const positionContextMenu = async (clientX: number, clientY: number) => {
  await nextTick()
  if (!contextMenu.value) return
  const menuEl = contextMenuRef.value
  const width = menuEl?.offsetWidth || 192
  const height = menuEl?.offsetHeight || Math.max(44, contextMenuEntries.value.length * 32 + 12)
  const margin = 8
  const left = Math.max(margin, Math.min(clientX, window.innerWidth - width - margin))
  const top =
    clientY + height + margin > window.innerHeight
      ? Math.max(margin, clientY - height)
      : Math.max(margin, clientY)
  contextMenu.value = { ...contextMenu.value, left: Math.round(left), top: Math.round(top) }
}

const openItemContextMenu = (event: MouseEvent, item: FsItem) => {
  if (isVirtualRoot.value) return
  window.dispatchEvent(new CustomEvent("workspace-row-actions-open", { detail: "context-menu" }))
  recentOpen.value = false
  createOpen.value = false
  typeFilterOpen.value = false
  contextMenu.value = { top: event.clientY, left: event.clientX, item }
  void positionContextMenu(event.clientX, event.clientY)
}

const openEmptyContextMenu = (event: MouseEvent) => {
  if (isVirtualRoot.value || (!canCreateHere.value && !inTrash.value)) return
  window.dispatchEvent(new CustomEvent("workspace-row-actions-open", { detail: "context-menu" }))
  recentOpen.value = false
  createOpen.value = false
  typeFilterOpen.value = false
  contextMenu.value = { top: event.clientY, left: event.clientX, item: null }
  void positionContextMenu(event.clientX, event.clientY)
}

const runContextMenuAction = (key: ManageRowActionKey) => {
  const item = contextMenu.value?.item || null
  closeContextMenu()
  if (!item) {
    if (key === "create-dir") openCreate("dir")
    else if (key === "create-file") openCreate("file")
    else if (key === "upload") fileInputRef.value?.click()
    else if (key === "empty-trash") askEmptyTrash()
    return
  }
  switch (key) {
    case "open-dir":
      handleOpenDir(item)
      break
    case "preview":
      handlePreview(item)
      break
    case "download":
      handleDownload(item)
      break
    case "download-zip":
      handleDownloadZip(item)
      break
    case "share":
      openShare(item)
      break
    case "copy-and-share":
      void handleCopyAndShare(item)
      break
    case "copy-to-workspace":
      askCopyToWorkspace(item)
      break
    case "ask-ai":
      handleAskAi(item)
      break
    case "move":
      askMove(item)
      break
    case "rename":
      openRename(item)
      break
    case "copy-path":
      void handleCopyPath(item)
      break
    case "extract":
      askExtract(item)
      break
    case "delete":
      askDelete(item)
      break
    case "restore":
      void handleRestore(item)
      break
    case "purge":
      askPurge(item)
      break
    case "empty-trash":
      askEmptyTrash()
      break
    default:
      break
  }
}

/* ---------------- 文件操作 ---------------- */
/**
 * 预览走聊天页同一套画布。
 *
 * 关键：`buildWorkspaceCanvasPayload()` 产出的是 `canvas://file?path=...` 这种**协议字符串**，
 * 必须交给 `useWorkspaceCanvas.handleOpenCanvas()` 解析成真实内容（文本/图片/PDF/Office 分流、
 * CSV 透视、旧版 .ppt 降级为下载），再把结果喂给 `ChatCanvas` 渲染。
 * 独立页传入 `defaultPinned: false`，避免预览默认钉住右侧把列表操作列盖死。
 */
const {
  canvasVisible,
  canvasPinned,
  canvasData,
  handleOpenCanvas,
  closeCanvas,
} = useWorkspaceCanvas({
  // 独立页没有会话上下文：预览接口的 conversation_id 是可选参数
  getConversationId: () => "",
  resolveFileUrl: (url: string) => resolveFsPreviewUrl(url, null),
  showToast,
  defaultPinned: false,
})

/** 当画布正在展示某个被删除/移动/重命名的路径（或其下子文件）时自动同步关闭或更新。 */
const syncCanvasOnPathAffected = (affectedPath: string, nextPath?: string, nextName?: string) => {
  const openPath = canvasData.value?.sourcePath
  if (!canvasVisible.value || !openPath) return
  if (!isWithinDirectory(openPath, affectedPath)) return
  if (nextPath && normalizeWorkspacePath(openPath) === normalizeWorkspacePath(affectedPath) && canvasData.value) {
    canvasData.value = {
      ...canvasData.value,
      sourcePath: nextPath,
      title: nextName ? displayFileName(nextName) : canvasData.value.title,
    }
    return
  }
  closeCanvas()
}

const handleCanvasSaved = (payload: { path: string; content: string }) => {
  if (!payload?.path) return
  if (
    searchMode.value === "all" ||
    normalizeWorkspacePath(parentDirectoryOf(payload.path)) === normalizeWorkspacePath(currentPath.value)
  ) {
    refreshCurrentView(true)
  }
}

const handleCanvasAnalyzeOutput = (question: string) => {
  const trimmed = String(question || "").trim()
  if (!trimmed) return
  void router.push({
    path: "/dashboard/chat",
    query: {
      new: "1",
      portal_question: trimmed,
      portal_action: "fill",
    },
  })
}

const handleOpenDir = (item: FsItem) => {
  if (item.is_dir) navigate(item.path)
}

/** 双击送达的统一入口：目录进去，文件开预览 */
const openItem = (item: FsItem) => {
  if (item.is_dir) {
    navigate(item.path)
    return
  }
  handlePreview(item)
}

const handlePreview = (item: Pick<FsItem, "path" | "name">) => {
  if (!canPreviewWorkspaceFile(item.name)) {
    showToast("该格式不支持预览，请下载后查看", "warning")
    return
  }
  void recordRecentFile(item)
  void handleOpenCanvas(buildWorkspaceCanvasPayload(item.path, displayFileName(item.name)))
}

const handleDownload = (item: FsItem) => {
  void recordRecentFile(item)
  void downloadWorkspaceFile({
    path: item.path,
    name: displayFileName(item.name),
    showToast: (message: string, type?: "success" | "error" | "warning" | "info") => showToast(message, type || "info"),
  })
}

const handleDownloadZip = (item: FsItem) => {
  void batchDownloadWorkspaceEntries({
    paths: [item.path],
    archiveName: displayFileName(item.name),
    showToast: (message: string, type?: "success" | "error" | "warning" | "info") => showToast(message, type || "info"),
  })
}

const handleBatchDownload = async () => {
  if (batchDownloading.value || !selectedPaths.size) return
  batchDownloading.value = true
  try {
    await batchDownloadWorkspaceEntries({
      paths: Array.from(selectedPaths),
      showToast: (message: string, type?: "success" | "error" | "warning" | "info") => showToast(message, type || "info"),
    })
  } finally {
    batchDownloading.value = false
  }
}

const handleAskAi = (item: FsItem) => {
  const root = normalizeWorkspacePath(userWorkspaceRoot.value)
  const norm = normalizeWorkspacePath(item.path)
  const logicalPath =
    root && isWithinDirectory(norm, root)
      ? `/workspace/${norm.slice(root.length).replace(/^\/+/, "")}`
      : item.path
  const prompt = `请帮我读取并分析工作空间里的文件「${displayFileName(item.name)}」（路径：${logicalPath}），总结核心内容与关键结论。`
  // 必须用 send：fill 只会把问题填进聊天页输入框、不发送，用户点完看起来「什么都没发生」，
  // 而这颗菜单项的语义就是「直接让 AI 分析这个文件」。
  void router.push({
    path: "/dashboard/chat",
    query: {
      new: "1",
      portal_question: prompt,
      portal_action: "send",
    },
  })
}

/** 文件分享：路径原样交给后端解析（绝对路径或相对工作区根都能认）。 */
const shareTarget = ref<null | { path: string; name: string; size: number }>(null)

const openShare = (item: FsItem) => {
  shareTarget.value = { path: item.path, name: displayFileName(item.name), size: item.size }
}

const handleShareCreated = () => {
  showToast("分享已创建", "success")
}

/**
 * 公共文件不能直接分享（分享只覆盖本人工作区内的文件），先复制一份到我的目录再分享。
 *
 * 复制成功后直接弹出副本的分享弹窗：用户点这个菜单项就是为了分享，
 * 再让他自己找到副本、再点一次分享纯属多余。
 */
const copyingShareSource = ref<Record<string, boolean>>({})
const handleCopyAndShare = async (item: FsItem) => {
  if (copyingShareSource.value[item.path]) return
  copyingShareSource.value = { ...copyingShareSource.value, [item.path]: true }
  try {
    const res = await copyFileToWorkspace(item.path)
    const created = res.data?.data
    if (!created?.path) {
      showToast("复制失败：服务端未返回新文件路径", "error")
      return
    }
    showToast(`已复制到我的目录：${created.name}`, "success")
    // 副本落在本人工作区根（target_dir 未指定），而此刻用户看的是公共目录——
    // 刷新当前目录永远看不到副本，必须跳到副本所在目录才算「让副本可见」。
    void load(parentDirectoryOf(created.path))
    openShare({ path: created.path, name: created.name, size: created.size } as FsItem)
  } catch (err: any) {
    const msg = err?.response?.data?.detail || err?.message || "复制失败"
    showToast(msg, "error")
  } finally {
    const next = { ...copyingShareSource.value }
    delete next[item.path]
    copyingShareSource.value = next
  }
}

/* ---------------- 移动 / 复制到目录 ---------------- */
const dirActionDialog = ref<null | {
  mode: "move" | "copy"
  items: FsItem[]
  defaultPath: string
  subtitle: string
}>(null)
const dirActionSubmitting = ref(false)

/**
 * 「移动到…」的默认目标目录。
 *
 * 早先默认当前目录：用户点开选择器直接确认，后端必然回 400「目标目录与当前所在目录相同」。
 * 改为当前目录的父目录（仍限本人工作区内），根目录下才回落到工作区根。
 */
const moveDefaultDir = () => {
  const root = normalizeWorkspacePath(userWorkspaceRoot.value)
  const parent = normalizeWorkspacePath(parentDirectoryOf(currentPath.value))
  if (!inTrash.value && root && parent && parent !== root && isWithinDirectory(parent, root)) {
    return parent
  }
  return userWorkspaceRoot.value
}

const askMove = (item: FsItem) => {
  const defaultPath = moveDefaultDir()
  dirActionDialog.value = {
    mode: "move",
    items: [item],
    defaultPath,
    subtitle: `将「${displayFileName(item.name)}」移动到所选目录`,
  }
}

const askBatchMove = () => {
  const byPath = new Map(sourceItems.value.map((entry) => [entry.path, entry]))
  const movable = Array.from(selectedPaths)
    .map((path) => byPath.get(path))
    .filter((item): item is FsItem => Boolean(item && canMoveItem(item)))
  if (!movable.length) {
    showToast("选中的项目不支持移动", "warning")
    return
  }
  const defaultPath = moveDefaultDir()
  dirActionDialog.value = {
    mode: "move",
    items: movable,
    defaultPath,
    subtitle: `将已选的 ${movable.length} 项移动到所选目录`,
  }
}

const askCopyToWorkspace = (item: FsItem) => {
  dirActionDialog.value = {
    mode: "copy",
    items: [item],
    defaultPath: userWorkspaceRoot.value,
    subtitle: `将「${displayFileName(item.name)}」复制到我的目录`,
  }
}

const submitDirAction = async (payload: { parentPath: string; name: string }) => {
  if (!dirActionDialog.value || dirActionSubmitting.value) return
  const action = dirActionDialog.value
  dirActionSubmitting.value = true
  try {
    if (action.mode === "copy") {
      const target = action.items[0]
      if (!target) return
      const res = await copyFileToWorkspace(target.path, payload.parentPath)
      const created = res.data?.data
      const relDir = formatRelativeWorkspacePath(payload.parentPath, userWorkspaceRoot.value)
      showToast(`已复制到 ${relDir}${created?.name ? `：${created.name}` : ""}`, "success")
      dirActionDialog.value = null
      void refreshUsage(true)
      if (normalizeWorkspacePath(payload.parentPath) === normalizeWorkspacePath(currentPath.value)) {
        refreshCurrentView()
      }
      return
    }

    let movedOk = 0
    const failedMessages: string[] = []
    for (const item of action.items) {
      try {
        const res = await moveWorkspaceEntry(item.path, payload.parentPath)
        const nextPath = res.data?.data?.path
        syncCanvasOnPathAffected(item.path, nextPath, item.name)
        selectedPaths.delete(item.path)
        movedOk += 1
      } catch (err: any) {
        failedMessages.push(err?.response?.data?.detail || `${displayFileName(item.name)} 移动失败`)
      }
    }
    const relDir = formatRelativeWorkspacePath(payload.parentPath, userWorkspaceRoot.value)
    if (movedOk > 0 && failedMessages.length === 0) {
      showToast(`已将 ${movedOk} 项移动到 ${relDir}`, "success")
    } else if (movedOk > 0) {
      showToast(`已移动 ${movedOk} 项，${failedMessages.length} 项失败：${failedMessages[0]}`, "warning")
    } else {
      showToast(failedMessages[0] || "移动失败，请稍后重试", "error")
    }
    dirActionDialog.value = null
    refreshCurrentView(true)
  } catch (err: any) {
    showToast(err?.response?.data?.detail || "操作失败，请稍后重试", "error")
  } finally {
    dirActionSubmitting.value = false
  }
}

const handleCopyPath = async (item: FsItem) => {
  try {
    await copyTextToClipboard(item.path)
    showToast("已复制路径", "success")
  } catch {
    showToast("复制失败，请手动选择", "error")
  }
}

/* ---------------- 新建 / 重命名 ---------------- */
const dialog = ref<null | "dir" | "file" | "rename">(null)
const dialogDraft = ref("")
const dialogSubmitting = ref(false)
const renameTarget = ref<FsItem | null>(null)

const dialogTitle = computed(() =>
  dialog.value === "dir" ? "新建文件夹" : dialog.value === "file" ? "新建文件" : "重命名",
)
const dialogMessage = computed(() => {
  if (dialog.value === "rename") return `修改「${renameTarget.value?.name ?? ""}」的名称`
  return `在「${currentLabel.value}」下创建`
})
const dialogPlaceholder = computed(() =>
  dialog.value === "file" ? "例如 report.md" : dialog.value === "dir" ? "文件夹名称" : "新名称",
)

const openCreate = (kind: "dir" | "file") => {
  createOpen.value = false
  dialog.value = kind
  dialogDraft.value = ""
}

const openRename = (item: FsItem) => {
  renameTarget.value = item
  dialog.value = "rename"
  dialogDraft.value = item.name
}

const closeDialog = () => {
  dialog.value = null
  dialogSubmitting.value = false
  renameTarget.value = null
}

const submitDialog = async (value: string) => {
  const name = (value || "").trim()
  if (!name) {
    showToast("请输入名称", "warning")
    return
  }
  if (dialog.value === "rename" && renameTarget.value && name === renameTarget.value.name) {
    closeDialog()
    return
  }
  dialogSubmitting.value = true
  try {
    if (dialog.value === "dir" || dialog.value === "file") {
      await createWorkspaceEntry({ parentPath: currentPath.value, name, kind: dialog.value === "dir" ? "dir" : "file" })
      showToast(dialog.value === "dir" ? "文件夹已创建" : "文件已创建", "success")
    } else if (dialog.value === "rename" && renameTarget.value) {
      const oldPath = renameTarget.value.path
      const res = await renameWorkspaceEntry(oldPath, name)
      const nextPath = res.data?.data?.path
      syncCanvasOnPathAffected(oldPath, nextPath, name)
      showToast("已重命名", "success")
    }
    closeDialog()
    refreshCurrentView(true)
  } catch (error: any) {
    showToast(error?.response?.data?.detail || "操作失败，请稍后重试", "error")
  } finally {
    dialogSubmitting.value = false
  }
}

/* ---------------- 解压 ---------------- */
const extractDialog = ref<{ item: FsItem; parentPath: string; defaultName: string } | null>(null)
const extractSubmitting = ref(false)

const askExtract = (item: FsItem) => {
  if (!isExtractableArchive(item.name)) {
    showToast("暂不支持 rar / 7z，请先转成 zip", "warning")
    return
  }
  const defaultTargetDir =
    !inTrash.value && isWithinDirectory(currentPath.value, userWorkspaceRoot.value)
      ? currentPath.value
      : userWorkspaceRoot.value
  extractDialog.value = {
    item,
    parentPath: defaultTargetDir,
    defaultName: displayFileName(item.name).replace(/\.(tar\.gz|tgz|tar|zip)$/i, ""),
  }
}

const submitExtract = async (payload: { parentPath: string; name: string }) => {
  if (!extractDialog.value || extractSubmitting.value) return
  extractSubmitting.value = true
  try {
    const res = await extractWorkspaceArchive({
      archivePath: extractDialog.value.item.path,
      targetDir: payload.parentPath,
      subdir: payload.name,
    })
    const data = res.data?.data
    const count = Number(data?.file_count ?? 0)
    showToast(`已解压 ${count} 个文件`, "success")
    extractDialog.value = null
    void refreshUsage(true)
    if (normalizeWorkspacePath(payload.parentPath) === normalizeWorkspacePath(currentPath.value)) {
      refreshCurrentView()
    } else {
      void load(payload.parentPath)
    }
  } catch (error: any) {
    showToast(error?.response?.data?.detail || "解压失败，请稍后重试", "error")
  } finally {
    extractSubmitting.value = false
  }
}

/* ---------------- 删除 / 回收站 ---------------- */
const confirmAction = ref<{
  title: string
  message: string
  confirmText: string
  type?: "danger" | "primary" | "warning"
  run: () => Promise<void>
} | null>(null)
const confirmSubmitting = ref(false)

/** 有限并发批量执行辅助，避免选中大量文件时串行阻塞。 */
const runConcurrentBatch = async <T>(
  list: T[],
  worker: (item: T) => Promise<void>,
  concurrency = 4,
) => {
  let cursor = 0
  const runners = Array.from({ length: Math.min(concurrency, list.length) }, async () => {
    while (cursor < list.length) {
      const idx = cursor
      cursor += 1
      await Promise.resolve(worker(list[idx]!))
    }
  })
  await Promise.all(runners)
}

/**
 * 删除确认。
 *
 * 管理员删公共目录（docs / skills / branding 等）时，后端 `/fs/delete-entry`
 * 直接 rmtree/remove，**不会**进本人回收站（公共目录不属于任何人的回收站）。
 * 所以确认框必须说「永久删除」，否则管理员会以为还能还原回来。
 * 判定复用 `isPublicWorkspaceItem`，与聊天抽屉同口径。
 */
const askDelete = (item: FsItem) => {
  const permanent = scope.value === "admin_all" && isPublicWorkspaceItem(item, userWorkspaceRoot.value)
  confirmAction.value = permanent
    ? {
        title: "永久删除",
        message: `「${displayFileName(item.name)}」属于公共目录，删除后无法恢复，也不会进入回收站。`,
        confirmText: "永久删除",
        type: "danger",
        run: async () => {
          await deleteWorkspaceEntry(item.path)
          syncCanvasOnPathAffected(item.path)
          showToast("已永久删除", "success")
          refreshCurrentView(true)
        },
      }
    : {
        title: "移到回收站",
        message: `「${displayFileName(item.name)}」会被移到回收站，可以再还原回来。`,
        confirmText: "移到回收站",
        type: "danger",
        run: async () => {
          await deleteWorkspaceEntry(item.path)
          syncCanvasOnPathAffected(item.path)
          showToast("已移到回收站", "success")
          refreshCurrentView(true)
        },
      }
}

const askPurge = (item: FsItem) => {
  const cleanName = inTrash.value ? parseTrashedFileName(item.name).displayName : displayFileName(item.name)
  confirmAction.value = {
    title: "彻底删除",
    message: `「${cleanName}」将无法恢复，确定删除吗？`,
    confirmText: "彻底删除",
    type: "danger",
    run: async () => {
      await purgeWorkspaceEntry(item.path)
      syncCanvasOnPathAffected(item.path)
      showToast("已彻底删除", "success")
      refreshCurrentView(true)
    },
  }
}

const askEmptyTrash = () => {
  confirmAction.value = {
    title: "清空回收站",
    message: "回收站里的文件将全部永久删除，无法恢复。",
    confirmText: "清空",
    type: "danger",
    run: async () => {
      const trashRoot = activeTrashRoot.value
      await emptyWorkspaceTrash(trashRoot || undefined)
      if (trashRoot) {
        syncCanvasOnPathAffected(trashRoot)
      }
      showToast("回收站已清空", "success")
      void refreshUsage(true)
      // 在回收站视图里清空时回到回收站根（子目录可能已不存在，刷新会 404）；
      // 在「我的目录」右键回收站图标清空时不能把用户强行切到回收站视图。
      if (trashRootOfCurrentPath.value) {
        void load(trashRoot)
      } else {
        refreshCurrentView()
      }
    },
  }
}

const askBatchDelete = () => {
  const byPath = new Map(sourceItems.value.map((entry) => [entry.path, entry]))
  const selectedItems = Array.from(selectedPaths)
    .map((path) => byPath.get(path))
    .filter((item): item is FsItem => !!item)
  const { deletable, skipped } = planBatchDelete(selectedItems, canManageItem)
  if (!deletable.length) {
    showToast("选中的项目都不能删除", "warning")
    return
  }
  // 管理员勾中的公共目录条目会被后端直接永久删除（不进任何人的回收站），
  // 文案必须分开说清楚，否则「可以再还原回来」是假的。
  const permanentCount = deletable.filter(
    (item) => scope.value === "admin_all" && isPublicWorkspaceItem(item, userWorkspaceRoot.value),
  ).length
  const trashedCount = deletable.length - permanentCount
  const lines: string[] = []
  if (trashedCount > 0) lines.push(`${trashedCount} 项将被移到回收站，可以再还原回来。`)
  if (permanentCount > 0) lines.push(`${permanentCount} 项属于公共目录，将被永久删除且不会进入回收站。`)
  if (skipped.length > 0) lines.push(`另有 ${skipped.length} 项受保护或只读，将自动跳过。`)
  confirmAction.value = {
    title: "批量删除",
    message: lines.join("\n"),
    confirmText: permanentCount > 0 ? "永久删除" : "移到回收站",
    type: "danger",
    run: async () => {
      let trashedOk = 0
      let permanentOk = 0
      const failedPaths: string[] = []
      await runConcurrentBatch(deletable, async (item) => {
        const isPermanent = scope.value === "admin_all" && isPublicWorkspaceItem(item, userWorkspaceRoot.value)
        try {
          await deleteWorkspaceEntry(item.path)
          syncCanvasOnPathAffected(item.path)
          selectedPaths.delete(item.path)
          if (isPermanent) permanentOk += 1
          else trashedOk += 1
        } catch {
          failedPaths.push(item.path)
        }
      })
      const failedCount = failedPaths.length
      const summary: string[] = []
      if (trashedOk > 0) summary.push(`${trashedOk} 项已移到回收站`)
      if (permanentOk > 0) summary.push(`${permanentOk} 项已永久删除`)
      if (failedCount > 0) summary.push(`${failedCount} 项删除失败`)
      if (summary.length) {
        showToast(summary.join("，"), failedCount > 0 ? "warning" : "success")
      }
      void refreshUsage(true)
      await refreshCurrentViewAsync()
      // 失败的条目仍在原处：刷新后把勾选补回来，用户点一次就能重试
      failedPaths.forEach((path) => selectedPaths.add(path))
    },
  }
}

const runConfirmAction = async () => {
  if (!confirmAction.value) return
  const action = confirmAction.value
  confirmSubmitting.value = true
  try {
    await action.run()
    confirmAction.value = null
  } catch (error: any) {
    showToast(error?.response?.data?.detail || "操作失败，请稍后重试", "error")
  } finally {
    confirmSubmitting.value = false
  }
}

const handleRestore = async (item: FsItem) => {
  try {
    const res = await restoreWorkspaceEntry(item.path)
    const restored = res.data?.data
    if (restored?.path) {
      const targetDirLabel = formatRelativeWorkspacePath(parentDirectoryOf(restored.path), userWorkspaceRoot.value)
      const renamedHint =
        restored.original_name && restored.name && restored.name !== restored.original_name
          ? `（重命名为 ${restored.name}）`
          : ""
      showToast(`已还原至 ${targetDirLabel}${renamedHint}`, "success")
    } else {
      showToast("已还原", "success")
    }
    refreshCurrentView(true)
  } catch (error: any) {
    showToast(error?.response?.data?.detail || "还原失败，请稍后重试", "error")
  }
}

const askBatchRestore = () => {
  const paths = Array.from(selectedTrashPaths)
  if (!paths.length) return
  confirmAction.value = {
    title: "批量还原",
    message: `将把选中的 ${paths.length} 项从回收站还原（优先恢复至删除前原目录，否则恢复至我的目录）。`,
    confirmText: "还原",
    type: "primary",
    run: async () => {
      let okCount = 0
      const failed: string[] = []
      await runConcurrentBatch(paths, async (path) => {
        try {
          await restoreWorkspaceEntry(path)
          selectedTrashPaths.delete(path)
          okCount += 1
        } catch {
          failed.push(path)
        }
      })
      const msg = failed.length
        ? `已还原 ${okCount} 项，${failed.length} 项失败`
        : `已还原 ${okCount} 项`
      showToast(msg, failed.length ? "warning" : "success")
      void refreshUsage(true)
      await refreshCurrentViewAsync()
      failed.forEach((p) => selectedTrashPaths.add(p))
    },
  }
}

const askBatchPurge = () => {
  const paths = Array.from(selectedTrashPaths)
  if (!paths.length) return
  confirmAction.value = {
    title: "批量彻底删除",
    message: `选中的 ${paths.length} 项将被永久删除且无法恢复，确定继续吗？`,
    confirmText: "彻底删除",
    type: "danger",
    run: async () => {
      let okCount = 0
      const failed: string[] = []
      await runConcurrentBatch(paths, async (path) => {
        try {
          await purgeWorkspaceEntry(path)
          syncCanvasOnPathAffected(path)
          selectedTrashPaths.delete(path)
          okCount += 1
        } catch {
          failed.push(path)
        }
      })
      const msg = failed.length
        ? `已彻底删除 ${okCount} 项，${failed.length} 项失败`
        : `已彻底删除 ${okCount} 项`
      showToast(msg, failed.length ? "warning" : "success")
      void refreshUsage(true)
      await refreshCurrentViewAsync()
      failed.forEach((p) => selectedTrashPaths.add(p))
    },
  }
}

/* ---------------- 键盘快捷键与 URL 同步 ---------------- */
const isTypingTarget = (target: EventTarget | null): boolean => {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable
}

const onGlobalKeydown = (event: KeyboardEvent) => {
  if (event.key === "Escape") {
    if (contextMenu.value) {
      closeContextMenu()
      return
    }
    if (!dialog.value && !confirmAction.value && !dirActionDialog.value && !extractDialog.value && !shareTarget.value) {
      clearSelection()
      clearTrashSelection()
    }
    return
  }
  if (isTypingTarget(event.target)) return
  if (dialog.value || confirmAction.value || dirActionDialog.value || extractDialog.value || shareTarget.value || canvasVisible.value) {
    return
  }

  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "a") {
    event.preventDefault()
    if (inTrash.value) toggleSelectAllTrash()
    else toggleSelectAll()
    return
  }

  if (event.key === "F2" && !inTrash.value && selectedPaths.size === 1) {
    const onlyPath = Array.from(selectedPaths)[0]
    const item = sourceItems.value.find((entry) => entry.path === onlyPath)
    if (item && canManageItem(item)) {
      event.preventDefault()
      openRename(item)
    }
    return
  }

  if (event.key === "Delete" || (event.metaKey && event.key === "Backspace")) {
    if (inTrash.value && selectedTrashPaths.size > 0) {
      event.preventDefault()
      askBatchPurge()
    } else if (!inTrash.value && selectedPaths.size > 0) {
      event.preventDefault()
      askBatchDelete()
    }
  }
}

watch(
  () => route.query.path,
  (nextQueryPath) => {
    if (syncingUrlPath) return
    const target = typeof nextQueryPath === "string" ? nextQueryPath.trim() : ""
    if (target === VIRTUAL_ROOT_URL_TOKEN) {
      if (!isVirtualRoot.value) {
        void load("", { syncUrl: false })
      }
      return
    }
    const normTarget = normalizeWorkspacePath(target)
    const normCurrent = normalizeWorkspacePath(currentPath.value)
    if (!target) {
      if (isVirtualRoot.value || (userWorkspaceRoot.value && normCurrent !== normalizeWorkspacePath(userWorkspaceRoot.value))) {
        void load("", { preferUserRoot: true, syncUrl: false })
      }
      return
    }
    if (normTarget && normTarget !== normCurrent) {
      void load(target, { syncUrl: false })
    }
  },
)

onMounted(() => {
  document.addEventListener("click", onDocumentClick)
  document.addEventListener("keydown", onGlobalKeydown)
  window.addEventListener("workspace-row-actions-open", onRowActionsOpen)
  window.addEventListener("scroll", closeContextMenu, true)
  window.addEventListener("resize", closeContextMenu)
  const initialPath = typeof route.query.path === "string" ? route.query.path.trim() : ""
  if (initialPath === VIRTUAL_ROOT_URL_TOKEN) {
    void load("", { syncUrl: false })
  } else if (initialPath) {
    void load(initialPath, { syncUrl: false, fallbackToHomeOnError: true })
  } else {
    // 首屏直接落「我的目录」（后端 prefer=user_root 解析到本人私有工作区），
    // 公共目录走侧栏「公共资源」入口。
    void load("", { preferUserRoot: true })
  }
  void loadRecent()
  void refreshUsage()
})

onBeforeUnmount(() => {
  document.removeEventListener("click", onDocumentClick)
  document.removeEventListener("keydown", onGlobalKeydown)
  window.removeEventListener("workspace-row-actions-open", onRowActionsOpen)
  window.removeEventListener("scroll", closeContextMenu, true)
  window.removeEventListener("resize", closeContextMenu)
  if (searchTimer) clearTimeout(searchTimer)
})
</script>
