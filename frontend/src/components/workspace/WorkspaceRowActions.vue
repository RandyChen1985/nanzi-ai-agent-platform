<!--
  工作空间的行内操作菜单（列表与卡片视图共用）。

  条目与聊天抽屉的右键菜单保持一致（复用 `buildWorkspaceActionEntries` 的语义），
  唯一的差异是**去掉「挂载到会话」**：独立页没有目标会话，这个动作只属于聊天。

  菜单面板通过 Teleport 挂载到 body 并按视口边界自动上下翻转，避免：
  1. 列表/卡片最末行被父容器 `overflow-auto` 裁切；
  2. 卡片视图里鼠标移出卡片范围后因 `group-hover` 失效导致菜单隐身。
-->
<template>
  <div class="relative">
    <button
      ref="triggerRef"
      type="button"
      class="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
      :class="open ? 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200' : ''"
      title="更多操作"
      aria-haspopup="menu"
      :aria-expanded="open"
      @click.stop="toggleMenu"
    >
      <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
        <circle cx="5.5" cy="12" r=".9" fill="currentColor" />
        <circle cx="12" cy="12" r=".9" fill="currentColor" />
        <circle cx="18.5" cy="12" r=".9" fill="currentColor" />
      </svg>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="menuRef"
        :style="menuStyle"
        class="fixed z-[1200] w-44 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-xl dark:border-gray-700 dark:bg-gray-800"
        role="menu"
        @click.stop
      >
        <template v-for="entry in entries" :key="entry.key">
          <!-- 表头项（如「公共目录（管理员）」）只是说明，不是动作 -->
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
              @click="run(entry.key)"
            >
              <span aria-hidden="true">{{ entry.icon }}</span>
              <span class="truncate">{{ entry.label }}</span>
            </button>
          </template>
        </template>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"
import {
  buildWorkspaceActionEntries,
  buildWorkspaceItemMenuContext,
  isPublicWorkspaceItem,
} from "@/utils/workspaceActionEntries"
import type { WorkspaceActionEntry, WorkspaceActionKey } from "@/utils/workspaceActionEntries"
import { canPreviewWorkspaceFile } from "@/utils/workspaceFilePreview"

export type ManageRowActionKey =
  | WorkspaceActionKey
  | "move"
  | "copy-to-workspace"
  | "download-zip"
  | "ask-ai"

export interface ManageRowActionEntry extends Omit<WorkspaceActionEntry, "key"> {
  key: ManageRowActionKey
}

interface RowItem {
  name: string
  path: string
  is_dir: boolean
  /**
   * 大小与修改时间。
   *
   * 行操作本身用不到它们，但页面把 `FsItem` 直接传进来，而下载 / 重命名 / 删除等
   * 处理函数的签名都收 `FsItem`。缺这两个字段，RowItem 就不是 FsItem 的子类型，
   * 参数逆变检查会让两处调用的 18 个事件绑定全部报「类型不兼容」。
   */
  size: number
  mtime: number
  is_public?: boolean
  is_user_workspace?: boolean
  readonly?: boolean
}

const props = withDefaults(
  defineProps<{
    item: RowItem
    inTrash?: boolean
    /** 是否为回收站根目录（`.trash` 本身）。 */
    isTrashRoot?: boolean
    /** 当前是否管理员视图：公共目录条目据它决定是否露出重命名/永久删除。 */
    isAdminScope?: boolean
    /** 系统保护目录（docs / skills / branding / uploads 等根级目录），不可删除/重命名。 */
    isSystemProtected?: boolean
    /** 该条目是否允许重命名/删除（结合权限、归属与保护规则）。 */
    canManage?: boolean
    /** 本人私有工作区根：`is_public` 缺失时兜底判定公共性。 */
    userWorkspaceRoot?: string
  }>(),
  {
    inTrash: false,
    isTrashRoot: false,
    isAdminScope: false,
    isSystemProtected: false,
    canManage: true,
    userWorkspaceRoot: "",
  },
)

const emit = defineEmits<{
  (e: "open-dir", item: RowItem): void
  (e: "preview", item: RowItem): void
  (e: "download", item: RowItem): void
  (e: "download-zip", item: RowItem): void
  (e: "share", item: RowItem): void
  (e: "copy-and-share", item: RowItem): void
  (e: "copy-to-workspace", item: RowItem): void
  (e: "ask-ai", item: RowItem): void
  (e: "move", item: RowItem): void
  (e: "rename", item: RowItem): void
  (e: "copy-path", item: RowItem): void
  (e: "extract", item: RowItem): void
  (e: "delete", item: RowItem): void
  (e: "restore", item: RowItem): void
  (e: "purge", item: RowItem): void
  (e: "empty-trash"): void
  (e: "open-change", open: boolean): void
}>()

const MENU_OPEN_EVENT = "workspace-row-actions-open"
const MENU_WIDTH = 176
const VIEWPORT_MARGIN = 8

const instanceId = Symbol("workspace-row-actions")
const open = ref(false)
const triggerRef = ref<HTMLElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
const menuPos = ref<{ top: number; left: number }>({ top: 0, left: 0 })

const menuStyle = computed(() => ({
  top: `${menuPos.value.top}px`,
  left: `${menuPos.value.left}px`,
}))

const entries = computed<ManageRowActionEntry[]>(() => {
  const target = {
    name: props.item.name,
    path: props.item.path,
    isDir: props.item.is_dir,
    isPreviewable: !props.item.is_dir && canPreviewWorkspaceFile(props.item.name),
  }

  const raw = buildWorkspaceActionEntries(
    buildWorkspaceItemMenuContext({
      item: props.item,
      target,
      userWorkspaceRoot: props.userWorkspaceRoot,
      isAdminScope: props.isAdminScope,
      inTrash: props.inTrash,
      isTrashRoot: props.isTrashRoot,
      isSystemProtected: props.isSystemProtected,
      canManage: props.canManage && !props.isSystemProtected,
    }),
  )

  const base: ManageRowActionEntry[] = raw
    .filter((entry) => entry.key !== "mount") // 独立页没有会话，不展示挂载
    .map((entry) => ({
      ...entry,
      // 抽屉里「画布打开预览」这个名字依赖画布宿主，这里改成通用的「预览」
      label: entry.key === "preview" ? "预览" : entry.key === "download" ? "下载" : entry.label,
    }))

  // 独立页扩展动作：保持与抽屉的基础语义一致，同时补齐文件管理高频能力
  const result: ManageRowActionEntry[] = []
  const isPublic = isPublicWorkspaceItem(props.item, props.userWorkspaceRoot)
  const canMove =
    !props.inTrash &&
    !props.isTrashRoot &&
    !props.isSystemProtected &&
    !isPublic &&
    props.canManage

  for (const item of base) {
    result.push(item)
    // 目录紧跟「打开目录」提供「打包下载」（排除回收站根与回收站子树）
    if (item.key === "open-dir" && props.item.is_dir && !props.inTrash && !props.isTrashRoot) {
      result.push({ key: "download-zip", label: "打包下载", icon: "📦" })
    }
    // 公共文件紧跟「复制并分享」提供单独「复制到我的目录」，无需强制走分享流程
    if (item.key === "copy-and-share" && !props.item.is_dir) {
      result.push({ key: "copy-to-workspace", label: "复制到我的目录", icon: "📥" })
    }
    // 普通文件紧跟「复制路径」提供「用 AI 分析」与「复制到我的目录」
    if (item.key === "copy-path" && !props.item.is_dir && !props.inTrash) {
      result.push({ key: "ask-ai", label: "用 AI 分析", icon: "✨" })
    }
    // 可管理条目紧跟「重命名」提供「移动到…」
    if (item.key === "rename" && canMove) {
      result.push({ key: "move", label: "移动到…", icon: "📁" })
    }
  }

  return result
})

const updateMenuPosition = () => {
  const trigger = triggerRef.value
  if (!trigger) return
  const rect = trigger.getBoundingClientRect()
  const menuEl = menuRef.value
  const menuWidth = menuEl?.offsetWidth || MENU_WIDTH
  const menuHeight = menuEl?.offsetHeight || Math.max(40, entries.value.length * 32 + 12)

  let left = rect.right - menuWidth
  left = Math.max(VIEWPORT_MARGIN, Math.min(left, window.innerWidth - menuWidth - VIEWPORT_MARGIN))

  const spaceBelow = window.innerHeight - rect.bottom - VIEWPORT_MARGIN
  const spaceAbove = rect.top - VIEWPORT_MARGIN
  let top = rect.bottom + 4
  if (spaceBelow < menuHeight && spaceAbove > spaceBelow) {
    top = Math.max(VIEWPORT_MARGIN, rect.top - menuHeight - 4)
  } else {
    top = Math.max(VIEWPORT_MARGIN, Math.min(top, window.innerHeight - menuHeight - VIEWPORT_MARGIN))
  }
  menuPos.value = { top: Math.round(top), left: Math.round(left) }
}

const closeMenu = () => {
  if (!open.value) return
  open.value = false
}

const toggleMenu = async () => {
  if (open.value) {
    open.value = false
    return
  }
  // 通知其它已展开的行菜单（以及页面顶栏菜单）自动收起，一次点击直接切换
  window.dispatchEvent(new CustomEvent(MENU_OPEN_EVENT, { detail: instanceId }))
  open.value = true
  await nextTick()
  updateMenuPosition()
}

watch(open, (val) => {
  emit("open-change", val)
})

const run = (key: ManageRowActionKey) => {
  open.value = false
  switch (key) {
    case "open-dir":
      emit("open-dir", props.item)
      break
    case "preview":
      emit("preview", props.item)
      break
    case "download":
      emit("download", props.item)
      break
    case "download-zip":
      emit("download-zip", props.item)
      break
    case "rename":
      emit("rename", props.item)
      break
    case "move":
      emit("move", props.item)
      break
    case "share":
      emit("share", props.item)
      break
    case "copy-and-share":
      emit("copy-and-share", props.item)
      break
    case "copy-to-workspace":
      emit("copy-to-workspace", props.item)
      break
    case "ask-ai":
      emit("ask-ai", props.item)
      break
    case "copy-path":
      emit("copy-path", props.item)
      break
    case "extract":
      emit("extract", props.item)
      break
    case "delete":
      emit("delete", props.item)
      break
    case "restore":
      emit("restore", props.item)
      break
    case "purge":
      emit("purge", props.item)
      break
    case "empty-trash":
      emit("empty-trash")
      break
    default:
      break
  }
}

const onDocumentClick = (event: MouseEvent) => {
  if (!open.value) return
  const target = event.target as Node | null
  if (target && (triggerRef.value?.contains(target) || menuRef.value?.contains(target))) return
  open.value = false
}

const onOtherMenuOpen = (event: Event) => {
  const detail = (event as CustomEvent).detail
  if (detail !== instanceId) closeMenu()
}

const onKeydown = (event: KeyboardEvent) => {
  if (open.value && event.key === "Escape") closeMenu()
}

onMounted(() => {
  document.addEventListener("click", onDocumentClick)
  window.addEventListener(MENU_OPEN_EVENT, onOtherMenuOpen)
  window.addEventListener("scroll", closeMenu, true)
  window.addEventListener("resize", closeMenu)
  document.addEventListener("keydown", onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener("click", onDocumentClick)
  window.removeEventListener(MENU_OPEN_EVENT, onOtherMenuOpen)
  window.removeEventListener("scroll", closeMenu, true)
  window.removeEventListener("resize", closeMenu)
  document.removeEventListener("keydown", onKeydown)
})
</script>
