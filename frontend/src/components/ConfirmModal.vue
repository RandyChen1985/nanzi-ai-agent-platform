<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

interface Props {
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  type?: 'danger' | 'primary' | 'warning'
}

withDefaults(defineProps<Props>(), {
  confirmText: '确认',
  cancelText: '取消',
  type: 'danger'
})

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()

const confirmButtonRef = ref<HTMLButtonElement | null>(null)

const handleKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Enter') {
    e.preventDefault()
    e.stopPropagation()
    emit('confirm')
  } else if (e.key === 'Escape') {
    e.preventDefault()
    e.stopPropagation()
    emit('cancel')
  }
}

onMounted(() => {
  confirmButtonRef.value?.focus()
  document.addEventListener('keydown', handleKeydown, true)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown, true)
})
</script>

<template>
  <div
    class="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
    role="dialog"
    aria-modal="true"
    @click.self="$emit('cancel')"
  >
    <div class="bg-white rounded-xl shadow-2xl max-w-sm w-full overflow-hidden scale-100 transition-transform duration-200">
      <div class="p-6 text-center">
        <div class="mx-auto flex items-center justify-center h-12 w-12 rounded-full mb-4" :class="{
          'bg-red-100 text-red-600': type === 'danger',
          'bg-blue-100 text-blue-600': type === 'primary',
          'bg-yellow-100 text-yellow-600': type === 'warning'
        }">
          <svg v-if="type === 'danger'" class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
          <svg v-else-if="type === 'warning'" class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
          <svg v-else class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
        </div>
        <h3 class="text-lg leading-6 font-medium text-gray-900">{{ title }}</h3>
        <p class="text-sm text-gray-500 mt-2">{{ message }}</p>
      </div>
      <div class="bg-gray-50 px-4 py-3 sm:px-6 sm:flex sm:flex-row-reverse">
        <button
          ref="confirmButtonRef"
          @click="$emit('confirm')"
          type="button"
          class="w-full inline-flex justify-center rounded-md border border-transparent shadow-sm px-4 py-2 text-base font-medium text-white sm:ml-3 sm:w-auto sm:text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2"
          :class="{
            'bg-red-600 hover:bg-red-700 focus:ring-red-500': type === 'danger',
            'bg-primary hover:bg-primary-dark focus:ring-primary': type === 'primary',
            'bg-yellow-600 hover:bg-yellow-700 focus:ring-yellow-500': type === 'warning'
          }"
        >
          {{ confirmText }}
        </button>
        <button 
          @click="$emit('cancel')" 
          type="button" 
          class="mt-3 w-full inline-flex justify-center rounded-md border border-gray-300 shadow-sm px-4 py-2 bg-white text-base font-medium text-gray-700 hover:bg-gray-50 sm:mt-0 sm:ml-3 sm:w-auto sm:text-sm focus:outline-none"
        >
          {{ cancelText }}
        </button>
      </div>
    </div>
  </div>
</template>
