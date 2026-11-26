<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useConfirm } from '../../composables/useConfirm'

/**
 * 全局确认框的渲染出口。在 App.vue 挂一次即可，业务组件用 `useConfirm()` 唤起。
 *
 * z-index 取到最高：确认框可能从任何一层的弹窗里弹出（参数设置 z-[120]、
 * 错误横幅 z-[130]、凭据编辑器 z-[120]），被压在下面就等于没弹。
 *
 * 无障碍：它是模态弹窗，所以要 `role="dialog"` + `aria-modal`；打开时把焦点
 * 移进来，否则焦点还留在背景页面上——Esc 收不到（keydown 只在有焦点的子树里
 * 冒泡）、Tab 也会跑到弹窗后面那些看不见的按钮上。
 */
const { state, settle } = useConfirm()

const confirmButtonRef = ref<HTMLButtonElement | null>(null)

watch(
  () => state.open,
  async (open) => {
    if (!open) return
    await nextTick()
    // 聚焦**确认键**而不是弹窗容器：键盘用户打开后直接回车即可，
    // 想反悔就 Tab 到取消或按 Esc。
    confirmButtonRef.value?.focus()
  },
)
</script>

<template>
  <Teleport to="body">
    <div
      v-if="state.open"
      class="fixed inset-0 z-[200] flex items-center justify-center bg-black/40 p-4"
      @click.self="settle(false)"
      @keydown.esc="settle(false)"
    >
      <div
        class="w-full max-w-md rounded-xl bg-white p-5 shadow-xl"
        role="dialog"
        aria-modal="true"
        :aria-label="state.title || '确认操作'"
      >
        <h3 v-if="state.title" class="text-sm font-semibold text-gray-900">{{ state.title }}</h3>
        <p class="mt-2 whitespace-pre-line text-sm leading-relaxed text-gray-600">{{ state.message }}</p>
        <div class="mt-5 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-lg border border-gray-200 bg-white px-3.5 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
            @click="settle(false)"
          >{{ state.cancelText }}</button>
          <button
            ref="confirmButtonRef"
            type="button"
            class="rounded-lg px-3.5 py-1.5 text-sm font-semibold text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
            :class="state.danger
              ? 'bg-rose-600 hover:bg-rose-700 focus-visible:ring-rose-400'
              : 'bg-primary hover:bg-primary-dark focus-visible:ring-primary'"
            @click="settle(true)"
          >{{ state.confirmText }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
