<script setup lang="ts">
import type { McpModelResultPolicy } from '@/utils/mcpCredentialProfiles'

defineProps<{ modelValue: string; policy: McpModelResultPolicy; busy: boolean; disabled: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [string]; save: [] }>()
</script>

<template>
  <div class="space-y-2 rounded-lg border bg-slate-50 p-3">
    <label for="mcp-tool-result-fields" class="block text-xs font-medium">模型可见的结果字段</label>
    <textarea id="mcp-tool-result-fields" :value="modelValue" :disabled="busy" rows="3"
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
      spellcheck="false" aria-describedby="mcp-tool-result-help" placeholder="$.messages[*][1]"
      class="w-full rounded-lg border bg-white px-2.5 py-2 font-mono text-xs" />
    <div id="mcp-tool-result-help" class="space-y-1 text-xs leading-relaxed text-slate-600">
      <p>每行填写一个 JSONPath（RFC 9535），最多 20 个。路径从解包后的业务 JSON 开始。</p>
      <ul class="list-disc space-y-0.5 pl-4">
        <li><code>$</code> 选择整份业务结果。</li>
        <li><code>$.messages[*]</code> 选择 <code>messages</code> 中的每封邮件或整行。</li>
        <li><code>$.messages[*][1]</code> 选择二维数组每行的第 2 列。</li>
        <li>业务结果有外层包装时，<code>$..messages[*]</code> 可在嵌套层级查找。</li>
      </ul>
      <p>留空、路径无效或无匹配时，只显示执行状态。按 JSONPath 选中的内容原样返回。</p>
      <p>所选内容可能出现在对话和执行追踪中。</p>
      <p v-if="policy.stale" class="text-amber-800">工具参数或目标已变化，已保留原结果字段。请核对当前响应后重新保存。</p>
      <p>结果字段与下方参数来源一起保存。</p>
    </div>
    <div class="flex justify-end">
      <button type="button" :disabled="disabled" class="rounded-lg border bg-white px-3 py-1.5 text-xs disabled:opacity-50" @click="emit('save')">
        {{ busy ? '保存中…' : '保存结果字段与参数来源' }}
      </button>
    </div>
  </div>
</template>
