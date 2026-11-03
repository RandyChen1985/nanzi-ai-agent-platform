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
        <input
          v-model="query"
          type="search"
          :placeholder="`在 ${docs.length} 份文档中搜索`"
          class="w-full rounded-lg border border-gray-200 py-1.5 pl-8 pr-2 text-xs text-gray-700 outline-none transition-colors placeholder:text-gray-400 focus:border-primary"
        />
      </div>

      <div class="flex shrink-0 items-center gap-1">
        <button
          v-for="chip in statusChips"
          :key="chip.key"
          type="button"
          class="flex items-center gap-1 rounded-full border px-2 py-1 text-[11px] transition-colors"
          :class="statusFilter === chip.key
            ? 'border-primary bg-primary/5 text-primary'
            : 'border-gray-200 text-gray-500 hover:border-gray-300 hover:text-gray-700'"
          @click="statusFilter = statusFilter === chip.key ? '' : chip.key"
        >
          <span class="h-1.5 w-1.5 rounded-full" :class="chip.dot" />
          {{ chip.label }}
          <span class="font-semibold">{{ chip.count }}</span>
        </button>
      </div>

      <select
        v-model="sortKey"
        class="shrink-0 rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-[11px] text-gray-600 outline-none focus:border-primary"
        aria-label="排序方式"
      >
        <option v-for="option in SORT_OPTIONS" :key="option.key" :value="option.key">{{ option.label }}</option>
      </select>

      <!-- 上传入口在库头：工具条这一排加上它会挤到折行 -->

    </div>

    <!-- ── 面包屑 ─────────────────────────────────────────────────────── -->
    <div class="flex items-center gap-1 border-b border-gray-100 px-4 py-1.5 text-[11px] text-gray-500">
      <button type="button" class="rounded px-1 py-0.5 hover:bg-gray-100 hover:text-gray-700" @click="goRoot">
        全部文件
      </button>
      <template v-for="(item, index) in breadcrumb" :key="item.path">
        <span class="text-gray-300">/</span>
        <button
          type="button"
          class="max-w-[180px] truncate rounded px-1 py-0.5"
          :class="index === breadcrumb.length - 1 ? 'font-semibold text-gray-700' : 'hover:bg-gray-100 hover:text-gray-700'"
          @click="enterFolder(item.path)"
        >
          {{ item.name }}
        </button>
      </template>
      <span class="ml-auto hidden shrink-0 text-gray-400 sm:inline">
        拖文件到文件夹行即可移动
      </span>
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

      <div v-else-if="!docs.length" class="px-6 py-16 text-center">
        <p class="text-sm font-medium text-gray-700">这个知识库还没有文档</p>
        <p class="mt-1 text-xs text-gray-400">上传之后，解析完成的文档会被智能体检索到。</p>
      </div>

      <div v-else-if="!visibleRows.length" class="px-6 py-16 text-center">
        <p class="text-sm font-medium text-gray-700">
          {{ isFiltering ? '没有匹配的文档' : '这个文件夹还是空的' }}
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
              :aria-label="allVisibleSelected ? '取消全选' : '全选当前列表'"
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
          v-for="row in visibleRows"
          :key="rowKey(row)"
          class="group grid items-center gap-2 border-b border-gray-50 px-4 py-2 text-xs transition-colors"
          :class="[
            GRID_COLS,
            row.kind === 'folder' ? 'cursor-pointer hover:bg-gray-50' : 'hover:bg-blue-50/40',
            isDragOver(row) ? 'bg-primary/10 ring-1 ring-inset ring-primary/30' : '',
            row.kind === 'doc' && activeId === row.doc.id ? 'bg-blue-50/60' : '',
          ]"
          :draggable="row.kind === 'doc' && canManage"
          @click="onRowClick(row)"
          @dragstart="row.kind === 'doc' ? onDragStart(row.doc) : undefined"
          @dragover.prevent="onFolderDragOver(row)"
          @dragleave="onFolderDragLeave(row)"
          @drop.prevent="onFolderDrop(row)"
        >
          <!-- 勾选 / 缩进箭头 -->
          <span class="flex items-center justify-center" @click.stop>
            <input
              v-if="row.kind === 'doc' && canManage"
              type="checkbox"
              class="h-3 w-3 cursor-pointer rounded border-gray-300 text-primary focus:ring-primary"
              :checked="selectedIds.includes(row.doc.id)"
              :aria-label="`选择文档 ${row.doc.name || row.doc.id}`"
              @change="toggleSelect(row.doc.id)"
            />
            <svg v-else-if="row.kind === 'folder'" class="h-3 w-3 text-gray-400 transition-transform" :class="isExpanded(row.node.path) ? 'rotate-90' : ''" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24" @click.stop="toggleExpand(row.node.path)">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          </span>

          <!-- 名称 -->
          <div class="flex min-w-0 items-center gap-1.5" :style="{ paddingLeft: `${row.depth * 16}px` }">
            <template v-if="row.kind === 'folder'">
              <svg class="h-3.5 w-3.5 shrink-0 text-amber-500" viewBox="0 0 24 24" fill="currentColor">
                <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
              </svg>
              <input
                v-if="renaming?.path === row.node.path"
                ref="renameInput"
                v-model="renaming.value"
                class="w-40 rounded border border-primary px-1 py-0.5 text-xs outline-none"
                @keydown.enter.prevent="commitRename(row.node)"
                @keydown.esc.prevent="renaming = null"
                @blur="commitRename(row.node)"
              />
              <span v-else class="truncate font-medium text-gray-700">{{ row.node.name }}</span>
              <span class="shrink-0 text-[10px] text-gray-400">{{ row.node.totalDocCount }} 份</span>
            </template>
            <template v-else>
              <span class="flex h-4 w-4 shrink-0 items-center justify-center rounded text-[8px] font-bold" :class="fileKindOf(row.doc).className">
                {{ fileKindOf(row.doc).label }}
              </span>
              <span class="truncate" :class="isDocEnabled(row.doc) ? 'text-gray-700' : 'text-gray-400 line-through'">
                {{ row.doc.name || row.doc.id }}
              </span>
              <span v-if="!isDocEnabled(row.doc)" class="shrink-0 rounded bg-amber-100 px-1 text-[9px] font-semibold text-amber-700">已停用</span>
              <span v-if="isFiltering && row.folderPath" class="shrink-0 truncate text-[10px] text-gray-400">
                {{ row.folderPath }}
              </span>
            </template>
          </div>

          <!-- 状态 -->
          <span v-if="row.kind === 'doc'" class="flex items-center gap-1 whitespace-nowrap text-[11px]" :class="statusMeta(row.doc).textClass">
            <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="statusMeta(row.doc).dotClass" />
            {{ statusMeta(row.doc).label }}
            <span v-if="progressLabel(row.doc)" class="shrink-0 tabular-nums text-[10px] text-gray-400">
              {{ progressLabel(row.doc) }}
            </span>
          </span>
          <span v-else class="text-[10px] text-gray-400">{{ row.node.children.length ? `${row.node.children.length} 个子文件夹` : '' }}</span>

          <span class="text-right text-[11px] text-gray-500">{{ row.kind === 'doc' ? (row.doc.chunk_count ?? '—') : '' }}</span>
          <span class="text-right text-[11px] text-gray-500">{{ row.kind === 'doc' ? formatSize(row.doc.size) : '' }}</span>
          <span class="text-right text-[11px] text-gray-400">{{ row.kind === 'doc' ? formatTime(row.doc.update_time || row.doc.created_at) : '' }}</span>

          <!-- 行操作 -->
          <span class="flex items-center justify-end gap-0.5" @click.stop>
            <template v-if="row.kind === 'folder' && canManage">
              <button type="button" class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600" title="在此文件夹下新建子文件夹" @click="startCreate(row.node.path)">
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" d="M12 5v14M5 12h14" />
                </svg>
              </button>
              <button type="button" class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600" title="重命名" @click="startRename(row.node)">
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
              </button>
              <button type="button" class="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600" title="删除文件夹" @click="emit('delete-folder', row.node.path)">
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </template>

            <template v-if="row.kind === 'doc'">
              <button
                v-if="canParse && statusOf(row.doc) !== 'parsed'"
                type="button"
                class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-primary"
                :title="statusOf(row.doc) === 'failed' ? '重试解析' : '开始解析'"
                @click="emit('parse-doc', row.doc)"
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
                  @click.stop="rowMenuId = rowMenuId === row.doc.id ? null : row.doc.id"
                >
                  <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                </button>
                <div
                  v-if="rowMenuId === row.doc.id"
                  class="absolute right-0 top-full z-30 mt-1 max-h-64 w-48 overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
                  @click.stop
                >
                  <p class="px-2 py-1 text-[10px] font-semibold text-gray-400">移动到</p>
                  <button
                    v-for="folderPath in allFolderPaths"
                    :key="folderPath"
                    type="button"
                    class="block w-full truncate px-3 py-1.5 text-left text-[11px] text-gray-700 hover:bg-gray-50"
                    :disabled="folderPath === row.folderPath"
                    :class="folderPath === row.folderPath ? 'opacity-40' : ''"
                    @click="moveDoc(row.doc, folderPath)"
                  >
                    {{ folderPath }}
                  </button>
                  <button
                    v-if="row.folderPath"
                    type="button"
                    class="block w-full px-3 py-1.5 text-left text-[11px] text-red-600 hover:bg-red-50"
                    @click="moveDoc(row.doc, '')"
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
                @click="emit('delete-doc', row.doc)"
              >
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </template>
          </span>
        </div>

        <!-- 新建文件夹的内联输入 -->
        <div v-if="creating" class="flex items-center gap-2 border-b border-gray-50 px-4 py-2 text-xs">
          <span class="w-[30px]" />
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

        <div v-if="canManage && !creating" class="px-4 py-2">
          <button type="button" class="text-[11px] text-gray-400 hover:text-primary" @click="startCreate(currentPath)">
            + 新建文件夹
          </button>
        </div>
        </div>
      </template>

      <!-- 网格视图 -->
      <div v-else-if="viewMode === 'grid'" class="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 lg:grid-cols-4">
        <button
          v-for="row in visibleRows"
          :key="rowKey(row)"
          type="button"
          class="group flex h-[104px] flex-col rounded-xl border p-3 text-left transition-all"
          :class="[
            row.kind === 'folder' ? 'border-dashed border-amber-200 bg-amber-50/40 hover:border-amber-300' : 'border-gray-200 bg-white hover:border-primary/40 hover:shadow-sm',
            isDragOver(row) ? 'ring-2 ring-primary/40' : '',
          ]"
          :draggable="false"
          @click="onRowClick(row)"
          @dragover.prevent="onFolderDragOver(row)"
          @dragleave="onFolderDragLeave(row)"
          @drop.prevent="onFolderDrop(row)"
        >
          <div class="flex min-w-0 items-start gap-2">
            <svg v-if="row.kind === 'folder'" class="mt-0.5 h-4 w-4 shrink-0 text-amber-500" viewBox="0 0 24 24" fill="currentColor">
              <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
            </svg>
            <span v-else class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded text-[8px] font-bold" :class="fileKindOf(row.doc).className">
              {{ fileKindOf(row.doc).label }}
            </span>
            <span class="line-clamp-2 text-xs font-medium text-gray-700">
              {{ row.kind === 'folder' ? row.node.name : (row.doc.name || row.doc.id) }}
            </span>
          </div>
          <div class="mt-auto flex items-center justify-between text-[10px] text-gray-400">
            <template v-if="row.kind === 'folder'">
              <span>{{ row.node.totalDocCount }} 份文档</span>
              <span>打开 →</span>
            </template>
            <template v-else>
              <span class="flex items-center gap-1" :class="statusMeta(row.doc).textClass">
                <span class="h-1.5 w-1.5 rounded-full" :class="statusMeta(row.doc).dotClass" />
                {{ statusMeta(row.doc).label }}
              </span>
              <span>{{ formatSize(row.doc.size) }}</span>
            </template>
          </div>
        </button>
      </div>

      <!-- 解析看板 -->
      <div v-else class="flex h-full gap-3 overflow-x-auto p-4">
        <section v-for="column in kanbanColumns" :key="column.status" class="flex min-w-[220px] flex-1 flex-col rounded-xl bg-gray-50/70">
          <header class="flex items-center gap-2 px-3 py-2 text-xs font-medium text-gray-600">
            <span class="h-1.5 w-1.5 rounded-full" :class="column.dotClass" />
            {{ column.label }}
            <span class="ml-auto text-[11px] text-gray-400">{{ column.docs.length }}</span>
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
          </div>
        </section>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  buildFolderIndex,
  canMoveFolderInto,
  joinFolderPath,
  splitFolderPath,
  type FolderNode,
} from '@/utils/knowledgeFolders'
import { docStatusOf, isDocEnabled, type DocStatus } from '@/utils/knowledgeDocStatus'

/**
 * 文档工作区：把「浏览 / 排序 / 筛选 / 多选 / 移动」这些高频操作从左侧树里搬出来。
 *
 * 这一层刻意不碰接口：所有写操作都 emit 给页面，由页面复用既有的 API 调用
 * （移动走 folder-structure、解析走 RAGFlow run、删除走原弹窗），避免同一条链路
 * 出现两套实现。
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

type Row =
  | { kind: 'folder'; node: FolderNode; depth: number }
  | { kind: 'doc'; doc: WorkspaceDoc; depth: number; folderPath: string }

const props = withDefaults(
  defineProps<{
    docs: WorkspaceDoc[]
    folderStructure?: Record<string, string[]>
    loading?: boolean
    canManage?: boolean
    canUpload?: boolean
    bulkEnabledBusy?: boolean
    canParse?: boolean
    canDeleteDoc?: boolean
    selectedIds?: string[]
    activeId?: string | null
    /** 受控的当前文件夹路径，页面切换知识库时用来复位 */
    rootKey?: string
  }>(),
  {
    folderStructure: () => ({}),
    loading: false,
    canManage: false,
    canUpload: false,
    bulkEnabledBusy: false,
    canParse: false,
    canDeleteDoc: false,
    selectedIds: () => [],
    activeId: null,
    rootKey: '',
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
  (event: 'upload'): void
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
  { key: 'status' as const, label: '解析状态' },
  { key: 'size' as const, label: '文件大小' },
  { key: 'chunks' as const, label: '切片数' },
]

/** 列宽集中在一处，表头与数据行共用，避免两者对不齐 */
const GRID_COLS = 'grid-cols-[30px_minmax(180px,1fr)_96px_56px_72px_88px_88px]'

const viewMode = ref<'list' | 'grid' | 'kanban'>('list')
const sortKey = ref<'updated' | 'name' | 'status' | 'size' | 'chunks'>('updated')
const query = ref('')
const statusFilter = ref<'' | DocStatus>('')
const currentPath = ref('')
const expanded = ref<string[]>([])
const dragOverPath = ref<string | null>(null)
const rowMenuId = ref<string | null>(null)
const renaming = ref<{ path: string; value: string } | null>(null)
const creating = ref<{ parentPath: string; value: string } | null>(null)
const renameInput = ref<HTMLInputElement | null>(null)
const createInput = ref<HTMLInputElement | null>(null)
let draggingId = ''

const index = computed(() => buildFolderIndex(props.folderStructure))
const docById = computed(() => new Map(props.docs.map((doc) => [doc.id, doc])))

/** 文档 → 所属文件夹路径（用于搜索结果的路径后缀、看板卡片） */
const docFolderMap = computed(() => {
  const map = new Map<string, string>()
  for (const node of index.value.byPath.values()) {
    for (const id of node.docIds) map.set(id, node.path)
  }
  return map
})

const pathOfDoc = (docId: string) => docFolderMap.value.get(docId) ?? ''

/** 没有被任何文件夹收进来的文档：根目录平铺显示 */
const unclassified = computed(() =>
  props.docs.filter((doc) => !index.value.assigned.has(doc.id)),
)

const isFiltering = computed(() => !!query.value.trim() || !!statusFilter.value)

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
  const counts = { failed: 0, parsing: 0, unparsed: 0, parsed: 0 }
  for (const doc of props.docs) counts[statusOf(doc)] += 1
  return [
    { key: 'failed' as const, label: '解析失败', count: counts.failed, dot: 'bg-red-500' },
    { key: 'parsing' as const, label: '解析中', count: counts.parsing, dot: 'bg-amber-500' },
    { key: 'unparsed' as const, label: '未解析', count: counts.unparsed, dot: 'bg-gray-300' },
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

/** 当前层的文档过滤 + 排序 */
function sortDocs(list: WorkspaceDoc[]): WorkspaceDoc[] {
  const sorted = [...list]
  sorted.sort((a, b) => {
    switch (sortKey.value) {
      case 'name':
        return String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN')
      case 'size':
        return (b.size ?? 0) - (a.size ?? 0)
      case 'chunks':
        return (b.chunk_count ?? 0) - (a.chunk_count ?? 0)
      case 'status': {
        const rank = { failed: 0, parsing: 1, unparsed: 2, parsed: 3 }
        return rank[statusOf(a)] - rank[statusOf(b)]
      }
      default:
        return timestampOf(b.update_time || b.created_at) - timestampOf(a.update_time || a.created_at)
    }
  })
  return sorted
}

function matches(doc: WorkspaceDoc): boolean {
  const keyword = query.value.trim().toLowerCase()
  if (keyword && !String(doc.name || '').toLowerCase().includes(keyword)) return false
  if (statusFilter.value && statusOf(doc) !== statusFilter.value) return false
  return true
}

/**
 * 搜索/筛选时平铺显示（忽略层级，带路径后缀）；
 * 否则按「当前层 → 展开的子层」递归展开，和文件管理器一致。
 */
const visibleRows = computed<Row[]>(() => {
  if (isFiltering.value) {
    return sortDocs(props.docs.filter(matches)).map((doc) => ({
      kind: 'doc' as const,
      doc,
      depth: 0,
      folderPath: pathOfDoc(doc.id),
    }))
  }

  const rows: Row[] = []
  const pushDocs = (docIds: string[], depth: number, folderPath: string) => {
    const list = docIds
      .map((id) => docById.value.get(id))
      .filter((doc): doc is WorkspaceDoc => !!doc)
    for (const doc of sortDocs(list)) rows.push({ kind: 'doc', doc, depth, folderPath })
  }
  const walk = (path: string, depth: number) => {
    const node = index.value.byPath.get(path)
    if (!node) return
    rows.push({ kind: 'folder', node, depth })
    if (!expanded.value.includes(path)) return
    for (const childPath of node.children) walk(childPath, depth + 1)
    pushDocs(node.docIds, depth + 1, path)
  }

  if (currentPath.value) {
    const node = index.value.byPath.get(currentPath.value)
    if (node) {
      for (const childPath of node.children) walk(childPath, 0)
      pushDocs(node.docIds, 0, node.path)
    }
  } else {
    for (const path of index.value.topLevel) walk(path, 0)
    pushDocs(unclassified.value.map((doc) => doc.id), 0, '')
  }
  return rows
})

const allFolderPaths = computed(() => [...index.value.byPath.keys()].sort())

const breadcrumb = computed(() => {
  const segments = splitFolderPath(currentPath.value)
  return segments.map((name, i) => ({ name, path: joinFolderPath(segments.slice(0, i + 1)) }))
})

const kanbanColumns = computed(() => {
  const buckets: Record<DocStatus, WorkspaceDoc[]> = { failed: [], parsing: [], unparsed: [], parsed: [] }
  for (const doc of props.docs.filter(matches)) buckets[statusOf(doc)].push(doc)
  return [
    { status: 'failed' as DocStatus, label: '解析失败', dotClass: 'bg-red-500', docs: sortDocs(buckets.failed) },
    { status: 'parsing' as DocStatus, label: '解析中', dotClass: 'bg-amber-500', docs: sortDocs(buckets.parsing) },
    { status: 'unparsed' as DocStatus, label: '未解析', dotClass: 'bg-gray-300', docs: sortDocs(buckets.unparsed) },
    { status: 'parsed' as DocStatus, label: '已完成', dotClass: 'bg-emerald-500', docs: sortDocs(buckets.parsed) },
  ]
})

/** 当前可见的待选文档（全选只作用于列表里看得见的那些） */
const visibleDocIds = computed(() =>
  visibleRows.value.filter((row): row is Extract<Row, { kind: 'doc' }> => row.kind === 'doc').map((row) => row.doc.id),
)
const allVisibleSelected = computed(
  () => visibleDocIds.value.length > 0 && visibleDocIds.value.every((id) => props.selectedIds.includes(id)),
)

const rowKey = (row: Row) => (row.kind === 'folder' ? `folder:${row.node.path}` : `doc:${row.doc.id}`)

const isExpanded = (path: string) => expanded.value.includes(path)

function toggleExpand(path: string) {
  expanded.value = isExpanded(path) ? expanded.value.filter((item) => item !== path) : [...expanded.value, path]
}

function enterFolder(path: string) {
  currentPath.value = path
  if (path && !isExpanded(path)) expanded.value = [...expanded.value, path]
}

function goRoot() {
  currentPath.value = ''
}

function onRowClick(row: Row) {
  closeRowMenu()
  if (row.kind === 'folder') {
    enterFolder(row.node.path)
    return
  }
  emit('open-doc', row.doc)
}

function closeRowMenu() {
  rowMenuId.value = null
}

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
  const merged = new Set([...props.selectedIds, ...visibleDocIds.value])
  emit('update:selectedIds', [...merged])
}

function clearSelection() {
  emit('update:selectedIds', [])
}

function moveDoc(doc: WorkspaceDoc, folderPath: string) {
  rowMenuId.value = null
  emit('move-doc', { docId: doc.id, folderPath })
}

function onDragStart(doc: WorkspaceDoc) {
  draggingId = doc.id
}

function isDragOver(row: Row): boolean {
  return row.kind === 'folder' && dragOverPath.value === row.node.path
}

function onFolderDragOver(row: Row) {
  if (row.kind !== 'folder') return
  dragOverPath.value = row.node.path
}

function onFolderDragLeave(row: Row) {
  if (row.kind !== 'folder') return
  dragOverPath.value = null
}

function onFolderDrop(row: Row) {
  if (row.kind !== 'folder') return
  onDropToFolder(row.node.path)
}

function onDropToFolder(folderPath: string) {
  dragOverPath.value = null
  if (!draggingId) return
  const docId = draggingId
  draggingId = ''
  if (pathOfDoc(docId) === folderPath) return
  emit('move-doc', { docId, folderPath })
}

function startCreate(parentPath: string) {
  if (parentPath && !isExpanded(parentPath)) toggleExpand(parentPath)
  creating.value = { parentPath, value: '' }
  void nextTick(() => createInput.value?.focus())
}

function commitCreate() {
  const draft = creating.value
  if (!draft) return
  const name = draft.value.trim()
  if (!name) {
    creating.value = null
    return
  }
  creating.value = null
  emit('create-folder', { parentPath: draft.parentPath, name })
}

function startRename(node: FolderNode) {
  renaming.value = { path: node.path, value: node.name }
  void nextTick(() => {
    const input = Array.isArray(renameInput.value) ? renameInput.value[0] : renameInput.value
    input?.focus()
    input?.select()
  })
}

function commitRename(node: FolderNode) {
  const draft = renaming.value
  if (!draft || draft.path !== node.path) return
  const name = draft.value.trim()
  renaming.value = null
  if (!name || name === node.name) return
  if (name.includes('/')) return
  emit('rename-folder', { path: node.path, name })
}

// 拖拽只允许「文档 → 文件夹」；文件夹自身的移动依赖菜单，避免误拖成环
const canDropInto = (sourcePath: string, targetPath: string) => canMoveFolderInto(sourcePath, targetPath)
void canDropInto

// 切换知识库时复位浏览位置，避免停在上一个库的某个子文件夹里
watch(
  () => props.rootKey,
  () => {
    currentPath.value = ''
    query.value = ''
    statusFilter.value = ''
    renaming.value = null
    creating.value = null
    rowMenuId.value = null
    const topLevel = [...index.value.topLevel]
    expanded.value = topLevel
  },
  { immediate: true },
)

// 文件夹结构变化（新建/重命名/删除）后，顶层默认展开到可见
watch(
  () => index.value.topLevel.join('|'),
  (next) => {
    if (!next) return
    const known = new Set(expanded.value)
    for (const path of next.split('|')) known.add(path)
    expanded.value = [...known]
  },
)

// 左侧树/快捷键等外部入口需要让工作区进入「新建文件夹」状态
const startCreateAtRoot = () => startCreate(currentPath.value)
defineExpose({ startCreateAtRoot })
</script>
