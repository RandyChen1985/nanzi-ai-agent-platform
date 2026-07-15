<script setup lang="ts">
import { ref, watch, onUnmounted } from "vue";
import axios from "@/utils/axios";
import {
  attachmentPreviewNeedsAuthFetch,
  getAttachmentPreviewUrl,
  isImageAttachment,
} from "@/utils/attachmentImages";

const props = withDefaults(
  defineProps<{
    file: { url?: string; type?: string; ext?: string; filename?: string };
    clickable?: boolean;
    sizeClass?: string;
  }>(),
  {
    clickable: false,
    sizeClass: "w-8 h-8",
  },
);

const emit = defineEmits<{
  (e: "click", url: string): void;
}>();

const displayUrl = ref<string | null>(null);
const loading = ref(false);
const failed = ref(false);
let objectUrl: string | null = null;

const cleanupObjectUrl = () => {
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  }
};

const loadPreview = async () => {
  cleanupObjectUrl();
  displayUrl.value = null;
  failed.value = false;

  if (!isImageAttachment(props.file)) return;

  const previewUrl = getAttachmentPreviewUrl(props.file);
  if (!previewUrl) return;

  // 公网/静态资源可直接用 img；鉴权预览 API 必须走 axios + blob
  if (!attachmentPreviewNeedsAuthFetch(previewUrl)) {
    displayUrl.value = previewUrl;
    return;
  }

  loading.value = true;
  try {
    const res = await axios.get(previewUrl, { responseType: "blob" });
    objectUrl = URL.createObjectURL(res.data);
    displayUrl.value = objectUrl;
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
};

watch(() => props.file, loadPreview, { immediate: true, deep: true });
onUnmounted(cleanupObjectUrl);

const handleClick = () => {
  if (props.clickable && displayUrl.value) {
    emit("click", displayUrl.value);
  }
};
</script>

<template>
  <div
    v-if="isImageAttachment(file)"
    class="rounded overflow-hidden flex-shrink-0 border border-gray-200/30 dark:border-gray-700/30 bg-gray-200 dark:bg-gray-700 flex items-center justify-center"
    :class="sizeClass"
  >
    <div
      v-if="loading"
      class="w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin"
    />
    <img
      v-else-if="displayUrl"
      :src="displayUrl"
      :alt="file.filename || '图片预览'"
      class="w-full h-full object-cover"
      :class="clickable ? 'cursor-pointer' : ''"
      @click="handleClick"
      @error="failed = true; displayUrl = null"
    />
    <span v-else-if="failed" class="text-[10px]" title="预览加载失败">🖼️</span>
  </div>
</template>
