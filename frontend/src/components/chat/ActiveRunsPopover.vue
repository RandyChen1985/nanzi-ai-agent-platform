<template>
  <Teleport to="body" :disabled="variant !== 'modal'">
    <div
      v-if="open && variant === 'modal'"
      class="fixed inset-0 z-40 bg-black/20"
      @click="emit('close')"
    ></div>
    <div
      v-if="open"
      class="z-50"
      :class="[
        align === 'left' ? 'origin-top-left' : 'origin-top-right',
        variant === 'modal'
          ? 'fixed left-1/2 top-16 w-[min(22rem,calc(100vw-2rem))] -translate-x-1/2'
          : `absolute top-full mt-2 w-80 ${align === 'left' ? 'left-0' : 'right-0'}`,
      ]"
    >
      <div class="rounded-xl border border-gray-200 bg-white shadow-xl dark:border-gray-700 dark:bg-gray-800">
        <div class="flex items-center justify-between border-b border-gray-100 px-3 py-2 dark:border-gray-700">
          <span class="text-[12px] font-semibold text-gray-700 dark:text-gray-200">
            {{ items.length }} 个进行中的会话
          </span>
          <div class="flex items-center gap-1">
            <button
              type="button"
              class="rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-primary disabled:opacity-50 dark:hover:bg-gray-700"
              :disabled="loading"
              title="刷新列表"
              @click="emit('refresh')"
            >
              <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v6h6M20 20v-6h-6M20 9A8 8 0 006.6 5.6M4 15a8 8 0 0013.4 3.4" />
              </svg>
            </button>
            <button
              type="button"
              class="rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-primary dark:hover:bg-gray-700"
              title="关闭"
              @click="emit('close')"
            >
              <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <p
          v-if="failed"
          class="border-b border-amber-100 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-700 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300"
        >
          刷新失败，正在重试（显示的是上一次结果）
        </p>

        <div v-if="!items.length" class="px-3 py-6 text-center">
          <p class="text-[12px] text-gray-400">当前没有进行中的会话</p>
          <!-- 空状态要给一条出路：用户点这个入口多半是在找某个会话，进行中的没有，去处就只剩
               会话历史，而界面上此前没有任何指向它的引导。 -->
          <p class="mt-1 text-[11px] text-gray-400/80">已结束的会话都在会话历史里</p>
          <button
            type="button"
            class="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 py-1.5 text-[12px] text-gray-600 transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-primary dark:border-gray-700 dark:text-gray-300"
            @click="emit('open-history')"
          >
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 2M3.05 11a9 9 0 1 1 .5 4M3 4v5h5" />
            </svg>
            打开会话历史
          </button>
        </div>

        <template v-else>
          <div v-if="activeItems.length" class="py-1">
            <div class="px-3 py-1 text-[11px] font-medium text-gray-400">进行中 {{ activeItems.length }}</div>
            <div
              v-for="item in activeItems"
              :key="item.conversation_id"
              class="flex items-center gap-2 px-3 py-2 transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/40"
            >
              <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"></span>
              <button
                type="button"
                class="min-w-0 flex-1 text-left"
                :title="item.title"
                @click="emit('select', item.conversation_id)"
              >
                <span class="block truncate text-[12px] text-gray-700 dark:text-gray-200">{{ item.title }}</span>
                <span class="block truncate text-[10px] text-gray-400">
                  {{ ttlText(item) }}<template v-if="item.conversation_id === activeConversationId"> · 当前会话</template>
                </span>
              </button>
              <button
                type="button"
                class="shrink-0 rounded px-2 py-1 text-[11px] font-medium text-red-600 transition-colors hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
                @click.stop="emit('terminate', item)"
              >
                终止
              </button>
            </div>
          </div>

          <div v-if="staleItems.length" class="border-t border-gray-100 py-1 dark:border-gray-700">
            <div class="px-3 py-1 text-[11px] font-medium text-amber-500">疑似僵死 {{ staleItems.length }}</div>
            <div
              v-for="item in staleItems"
              :key="item.conversation_id"
              class="flex items-center gap-2 px-3 py-2 transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/40"
            >
              <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400"></span>
              <button
                type="button"
                class="min-w-0 flex-1 text-left"
                :title="item.title"
                @click="emit('select', item.conversation_id)"
              >
                <span class="block truncate text-[12px] text-gray-700 dark:text-gray-200">{{ item.title }}</span>
                <span class="block truncate text-[10px] text-amber-500">续约已停，{{ ttlText(item) }}</span>
              </button>
              <button
                type="button"
                class="shrink-0 rounded px-2 py-1 text-[11px] font-medium text-amber-600 transition-colors hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-950/40"
                @click.stop="emit('terminate', item)"
              >
                强制释放
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface ActiveRunView {
  conversation_id: string
  trace_id: string | null
  ttl_seconds: number | null
  state: 'active' | 'stale'
  title: string
}

const props = withDefaults(
  defineProps<{
    open: boolean
    items: ActiveRunView[]
    loading?: boolean
    failed?: boolean
    activeConversationId?: string
    /** anchored=桌面端锚定在按钮下方；modal=移动端居中卡片（带遮罩） */
    variant?: 'anchored' | 'modal'
    /** anchored 时浮层贴按钮哪一边：入口在标题区左侧时用 left，避免向视口外展开 */
    align?: 'left' | 'right'
  }>(),
  {
    loading: false,
    failed: false,
    activeConversationId: '',
    variant: 'anchored',
    align: 'right',
  }
)

const emit = defineEmits<{
  (e: 'select', conversationId: string): void
  (e: 'terminate', item: ActiveRunView): void
  (e: 'refresh'): void
  (e: 'close'): void
  (e: 'open-history'): void
}>()

const activeItems = computed(() => props.items.filter((item) => item.state !== 'stale'))
const staleItems = computed(() => props.items.filter((item) => item.state === 'stale'))

const ttlText = (item: ActiveRunView): string =>
  item.ttl_seconds === null ? '未设置过期时间' : `剩余 ${item.ttl_seconds}s`
</script>
