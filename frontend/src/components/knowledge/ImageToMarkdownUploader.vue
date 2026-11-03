<template>
  <div class="space-y-3">
    <!-- 多模态模型没配时，整个入口不可用：与其让用户传完图等超时，不如先说清楚 -->
    <div
      v-if="visionState === 'missing'"
      class="rounded-xl border border-amber-200 bg-amber-50/70 px-3.5 py-3 text-[12px] leading-relaxed text-amber-900"
    >
      <p class="font-semibold">图片解析需要一个多模态模型</p>
      <p class="mt-1 text-amber-800/80">
        系统还没有配置可用的默认多模态模型。请管理员在「系统配置 · 模型配置」里指定
        multimodal / vision 类型的模型后，这里会自动可用。
      </p>
    </div>

    <div
      v-else
      class="rounded-xl border border-dashed px-3.5 py-3 transition-all duration-300"
      :class="[
        dragOver ? 'border-primary bg-primary/5' : 'border-gray-300 hover:border-primary',
        busy ? 'pointer-events-none opacity-70' : 'bg-gray-50/30',
      ]"
      @dragover.prevent="dragOver = true"
      @dragleave.prevent="dragOver = false"
      @drop.prevent="onDrop"
      @click="pick"
    >
      <div class="flex items-center gap-2.5">
        <div class="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-gray-200 bg-white text-primary">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
            <circle cx="8.5" cy="9.5" r="1.6" />
            <path d="M4 17l5-4.5 4 3.5 3-2.5 4 3.5" />
          </svg>
        </div>
        <div class="min-w-0">
          <p class="text-[12.5px] font-semibold leading-tight text-gray-700">拖拽图片到此处，或 <span class="text-primary">点击选择</span></p>
          <p class="mt-0.5 text-[10.5px] leading-snug text-gray-400">
            PNG / JPG / WEBP · 单张 ≤ 10MB · 最多 20 张 · 解析成 md 后确认入库
          </p>
        </div>
      </div>
      <input
        ref="inputRef"
        type="file"
        multiple
        class="hidden"
        accept=".png,.jpg,.jpeg,.webp"
        @change="onPick"
      />
    </div>

    <!-- 队列 -->
    <div v-if="items.length" class="space-y-2">
      <div
        v-for="item in items"
        :key="item.id"
        class="flex items-center gap-2.5 rounded-lg border px-3 py-2 transition-colors"
        :class="item.active ? 'border-primary/60 bg-blue-50/40' : 'border-gray-100'"
        @click="selectItem(item)"
      >
        <span class="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-blue-50 text-[9px] font-bold text-blue-500">
          {{ extLabel(item.name) }}
        </span>
        <div class="min-w-0 flex-1">
          <p class="truncate text-[12px] font-medium text-gray-800">{{ item.name }}</p>
          <p class="mt-0.5 truncate text-[10.5px] text-gray-400">{{ subline(item) }}</p>
          <div v-if="item.status === 'parsing'" class="mt-1 h-[3px] overflow-hidden rounded-full bg-gray-100">
            <div class="h-full rounded-full bg-primary transition-all duration-300" :style="{ width: item.progress + '%' }" />
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <span class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold" :class="badgeClass(item.status)">
            {{ badgeText(item.status) }}
          </span>
          <button
            v-if="item.status === 'failed'"
            type="button"
            class="text-[10.5px] text-gray-400 underline decoration-dotted hover:text-primary"
            @click.stop="parseOne(item)"
          >
            重试
          </button>
          <button
            v-if="item.status === 'ready'"
            type="button"
            class="text-[10.5px] text-gray-400 hover:text-primary"
            title="从队列移除（原图保留在附件目录）"
            @click.stop="removeItem(item)"
          >
            ✕
          </button>
        </div>
      </div>
    </div>

    <!-- 操作条 -->
    <div v-if="items.length" class="flex flex-wrap items-center gap-2 border-t border-gray-100 pt-3">
      <button
        type="button"
        class="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[12px] font-semibold text-white transition-colors hover:bg-primary-hover disabled:opacity-50"
        :disabled="busy || !pendingCount"
        @click="runQueue"
      >
        开始解析{{ pendingCount ? `（${pendingCount}）` : '' }}
      </button>
      <button
        type="button"
        class="rounded-lg border border-gray-200 px-3 py-1.5 text-[12px] font-medium text-gray-600 transition-colors hover:bg-gray-50 disabled:opacity-50"
        :disabled="busy || !readyCount"
        @click="commitAll"
      >
        确认入库{{ readyCount ? `（${readyCount}）` : '' }}
      </button>
      <span class="text-[11px] text-gray-400">
        原图存平台附件，RAGFlow 只收 .md
      </span>
    </div>

    <!-- Markdown 预览 / 编辑 -->
    <div v-if="activeItem" class="rounded-xl border border-gray-200">
      <div class="flex flex-wrap items-center gap-2 border-b border-gray-100 px-3 py-2">
        <p class="min-w-0 flex-1 truncate text-[12px] font-semibold text-gray-700">
          {{ activeItem.mdFilename || activeItem.name }}
        </p>
        <span v-if="activeItem.model" class="text-[10.5px] text-gray-400">{{ activeItem.model }}</span>
        <span v-if="activeItem.costMs" class="text-[10.5px] text-gray-400">{{ (activeItem.costMs / 1000).toFixed(1) }}s</span>
        <span v-if="activeItem.charCount" class="text-[10.5px] text-gray-400">{{ activeItem.charCount }} 字</span>
        <button
          v-if="activeItem.status === 'ready'"
          type="button"
          class="rounded px-2 py-0.5 text-[11px] font-medium text-primary hover:bg-primary/5"
          @click="editing = !editing"
        >
          {{ editing ? '完成' : '编辑' }}
        </button>
      </div>
      <div class="p-3">
        <p v-if="activeItem.error" class="text-[11.5px] leading-relaxed text-red-600">
          {{ activeItem.error }}
        </p>
        <textarea
          v-else-if="editing"
          v-model="activeItem.markdown"
          class="h-[320px] w-full resize-y rounded-lg border border-primary/40 p-2.5 font-mono text-[11.5px] leading-relaxed text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
        <pre
          v-else-if="activeItem.markdown"
          class="max-h-[320px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-gray-50/80 p-2.5 font-mono text-[11.5px] leading-relaxed text-gray-600"
        >{{ activeItem.markdown }}</pre>
        <p v-else class="text-[11.5px] text-gray-400">解析中，稍后这里会显示模型生成的 Markdown。</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import axios from 'axios'

/**
 * 图片 → Markdown 上传器。
 *
 * RAGFlow 的图片解析要视觉模型配合，当前部署没打通；所以图片改走「平台自己读成 md，
 * 再把 md 当普通文档入库」这条路，检索侧完全不用改。
 *
 * 两个刻意的取舍：
 * - **解析与入库分两步**：md 一入库就会被检索召回，模型编造的数字没人能分辨，
 *   所以停在「待确认」等人看一眼，确认后才上传。
 * - **原图不上传 RAGFlow**：会占文档配额，且解析失败会在列表留红点。原图只落平台
 *   附件目录，用于模型读错时回看核对；md 与原图共用内容哈希尾巴，靠命名对应。
 */

const props = defineProps<{
  datasetId: string
  canUpload: boolean
}>()

const emit = defineEmits<{
  (e: 'uploaded'): void
  (e: 'notify', payload: { message: string; type: 'success' | 'error' | 'warning' }): void
}>()

const MAX_MB = 10
const MAX_FILES = 20
/** 并发 2：模型侧限流通常按 QPS 算，串行太慢、并发太多容易被上游掐断 */
const CONCURRENCY = 2

type ItemStatus = 'pending' | 'parsing' | 'ready' | 'failed'

interface ImageItem {
  id: string
  name: string
  size: number
  file: File
  status: ItemStatus
  progress: number
  markdown: string
  mdFilename: string
  model: string
  costMs: number
  charCount: number
  error: string
  active: boolean
}

const items = ref<ImageItem[]>([])
const visionState = ref<'checking' | 'ready' | 'missing'>('checking')
const dragOver = ref(false)
const busy = ref(false)
const editing = ref(false)
const inputRef = ref<HTMLInputElement | null>(null)

const pendingCount = computed(() => items.value.filter((i) => i.status === 'pending').length)
const readyCount = computed(() => items.value.filter((i) => i.status === 'ready').length)
const activeItem = computed(() => items.value.find((i) => i.active) || null)

const extLabel = (name: string) => (name.split('.').pop() || 'img').toUpperCase().slice(0, 4)

const subline = (item: ImageItem) => {
  const size = item.size >= 1024 * 1024
    ? `${(item.size / 1024 / 1024).toFixed(1)} MB`
    : `${Math.round(item.size / 1024)} KB`
  if (item.status === 'parsing') return `${size} · 调用多模态模型…`
  if (item.status === 'ready') return `${size} · ${item.costMs ? (item.costMs / 1000).toFixed(1) + 's' : ''} · 待确认`
  if (item.status === 'failed') return `${size} · ${item.error || '解析失败'}`
  return `${size} · 待解析`
}

const badgeText = (status: ItemStatus) =>
  ({ pending: '待解析', parsing: '解析中', ready: '已生成 md', failed: '解析失败' }[status])

const badgeClass = (status: ItemStatus) =>
  ({
    pending: 'bg-gray-100 text-gray-500',
    parsing: 'bg-blue-50 text-blue-600',
    ready: 'bg-emerald-50 text-emerald-600',
    failed: 'bg-red-50 text-red-600',
  }[status])

const toast = (message: string, type: 'success' | 'error' | 'warning' = 'success') => {
  // 提示条由宿主页面统一渲染：组件里再放一个 toast 容器会和页面级的重复
  emit('notify', { message, type })
}

/** 长边压到 1600px：模型按固定分辨率缩放，超大图只多花 token 不多读字 */
const compress = (file: File): Promise<File> =>
  new Promise((resolve) => {
    if (!file.type.startsWith('image/')) return resolve(file)
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      const longEdge = Math.max(img.width, img.height)
      if (longEdge <= 1600) {
        URL.revokeObjectURL(url)
        return resolve(file)
      }
      const scale = 1600 / longEdge
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(img.width * scale)
      canvas.height = Math.round(img.height * scale)
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        URL.revokeObjectURL(url)
        return resolve(file)
      }
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      canvas.toBlob((blob) => {
        URL.revokeObjectURL(url)
        if (!blob) return resolve(file)
        resolve(new File([blob], file.name.replace(/\.(png|jpe?g|webp)$/i, '.jpg'), { type: 'image/jpeg' }))
      }, 'image/jpeg', 0.85)
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      resolve(file)
    }
    img.src = url
  })

const pick = () => {
  if (busy.value || !props.canUpload) return
  inputRef.value?.click()
}

const addFiles = async (files: File[]) => {
  const room = MAX_FILES - items.value.length
  const accepted = files
    .filter((f) => /\.(png|jpe?g|webp)$/i.test(f.name))
    .filter((f) => {
      if (f.size > MAX_MB * 1024 * 1024) {
        toast(`${f.name} 超过 ${MAX_MB}MB，已跳过`, 'warning')
        return false
      }
      return true
    })
    .slice(0, Math.max(0, room))

  if (files.length > accepted.length + Math.max(0, -room)) {
    // 被格式或大小挡掉的数量在上面各自提示，这里只补「超出上限」
    if (files.length > room && room >= 0) toast(`一次最多 ${MAX_FILES} 张，多余的已忽略`, 'warning')
  }
  if (!accepted.length) return

  const compressed = await Promise.all(accepted.map(compress))
  for (const file of compressed) {
    const item: ImageItem = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name: file.name,
      size: file.size,
      file,
      status: 'pending',
      progress: 0,
      markdown: '',
      mdFilename: '',
      model: '',
      costMs: 0,
      charCount: 0,
      error: '',
      active: false,
    }
    items.value.push(item)
    if (!items.value.some((i) => i.active)) item.active = true
  }
}

const onPick = async (e: Event) => {
  const input = e.target as HTMLInputElement
  if (input.files?.length) await addFiles(Array.from(input.files))
  input.value = ''
}

const onDrop = async (e: DragEvent) => {
  dragOver.value = false
  const dropped = Array.from(e.dataTransfer?.files || [])
  if (dropped.length) await addFiles(dropped)
}

const selectItem = (item: ImageItem) => {
  items.value.forEach((i) => (i.active = i === item))
  editing.value = false
}

const removeItem = (item: ImageItem) => {
  items.value = items.value.filter((i) => i !== item)
  if (!items.value.some((i) => i.active) && items.value.length) items.value[0]!.active = true
}

const parseOne = async (item: ImageItem) => {
  item.status = 'parsing'
  item.progress = 8
  item.error = ''
  // 解析没有真实进度：用一个缓动的假进度条表达「在动」，到 90% 停住等结果
  const timer = window.setInterval(() => {
    item.progress = Math.min(90, item.progress + 6)
  }, 500)

  try {
    const payload = new FormData()
    payload.append('file', item.file, item.name)
    const response = await axios.post(
      `/api/portal/ragflow/datasets/${props.datasetId}/images/parse`,
      payload,
    )
    const data = response.data?.data || {}
    item.markdown = data.markdown || ''
    item.mdFilename = data.md_filename || ''
    item.model = data.model || ''
    item.costMs = data.cost_ms || 0
    item.charCount = data.char_count || 0
    item.status = item.markdown ? 'ready' : 'failed'
    if (!item.markdown) item.error = '模型返回空内容'
    item.progress = 100
    item.active = true
  } catch (err: any) {
    item.status = 'failed'
    item.error = err?.response?.data?.detail || err?.message || '解析失败'
  } finally {
    window.clearInterval(timer)
  }
}

const runQueue = async () => {
  const queue = items.value.filter((i) => i.status === 'pending')
  if (!queue.length) return
  busy.value = true
  let cursor = 0
  const worker = async () => {
    while (cursor < queue.length) {
      const item = queue[cursor++]
      if (!item) return
      await parseOne(item)
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker))
  busy.value = false
  const failed = queue.filter((i) => i.status === 'failed').length
  if (failed) toast(`${queue.length - failed} 张已生成 md，${failed} 张失败可重试`, 'warning')
  else toast(`${queue.length} 张图已生成 md，确认后才入库`)
}

const commitAll = async () => {
  const ready = items.value.filter((i) => i.status === 'ready')
  if (!ready.length) return
  busy.value = true
  let ok = 0
  let fail = 0
  for (const item of ready) {
    try {
      await axios.post(
        `/api/portal/ragflow/datasets/${props.datasetId}/images/commit`,
        { md_filename: item.mdFilename, markdown: item.markdown },
      )
      ok += 1
      items.value = items.value.filter((i) => i !== item)
    } catch (err: any) {
      fail += 1
      item.error = err?.response?.data?.detail || err?.message || '入库失败'
      item.status = 'failed'
    }
  }
  busy.value = false
  if (ok) {
    emit('uploaded')
    toast(`${ok} 份 md 已入库`)
  }
  if (fail) toast(`${fail} 份入库失败`, 'error')
  if (items.value.length && !items.value.some((i) => i.active)) items.value[0]!.active = true
}

onMounted(async () => {
  try {
    const response = await axios.get('/api/portal/ragflow/knowledge-images/vision-model')
    visionState.value = response.data?.data?.ready ? 'ready' : 'missing'
  } catch {
    // 探测失败按「不可用」处理：宁可不放行，也不要用户传完图才报错
    visionState.value = 'missing'
  }
})
</script>
