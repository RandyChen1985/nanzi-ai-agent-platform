<!--
  工作空间（独立页）。

  与聊天页右侧「浏览工作空间」抽屉共用同一套文件接口（`/api/v1/chat/fs/*`）与工具函数
  （`utils/workspaceFilePreview`、`useWorkspaceUploadQueue`）。能力对齐清单：

  - 浏览：面包屑 / 返回上级 / 快捷导航 / 最近文件 / 列表-卡片切换 / 排序 / 类型筛选
  - 落点：默认直接进入本人的「我的目录」（后端 `prefer=user_root`），公共目录走「公共资源」入口
  - 搜索：默认过滤当前目录，输入 ≥2 字可切「全目录」走服务端 `/fs/search`
  - 文件：预览 / 下载 / 重命名 / 复制路径 / 解压 / 删除（移入回收站）
  - 目录：打开 / 新建文件夹 / 新建文件 / 上传（点选 + 拖拽）
  - 回收站：还原 / 永久删除 / 清空
  - 批量：多选 / 全选 / 批量删除

  刻意不提供（与抽屉的差异）：
  1. 「挂载到会话」——需要目标会话，只属于聊天；
  2. 「本会话目录」快捷入口——同上；
  3. 画布内预览——画布是聊天页的宿主能力，这里用新窗口只读预览；
  4. 钉住 / 拖拽宽度——页面不需要。

  也没有直接复用抽屉组件：它是 teleport 到 body 的抽屉，带拖拽宽度与 pinned 停靠，
  和聊天布局强耦合，强行页面化会破坏现有功能（抽屉文件保持零改动）。
-->
<template>
  <div
    class="relative flex h-full w-full max-w-[1200px] flex-col gap-3 px-4 py-4 sm:px-6 sm:py-5"
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
        <h1 class="text-xl font-bold tracking-tight text-gray-900 sm:text-2xl">工作空间</h1>
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
              <span class="min-w-0 flex-1 truncate">{{ recent.name }}</span>
            </button>
          </div>
        </div>

        <!-- 新建：文件夹 / 文件 -->
        <div ref="createMenuRef" class="relative">
          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
            :disabled="!canCreateHere"
            @click="createOpen = !createOpen"
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
        :disabled="!parentPath || isRoot || searchAll"
        title="返回上级目录"
        @click="navigate(parentPath)"
      >
        <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 6l-6 6 6 6" />
        </svg>
        上一级
      </button>

      <nav class="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto text-xs text-gray-400">
        <span v-for="(seg, index) in pathSegments" :key="seg.path" class="flex flex-shrink-0 items-center gap-1">
          <span v-if="index > 0" class="text-gray-300">/</span>
          <button
            type="button"
            class="max-w-[160px] truncate rounded px-1 py-0.5 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800"
            :class="index === pathSegments.length - 1 ? 'font-semibold text-gray-700 dark:text-gray-200' : ''"
            :title="seg.path"
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
        class="rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-100"
        @click="askEmptyTrash"
      >清空回收站</button>

      <span v-if="scope === 'admin_all' && !isInsideOwnWorkspace" class="rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-medium text-amber-700">
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
      class="flex items-center gap-3 rounded-xl border border-blue-100 bg-blue-50/70 px-3 py-2 text-xs text-blue-700"
    >
      <span>已选 {{ selectedPaths.size }} 项</span>
      <button type="button" class="font-semibold hover:underline" @click="askBatchDelete">批量删除</button>
      <button type="button" class="text-blue-500 hover:underline" @click="clearSelection">取消选择</button>
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
                :checked="allSelected"
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
          <tr v-if="loading">
            <td colspan="5" class="px-3 py-10 text-center text-xs text-gray-400">正在加载…</td>
          </tr>
          <tr v-else-if="!visibleItems.length">
            <td colspan="5" class="px-3 py-10 text-center text-xs text-gray-400">
              {{ keyword ? "没有匹配的文件" : "这个目录还是空的，上传一个文件试试" }}
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
                @click="navigate(item.path)"
              >
                <span class="text-base leading-none">📁</span>
                <span class="truncate font-medium text-gray-800 hover:text-primary dark:text-gray-200">{{ item.name }}</span>
              </button>
              <div v-else class="flex min-w-0 items-center gap-2">
                <span class="text-base leading-none">{{ fileEmoji(item.name) }}</span>
                <span class="truncate text-gray-800 dark:text-gray-200" :title="item.path">{{ item.name }}</span>
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
                  @preview="handlePreview"
                  @download="handleDownload"
                  @rename="openRename"
                  @copy-path="handleCopyPath"
                  @extract="askExtract"
                  @delete="askDelete"
                  @restore="handleRestore"
                  @purge="askPurge"
                />
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 卡片视图 -->
    <div v-else class="min-h-0 flex-1 overflow-auto rounded-xl bg-white p-2 dark:bg-gray-900">
      <p v-if="loading" class="py-10 text-center text-xs text-gray-400">正在加载…</p>
      <p v-else-if="!visibleItems.length" class="py-10 text-center text-xs text-gray-400">
        {{ keyword ? "没有匹配的文件" : "这个目录还是空的，上传一个文件试试" }}
      </p>
      <div v-else class="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4">
        <div
          v-for="item in visibleItems"
          :key="item.path"
          class="group flex flex-col gap-1 rounded-xl border border-gray-200 bg-white p-3 transition-colors hover:border-primary/40 dark:border-gray-800 dark:bg-gray-800/60"
          :class="selectedPaths.has(item.path) ? 'ring-1 ring-primary' : ''"
          @dblclick.stop="openItem(item)"
        >
          <div class="flex items-start justify-between gap-1">
            <button
              type="button"
              class="min-w-0 flex-1 text-left"
              :title="item.path"
              @click="item.is_dir ? navigate(item.path) : handlePreview(item)"
            >
              <div class="text-2xl leading-none">{{ item.is_dir ? "📁" : fileEmoji(item.name) }}</div>
              <p class="mt-2 truncate text-xs font-medium text-gray-800 dark:text-gray-100">{{ item.name }}</p>
              <p class="mt-0.5 text-[10px] text-gray-400">{{ item.is_dir ? "文件夹" : formatSize(item.size) }}</p>
            </button>
            <input
              v-if="canSelect(item)"
              type="checkbox"
              class="mt-0.5 rounded border-gray-300 text-primary focus:ring-primary/30"
              :checked="selectedPaths.has(item.path)"
              @change="toggleSelect(item.path)"
            />
          </div>
          <div class="flex justify-end opacity-0 transition-opacity group-hover:opacity-100">
            <WorkspaceRowActions
              v-if="!isVirtualRoot"
              :item="item"
              :in-trash="inTrash"
              @preview="handlePreview"
              @download="handleDownload"
              @rename="openRename"
              @copy-path="handleCopyPath"
              @extract="askExtract"
              @delete="askDelete"
              @restore="handleRestore"
              @purge="askPurge"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- 上传进度 -->
    <div v-if="showUploadPanel" class="rounded-xl border border-gray-200 bg-gray-50/70 px-3 py-2 dark:border-gray-800 dark:bg-gray-800/50">
      <div class="mb-1 flex items-center justify-between text-xs text-gray-600 dark:text-gray-300">
        <span>上传进度 {{ snapshot.aggregate.done }} / {{ snapshot.aggregate.total }}</span>
        <button type="button" class="text-gray-400 hover:text-gray-700" @click="clearFinishedUploads">清除已完成</button>
      </div>
      <ul class="max-h-28 space-y-1 overflow-y-auto">
        <li v-for="task in snapshot.tasks" :key="task.id" class="flex items-center gap-2 text-[11px]">
          <span class="min-w-0 flex-1 truncate text-gray-600 dark:text-gray-300">{{ task.name }}</span>
          <span :class="task.status === 'error' ? 'text-red-600' : 'text-gray-400'">
            {{ task.status === 'error' ? (task.error || '失败') : task.status === 'done' ? '完成' : `${Math.round(task.progress * 100)}%` }}
          </span>
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

    <!-- 预览复用聊天页的 Canvas：markdown 渲染 / 代码高亮 / CSV 透视 / 文档查看器全都在这里，
         自己再写一套预览只会出现「同一个文件两种样子」 -->
    <ChatCanvas
      v-model:pinned="canvasPinned"
      :visible="canvasVisible"
      :data="canvasData"
      dock-side="right"
      @close="closeCanvas"
    />

    <ConfirmModal
      v-if="confirmAction"
      :title="confirmAction.title"
      :message="confirmAction.message"
      :confirm-text="confirmAction.confirmText"
      type="danger"
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
import HeroSilhouette from "@/components/common/HeroSilhouette.vue"
import ChatCanvas from "@/components/embed/ChatCanvas.vue"
import { useWorkspaceCanvas } from "@/composables/chat/useWorkspaceCanvas"
import { useToast } from "@/composables/useToast"
import { useWorkspaceUploadQueue } from "@/composables/useWorkspaceUploadQueue"
import { isExtractableArchive } from "@/utils/workspaceActionEntries"
import {
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
}

const { showToast } = useToast()
const { snapshot, enqueue, clearFinished } = useWorkspaceUploadQueue()

const items = ref<FsItem[]>([])
const currentPath = ref("")
const parentPath = ref("")
const userWorkspaceRoot = ref("")
const isRoot = ref(true)
const isVirtualRoot = ref(false)
const writable = ref(false)
const scope = ref<"user_scoped" | "admin_all">("user_scoped")
const loading = ref(false)
const keyword = ref("")
const searchAll = ref(false)
const recursiveItems = ref<FsItem[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)
const viewMode = ref<"list" | "grid">("list")
const sortBy = ref<"mtime" | "name" | "size">("mtime")
const typeFilter = ref<"all" | "doc" | "sheet" | "image" | "archive" | "code">("all")
const selectedPaths = reactive(new Set<string>())
const dragging = ref(false)
const createOpen = ref(false)
const recentOpen = ref(false)
const recentFiles = ref<Array<{ path: string; name: string; mtime: number }>>([])
const recentMenuRef = ref<HTMLElement | null>(null)
const createMenuRef = ref<HTMLElement | null>(null)

const SYSTEM_PROTECTED = new Set(["docs", "skills", "branding", "agent_workspaces", ".trash", "sessions", "uploads", "sandbox"])

const uploading = computed(() => snapshot.value.aggregate.active)
const showUploadPanel = computed(() => snapshot.value.tasks.length > 0 && (uploading.value || snapshot.value.aggregate.failed > 0))
const inTrash = computed(() => Boolean(userWorkspaceRoot.value) && currentPath.value === `${userWorkspaceRoot.value}/.trash`)
// 管理员默认落在自己的私有工作区：那里不是「全部用户」视图，别让徽标误导。
const isInsideOwnWorkspace = computed(() => {
  const root = userWorkspaceRoot.value
  if (!root || !currentPath.value) return false
  return currentPath.value === root || currentPath.value.startsWith(`${root}/`)
})
// 虚拟根（「公共资源」）是授权根汇总，不是真实可写目录：新建/上传/勾选都必须关掉，
// 否则按钮点下去只会吃后端 403。
const canCreateHere = computed(
  () => !inTrash.value && !loading.value && Boolean(currentPath.value) && !searchAll.value && writable.value,
)

const searchMode = computed(() => (searchAll.value && keyword.value.trim().length >= 2 ? "all" : "current"))
const sourceItems = computed(() => (searchMode.value === "all" ? recursiveItems.value : items.value))

const TYPE_GROUPS: Record<string, string[]> = {
  doc: ["md", "txt", "doc", "docx", "pdf", "rtf", "epub", "html"],
  sheet: ["xls", "xlsx", "csv", "tsv"],
  image: ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"],
  archive: ["zip", "tar", "gz", "tgz", "7z", "rar"],
  code: ["py", "sh", "js", "ts", "json", "yaml", "yml", "sql", "go", "java", "c", "cpp", "css"],
}

const extOf = (name: string) => (name.split(".").pop() || "").toLowerCase()

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
    if (sortBy.value === "name") return a.name.localeCompare(b.name, "zh-CN")
    if (sortBy.value === "size") return (b.size || 0) - (a.size || 0)
    return (b.mtime || 0) - (a.mtime || 0)
  })
  return list
})

const selectableItems = computed(() => visibleItems.value.filter((item) => canSelect(item)))
const allSelected = computed(
  () => selectableItems.value.length > 0 && selectableItems.value.every((item) => selectedPaths.has(item.path)),
)

const pathSegments = computed(() => {
  const root = userWorkspaceRoot.value
  if (searchMode.value === "all") return [{ label: `全目录搜索：${keyword.value.trim()}`, path: currentPath.value }]
  // 虚拟根不是真实目录，面包屑只留一级「公共资源」（空 path 即回到汇总）。
  if (isVirtualRoot.value) return [{ label: "公共资源", path: "" }]
  if (!currentPath.value) return []
  const relative = root && currentPath.value.startsWith(root)
    ? currentPath.value.slice(root.length).replace(/^\/+/, "")
    : currentPath.value
  const segments: Array<{ label: string; path: string }> = []
  if (root) segments.push({ label: "我的目录", path: root })
  let acc = root
  for (const part of relative.split("/").filter(Boolean)) {
    acc = acc ? `${acc}/${part}` : part
    segments.push({ label: part, path: acc })
  }
  return segments
})

const currentLabel = computed(() => pathSegments.value[pathSegments.value.length - 1]?.label || "我的目录")

const quickNavLinks = computed(() => {
  const root = userWorkspaceRoot.value
  if (!root) return []
  return [
    { key: "home", label: "我的目录", path: root },
    { key: "docs", label: "我的文档", path: `${root}/docs` },
    { key: "uploads", label: "上传目录", path: `${root}/uploads` },
    { key: "trash", label: "回收站", path: `${root}/.trash` },
    { key: "public", label: "公共资源", path: "" },
  ].map((link) => ({
    ...link,
    active: link.key === "public"
      ? isVirtualRoot.value
      : !isVirtualRoot.value && currentPath.value === link.path && !searchAll.value,
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
  if (["md", "txt", "doc", "docx", "pdf"].includes(ext)) return "📄"
  if (["xls", "xlsx", "csv"].includes(ext)) return "📊"
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "🖼️"
  if (["zip", "tar", "gz", "tgz", "7z", "rar"].includes(ext)) return "🗜️"
  if (["py", "sh", "js", "ts", "json", "yaml", "yml"].includes(ext)) return "📜"
  return "📄"
}

const canSelect = (item: FsItem) => !inTrash.value && !isVirtualRoot.value && !SYSTEM_PROTECTED.has(item.name)

const canManageItem = (item: FsItem) => {
  if (SYSTEM_PROTECTED.has(item.name)) return false
  if (!userWorkspaceRoot.value) return false
  return currentPath.value !== userWorkspaceRoot.value || !item.is_user_workspace
}

/**
 * 读取目录。
 *
 * `preferUserRoot` 只用于首屏：把空 path 交给后端解析成「本人私有工作区」
 * （`GET /chat/fs/list?prefer=user_root`），不必先拿虚拟根再二次跳转。
 * 「公共资源」入口走默认空 path，拿到的就是授权根汇总。
 */
const load = async (path = "", options: { preferUserRoot?: boolean } = {}) => {
  loading.value = true
  clearSelection()
  try {
    const params: Record<string, string> = { path }
    if (options.preferUserRoot) params.prefer = "user_root"
    const res = await axios.get("/api/v1/chat/fs/list", { params })
    const data = res.data?.data
    if (!data) return
    currentPath.value = data.current_path || ""
    parentPath.value = data.parent_path || ""
    isRoot.value = Boolean(data.is_root)
    isVirtualRoot.value = Boolean(data.is_virtual_root)
    writable.value = Boolean(data.writable)
    scope.value = data.scope === "admin_all" ? "admin_all" : "user_scoped"
    items.value = Array.isArray(data.items) ? data.items : []
    if (data.user_workspace_root) userWorkspaceRoot.value = data.user_workspace_root
    keyword.value = ""
    searchAll.value = false
    recursiveItems.value = []
  } catch {
    showToast("读取目录失败，请稍后重试", "error")
    items.value = []
  } finally {
    loading.value = false
  }
}

const navigate = (path: string | null | undefined) => {
  // 空字符串是「公共资源」虚拟根的合法目标，只有 null/undefined 才忽略。
  if (path === null || path === undefined) return
  void load(path)
}

/* ---------------- 搜索 ---------------- */
let searchTimer: ReturnType<typeof setTimeout> | null = null
const onKeywordInput = () => {
  if (searchTimer) clearTimeout(searchTimer)
  const kw = keyword.value.trim()
  if (!searchAll.value || kw.length < 2) {
    recursiveItems.value = []
    return
  }
  searchTimer = setTimeout(() => {
    void runRecursiveSearch(kw)
  }, 300)
}

const runRecursiveSearch = async (kw: string) => {
  try {
    const res = await axios.get("/api/v1/chat/fs/search", {
      params: { q: kw, path: currentPath.value, max_results: 100 },
    })
    const found = res.data?.data?.items
    recursiveItems.value = Array.isArray(found) ? found : []
  } catch {
    recursiveItems.value = []
    showToast("搜索失败，请稍后重试", "error")
  }
}

/* ---------------- 选择 ---------------- */
const toggleSelect = (path: string) => {
  if (selectedPaths.has(path)) selectedPaths.delete(path)
  else selectedPaths.add(path)
}
const toggleSelectAll = () => {
  if (allSelected.value) clearSelection()
  else selectableItems.value.forEach((item) => selectedPaths.add(item.path))
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
  void load(currentPath.value)
}

watch(uploading, (now, before) => {
  if (before && !now) void load(currentPath.value)
})

let dragDepth = 0
const onDragEnter = (event: DragEvent) => {
  if (!canCreateHere.value || !event.dataTransfer?.types.includes("Files")) return
  dragDepth += 1
  dragging.value = true
}
const onDragOver = (event: DragEvent) => {
  if (canCreateHere.value && event.dataTransfer) event.dataTransfer.dropEffect = "copy"
}
const onDragLeave = () => {
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragging.value = false
}
const onDrop = (event: DragEvent) => {
  dragDepth = 0
  dragging.value = false
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

const toggleRecentMenu = async () => {
  createOpen.value = false
  recentOpen.value = !recentOpen.value
  if (recentOpen.value) await loadRecent()
}

const openRecent = (recent: { path: string; name: string }) => {
  recentOpen.value = false
  handlePreview({ path: recent.path, name: recent.name })
}

const onDocumentClick = (event: MouseEvent) => {
  const target = event.target as Node
  if (recentMenuRef.value && !recentMenuRef.value.contains(target)) recentOpen.value = false
  if (createMenuRef.value && !createMenuRef.value.contains(target)) createOpen.value = false
}

/* ---------------- 文件操作 ---------------- */
/**
 * 预览走聊天页同一套画布。
 *
 * 关键：`buildWorkspaceCanvasPayload()` 产出的是 `canvas://file?path=...` 这种**协议字符串**，
 * 必须交给 `useWorkspaceCanvas.handleOpenCanvas()` 解析成真实内容（文本/图片/PDF/Office 分流、
 * CSV 透视、旧版 .ppt 降级为下载），再把结果喂给 `ChatCanvas` 渲染。
 * 直接把 payload 塞进 canvas，画布拿到的只是一串 `canvas://`，所以什么都显示不出来。
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
})

/** 双击/单击送达的统一入口：目录进去，文件开预览 */
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
  void handleOpenCanvas(buildWorkspaceCanvasPayload(item.path, item.name))
}

const handleDownload = (item: FsItem) => {
  void downloadWorkspaceFile({
    path: item.path,
    name: item.name,
    showToast: (message: string, type?: "success" | "error" | "warning" | "info") => showToast(message, type || "info"),
  })
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
    void load(currentPath.value)
  } catch (error: any) {
    showToast(error?.response?.data?.detail || "操作失败，请稍后重试", "error")
  } finally {
    dialogSubmitting.value = false
  }
}

/* ---------------- 解压 ---------------- */
const askExtract = (item: FsItem) => {
  if (!isExtractableArchive(item.name)) {
    showToast("暂不支持 rar / 7z，请先转成 zip", "warning")
    return
  }
  confirmAction.value = {
    title: "解压",
    message: `将「${item.name}」解压到当前目录下的同名文件夹。`,
    confirmText: "解压",
    run: async () => {
      const subdir = item.name.replace(/\.(tar\.gz|tgz|tar|zip)$/i, "")
      const res = await extractWorkspaceArchive({
        archivePath: item.path,
        targetDir: currentPath.value,
        subdir,
      })
      const count = Number(res.data?.data?.file_count ?? 0)
      showToast(`已解压 ${count} 个文件`, "success")
      void load(currentPath.value)
    },
  }
}

/* ---------------- 删除 / 回收站 ---------------- */
const confirmAction = ref<{ title: string; message: string; confirmText: string; run: () => Promise<void> } | null>(null)
const confirmSubmitting = ref(false)

const askDelete = (item: FsItem) => {
  confirmAction.value = {
    title: "移到回收站",
    message: `「${item.name}」会被移到回收站，可以再还原回来。`,
    confirmText: "移到回收站",
    run: async () => {
      await deleteWorkspaceEntry(item.path)
      showToast("已移到回收站", "success")
      void load(currentPath.value)
    },
  }
}

const askPurge = (item: FsItem) => {
  confirmAction.value = {
    title: "彻底删除",
    message: `「${item.name}」将无法恢复，确定删除吗？`,
    confirmText: "彻底删除",
    run: async () => {
      await purgeWorkspaceEntry(item.path)
      showToast("已彻底删除", "success")
      void load(currentPath.value)
    },
  }
}

const askEmptyTrash = () => {
  confirmAction.value = {
    title: "清空回收站",
    message: "回收站里的文件将全部永久删除，无法恢复。",
    confirmText: "清空",
    run: async () => {
      await emptyWorkspaceTrash()
      showToast("回收站已清空", "success")
      void load(currentPath.value)
    },
  }
}

const askBatchDelete = () => {
  const deletable = Array.from(selectedPaths).filter((path) => {
    const item = items.value.find((entry) => entry.path === path)
    return item ? canManageItem(item) : false
  })
  if (!deletable.length) {
    showToast("选中的项目都不能删除", "warning")
    return
  }
  confirmAction.value = {
    title: "批量删除",
    message: `${deletable.length} 项将被移到回收站，可以再还原回来。`,
    confirmText: "移到回收站",
    run: async () => {
      for (const path of deletable) {
        await deleteWorkspaceEntry(path)
      }
      showToast(`已删除 ${deletable.length} 项`, "success")
      clearSelection()
      void load(currentPath.value)
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
    void load(currentPath.value)
  } catch (error: any) {
    showToast(error?.response?.data?.detail || "还原失败，请稍后重试", "error")
  }
}

onMounted(() => {
  document.addEventListener("click", onDocumentClick)
  // 首屏直接落「我的目录」（后端 prefer=user_root 解析到本人私有工作区），
  // 公共目录走侧栏「公共资源」入口。
  void load("", { preferUserRoot: true })
})

onBeforeUnmount(() => {
  document.removeEventListener("click", onDocumentClick)
  if (searchTimer) clearTimeout(searchTimer)
})
</script>
