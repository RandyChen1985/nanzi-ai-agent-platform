<template>
  <div class="fixed top-6 left-1/2 -translate-x-1/2 z-[100000] flex flex-col items-center gap-2.5 pointer-events-none w-full max-w-md px-4">
    <TransitionGroup
      enter-active-class="transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
      enter-from-class="transform -translate-y-4 opacity-0 scale-95"
      enter-to-class="transform translate-y-0 opacity-100 scale-100"
      leave-active-class="transition-all duration-200 ease-in pointer-events-none"
      leave-from-class="transform translate-y-0 opacity-100 scale-100"
      leave-to-class="transform -translate-y-2 opacity-0 scale-95"
      move-class="transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
    >
      <Toast
        v-for="toast in toasts"
        :key="toast.id"
        :message="toast.message"
        :title="toast.title"
        :description="toast.description"
        :type="toast.type"
        :duration="toast.duration"
        :action="toast.action"
        inline
        @close="removeToast(toast.id)"
      />
    </TransitionGroup>
  </div>
</template>

<script setup lang="ts">
import { useToast } from '../composables/useToast'
import Toast from './Toast.vue'

const { toasts, removeToast } = useToast()
</script>
