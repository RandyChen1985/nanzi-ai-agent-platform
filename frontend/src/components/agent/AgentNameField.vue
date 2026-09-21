<script setup lang="ts">
/**
 * 物理标识符输入控件（新建 / 编辑智能体共用）。
 *
 * 该字段全局唯一，因此除输入框外还必须承载「重名」的即时反馈：
 * 失焦时触发 `check` 由父组件发起预检，不可用时在下方显示内联错误。
 * 校验状态由父组件持有（`useAgentNameAvailability`），保证提交前的兜底校验
 * 与字段展示的是同一份结论。
 */
const props = defineProps<{
  modelValue?: string;
  /** 编辑已有智能体时标识符不可改 */
  disabled?: boolean;
  required?: boolean;
  /** 字段下方的常规说明 */
  hint?: string;
  /** 预检进行中 */
  checking?: boolean;
  /** 预检结论：不可用原因 */
  errorMessage?: string;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
  (e: 'check', value: string): void;
  (e: 'reset'): void;
}>();

const onInput = (event: Event) => {
  emit('update:modelValue', (event.target as HTMLInputElement).value);
  // 一旦修改就作废旧结论，避免「改了名字但还显示上一个名字的报错」
  emit('reset');
};

const onBlur = () => {
  if (props.disabled) return;
  emit('check', String(props.modelValue || '').trim());
};
</script>

<template>
  <div>
    <label class="block text-sm font-medium text-gray-700 mb-1">
      物理标识符
      <span v-if="required" class="text-red-500">*</span>
    </label>
    <input
      :value="modelValue"
      :disabled="disabled"
      placeholder="例如 sales-data-agent"
      class="w-full rounded-lg border px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30 disabled:bg-gray-50 disabled:text-gray-400"
      :class="errorMessage ? 'border-red-300 bg-red-50/40' : 'border-gray-300'"
      @input="onInput"
      @blur="onBlur"
    />
    <p v-if="errorMessage" class="mt-1 flex items-center gap-1 text-[10px] text-red-500">
      <span aria-hidden="true">⚠</span>
      <span>{{ errorMessage }}</span>
    </p>
    <p v-else-if="checking" class="mt-1 text-[10px] text-gray-400">正在检查标识符是否可用…</p>
    <p v-else-if="hint" class="mt-1 text-[10px] text-gray-400">{{ hint }}</p>
  </div>
</template>
