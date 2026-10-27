<template>
  <div class="embed-layout w-screen overflow-hidden bg-transparent">
    <router-view />
  </div>
</template>

<script setup lang="ts">
// Embed Layout - Minimalist full screen container
// Used for /embed/chat route
</script>

<style scoped>
.embed-layout {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  /* 刻意不用 bottom: 0：那样高度恒等于「大视口」，地址栏展开或软键盘弹出时底部输入区
     会被压住。改由 --app-height（visualViewport 实测，见 useAppViewportHeight）定高，
     拿不到时退回 100dvh；两条都不认的老浏览器保留 100vh。 */
  height: 100vh;
  height: var(--app-height, 100dvh);
  z-index: 0; /* Let router-view content handle z-index if needed */
}
</style>
