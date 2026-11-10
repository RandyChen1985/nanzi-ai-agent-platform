<script setup lang="ts">
/**
 * 凭据行的「更多操作」菜单。
 *
 * 卡片视图把四个动作平铺是合适的——横向空间够，一步点到。列表视图不行：
 * 一行里塞四个文字按钮会把「名称」和「使用情况」挤到看不见，而这两列才
 * 是扫列表时真正要看的东西。
 *
 * 定位照 `SavedReportItemCard` 的做法：菜单进 `Teleport`，按触发按钮的位置
 * 算 fixed 坐标，下方放不下就翻到上方。**不能**用 `absolute` 挂在行里——
 * 表格容器有 `overflow-x-auto`，绝对定位的菜单会被裁掉。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{
  /** 停用的凭据没有「强制停用」这一项 */
  active: boolean
  /** 无障碍标签用的凭据名 */
  name: string
}>()

const emit = defineEmits<{
  (e: 'usage'): void
  (e: 'edit'): void
  (e: 'rotate'): void
  (e: 'grant'): void
  (e: 'disable'): void
}>()

const open = ref(false)
const menuStyle = ref<Record<string, string>>({})
const menuRef = ref<HTMLElement | null>(null)
const triggerRef = ref<HTMLElement | null>(null)

const MENU_WIDTH = 160
const ESTIMATED_HEIGHT = 190
const GAP = 6

function place(rect: DOMRect | undefined, height: number) {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const anchorTop = rect?.top ?? GAP
  const anchorBottom = rect?.bottom ?? GAP
  const anchorRight = rect?.right ?? vw - GAP

  // 优先向上展开：列表的最后一行贴着视口底部，向下必然要翻
  const top =
    anchorTop >= height + GAP
      ? anchorTop - height - GAP
      : Math.min(vh - height - GAP, anchorBottom + GAP)
  const left = Math.max(GAP, Math.min(anchorRight - MENU_WIDTH, vw - MENU_WIDTH - GAP))

  menuStyle.value = {
    position: 'fixed',
    top: `${Math.max(GAP, top)}px`,
    left: `${left}px`,
    width: `${MENU_WIDTH}px`,
  }
}

async function toggle() {
  if (open.value) {
    open.value = false
    return
  }
  place(triggerRef.value?.getBoundingClientRect(), ESTIMATED_HEIGHT)
  open.value = true
  await nextTick()
  // 估算高度不准（比如停用的凭据少一项）时按实测再摆一次
  const rect = triggerRef.value?.getBoundingClientRect()
  const actual = menuRef.value?.getBoundingClientRect().height || ESTIMATED_HEIGHT
  place(rect, actual)
}

function fire(action: 'usage' | 'edit' | 'rotate' | 'grant' | 'disable') {
  open.value = false
  // 逐个分发而不是 `emit(action)`：defineEmits 生成的是重载签名，
  // 传联合类型过不了类型检查（每个重载都要求精确的参数类型）。
  if (action === 'usage') emit('usage')
  else if (action === 'edit') emit('edit')
  else if (action === 'rotate') emit('rotate')
  else if (action === 'grant') emit('grant')
  else emit('disable')
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

watch(open, (value) => {
  if (value) window.addEventListener('keydown', onKeydown)
  else window.removeEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

const label = computed(() => `更多操作：${props.name}`)

const itemClass =
  'block w-full rounded-md px-2.5 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-100 disabled:opacity-40'
</script>

<template>
  <button
    ref="triggerRef"
    type="button"
    class="rounded-md px-2 py-1 text-sm leading-none text-gray-500 hover:bg-gray-100 hover:text-gray-800"
    :aria-expanded="open"
    aria-haspopup="menu"
    :aria-label="label"
    :title="label"
    @click.stop="toggle"
  >
    ⋯
  </button>

  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-[320]" role="presentation" @click="open = false">
      <div ref="menuRef" class="rounded-xl border border-gray-200 bg-white p-1 shadow-2xl" :style="menuStyle" role="menu" @click.stop>
        <button type="button" :class="itemClass" role="menuitem" @click="fire('usage')">使用记录</button>
        <button type="button" :class="itemClass" role="menuitem" @click="fire('edit')">编辑</button>
        <button type="button" :class="itemClass" role="menuitem" @click="fire('rotate')">轮换</button>
        <button type="button" :class="itemClass" role="menuitem" @click="fire('grant')">授权</button>
        <button
          v-if="active"
          type="button"
          class="block w-full rounded-md px-2.5 py-1.5 text-left text-xs text-rose-600 hover:bg-rose-50"
          role="menuitem"
          @click="fire('disable')"
        >
          强制停用
        </button>
      </div>
    </div>
  </Teleport>
</template>
