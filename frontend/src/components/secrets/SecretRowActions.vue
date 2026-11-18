<script setup lang="ts">
/**
 * 凭据的「更多操作」菜单，卡片视图与列表视图共用。
 *
 * 早期只有列表用它：卡片横向空间够，平铺一步点到更快。改成三列之后这条前提没了
 * ——每列只有 ~440px，七八个文字按钮会让「名称」和「摘要」各折一行，而那两个
 * 才是扫卡片时要看的东西。现在两边都走这里，动作清单也就只有一份，不会各长各的。
 *
 * 七个动作里只有「删除」是不可逆的（`delete` 会连授权与绑定一起清掉），
 * 所以它单独用玫瑰色、并放在菜单最末——误点一行的其他操作都能改回来。
 *
 * 定位照 `SavedReportItemCard` 的做法：菜单进 `Teleport`，按触发按钮的位置
 * 算 fixed 坐标，下方放不下就翻到上方。**不能**用 `absolute` 挂在行里——
 * 表格容器有 `overflow-x-auto`，绝对定位的菜单会被裁掉。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{
  /** 停用的凭据没有「强制停用」这一项，换成「恢复启用」 */
  active: boolean
  /** 无障碍标签用的凭据名 */
  name: string
}>()

const emit = defineEmits<{
  (e: 'usage'): void
  (e: 'audit'): void
  (e: 'edit'): void
  (e: 'rotate'): void
  (e: 'grant'): void
  (e: 'disable'): void
  (e: 'enable'): void
  (e: 'delete'): void
}>()

type Action = 'usage' | 'audit' | 'edit' | 'rotate' | 'grant' | 'disable' | 'enable' | 'delete'

const open = ref(false)
const menuStyle = ref<Record<string, string>>({})
const menuRef = ref<HTMLElement | null>(null)
const triggerRef = ref<HTMLElement | null>(null)

const MENU_WIDTH = 160
//: 六到七项（使用记录 / 变更记录 / 编辑 / 轮换 / 授权 / 停用或恢复 / 删除）约 262px。
//: 只用于展开第一帧的落点，`toggle()` 随后按实测高度再摆一次。
const ESTIMATED_HEIGHT = 262
const GAP = 6

function place(rect: DOMRect | undefined, height: number) {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const anchorTop = rect?.top ?? GAP
  const anchorBottom = rect?.bottom ?? GAP
  const anchorRight = rect?.right ?? vw - GAP

  // 默认向下展开，下方放不下且上方够时才翻转。参照实现（SavedReportItemCard）
  // 是「能向上就向上」，那是给网格卡片的——卡片高度大、菜单 260px，向上更稳；
  // 表格行只有几十像素高，照搬的话连第一行都会往上弹，正好盖住表头和筛选栏。
  const spaceBelow = vh - anchorBottom
  const top =
    spaceBelow < height + GAP && anchorTop >= height + GAP
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

function fire(action: Action) {
  open.value = false
  // 逐个分发而不是 `emit(action)`：defineEmits 生成的是重载签名，
  // 传联合类型过不了类型检查（每个重载都要求精确的参数类型）。
  if (action === 'usage') emit('usage')
  else if (action === 'audit') emit('audit')
  else if (action === 'edit') emit('edit')
  else if (action === 'rotate') emit('rotate')
  else if (action === 'grant') emit('grant')
  else if (action === 'disable') emit('disable')
  else if (action === 'enable') emit('enable')
  else emit('delete')
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

/**
 * 页面滚动/视口变化时重新贴合按钮。
 *
 * 菜单是 fixed 定位，表格又是整页滚动（容器只有 overflow-x-auto），不跟随的话
 * 滑轮一滚菜单就停在原地、和刚点的按钮错位。按钮被滚出视口时直接收起，
 * 免得留一个飘在边上的菜单。
 */
function reposition() {
  const rect = triggerRef.value?.getBoundingClientRect()
  if (!rect || rect.bottom < 0 || rect.top > window.innerHeight) {
    open.value = false
    return
  }
  place(rect, menuRef.value?.getBoundingClientRect().height || ESTIMATED_HEIGHT)
}

function bindWindowListeners(bound: boolean) {
  if (bound) {
    window.addEventListener('keydown', onKeydown)
    // capture：表格若换成内层滚动容器，滚动事件不会冒泡到 window
    window.addEventListener('scroll', reposition, true)
    window.addEventListener('resize', reposition)
    return
  }
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('scroll', reposition, true)
  window.removeEventListener('resize', reposition)
}

watch(open, (value) => bindWindowListeners(value))

onBeforeUnmount(() => bindWindowListeners(false))

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
        <button type="button" :class="itemClass" role="menuitem" @click="fire('audit')">变更记录</button>
        <button type="button" :class="itemClass" role="menuitem" @click="fire('edit')">编辑</button>
        <button type="button" :class="itemClass" role="menuitem" @click="fire('rotate')">轮换</button>
        <button type="button" :class="itemClass" role="menuitem" @click="fire('grant')">授权</button>
        <button
          v-if="active"
          type="button"
          :class="itemClass"
          role="menuitem"
          @click="fire('disable')"
        >
          强制停用
        </button>
        <button
          v-else
          type="button"
          :class="itemClass"
          role="menuitem"
          @click="fire('enable')"
        >
          恢复启用
        </button>
        <!-- 不可逆，永远排在最后：上面的都能反悔，这个不能 -->
        <button
          type="button"
          class="mt-1 block w-full rounded-md border-t border-gray-100 px-2.5 py-1.5 pt-2 text-left text-xs text-rose-600 hover:bg-rose-50"
          role="menuitem"
          @click="fire('delete')"
        >
          删除
        </button>
      </div>
    </div>
  </Teleport>
</template>
