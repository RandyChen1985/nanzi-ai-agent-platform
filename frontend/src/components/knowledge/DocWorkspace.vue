<template>
  <section class="flex min-h-0 flex-1 flex-col bg-white">
    <!-- ── 工具条：视图 / 搜索 / 筛选 / 排序 ───────────────────────────── -->
    <div class="flex flex-wrap items-center gap-2 border-b border-gray-100 px-4 py-2">
      <div class="flex shrink-0 items-center rounded-lg bg-gray-100 p-0.5" role="tablist" aria-label="视图切换">
        <button
          v-for="option in VIEW_OPTIONS"
          :key="option.key"
          type="button"
          class="flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-colors"
          :class="viewMode === option.key ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:text-gray-700'"
          :aria-selected="viewMode === option.key"
          role="tab"
          @click="viewMode = option.key"
        >
          <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" :d="option.icon" />
          </svg>
          {{ option.label }}
        </button>
      </div>

      <div class="relative min-w-[160px] flex-1">
        <svg
          class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" />
        </svg>
        <!--
          搜索走服务端 name 参数：本地只持有当前页，过滤当前页会把「第 2 页的命中」
          直接漏掉。输入防抖在页面层做，这里只上报原始值。
        -->
        <input
          :value="keyword"
          type="search"
          :placeholder="`在 ${allTotal} 份文档中搜索`"
          class="w-full rounded-lg border border-gray-200 py-1.5 pl-8 pr-2 text-xs text-gray-700 outline-none transition-colors placeholder:text-gray-400 focus:border-primary"
          @input="onKeywordInput"
        />
      </div>

      <div class="flex shrink-0 items-center gap-1">
        <button
          v-for="chip in statusChips"
          :key="chip.key"
          type="button"
          class="flex items-center gap-1 rounded-full border px-2 py-1 text-[11px] transition-colors"
          :class="status === chip.key
            ? 'border-primary bg-primary/5 text-primary'
            : 'border-gray-200 text-gray-500 hover:border-gray-300 hover:text-gray-700'"
          :title="chip.title"
          @click="toggleStatus(chip.key)"
        >
          <span class="h-1.5 w-1.5 rounded-full" :class="chip.dot" />
          {{ chip.label }}
          <span class="font-semibold">{{ chip.count }}</span>
        </button>
      </div>

      <select
        :value="sort"
        class="shrink-0 rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-[11px] text-gray-600 outline-none focus:border-primary"
        aria-label="排序方式"
        @change="onSortChange"
      >
        <option v-for="option in SORT_OPTIONS" :key="option.key" :value="option.key">{{ option.label }}</option>
      </select>
    </div>

    <!--
      ── 面包屑 + 文件夹筛选 ────────────────────────────────────────────
      文件夹从「树内展开的行」改成过滤维度：树形展开 + 页码会互相打架
      （展开态跨页丢失、一个文件夹被切到两页），而点文件夹=按路径过滤后，
      分页只作用在过滤结果上，页码与总数始终自洽。
    -->
    <div class="flex flex-wrap items-center gap-1 border-b border-gray-100 px-4 py-1.5 text-[11px] text-gray-500">
      <button
        type="button"
        class="rounded px-1 py-0.5 hover:bg-gray-100 hover:text-gray-700"
        :class="!folder ? 'font-semibold text-gray-700' : ''"
        @click="openFolder('')"
      >
        全部文件
      </button>
      <template v-for="(item, index) in breadcrumb" :key="item.path">
        <span class="text-gray-300">/</span>
        <button
          type="button"
          class="max-w-[180px] truncate rounded px-1 py-0.5"
          :class="index === breadcrumb.length - 1 ? 'font-semibold text-gray-700' : 'hover:bg-gray-100 hover:text-gray-700'"
          @click="openFolder(item.path)"
        >
          {{ item.name }}
        </button>
      </template>
      <span class="ml-auto hidden shrink-0 text-gray-400 sm:inline">
        拖文件到下方文件夹标签即可移动
      </span>
    </div>

    <div v-if="folder || subFolders.length || canManage" class="flex flex-wrap items-center gap-1.5 border-b border-gray-100 px-4 py-2">
      <button
        v-if="folder"
        type="button"
        class="flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[11px] text-gray-500 transition-colors hover:border-primary hover:text-primary"
        @click="openFolder(parentPath)"
      >
        <svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7" />
        </svg>
        上一级
      </button>
      <button
        v-if="folder"
        type="button"
        class="rounded-full border px-2.5 py-1 text-[11px] transition-colors"
        :class="folder === '__unclassified__'
          ? 'border-primary bg-primary/5 font-semibold text-primary'
          : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300'"
        @click="openFolder('')"
      >
        全部文件夹
        <span class="ml-0.5 tabular-nums opacity-70">{{ allTotal }}</span>
      </button>

      <button
        v-for="node in subFolders"
        :key="node.path"
        type="button"
        class="flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[11px] text-gray-600 transition-colors hover:border-primary hover:text-primary"
        :class="dragOverPath === node.path ? 'border-primary bg-primary/5 text-primary' : ''"
        :title="folderCountHint(node.path)"
        :data-folder-path="node.path"
        :data-count="countOf(node.path)"
        @click="openFolder(node.path)"
        @dragover.prevent="dragOverPath = node.path"
        @dragleave="dragOverPath = null"
        @drop.prevent="onFolderChipDrop(node.path)"
      >
        <svg class="h-3 w-3 shrink-0 text-amber-500" viewBox="0 0 24 24" fill="currentColor">
          <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
        </svg>
        <span class="max-w-[140px] truncate">{{ node.name }}</span>
        <span class="tabular-nums opacity-60">{{ countOf(node.path) }}</span>
      </button>

      <button
        v-if="!folder && unclassifiedCount > 0"
        type="button"
        class="flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[11px] text-gray-500 transition-colors hover:border-primary hover:text-primary"
        @click="openFolder(UNCLASSIFIED)"
      >
        未分类
        <span class="tabular-nums opacity-60">{{ unclassifiedCount }}</span>
      </button>

      <!-- 文件夹自身的管理：树行没了，操作挂到「当前打开的文件夹」上 -->
      <span v-if="folder && folder !== UNCLASSIFIED && canManage" class="flex items-center gap-0.5">
        <button
          type="button"
          class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
          title="在此文件夹下新建子文件夹"
          @click="startCreate(folder)"
        >
          <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" d="M12 5v14M5 12h14" />
          </svg>
        </button>
        <button
          type="button"
          class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
          title="重命名当前文件夹"
          @click="startRenamePath(folder)"
        >
          <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
          </svg>
        </button>
        <button
          type="button"
          class="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
          title="删除当前文件夹"
          @click="emit('delete-folder', folder)"
        >
          <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
        </button>
      </span>

      <button
        v-if="canManage && !creating"
        type="button"
        class="rounded-full border border-dashed border-gray-300 px-2.5 py-1 text-[11px] text-gray-400 transition-colors hover:border-primary hover:text-primary"
        @click="startCreate(folder === UNCLASSIFIED ? '' : folder)"
      >
        + 新建文件夹
      </button>
    </div>

    <!-- 新建 / 重命名文件夹的内联输入 -->
    <div v-if="creating" class="flex flex-wrap items-center gap-2 border-b border-gray-100 bg-gray-50/60 px-4 py-2 text-xs">
      <svg class="h-3.5 w-3.5 text-amber-500" viewBox="0 0 24 24" fill="currentColor">
        <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
      </svg>
      <input
        ref="createInput"
        v-model="creating.value"
        :placeholder="creating.parentPath ? `在「${creating.parentPath}」下新建` : '新文件夹名称'"
        class="w-56 rounded border border-primary px-1.5 py-1 text-xs outline-none"
        @keydown.enter.prevent="commitCreate"
        @keydown.esc.prevent="creating = null"
      />
      <button type="button" class="rounded px-2 py-1 text-primary hover:bg-primary/5" @click="commitCreate">确定</button>
      <button type="button" class="rounded px-2 py-1 text-gray-400 hover:bg-gray-100" @click="creating = null">取消</button>
      <span class="text-[10px] text-gray-400">用 / 分隔可以建多级，例如 制度文件/2025 年度</span>
    </div>

    <div v-if="renaming" class="flex flex-wrap items-center gap-2 border-b border-gray-100 bg-gray-50/60 px-4 py-2 text-xs">
      <span class="text-[11px] text-gray-500">重命名「{{ renaming.path }}」：</span>
      <input
        ref="renameInput"
        v-model="renaming.value"
        class="w-56 rounded border border-primary px-1.5 py-1 text-xs outline-none"
        @keydown.enter.prevent="commitRename"
        @keydown.esc.prevent="renaming = null"
      />
      <button type="button" class="rounded px-2 py-1 text-primary hover:bg-primary/5" @click="commitRename">确定</button>
      <button type="button" class="rounded px-2 py-1 text-gray-400 hover:bg-gray-100" @click="renaming = null">取消</button>
    </div>

    <!-- ── 批量操作条 ─────────────────────────────────────────────────── -->
    <div
      v-if="selectedIds.length"
      class="flex flex-wrap items-center gap-2 border-b border-primary/20 bg-primary/5 px-4 py-1.5 text-xs"
    >
      <span class="font-medium text-primary">已选 {{ selectedIds.length }} 份</span>
      <button
        v-if="canParse"
        type="button"
        class="rounded-md px-2 py-1 text-gray-600 hover:bg-white hover:text-primary"
        @click="emit('bulk-parse', selectedIds)"
      >
        批量解析
      </button>
      <button
        v-if="canManage"
        type="button"
        class="rounded-md px-2 py-1 text-gray-600 hover:bg-white hover:text-primary disabled:opacity-40"
        :disabled="bulkEnabledBusy"
        title="选中的文档重新参与检索召回（切片仍保留）"
        @click="emit('bulk-toggle-enabled', { ids: selectedIds, enabled: true })"
      >启用检索</button>
      <button
        v-if="canManage"
        type="button"
        class="rounded-md px-2 py-1 text-gray-600 hover:bg-white hover:text-primary disabled:opacity-40"
        :disabled="bulkEnabledBusy"
        title="选中的文档停用：切片不删，但不再被召回"
        @click="emit('bulk-toggle-enabled', { ids: selectedIds, enabled: false })"
      >停用检索</button>
      <button
        v-if="canManage"
        type="button"
        class="rounded-md px-2 py-1 text-gray-600 hover:bg-white hover:text-primary"
        @click="emit('bulk-move')"
      >
        移动到…
      </button>
      <button
        v-if="canDeleteDoc"
        type="button"
        class="rounded-md px-2 py-1 text-red-600 hover:bg-white"
        @click="emit('bulk-delete', selectedIds)"
      >
        删除
      </button>
      <button type="button" class="ml-auto rounded-md px-2 py-1 text-gray-500 hover:bg-white" @click="clearSelection">
        取消选择
      </button>
    </div>

    <!-- ── 内容区 ─────────────────────────────────────────────────────── -->
    <div class="min-h-0 flex-1 overflow-auto" @click="closeRowMenu">
      <div v-if="loading" class="px-4 py-16 text-center text-xs text-gray-400">正在读取文档…</div>

      <div v-else-if="!allTotal && !docs.length" class="px-6 py-16 text-center">
        <p class="text-sm font-medium text-gray-700">这个知识库还没有文档</p>
        <p class="mt-1 text-xs text-gray-400">上传之后，解析完成的文档会被智能体检索到。</p>
      </div>

      <div v-else-if="!docs.length" class="px-6 py-16 text-center">
        <p class="text-sm font-medium text-gray-700">
          {{ isFiltering ? '没有匹配的文档' : (folder ? '这个文件夹还是空的' : '这一页没有文档') }}
        </p>
        <p class="mt-1 text-xs text-gray-400">
          {{ isFiltering ? '换个关键词，或清掉上方的状态筛选。' : '把文件拖进来，或上传后选择归入此文件夹。' }}
        </p>
      </div>

      <!-- 列表视图：7 列在窄屏塞不下，给个最小宽度让容器横向滚动 -->
      <template v-else-if="viewMode === 'list'">
        <div class="min-w-[640px]">
        <div class="sticky top-0 z-10 grid items-center gap-2 border-b border-gray-100 bg-white px-4 py-2 text-[11px] font-medium text-gray-400" :class="GRID_COLS">
          <span class="flex items-center justify-center">
            <input
              v-if="canManage"
              type="checkbox"
              class="h-3 w-3 cursor-pointer rounded border-gray-300 text-primary focus:ring-primary"
              :checked="allVisibleSelected"
              :aria-label="allVisibleSelected ? '取消全选' : '全选当前页'"
              @change="toggleSelectAll"
            />
          </span>
          <span>名称</span>
          <span>解析状态</span>
          <span class="text-right">切片</span>
          <span class="text-right">大小</span>
          <span class="text-right">更新时间</span>
          <span />
        </div>

        <div
          v-for="doc in docs"
          :key="doc.id"
          class="group grid items-center gap-2 border-b border-gray-50 px-4 py-2 text-xs transition-colors"
          :class="[
            GRID_COLS,
            'cursor-pointer hover:bg-blue-50/40',
            activeId === doc.id ? 'bg-blue-50/60' : '',
          ]"
          :draggable="canManage"
          @click="emit('open-doc', doc)"
          @dragstart="onDragStart(doc)"
        >
          <!-- 勾选 -->
          <span class="flex items-center justify-center" @click.stop>
            <input
              v-if="canManage"
              type="checkbox"
              class="h-3 w-3 cursor-pointer rounded border-gray-300 text-primary focus:ring-primary"
              :checked="selectedIds.includes(doc.id)"
              :aria-label="`选择文档 ${doc.name || doc.id}`"
              @change="toggleSelect(doc.id)"
            />
          </span>

          <!-- 名称 -->
          <div class="flex min-w-0 items-center gap-1.5">
            <span class="flex h-4 w-4 shrink-0 items-center justify-center rounded text-[8px] font-bold" :class="fileKindOf(doc).className">
              {{ fileKindOf(doc).label }}
            </span>
            <span class="truncate" :class="isDocEnabled(doc) ? 'text-gray-700' : 'text-gray-400 line-through'">
              {{ doc.name || doc.id }}
            </span>
            <span v-if="!isDocEnabled(doc)" class="shrink-0 rounded bg-amber-100 px-1 text-[9px] font-semibold text-amber-700">已停用</span>
            <!-- 没有进入文件夹时补一个路径后缀，否则同名文件分不清在哪层 -->
            <span v-if="!folder && pathOfDoc(doc.id)" class="shrink-0 truncate text-[10px] text-gray-400">
              {{ pathOfDoc(doc.id) }}
            </span>
          </div>

          <!-- 状态 -->
          <span class="flex items-center gap-1 whitespace-nowrap text-[11px]" :class="statusMeta(doc).textClass">
            <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="statusMeta(doc).dotClass" />
            {{ statusMeta(doc).label }}
            <span v-if="progressLabel(doc)" class="shrink-0 tabular-nums text-[10px] text-gray-400">
              {{ progressLabel(doc) }}
            </span>
          </span>

          <span class="text-right text-[11px] text-gray-500">{{ doc.chunk_count ?? '—' }}</span>
          <span class="text-right text-[11px] text-gray-500">{{ formatSize(doc.size) }}</span>
          <span class="text-right text-[11px] text-gray-400">{{ formatTime(doc.update_time || doc.created_at) }}</span>

          <!-- 行操作 -->
          <span class="flex items-center justify-end gap-0.5" @click.stop>
            <button
              v-if="canParse && statusOf(doc) !== 'parsed'"
              type="button"
              class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-primary"
              :title="statusOf(doc) === 'failed' ? '重试解析' : '开始解析'"
              @click="emit('parse-doc', doc)"
            >
              <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                <path stroke-linecap="round" stroke-linejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </button>

            <div v-if="canManage" class="relative">
              <button
                type="button"
                class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-primary"
                title="移动到文件夹"
                @click.stop="rowMenuId = rowMenuId === doc.id ? null : doc.id"
              >
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
                </svg>
              </button>
              <div
                v-if="rowMenuId === doc.id"
                class="absolute right-0 top-full z-30 mt-1 max-h-64 w-48 overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
                @click.stop
              >
                <p class="px-2 py-1 text-[10px] font-semibold text-gray-400">移动到</p>
                <button
                  v-for="folderPath in allFolderPaths"
                  :key="folderPath"
                  type="button"
                  class="block w-full truncate px-3 py-1.5 text-left text-[11px] text-gray-700 hover:bg-gray-50"
                  :disabled="folderPath === pathOfDoc(doc.id)"
                  :class="folderPath === pathOfDoc(doc.id) ? 'opacity-40' : ''"
                  @click="moveDoc(doc, folderPath)"
                >
                  {{ folderPath }}
                </button>
                <button
                  v-if="pathOfDoc(doc.id)"
                  type="button"
                  class="block w-full px-3 py-1.5 text-left text-[11px] text-red-600 hover:bg-red-50"
                  @click="moveDoc(doc, '')"
                >
                  移出文件夹
                </button>
              </div>
            </div>

            <button
              v-if="canDeleteDoc"
              type="button"
              class="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
              title="删除文档"
              @click="emit('delete-doc', doc)"
            >
              <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          </span>
        </div>
        </div>
      </template>

      <!-- 网格视图 -->
      <div v-else-if="viewMode === 'grid'" class="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 lg:grid-cols-4">
        <button
          v-for="doc in docs"
          :key="doc.id"
          type="button"
          class="group flex h-[104px] flex-col rounded-xl border border-gray-200 bg-white p-3 text-left transition-all hover:border-primary/40 hover:shadow-sm"
          :class="activeId === doc.id ? 'border-primary/50 bg-blue-50/40' : ''"
          @click="emit('open-doc', doc)"
        >
          <div class="flex min-w-0 items-start gap-2">
            <span class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded text-[8px] font-bold" :class="fileKindOf(doc).className">
              {{ fileKindOf(doc).label }}
            </span>
            <span class="line-clamp-2 text-xs font-medium text-gray-700">{{ doc.name || doc.id }}</span>
          </div>
          <div class="mt-auto flex items-center justify-between text-[10px] text-gray-400">
            <span class="flex items-center gap-1" :class="statusMeta(doc).textClass">
              <span class="h-1.5 w-1.5 rounded-full" :class="statusMeta(doc).dotClass" />
              {{ statusMeta(doc).label }}
            </span>
            <span>{{ formatSize(doc.size) }}</span>
          </div>
        </button>
      </div>

      <!-- 解析看板 -->
      <div v-else class="flex h-full gap-3 overflow-x-auto p-4">
        <section v-for="column in kanbanColumns" :key="column.status" class="flex min-w-[220px] flex-1 flex-col rounded-xl bg-gray-50/70">
          <header class="flex items-center gap-2 px-3 py-2 text-xs font-medium text-gray-600">
            <span class="h-1.5 w-1.5 rounded-full" :class="column.dotClass" />
            {{ column.label }}
            <span class="ml-auto text-[11px] text-gray-400">{{ column.total }}</span>
          </header>
          <div class="min-h-0 flex-1 space-y-2 overflow-auto px-2 pb-2">
            <button
              v-for="doc in column.docs"
              :key="doc.id"
              type="button"
              class="block w-full rounded-lg border border-gray-200 bg-white p-2.5 text-left hover:border-primary/40"
              @click="emit('open-doc', doc)"
            >
              <div class="flex items-start gap-1.5">
                <span class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded text-[8px] font-bold" :class="fileKindOf(doc).className">
                  {{ fileKindOf(doc).label }}
                </span>
                <span class="line-clamp-2 text-[11px] font-medium text-gray-700">{{ doc.name || doc.id }}</span>
              </div>
              <div class="mt-1.5 flex items-center justify-between text-[10px] text-gray-400">
                <span class="truncate">{{ pathOfDoc(doc.id) || '未分类' }}</span>
                <span>{{ formatTime(doc.update_time || doc.created_at) }}</span>
              </div>
              <div v-if="statusOf(doc) === 'parsing'" class="mt-1.5 h-1 overflow-hidden rounded-full bg-gray-100">
                <div class="h-full rounded-full bg-amber-400" :style="{ width: `${progressPercent(doc)}%` }" />
              </div>
              <p v-if="statusOf(doc) === 'failed'" class="mt-1 line-clamp-2 text-[10px] text-red-500">
                {{ doc.progress_msg || '未提取到可用文本，可开启 OCR 后重试' }}
              </p>
            </button>
            <p v-if="!column.docs.length" class="px-1 py-6 text-center text-[10px] text-gray-300">暂无</p>
            <button
              v-if="column.total > column.docs.length"
              type="button"
              class="w-full rounded-md border border-dashed border-gray-300 bg-white px-2 py-1 text-[10px] text-gray-500 hover:border-primary hover:text-primary"
              @click="switchToList"
            >
              还有 {{ column.total - column.docs.length }} 份 · 切到列表看全部
            </button>
          </div>
        </section>
      </div>
    </div>

    <!--
      分页条：列表/网格共用同一套 page/pageSize（切视图不重置页码）。
      看板不参与分页——它是「看哪堆积压」的视图，逐份翻页请回列表。
    -->
    <PaginationBar
      v-if="viewMode !== 'kanban'"
      :total="total"
      :page="page"
      :page-size="pageSize"
      :page-sizes="pageSizes"
      unit="份文档"
      :note="selectedIds.length ? `已选 ${selectedIds.length} 份（跨页保留）` : ''"
      @update:page="emit('update:page', $event)"
      @update:page-size="emit('update:pageSize', $event)"
    />
    <div v-else class="flex flex-wrap items-center gap-2 border-t border-gray-200 px-3 py-2 text-[11px] text-gray-500">
      <span>看板展示<b class="mx-0.5 font-semibold text-gray-700">当前页</b>的解析状态分布，每列最多 {{ KANBAN_LIMIT }} 份</span>
      <span class="flex-1" />
      <button type="button" class="text-[11px] font-medium text-primary hover:underline" @click="switchToList">
        切到列表看全部 {{ total.toLocaleString() }} 份
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  buildFolderIndex,
  folderNameOf,
  joinFolderPath,
  parentFolderPath,
  splitFolderPath,
  type FolderNode,
} from '@/utils/knowledgeFolders'
import { docStatusOf, isDocEnabled, type DocStatus } from '@/utils/knowledgeDocStatus'
import PaginationBar from '@/components/common/PaginationBar.vue'

/**
 * 文档工作区：把「浏览 / 排序 / 筛选 / 多选 / 移动」这些高频操作从左侧树里搬出来。
 *
 * 这一层刻意不碰接口：所有写操作都 emit 给页面，由页面复用既有的 API 调用
 * （移动走 folder-structure、解析走 RAGFlow run、删除走原弹窗），避免同一条链路
 * 出现两套实现。
 *
 * 分页之后，**筛选/排序/翻页状态也归页面**（受控组件）：数据只有当前页，
 * 本地再过滤一次会漏掉其他页的命中，也会让「共 N 份」与页码对不上。
 */

type WorkspaceDoc = {
  id: string
  name?: string
  status?: string
  run?: string | number
  chunk_count?: number
  size?: number
  update_time?: string
  created_at?: string
  progress?: number
  progress_msg?: string
  enabled?: boolean | number
}

/** 未分类的哨兵值：与后端 folder 参数约定一致 */
const UNCLASSIFIED = '__unclassified__'
/** 看板每列最多渲染的卡片数 */
const KANBAN_LIMIT = 8

const props = withDefaults(
  defineProps<{
    /** 当前页文档（服务端已按 keyword/status/sort/folder 过滤） */
    docs: WorkspaceDoc[]
    folderStructure?: Record<string, string[]>
    loading?: boolean
    canManage?: boolean
    bulkEnabledBusy?: boolean
    canParse?: boolean
    canDeleteDoc?: boolean
    selectedIds?: string[]
    activeId?: string | null
    /** 受控的当前文件夹路径，页面切换知识库时用来复位 */
    rootKey?: string
    /** 服务端过滤后的总数 */
    total?: number
    /** 全库文档总数（未过滤），用于「共 N 份」与空态判断 */
    allTotal?: number
    page?: number
    pageSize?: number
    pageSizes?: number[]
    keyword?: string
    status?: string
    sort?: string
    folder?: string
    /** 后端给的各解析状态计数（基于 folder+keyword 过滤后） */
    statusCounts?: Record<string, number>
    /** 后端给的各文件夹文档数（含子路径），键为路径或 UNCLASSIFIED */
    folderCounts?: Record<string, number>
  }>(),
  {
    folderStructure: () => ({}),
    loading: false,
    canManage: false,
    bulkEnabledBusy: false,
    canParse: false,
    canDeleteDoc: false,
    selectedIds: () => [],
    activeId: null,
    rootKey: '',
    total: 0,
    allTotal: 0,
    page: 1,
    pageSize: 50,
    pageSizes: () => [20, 50, 100],
    keyword: '',
    status: '',
    sort: 'updated',
    folder: '',
    statusCounts: () => ({}),
    folderCounts: () => ({}),
  },
)

const emit = defineEmits<{
  (event: 'open-doc', doc: WorkspaceDoc): void
  (event: 'parse-doc', doc: WorkspaceDoc): void
  (event: 'delete-doc', doc: WorkspaceDoc): void
  (event: 'move-doc', payload: { docId: string; folderPath: string }): void
  (event: 'create-folder', payload: { parentPath: string; name: string }): void
  (event: 'rename-folder', payload: { path: string; name: string }): void
  (event: 'delete-folder', path: string): void
  (event: 'update:selectedIds', ids: string[]): void
  (event: 'update:page', page: number): void
  (event: 'update:pageSize', size: number): void
  (event: 'update:keyword', keyword: string): void
  (event: 'update:status', status: string): void
  (event: 'update:sort', sort: string): void
  (event: 'update:folder', folder: string): void
  (event: 'bulk-parse', ids: string[]): void
  (event: 'bulk-delete', ids: string[]): void
  (event: 'bulk-move'): void
  (event: 'bulk-toggle-enabled', payload: { ids: string[]; enabled: boolean }): void
}>()

const VIEW_OPTIONS = [
  { key: 'list' as const, label: '列表', icon: 'M4 6h16M4 12h16M4 18h16' },
  { key: 'grid' as const, label: '网格', icon: 'M4 5h6v6H4V5zm10 0h6v6h-6V5zM4 13h6v6H4v-6zm10 0h6v6h-6v-6z' },
  { key: 'kanban' as const, label: '解析看板', icon: 'M4 5h5v14H4V5zm7.5 0h5v9h-5V5zM19 5h1v14h-1V5z' },
]

const SORT_OPTIONS = [
  { key: 'updated' as const, label: '最近更新' },
  { key: 'name' as const, label: '名称' },
  { key: 'size' as const, label: '文件大小' },
  { key: 'chunks' as const, label: '切片数' },
]

/** 列宽集中在一处，表头与数据行共用，避免两者对不齐 */
const GRID_COLS = 'grid-cols-[30px_minmax(180px,1fr)_96px_56px_72px_88px_88px]'

const viewMode = ref<'list' | 'grid' | 'kanban'>('list')
const rowMenuId = ref<string | null>(null)
const renaming = ref<{ path: string; value: string } | null>(null)
const creating = ref<{ parentPath: string; value: string } | null>(null)
const renameInput = ref<HTMLInputElement | null>(null)
const createInput = ref<HTMLInputElement | null>(null)
const dragOverPath = ref<string | null>(null)
let draggingId = ''

const index = computed(() => buildFolderIndex(props.folderStructure))
/** 文档 → 所属文件夹路径（用于行尾路径后缀与移动菜单的当前值） */
const docFolderMap = computed(() => {
  const map = new Map<string, string>()
  for (const node of index.value.byPath.values()) {
    for (const id of node.docIds) map.set(id, node.path)
  }
  return map
})

const pathOfDoc = (docId: string) => docFolderMap.value.get(docId) ?? ''
const allFolderPaths = computed(() => [...index.value.byPath.keys()].sort())

const currentFolder = computed(() =>
  props.folder && props.folder !== UNCLASSIFIED ? props.folder : '',
)

const breadcrumb = computed(() => {
  const segments = splitFolderPath(currentFolder.value)
  return segments.map((name, i) => ({ name, path: joinFolderPath(segments.slice(0, i + 1)) }))
})

/** 当前路径的直接子文件夹（根层 = 顶层文件夹） */
const subFolders = computed<FolderNode[]>(() => {
  if (props.folder === UNCLASSIFIED) return []
  const node = currentFolder.value ? index.value.byPath.get(currentFolder.value) : null
  const paths = node ? node.children : index.value.topLevel
  return paths
    .map((path) => index.value.byPath.get(path))
    .filter((item): item is FolderNode => Boolean(item))
})

const parentPath = computed(() => parentFolderPath(currentFolder.value))

/**
 * 文件夹计数优先用后端返回值（只统计真实存在的文档）；
 * 拿不到时退回结构里的 docId 数——可能含已删文档，但比不显示强。
 */
const countOf = (path: string): number => {
  const fromServer = props.folderCounts?.[path]
  if (typeof fromServer === 'number') return fromServer
  return index.value.byPath.get(path)?.totalDocCount ?? 0
}

const unclassifiedCount = computed(() => {
  const fromServer = props.folderCounts?.[UNCLASSIFIED]
  if (typeof fromServer === 'number') return fromServer
  return 0
})

const folderCountHint = (path: string) => `打开「${path}」（含子文件夹共 ${countOf(path)} 份）`

const isFiltering = computed(() => !!props.keyword.trim() || !!props.status)

/**
 * RAGFlow 的 progress 两套口径都出现过：0~1 的小数与 0~100 的百分数。
 * 直接拼 "%" 会把 0.0000522 显示成 "0.00522143%"，进度条也几乎不可见。
 */
const progressPercent = (doc: WorkspaceDoc): number => {
  const raw = Number(doc.progress ?? 0)
  if (!Number.isFinite(raw) || raw <= 0) return 0
  return Math.min(100, raw <= 1 ? raw * 100 : raw)
}

const progressLabel = (doc: WorkspaceDoc): string => {
  const pct = progressPercent(doc)
  if (pct <= 0) return ''
  return pct < 1 ? '<1%' : `${Math.round(pct)}%`
}

const statusOf = (doc: WorkspaceDoc): DocStatus => docStatusOf(doc)

const statusChips = computed(() => {
  const counts = props.statusCounts || {}
  const hasServerCounts = Object.keys(counts).length > 0
  const local = { failed: 0, parsing: 0, unparsed: 0, parsed: 0 }
  if (!hasServerCounts) {
    for (const doc of props.docs) local[statusOf(doc)] += 1
  }
  const pick = (key: DocStatus) =>
    hasServerCounts ? Number(counts[key] ?? 0) : local[key]
  return [
    {
      key: 'failed' as const,
      label: '解析失败',
      count: pick('failed'),
      dot: 'bg-red-500',
      title: '只看解析失败的文档',
    },
    {
      key: 'parsing' as const,
      label: '解析中',
      count: pick('parsing'),
      dot: 'bg-amber-500',
      title: '只看正在解析的文档',
    },
    {
      key: 'unparsed' as const,
      label: '未解析',
      count: pick('unparsed'),
      dot: 'bg-gray-300',
      title: '只看还没开始解析的文档',
    },
  ]
})

function statusMeta(doc: WorkspaceDoc) {
  switch (statusOf(doc)) {
    case 'parsed':
      return { label: '已完成', dotClass: 'bg-emerald-500', textClass: 'text-emerald-600' }
    case 'parsing':
      return { label: '解析中', dotClass: 'bg-amber-500 animate-pulse', textClass: 'text-amber-600' }
    case 'failed':
      return { label: '解析失败', dotClass: 'bg-red-500', textClass: 'text-red-600' }
    default:
      return { label: '未解析', dotClass: 'bg-gray-300', textClass: 'text-gray-500' }
  }
}

interface FileKind {
  label: string
  className: string
}

// 用对象字面量而不是 Record<string, T>：后者在 noUncheckedIndexedAccess 下
// 索引访问会带 undefined，模板里 fileKindOf(doc).className 就要到处加兜底
const FILE_KINDS = {
  pdf: { label: 'PDF', className: 'bg-red-50 text-red-600' },
  doc: { label: 'DOC', className: 'bg-blue-50 text-blue-600' },
  xls: { label: 'XLS', className: 'bg-emerald-50 text-emerald-600' },
  ppt: { label: 'PPT', className: 'bg-orange-50 text-orange-600' },
  img: { label: 'IMG', className: 'bg-sky-50 text-sky-600' },
  other: { label: 'FILE', className: 'bg-gray-100 text-gray-500' },
} as const

function fileKindOf(doc: WorkspaceDoc): FileKind {
  const name = String(doc.name || '').toLowerCase()
  const ext = name.slice(name.lastIndexOf('.') + 1)
  if (ext === 'pdf') return FILE_KINDS.pdf
  if (['doc', 'docx', 'rtf'].includes(ext)) return FILE_KINDS.doc
  if (['xls', 'xlsx', 'csv', 'xlsm'].includes(ext)) return FILE_KINDS.xls
  if (['ppt', 'pptx'].includes(ext)) return FILE_KINDS.ppt
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) return FILE_KINDS.img
  return FILE_KINDS.other
}

function formatSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/** RAGFlow 的 update_time 可能是毫秒时间戳（数字或字符串），也可能是日期串 */
function timestampOf(raw?: string | number): number {
  if (raw === undefined || raw === null || raw === '') return 0
  if (typeof raw === 'number') return raw < 1e12 ? raw * 1000 : raw
  const text = String(raw).trim()
  if (/^\d+$/.test(text)) {
    const value = Number(text)
    return value < 1e12 ? value * 1000 : value
  }
  const parsed = Date.parse(text.replace(/-/g, '/'))
  return Number.isNaN(parsed) ? 0 : parsed
}

function formatTime(raw?: string | number): string {
  const ts = timestampOf(raw)
  if (!ts) return '—'
  const diff = Date.now() - ts
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  if (diff < 172_800_000) return '昨天'
  const date = new Date(ts)
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  if (date.getFullYear() === new Date().getFullYear()) return `${month}-${day}`
  return `${date.getFullYear()}-${month}-${day}`
}

/** 当前页内按状态分列；看板只做「哪堆积压」的判断，不承担逐条翻阅 */
const kanbanColumns = computed(() => {
  const buckets: Record<DocStatus, WorkspaceDoc[]> = { failed: [], parsing: [], unparsed: [], parsed: [] }
  for (const doc of props.docs) buckets[statusOf(doc)].push(doc)
  const order: Array<{ status: DocStatus; label: string; dotClass: string }> = [
    { status: 'failed', label: '解析失败', dotClass: 'bg-red-500' },
    { status: 'parsing', label: '解析中', dotClass: 'bg-amber-500' },
    { status: 'unparsed', label: '未解析', dotClass: 'bg-gray-300' },
    { status: 'parsed', label: '已完成', dotClass: 'bg-emerald-500' },
  ]
  return order.map((item) => {
    const list = buckets[item.status]
    return {
      ...item,
      total: list.length,
      docs: list.slice(0, KANBAN_LIMIT),
    }
  })
})

/** 当前页可见的待选文档（全选只作用于这一页，跨页勾选由页面保留） */
const visibleDocIds = computed(() => props.docs.map((doc) => doc.id))
const allVisibleSelected = computed(
  () => visibleDocIds.value.length > 0 && visibleDocIds.value.every((id) => props.selectedIds.includes(id)),
)

function toggleSelect(docId: string) {
  const next = props.selectedIds.includes(docId)
    ? props.selectedIds.filter((id) => id !== docId)
    : [...props.selectedIds, docId]
  emit('update:selectedIds', next)
}

function toggleSelectAll() {
  if (allVisibleSelected.value) {
    emit('update:selectedIds', props.selectedIds.filter((id) => !visibleDocIds.value.includes(id)))
    return
  }
  emit('update:selectedIds', [...new Set([...props.selectedIds, ...visibleDocIds.value])])
}

function clearSelection() {
  emit('update:selectedIds', [])
}

/* ── 受控筛选/排序/文件夹 ───────────────────────────────────────────── */

function onKeywordInput(event: Event) {
  emit('update:keyword', (event.target as HTMLInputElement).value)
}

function onSortChange(event: Event) {
  emit('update:sort', (event.target as HTMLSelectElement).value)
}

function toggleStatus(key: DocStatus) {
  emit('update:status', props.status === key ? '' : key)
}

function openFolder(path: string) {
  emit('update:folder', path === UNCLASSIFIED ? UNCLASSIFIED : path)
}

function switchToList() {
  viewMode.value = 'list'
}

function closeRowMenu() {
  rowMenuId.value = null
}

/* ── 拖拽移动 ───────────────────────────────────────────────────────── */

function onDragStart(doc: WorkspaceDoc) {
  draggingId = doc.id
}

function onFolderChipDrop(path: string) {
  dragOverPath.value = null
  const docId = draggingId
  draggingId = ''
  if (!docId) return
  if (pathOfDoc(docId) === path) return
  emit('move-doc', { docId, folderPath: path })
}

function moveDoc(doc: WorkspaceDoc, folderPath: string) {
  rowMenuId.value = null
  emit('move-doc', { docId: doc.id, folderPath })
}

/* ── 文件夹新建 / 重命名 ────────────────────────────────────────────── */

function startCreate(parentPathValue: string) {
  creating.value = { parentPath: parentPathValue, value: '' }
  void nextTick(() => createInput.value?.focus())
}

function commitCreate() {
  const draft = creating.value
  if (!draft) return
  const name = draft.value.trim()
  creating.value = null
  if (!name) return
  emit('create-folder', { parentPath: draft.parentPath, name })
}

function startRenamePath(path: string) {
  renaming.value = { path, value: folderNameOf(path) }
  void nextTick(() => {
    renameInput.value?.focus()
    renameInput.value?.select()
  })
}

function commitRename() {
  const draft = renaming.value
  if (!draft) return
  const name = draft.value.trim()
  renaming.value = null
  if (!name || name === folderNameOf(draft.path)) return
  // 路径分隔符由 / 承担，名字里再出现会建出意料之外的层级
  if (name.includes('/')) return
  emit('rename-folder', { path: draft.path, name })
}

/**
 * 当前文件夹被删掉（或改名）后回到根。
 *
 * 不兜这一步，页面会拿着一个不存在的路径继续请求，后端按前缀过滤返回空列表，
 * 界面表现为「这个文件夹里突然一份文档都没有」，而且面包屑还指着一个不存在的目录。
 */
watch(
  () => [props.folder, index.value.byPath.size] as const,
  ([folder]) => {
    if (!folder || folder === UNCLASSIFIED) return
    if (!index.value.byPath.has(folder)) emit('update:folder', '')
  },
)

/**
 * 切换知识库时复位浏览位置：文件夹回到根、搜索与状态清空。
 * 这些状态现在归页面所有，所以只能 emit 出去，不能只改本地 ref
 * （否则页面还拿着上一个库的 folder 值继续请求）。
 */
watch(
  () => props.rootKey,
  () => {
    renaming.value = null
    creating.value = null
    rowMenuId.value = null
    dragOverPath.value = null
    viewMode.value = 'list'
    emit('update:keyword', '')
    emit('update:status', '')
    emit('update:folder', '')
    emit('update:page', 1)
  },
  { immediate: true },
)

// 左侧树/快捷键等外部入口需要让工作区进入「新建文件夹」状态
const startCreateAtRoot = () => startCreate(currentFolder.value)
defineExpose({ startCreateAtRoot })
</script>
