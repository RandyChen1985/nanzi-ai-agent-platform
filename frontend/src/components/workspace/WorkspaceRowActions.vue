<!--
  工作空间的行内操作菜单（列表与卡片视图共用）。

  条目与聊天抽屉的右键菜单保持一致（复用 `buildWorkspaceActionEntries` 的语义），
  唯一的差异是**去掉「挂载到会话」**：独立页没有目标会话，这个动作只属于聊天。
-->
<template>
  <div class="relative">
    <button
      type="button"
      class="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-700"
      title="更多操作"
      aria-haspopup="menu"
      :aria-expanded="open"
      @click.stop="open = !open"
    >
      <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
        <circle cx="5.5" cy="12" r=".9" fill="currentColor" />
        <circle cx="12" cy="12" r=".9" fill="currentColor" />
        <circle cx="18.5" cy="12" r=".9" fill="currentColor" />
      </svg>
    </button>

    <div v-if="open" class="fixed inset-0 z-30" @click.stop="open = false" />

    <div
      v-if="open"
      class="absolute right-0 z-40 mt-1 w-40 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-xl dark:border-gray-700 dark:bg-gray-800"
      role="menu"
      @click.stop
    >
      <button
        v-for="entry in entries"
        :key="entry.key"
        type="button"
        class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40"
        :class="entry.danger
          ? 'text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40'
          : 'text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700/60'"
        :disabled="entry.disabled"
        :title="entry.disabledHint || entry.label"
        @click="run(entry.key)"
      >{{ entry.label }}</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue"
import { buildWorkspaceActionEntries, isExtractableArchive } from "@/utils/workspaceActionEntries"
import type { WorkspaceActionKey } from "@/utils/workspaceActionEntries"

interface RowItem {
  name: string
  path: string
  is_dir: boolean
  is_public?: boolean
  is_user_workspace?: boolean
}

const props = defineProps<{
  item: RowItem
  inTrash?: boolean
}>()

const emit = defineEmits<{
  (e: "preview", item: RowItem): void
  (e: "download", item: RowItem): void
  (e: "rename", item: RowItem): void
  (e: "copy-path", item: RowItem): void
  (e: "extract", item: RowItem): void
  (e: "delete", item: RowItem): void
  (e: "restore", item: RowItem): void
  (e: "purge", item: RowItem): void
}>()

const open = ref(false)

const entries = computed(() => {
  const target = {
    name: props.item.name,
    path: props.item.path,
    isDir: props.item.is_dir,
    isPreviewable: true,
  }

  const raw = buildWorkspaceActionEntries({
    scene: props.inTrash ? "trash-item" : "regular-item",
    target,
    canManage: true,
  })

  return raw
    .filter((entry) => entry.key !== "mount") // 独立页没有会话，不展示挂载
    .filter((entry) => entry.key !== "open-dir") // 打开目录由行本身承担
    .map((entry) => ({
      ...entry,
      // 抽屉里「画布打开预览」这个名字依赖画布宿主，这里改成通用的「预览」
      label: entry.key === "preview" ? "预览" : entry.key === "download" ? "下载" : entry.label,
    }))
})

const run = (key: WorkspaceActionKey) => {
  open.value = false
  switch (key) {
    case "preview":
      emit("preview", props.item)
      break
    case "download":
      emit("download", props.item)
      break
    case "rename":
      emit("rename", props.item)
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
    default:
      break
  }
}

const onDocumentClick = () => {
  open.value = false
}

onMounted(() => {
  document.addEventListener("click", onDocumentClick)
})
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocumentClick)
})
</script>
