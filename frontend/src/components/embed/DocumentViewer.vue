<script setup lang="ts">
import { computed, defineAsyncComponent, onUnmounted, ref, shallowRef, watch } from 'vue'
import axios from '@/utils/axios'
import type { CanvasDocumentMeta } from '@/types/canvas'
// 宿主容器的基础样式（width / height）；组件内部样式由 Shadow DOM 自动注入
import '@file-viewer/vue3/dist/file-viewer3.css'

const props = defineProps<{
  /** 需要鉴权的文档地址；走 axios 获取，因此跨站嵌入场景同样可用 */
  url: string
  filename: string
  meta?: CanvasDocumentMeta
  theme: 'light' | 'dark'
}>()

const emit = defineEmits<{
  (e: 'error', message: string): void
  (e: 'fallback-download'): void
}>()

/**
 * 懒加载组件本体：file-viewer 及其 renderer / Worker / WASM 资产
 * 不进入首屏包，仅在真正打开文档时才加载。
 */
const FileViewer = defineAsyncComponent(async () => {
  const mod = await import('@file-viewer/vue3')
  return mod.FileViewer
})

type ViewerStatus = 'loading' | 'ready' | 'error'

const status = ref<ViewerStatus>('loading')
const errorMessage = ref('')
/** 用 shallowRef 保存文件对象：无需深层响应式，也避免 Vue 代理二进制对象 */
const fileBlob = shallowRef<File | null>(null)

let controller: AbortController | null = null

async function loadBlob() {
  controller?.abort()
  controller = new AbortController()

  status.value = 'loading'
  errorMessage.value = ''
  fileBlob.value = null

  if (!props.url) {
    status.value = 'error'
    errorMessage.value = '缺少文件地址'
    emit('error', errorMessage.value)
    return
  }

  try {
    const response = await axios.get(props.url, {
      responseType: 'blob',
      signal: controller.signal,
    })
    // 必须包装成带文件名的 File：渲染器靠扩展名选择预览链路，
    // 裸 Blob 没有 name，会导致 renderer 选择失败。
    // 仍然不创建对象 URL，从根上避免对象 URL 泄漏。
    const blob = response.data as Blob
    fileBlob.value = new File(
      [blob],
      props.meta?.filename || props.filename || 'document',
      { type: props.meta?.mime || blob.type || '' },
    )
    status.value = 'ready'
  } catch (err: any) {
    const canceled = err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED'
    if (canceled) return
    status.value = 'error'
    errorMessage.value = err?.response?.data?.detail || err?.message || '文档加载失败'
    emit('error', errorMessage.value)
  }
}

const viewerOptions = computed(() => ({
  theme: props.theme,
  locale: 'auto',
  // 默认 auto 会解析为 Shadow DOM，组件样式不会污染画布
  styleIsolation: 'auto' as const,
}))

const resolvedFilename = computed(() => props.meta?.filename || props.filename || 'document')
const resolvedType = computed(() => props.meta?.mime)

watch(() => props.url, loadBlob, { immediate: true })

onUnmounted(() => {
  controller?.abort()
  controller = null
})
</script>

<template>
  <div class="w-full h-full min-h-[500px] relative">
    <div
      v-if="status === 'loading'"
      class="flex h-full min-h-[500px] w-full flex-col items-center justify-center gap-3 text-xs text-gray-400 dark:text-gray-500"
    >
      <div class="h-6 w-6 animate-spin rounded-full border-2 border-gray-200 border-t-primary dark:border-gray-700"></div>
      <span>正在加载文档…</span>
    </div>

    <div
      v-else-if="status === 'error'"
      class="flex h-full min-h-[500px] w-full flex-col items-center justify-center gap-3 px-6 text-center"
    >
      <p class="text-xs text-gray-500 dark:text-gray-400">{{ errorMessage }}</p>
      <button
        type="button"
        class="rounded-lg bg-primary px-3 py-1.5 text-xs font-bold text-white hover:bg-primary/90"
        @click="emit('fallback-download')"
      >
        下载原文件
      </button>
    </div>

    <FileViewer
      v-else-if="fileBlob"
      :file="fileBlob ?? undefined"
      :filename="resolvedFilename"
      :type="resolvedType"
      :options="viewerOptions"
      class="h-full w-full"
    />
  </div>
</template>
