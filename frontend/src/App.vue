<script setup lang="ts">
import ToastContainer from './components/ToastContainer.vue'
import { onMounted } from 'vue'
import { loadBranding } from './composables/useBranding'

onMounted(() => {
  loadBranding()
})
</script>

<template>
  <ToastContainer />
  <router-view></router-view>
</template>

<style>
</style>
