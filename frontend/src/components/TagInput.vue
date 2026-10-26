<template>
  <div
    class="flex min-h-[38px] w-full cursor-text flex-wrap items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-2.5 py-1.5 text-sm transition-all focus-within:border-primary focus-within:ring-1 focus-within:ring-primary"
    @click="focusInput"
  >
    <!-- 已选标签：每个都能单独点掉，不用回头改字符串 -->
    <span
      v-for="(tag, index) in modelValue"
      :key="`${tag}-${index}`"
      class="inline-flex max-w-full items-center gap-1 rounded-lg border border-blue-100 bg-blue-50/70 px-2 py-0.5 text-xs font-medium text-blue-700"
    >
      <span class="truncate">{{ tag }}</span>
      <button
        type="button"
        class="shrink-0 text-blue-400 transition-colors hover:text-blue-700"
        :aria-label="`移除标签 ${tag}`"
        @click.stop="removeTag(index)"
      >
        <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </span>

    <input
      ref="inputRef"
      v-model="draft"
      type="text"
      class="min-w-[7rem] flex-1 border-0 bg-transparent p-0 text-sm placeholder-gray-400 focus:outline-none focus:ring-0"
      :placeholder="modelValue.length ? '' : placeholder"
      @keydown="onKeydown"
      @blur="commitDraft"
      @paste="onPaste"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

/**
 * 标签输入：回车或逗号成块，点 × 移除。
 *
 * 替换原来的「英文逗号分隔的文本框」：那个方案要求使用者记住两点——
 * 必须用**英文**逗号、必须自己数分隔符；用中文逗号敲进去会变成一个整标签，
 * 保存后才发现错了。这里中文逗号、英文逗号、粘贴一长串都能正确拆开。
 *
 * 两个容易踩的坑：
 * 1. 在 <form> 里按回车默认提交表单，必须 preventDefault，否则"按回车加标签"
 *    会直接把弹窗提交掉；
 * 2. 输入框为空时按退格删除最后一个标签，符合常见的 tag input 习惯。
 */
const props = withDefaults(
  defineProps<{
    modelValue: string[]
    placeholder?: string
  }>(),
  { placeholder: '输入后按回车添加' },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: string[]): void }>()

const inputRef = ref<HTMLInputElement | null>(null)
const draft = ref('')

/** 中英文逗号、换行都算分隔符 */
const splitTags = (text: string) =>
  text
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)

const focusInput = () => inputRef.value?.focus()

const addTags = (text: string) => {
  const incoming = splitTags(text)
  if (!incoming.length) return
  const next = [...props.modelValue]
  for (const tag of incoming) {
    if (!next.includes(tag)) next.push(tag)
  }
  if (next.length !== props.modelValue.length) {
    emit('update:modelValue', next)
  }
}

const commitDraft = () => {
  if (!draft.value.trim()) {
    draft.value = ''
    return
  }
  addTags(draft.value)
  draft.value = ''
}

const removeTag = (index: number) => {
  const next = [...props.modelValue]
  next.splice(index, 1)
  emit('update:modelValue', next)
}

const onKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Enter') {
    // 必须拦掉：否则在表单里按回车会提交整个弹窗
    event.preventDefault()
    commitDraft()
    return
  }
  if (event.key === ',' || event.key === '，') {
    event.preventDefault()
    commitDraft()
    return
  }
  if (event.key === 'Backspace' && !draft.value && props.modelValue.length) {
    event.preventDefault()
    removeTag(props.modelValue.length - 1)
  }
}

const onPaste = (event: ClipboardEvent) => {
  const text = event.clipboardData?.getData('text') || ''
  // 单个词走浏览器默认粘贴；带分隔符的整串直接拆成多个标签
  if (!/[,，\n]/.test(text)) return
  event.preventDefault()
  addTags(text)
  draft.value = ''
}
</script>
