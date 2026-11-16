<script setup lang="ts">
import ToastContainer from './components/ToastContainer.vue'
import ConfirmHost from './components/common/ConfirmHost.vue'
import { onMounted } from 'vue'
import { loadBranding } from './composables/useBranding'
import { initializeTheme } from './composables/useAppTheme'
import { useAppViewportHeight } from './composables/useAppViewportHeight'

// 把 visualViewport 的实测高度写进 --app-height，供定高布局消费
// （地址栏伸缩 / 软键盘弹出时 100vh 不会变，底部输入框会被压住）
useAppViewportHeight()

onMounted(() => {
  loadBranding()
  initializeTheme()
})
</script>

<template>
  <ToastContainer />
  <!-- 全局确认框：替代 window.confirm，业务组件用 useConfirm() 唤起 -->
  <ConfirmHost />
  <router-view></router-view>
</template>

<style>
</style>
