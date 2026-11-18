<template>
  <section>
    <!-- 整行是按钮：键盘 Enter/Space 原生可用，不必额外绑 keydown -->
    <button
      type="button"
      class="flex w-full items-center gap-2 border-b border-dashed border-gray-200 pb-1 text-left"
      :aria-expanded="modelValue"
      :aria-controls="sectionId"
      @click="emit('update:modelValue', !modelValue)"
    >
      <span class="text-[11px] font-black text-primary tracking-wide">{{ title }}</span>
      <span v-if="hint" class="text-[10px] text-gray-400">{{ hint }}</span>
      <span
        v-if="hasError"
        class="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-red-500 text-[9px] font-black leading-none text-white"
        aria-hidden="true"
      >!</span>
      <span class="ml-auto flex items-center gap-1 text-[10px] font-black text-gray-400">
        <span aria-hidden="true">{{ modelValue ? '▾' : '▸' }}</span>
        <span class="sr-only">{{ modelValue ? '收起本节' : '展开本节' }}</span>
      </span>
    </button>

    <!-- 收起态：有错优先说「需补全」，否则给一行摘要 -->
    <p
      v-if="!modelValue && (hasError || summary)"
      class="truncate text-[11px] leading-relaxed"
      :class="hasError ? 'font-bold text-red-600' : 'text-gray-500'"
      :title="hasError ? '有必填项未完成，展开后补全' : summary"
    >
      {{ hasError ? '需补全' : summary }}
    </p>

    <!-- pt-5 与原 space-y-5 的组内间距对齐，展开态观感不变 -->
    <div v-show="modelValue" :id="sectionId" class="space-y-5 pt-5">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
defineProps<{
  /** 内容区 id，供 aria-controls 与保存失败时的滚动定位共用 */
  sectionId: string
  title: string
  hint?: string
  /** 收起态的一行摘要 */
  summary?: string
  /** 组内是否有必须补全的项 */
  hasError?: boolean
  /** 是否展开（受控） */
  modelValue: boolean
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>
