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
    <div class="hero-surface relative overflow-hidden rounded-2xl border border-gray-200/80 p-4 shadow-sm sm:p-5">
      <HeroSilhouette variant="cards" :height="44" />
      <div class="relative z-10 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div class="min-w-0">
          <h1 class="text-xl font-bold tracking-tight text-gray-900 dark:text-gray-100 sm:text-2xl">工作空间</h1>
          <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
            AI 干活时的文件都放这里：上传的资料、生成的报告、会话中间产物，都能直接打开看。
          </p>
        </div>

        <div class="flex flex-shrink-0 items-center gap-2">
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
                <span>{{ fileEmoji(recent.name) }}</span>
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
                📁 新建文件夹
              </button>
              <button type="button" class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700/60" @click="openCreate('file')">
                📄 新建文件
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
      <label class="flex flex-shrink-0 cursor-pointer items-center gap-1 text-[11px] text-gray-500 dark:text-gray-400">
        <input v-model="searchAll" type="checkbox" class="rounded border-gray-300 text-primary focus:ring-primary/30" @change="onKeywordInput" />
        全目录
      </label>
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
        <select
          v-model="typeFilter"
          class="h-7 rounded-lg border border-gray-200 bg-white px-2 py-0 text-xs text-gray-600 outline-none dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
        >
          <option value="all">全部类型</option>
          <option value="doc">文档</option>
          <option value="sheet">表格</option>
          <option value="image">图片</option>
          <option value="archive">压缩包</option>
          <option value="code">代码</option>
        </select>
        <select
          v-model="sortBy"
          class="h-7 rounded-lg border border-gray-200 bg-white px-2 py-0 text-xs text-gray-600 outline-none dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
        >
          <option value="mtime">按时间</option>
          <option value="name">按名称</option>
          <option value="size">按大小</option>
        </select>
        <div class="flex overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700">
          <button
            type="button"
            class="px-2 py-1 text-xs"
            :class="viewMode === 'list' ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white' : 'text-gray-500'"
            title="列表视图"
            @click="viewMode = 'list'"
          >☰</button>
          <button
            type="button"
            class="px-2 py-1 text-xs"
            :class="viewMode === 'grid' ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white' : 'text-gray-500'"
            title="卡片视图"
            @click="viewMode = 'grid'"
          >▦</button>
        </div>
      </div>
    </div>

    <!-- 批量操作条 -->
    <div
      v-if="selectedPaths.size && !inTrash"
      class="flex items-center gap-3 rounded-xl border border-blue-100 bg-blue-50/70 px-3 py-2 text-xs text-blue-700 dark:border-blue-900/50 dark:bg-blue-950/30 dark:text-blue-300"
    >
      <span>已选 {{ selectedPaths.size }} 项</span>
      <button type="button" class="font-semibold hover:underline" @click="askBatchDelete">批量删除</button>
      <button type="button" class="text-blue-500 hover:underline dark:text-blue-400" @click="clearSelection">取消选择</button>
    </div>

    <!-- 列表视图 -->
    <div v-if="viewMode === 'list'" class="min-h-0 flex-1 overflow-auto rounded-xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <table class="w-full border-collapse text-sm">
        <thead class="sticky top-0 z-10 bg-gray-50/90 backdrop-blur dark:bg-gray-800/90">
          <tr class="text-left text-xs text-gray-500 dark:text-gray-400">
            <th class="w-9 px-2 py-2">
              <input
                type="checkbox"
                class="rounded border-gray-300 text-primary focus:ring-primary/30"
                :checked="selectionState === 'all'"
                :indeterminate="selectionState === 'partial'"
                :disabled="!selectableItems.length"
                @change="toggleSelectAll"
              />
            </th>
            <th class="px-3 py-2 font-medium">名称</th>
            <th class="w-24 px-3 py-2 font-medium">大小</th>
            <th class="w-40 px-3 py-2 font-medium">修改时间</th>
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
              {{ loadFailed ? "目录读取失败，请重试" : keyword ? "没有匹配的文件" : "这个目录还是空的，上传一个文件试试" }}
            </td>
          </tr>
          <tr
            v-for="item in visibleItems"
            v-else
            :key="item.path"
            class="cursor-default select-none border-t border-gray-100 transition-colors hover:bg-gray-50/70 dark:border-gray-800 dark:hover:bg-gray-800/50"
            :title="item.is_dir ? '双击进入目录' : '双击打开预览'"
            @dblclick="openItem(item)"
          >
            <td class="px-2 py-2">
              <input
                v-if="canSelect(item)"
                type="checkbox"
                class="rounded border-gray-300 text-primary focus:ring-primary/30"
                :checked="selectedPaths.has(item.path)"
                @change="toggleSelect(item.path)"
              />
            </td>
            <td class="min-w-0 px-3 py-2">
              <button
                v-if="item.is_dir"
                type="button"
                class="flex min-w-0 items-center gap-2 text-left"
                @click.stop="navigate(item.path)"
                @dblclick.stop
              >
                <span class="text-base leading-none">📁</span>
                <span class="truncate font-medium text-gray-800 hover:text-primary dark:text-gray-200">{{ displayFileName(item.name) }}</span>
              </button>
              <div v-else class="flex min-w-0 items-center gap-2">
                <span class="text-base leading-none">{{ fileEmoji(item.name) }}</span>
                <span class="truncate text-gray-800 dark:text-gray-200" :title="item.path">{{ displayFileName(item.name) }}</span>
              </div>
            </td>
            <td class="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
              {{ item.is_dir ? "—" : formatSize(item.size) }}
            </td>
            <td class="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">{{ formatTime(item.mtime) }}</td>
            <td class="px-3 py-2">
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
                  @preview="handlePreview"
                  @download="handleDownload"
                  @share="openShare"
                  @copy-and-share="handleCopyAndShare"
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
    <div v-else class="min-h-0 flex-1 overflow-auto rounded-xl bg-white p-2 dark:bg-gray-900">
      <p v-if="loading || searching" class="py-10 text-center text-xs text-gray-400">
        {{ searching ? "正在搜索…" : "正在加载…" }}
      </p>
      <p v-else-if="!visibleItems.length" class="py-10 text-center text-xs text-gray-400">
        {{ loadFailed ? "目录读取失败，请重试" : keyword ? "没有匹配的文件" : "这个目录还是空的，上传一个文件试试" }}
      </p>
      <!-- 大图相册风：aspect-[4/3] 满宽媒体区 + 左上悬浮勾选 + 紧凑信息底栏（文件名与 ⋮ 并排） -->
      <div v-else class="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
        <div
          v-for="item in visibleItems"
          :key="item.path"
          class="group flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white transition-all hover:border-primary/40 hover:shadow-sm dark:border-gray-800 dark:bg-gray-800/60"
          :class="selectedPaths.has(item.path) ? 'border-primary/50 ring-2 ring-primary/70' : ''"
          :title="item.is_dir ? '双击进入目录' : '双击打开预览'"
          @dblclick.stop="openItem(item)"
        >
          <div
            class="relative flex aspect-[4/3] w-full items-center justify-center overflow-hidden bg-gray-50 dark:bg-gray-800/80"
            :title="item.path"
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
            <span v-else class="select-none text-4xl leading-none">{{ item.is_dir ? "📁" : fileEmoji(item.name) }}</span>

            <label
              v-if="canSelect(item)"
              class="absolute left-2 top-2 z-10 flex h-6 w-6 cursor-pointer items-center justify-center rounded-md bg-white/85 shadow-sm backdrop-blur-sm transition-opacity dark:bg-gray-900/80"
              :class="selectedPaths.has(item.path) ? 'opacity-100' : 'opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100'"
              @click.stop
              @dblclick.stop
            >
              <input
                type="checkbox"
                class="rounded border-gray-300 text-primary focus:ring-primary/30"
                :checked="selectedPaths.has(item.path)"
                @change="toggleSelect(item.path)"
              />
            </label>
          </div>

          <div class="flex items-center justify-between gap-1.5 border-t border-gray-100 px-2.5 py-2 dark:border-gray-700/60">
            <div class="min-w-0 flex-1" :title="item.path">
              <p class="truncate text-xs font-medium text-gray-800 dark:text-gray-100">{{ displayFileName(item.name) }}</p>
              <p class="mt-0.5 text-[10px] text-gray-400">{{ item.is_dir ? "文件夹" : formatSize(item.size) }}</p>
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
                @preview="handlePreview"
                @download="handleDownload"
                @share="openShare"
                @copy-and-share="handleCopyAndShare"
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

    <!-- 上传进度 -->
    <div v-if="showUploadPanel" class="rounded-xl border border-gray-200 bg-gray-50/70 px-3 py-2 dark:border-gray-800 dark:bg-gray-800/50">
      <div class="mb-1 flex items-center justify-between text-xs text-gray-600 dark:text-gray-300">
        <span>上传进度 {{ snapshot.aggregate.done }} / {{ snapshot.aggregate.total }}</span>
        <button type="button" class="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200" @click="clearFinishedUploads">清除已完成</button>
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

    <!-- 预览复用聊天页的 Canvas：markdown 渲染 / 代码高亮 / CSV 透视 / 文档查看器全都在这里，
         自己再写一套预览只会出现「同一个文件两种样子」 -->
    <ChatCanvas
      v-model:pinned="canvasPinned"
      :visible="canvasVisible"
      :data="canvasData"
      dock-side="right"
      @close="closeCanvas"
      @content-saved="handleCanvasSaved"
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
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue"
import axios from "@/utils/axios"
import InputModal from "@/components/InputModal.vue"
import ConfirmModal from "@/components/ConfirmModal.vue"
import WorkspaceRowActions from "@/components/workspace/WorkspaceRowActions.vue"
import WorkspaceDirectorySaveDialog from "@/components/embed/WorkspaceDirectorySaveDialog.vue"
import HeroSilhouette from "@/components/common/HeroSilhouette.vue"
import ChatCanvas from "@/components/embed/ChatCanvas.vue"
import { useWorkspaceCanvas } from "@/composables/chat/useWorkspaceCanvas"
import ShareFileDialog from "@/components/ShareFileDialog.vue"
import { useToast } from "@/composables/useToast"
import { useWorkspaceUploadQueue } from "@/composables/useWorkspaceUploadQueue"
import { shouldAutoDismissUploads } from "@/utils/workspaceUploadQueue"
import { isExtractableArchive, isPublicWorkspaceItem } from "@/utils/workspaceActionEntries"
import { planBatchDelete, selectAllState, toggleSelectAllPaths } from "@/utils/workspaceBatchSelection"
import { copyFileToWorkspace } from "@/utils/workspaceFilePreview"
import {
  WORKSPACE_ROOT_PROTECTED_NAMES,
  buildWorkspaceBreadcrumbs,
  isProtectedWorkspaceEntry,
  isWithinDirectory,
  normalizeWorkspacePath,
  parentDirectoryOf,
} from "@/utils/workspacePath"
import { displayFileName } from "@/utils/workspaceFileName"
import {
  IMAGE_EXTENSIONS,
  canPreviewWorkspaceFile,
  copyTextToClipboard,
  createWorkspaceEntry,
  deleteWorkspaceEntry,
  downloadWorkspaceFile,
  emptyWorkspaceTrash,
  extractWorkspaceArchive,
  purgeWorkspaceEntry,
  renameWorkspaceEntry,
  buildWorkspaceCanvasPayload,
  resolveFsPreviewUrl,
  restoreWorkspaceEntry,
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
type TypeFilterKey = "all" | "doc" | "sheet" | "image" | "archive" | "code"

const SORT_KEYS: readonly SortKey[] = ["mtime", "name", "size"]
const TYPE_FILTER_KEYS: readonly TypeFilterKey[] = ["all", "doc", "sheet", "image", "archive", "code"]

/**
 * 视图偏好：视图模式 / 排序 / 类型筛选。
 *
 * 三项都存 localStorage，刷新后原样恢复。没有改用后端 `/fs/browser-prefs`：
 * 那份存的抽屉的偏好（是否含子目录、类型筛选），语义与独立页的表格/卡片取舍并不等价，
 * 硬接会让两个界面的偏好互相覆盖。
 */
const readStoredPrefs = (): { viewMode?: ViewMode; sortBy?: SortKey; typeFilter?: TypeFilterKey } => {
  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return {
      viewMode: parsed?.viewMode === "grid" ? "grid" : parsed?.viewMode === "list" ? "list" : undefined,
      sortBy: SORT_KEYS.find((key) => key === parsed?.sortBy),
      typeFilter: TYPE_FILTER_KEYS.find((key) => key === parsed?.typeFilter),
    }
  } catch {
    return {}
  }
}

const storedPrefs = readStoredPrefs()

const { showToast } = useToast()
const { snapshot, enqueue, cancel: cancelUpload, retry: retryUpload, clearFinished } = useWorkspaceUploadQueue()

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
/** 最近一次目录读取失败：路径保持旧值，列表不能被清空伪装成「空目录」。 */
const loadFailed = ref(false)
const keyword = ref("")
const searchAll = ref(false)
const recursiveItems = ref<FsItem[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)
const viewMode = ref<ViewMode>(storedPrefs.viewMode || "list")
const sortBy = ref<SortKey>(storedPrefs.sortBy || "mtime")
const typeFilter = ref<TypeFilterKey>(storedPrefs.typeFilter || "all")
const selectedPaths = reactive(new Set<string>())
const dragging = ref(false)
const createOpen = ref(false)
const recentOpen = ref(false)
const openActionPath = ref("")
const recentFiles = ref<Array<{ path: string; name: string; mtime: number }>>([])
const recentMenuRef = ref<HTMLElement | null>(null)
const createMenuRef = ref<HTMLElement | null>(null)

const SYSTEM_PROTECTED = WORKSPACE_ROOT_PROTECTED_NAMES

watch([viewMode, sortBy, typeFilter], ([nextView, nextSort, nextFilter]) => {
  try {
    localStorage.setItem(
      PREFS_STORAGE_KEY,
      JSON.stringify({ viewMode: nextView, sortBy: nextSort, typeFilter: nextFilter }),
    )
  } catch {
    /* ignore storage quota or private mode errors */
  }
})

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
// 管理员默认落在自己的私有工作区：那里不是「全部用户」视图，别让徽标误导。
const isInsideOwnWorkspace = computed(() => isWithinDirectory(currentPath.value, userWorkspaceRoot.value))
const searchMode = computed<"current" | "all">(() => (searchAll.value && keyword.value.trim().length >= 2 ? "all" : "current"))
// 虚拟根（「公共资源」）是授权根汇总，不是真实可写目录：新建/上传/勾选都必须关掉，
// 否则按钮点下去只会吃后端 403。
const canCreateHere = computed(
  () => !inTrash.value && !loading.value && Boolean(currentPath.value) && searchMode.value !== "all" && writable.value,
)

const sourceItems = computed(() => (searchMode.value === "all" ? recursiveItems.value : items.value))

const TYPE_GROUPS: Record<string, string[]> = {
  doc: ["md", "txt", "doc", "docx", "pdf", "rtf", "epub", "html", "htm", "ppt", "pptx"],
  sheet: ["xls", "xlsx", "xlsm", "csv", "tsv"],
  image: ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"],
  archive: ["zip", "tar", "gz", "tgz", "7z", "rar"],
  code: ["py", "sh", "js", "ts", "json", "yaml", "yml", "sql", "go", "java", "c", "cpp", "css"],
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
 * 用 `IMAGE_EXTENSIONS` 而不是 `fileEmoji` 里那份更宽的表：那张表含 svg，
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
  list.sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
    if (sortBy.value === "name") return displayFileName(a.name).localeCompare(displayFileName(b.name), "zh-CN")
    if (sortBy.value === "size") return (b.size || 0) - (a.size || 0)
    return (b.mtime || 0) - (a.mtime || 0)
  })
  return list
})

const selectableItems = computed(() => visibleItems.value.filter((item) => canSelect(item)))
const selectablePaths = computed(() => selectableItems.value.map((item) => item.path))
const selectionState = computed(() => selectAllState(selectablePaths.value, selectedPaths))

// 筛选/搜索变化导致部分已选项不再可见时，自动清理隐藏勾选，防止批量删除误删看不见的文件。
watch(selectablePaths, (nextPaths) => {
  const allowed = new Set(nextPaths)
  for (const path of [...selectedPaths]) {
    if (!allowed.has(path)) selectedPaths.delete(path)
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

const fileEmoji = (name: string) => {
  const ext = extOf(name)
  if (["md", "txt", "doc", "docx", "pdf", "ppt", "pptx"].includes(ext)) return "📄"
  if (["xls", "xlsx", "xlsm", "csv"].includes(ext)) return "📊"
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "🖼️"
  if (["zip", "tar", "gz", "tgz", "7z", "rar"].includes(ext)) return "🗜️"
  if (["py", "sh", "js", "ts", "json", "yaml", "yml"].includes(ext)) return "📜"
  return "📄"
}

let loadSeq = 0
let searchSeq = 0
let searchTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 读取目录。
 *
 * `preferUserRoot` 只用于首屏：把空 path 交给后端解析成「本人私有工作区」
 * （`GET /chat/fs/list?prefer=user_root`），不必先拿虚拟根再二次跳转。
 * 「公共资源」入口走默认空 path，拿到的就是授权根汇总。
 */
const load = async (path = "", options: { preferUserRoot?: boolean } = {}) => {
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
    keyword.value = ""
    searchAll.value = false
    recursiveItems.value = []
  } catch {
    if (seq !== loadSeq) return
    loadFailed.value = true
    showToast("读取目录失败，请稍后重试", "error")
    // 刻意不清空 items：currentPath 仍是旧目录，清空会让旧目录看起来「空了」。
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

const navigate = (path: string | null | undefined) => {
  // 空字符串是「公共资源」虚拟根的合法目标，只有 null/undefined 才忽略。
  if (path === null || path === undefined) return
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
    const found = res.data?.data?.items
    recursiveItems.value = Array.isArray(found) ? found : []
  } catch {
    if (seq !== searchSeq) return
    recursiveItems.value = []
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
    return
  }
  searchTimer = setTimeout(() => {
    void runRecursiveSearch(kw)
  }, 300)
}

/** 保持当前视图模式刷新：全目录搜索态下重跑搜索，普通态下重读当前目录。 */
const refreshCurrentView = () => {
  if (searchMode.value === "all") {
    void runRecursiveSearch(keyword.value.trim())
    return
  }
  void load(currentPath.value)
}

/**
 * 等刷新完成再返回（批量删除用）。
 *
 * 删除失败的条目还留在原处，刷新后要重新勾上，用户点一次「批量删除」就能重试；
 * 而 `load()` 自己会清空选择，所以必须拿到「刷新已完成」的时点。
 */
const refreshCurrentViewAsync = async () => {
  if (searchMode.value === "all") {
    await runRecursiveSearch(keyword.value.trim())
    return
  }
  await load(currentPath.value)
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

/* ---------------- 上传 ---------------- */
const onPickFiles = (event: Event) => {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ""
  if (!files.length) return
  enqueue(files, currentPath.value)
}

const clearFinishedUploads = () => {
  clearFinished()
  refreshCurrentView()
}

watch(uploading, (now, before) => {
  if (before && !now) {
    refreshCurrentView()
    if (shouldAutoDismissUploads(snapshot.value.aggregate)) {
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
const onDragLeave = () => {
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
  const files = Array.from(event.dataTransfer?.files || [])
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
  recentOpen.value = !recentOpen.value
  if (recentOpen.value) await loadRecent()
}

const toggleCreateMenu = () => {
  recentOpen.value = false
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
}

const onRowActionsOpen = () => {
  recentOpen.value = false
  createOpen.value = false
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

const handleCanvasSaved = (payload: { path: string; content: string }) => {
  if (!payload?.path) return
  if (
    searchMode.value === "all" ||
    normalizeWorkspacePath(parentDirectoryOf(payload.path)) === normalizeWorkspacePath(currentPath.value)
  ) {
    refreshCurrentView()
  }
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
      await renameWorkspaceEntry(renameTarget.value.path, name)
      showToast("已重命名", "success")
    }
    closeDialog()
    refreshCurrentView()
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
          showToast("已永久删除", "success")
          refreshCurrentView()
        },
      }
    : {
        title: "移到回收站",
        message: `「${displayFileName(item.name)}」会被移到回收站，可以再还原回来。`,
        confirmText: "移到回收站",
        type: "danger",
        run: async () => {
          await deleteWorkspaceEntry(item.path)
          showToast("已移到回收站", "success")
          refreshCurrentView()
        },
      }
}

const askPurge = (item: FsItem) => {
  confirmAction.value = {
    title: "彻底删除",
    message: `「${displayFileName(item.name)}」将无法恢复，确定删除吗？`,
    confirmText: "彻底删除",
    type: "danger",
    run: async () => {
      await purgeWorkspaceEntry(item.path)
      showToast("已彻底删除", "success")
      refreshCurrentView()
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
      await emptyWorkspaceTrash()
      showToast("回收站已清空", "success")
      refreshCurrentView()
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
      for (const item of deletable) {
        const isPermanent = scope.value === "admin_all" && isPublicWorkspaceItem(item, userWorkspaceRoot.value)
        try {
          await deleteWorkspaceEntry(item.path)
          selectedPaths.delete(item.path)
          if (isPermanent) permanentOk += 1
          else trashedOk += 1
        } catch {
          failedPaths.push(item.path)
        }
      }
      const failedCount = failedPaths.length
      const summary: string[] = []
      if (trashedOk > 0) summary.push(`${trashedOk} 项已移到回收站`)
      if (permanentOk > 0) summary.push(`${permanentOk} 项已永久删除`)
      if (failedCount > 0) summary.push(`${failedCount} 项删除失败`)
      if (summary.length) {
        showToast(summary.join("，"), failedCount > 0 ? "warning" : "success")
      }
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
    await restoreWorkspaceEntry(item.path)
    showToast("已还原", "success")
    refreshCurrentView()
  } catch (error: any) {
    showToast(error?.response?.data?.detail || "还原失败，请稍后重试", "error")
  }
}

onMounted(() => {
  document.addEventListener("click", onDocumentClick)
  window.addEventListener("workspace-row-actions-open", onRowActionsOpen)
  // 首屏直接落「我的目录」（后端 prefer=user_root 解析到本人私有工作区），
  // 公共目录走侧栏「公共资源」入口。
  void load("", { preferUserRoot: true })
  void loadRecent()
})

onBeforeUnmount(() => {
  document.removeEventListener("click", onDocumentClick)
  window.removeEventListener("workspace-row-actions-open", onRowActionsOpen)
  if (searchTimer) clearTimeout(searchTimer)
})
</script>
