<template>
  <!-- 「新建智能体」按钮组：主按钮 + 更多创建方式下拉。
       抽成组件是因为窄屏要把它从页头挪到搜索行末尾，与「搜索框 + 筛选」同行——
       同一套按钮写两份迟早改一处漏一处，所以只留这一份实现。 -->
  <div v-has-perm="'element:agent:create'" class="relative" @click.stop>
    <div class="flex items-stretch overflow-hidden rounded-lg shadow-sm">
      <button
        type="button"
        class="flex items-center gap-1.5 whitespace-nowrap bg-primary px-3.5 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-dark cursor-pointer"
        @click="emit('create')"
      >
        <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
        </svg>
        新建<span class="hidden sm:inline">智能体</span>
      </button>
      <button
        type="button"
        class="border-l border-white/25 bg-primary px-2 text-white transition-colors hover:bg-primary-dark cursor-pointer"
        title="更多创建方式"
        :aria-expanded="menuOpen"
        @click="menuOpen = !menuOpen"
      >
        <svg class="h-4 w-4 transition-transform" :class="{ 'rotate-180': menuOpen }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
    </div>
    <div
      v-if="menuOpen"
      class="absolute right-0 top-full z-40 mt-1 w-52 overflow-hidden rounded-lg border border-gray-100 bg-white py-1 shadow-xl"
    >
      <button
        type="button"
        class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
        @click="closeMenu(); emit('create')"
      >
        <svg class="h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
        </svg>
        空白新建
      </button>
      <button
        type="button"
        class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
        @click="closeMenu(); emit('template')"
      >
        <svg class="h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7h16M4 12h10M4 17h7" />
        </svg>
        从场景模板交付
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  (e: 'create'): void
  (e: 'template'): void
}>()

/** 下拉展开态由组件自己持有：页头与窄屏搜索行各渲染一份，共享状态会互相牵连。 */
const menuOpen = ref(false)

const closeMenu = () => {
  menuOpen.value = false
}
</script>
