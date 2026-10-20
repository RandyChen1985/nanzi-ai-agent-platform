<template>
  <div v-if="!dept" class="p-6 text-center text-xs text-gray-400">
    从左侧选择一个部门，或点「新建部门」创建
  </div>

  <div v-else class="space-y-4 p-4">
    <div class="grid gap-3 sm:grid-cols-2">
      <label class="block">
        <span class="mb-1 block text-xs font-bold text-gray-500 dark:text-gray-400">部门名称</span>
        <input
          v-model="form.name"
          type="text"
          :disabled="readonly"
          class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:disabled:bg-gray-800"
          placeholder="例如：华东大区"
        />
      </label>

      <label class="block">
        <span class="mb-1 block text-xs font-bold text-gray-500 dark:text-gray-400">部门编码</span>
        <input
          v-model="form.code"
          type="text"
          :disabled="readonly"
          :class="[
            'w-full rounded-lg border px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 disabled:bg-gray-50 disabled:text-gray-500 dark:bg-gray-900 dark:text-gray-100 dark:disabled:bg-gray-800',
            codeAvailable === false
              ? 'border-red-400 focus:ring-red-500 dark:border-red-500/60'
              : 'border-gray-300 focus:ring-blue-500 dark:border-gray-700'
          ]"
          placeholder="EAST"
          @blur="onCodeBlur"
        />
        <span v-if="codeChecking" class="mt-1 block text-[11px] text-gray-400">正在检查编码…</span>
        <span v-else-if="codeAvailable === false" class="mt-1 block text-[11px] font-bold text-red-500">
          {{ codeMessage }}
        </span>
        <span v-else-if="codeAvailable === true" class="mt-1 block text-[11px] font-bold text-emerald-600">
          编码可用
        </span>
        <span v-else class="mt-1 block text-[11px] leading-relaxed text-gray-400">
          唯一标识。用户的「部门代码」取自这里，行级数据过滤按它匹配，改编码会同步刷新成员快照。
        </span>
      </label>

      <label class="block">
        <span class="mb-1 block text-xs font-bold text-gray-500 dark:text-gray-400">上级部门</span>
        <input
          :value="parentLabel"
          type="text"
          readonly
          class="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400"
        />
      </label>

      <label class="block">
        <span class="mb-1 block text-xs font-bold text-gray-500 dark:text-gray-400">同级排序</span>
        <input
          v-model.number="form.sort_order"
          type="number"
          :disabled="readonly"
          class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:disabled:bg-gray-800"
        />
        <span class="mt-1 block text-[11px] text-gray-400">数值小的排前面；也可用左侧树上的 ↑↓。</span>
      </label>

      <label class="block">
        <span class="mb-1 block text-xs font-bold text-gray-500 dark:text-gray-400">状态</span>
        <select
          v-model.number="form.status"
          :disabled="readonly"
          class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:disabled:bg-gray-800"
        >
          <option :value="1">启用</option>
          <option :value="0">停用</option>
        </select>
        <span class="mt-1 block text-[11px] text-gray-400">停用后不出现在用户侧的部门选择器里，已有成员保留。</span>
      </label>

      <label class="block sm:col-span-2">
        <span class="mb-1 block text-xs font-bold text-gray-500 dark:text-gray-400">描述</span>
        <textarea
          v-model="form.description"
          :disabled="readonly"
          rows="2"
          class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:disabled:bg-gray-800"
          placeholder="可选说明"
        />
      </label>
    </div>

    <p class="text-[11px] text-gray-400">
      路径：<code class="rounded bg-gray-100 px-1 py-0.5 dark:bg-gray-800">{{ dept.path }}</code>
      · 直属成员 {{ dept.member_count }} 人
    </p>

    <div v-if="!readonly" class="flex items-center gap-2">
      <button
        type="button"
        class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-700 disabled:opacity-50"
        :disabled="saving || codeAvailable === false || codeChecking"
        @click="onSave"
      >保存</button>
      <button
        v-if="canDelete"
        type="button"
        class="rounded-lg border border-red-200 px-3 py-1.5 text-xs font-bold text-red-600 hover:bg-red-50 disabled:opacity-50 dark:border-red-500/30 dark:hover:bg-red-500/10"
        :disabled="saving"
        @click="emit('remove')"
      >删除部门</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import type { DeptNode } from './DepartmentTree.vue'
import { useDepartmentCodeAvailability } from '../../composables/useDepartmentCodeAvailability'

/**
 * 部门信息面板。
 *
 * 只负责"编辑这个部门的属性"，不含组织树操作（新建子部门在树上做）。
 * 保存由父级发请求：面板不直接调接口，避免一个页面里出现两套错误处理。
 */
const props = withDefaults(
  defineProps<{
    dept: DeptNode | null
    parentLabel?: string
    readonly?: boolean
    saving?: boolean
    canDelete?: boolean
  }>(),
  { parentLabel: '无（根部门）', readonly: false, saving: false, canDelete: false }
)

const emit = defineEmits<{
  (e: 'save', payload: { name: string; code: string; description: string; sort_order: number; status: number }): void
  (e: 'remove'): void
}>()

const form = reactive({
  name: '',
  code: '',
  description: '',
  sort_order: 0,
  status: 1
})

const baselineCode = ref('')

const syncForm = (dept: DeptNode | null) => {
  form.name = dept?.name || ''
  form.code = dept?.code || ''
  form.description = dept?.description || ''
  form.sort_order = dept?.sort_order ?? 0
  form.status = dept?.status ?? 1
  baselineCode.value = form.code
}

watch(() => props.dept, syncForm, { immediate: true })

/**
 * 编码查重：只在**用户改动过之后**判定。
 *
 * 初始加载时 form.code 就是库里已有的编码，若立刻查重必然得到"已被使用"——
 * 所以首次同步只重置状态，等输入停顿或失焦再查，并用 excludeRef 排除自身。
 */
const {
  checking: codeChecking,
  available: codeAvailable,
  message: codeMessage,
  checkDebounced: checkCodeDebounced,
  ensureAvailable: ensureCodeAvailable,
  reset: resetCodeCheck,
} = useDepartmentCodeAvailability()

watch(
  () => form.code,
  (next) => {
    if (props.readonly) return
    // 程序回填（切部门、初始加载）不算用户改动，不该弹"已被使用"
    if ((next || '').trim() === (baselineCode.value || '').trim()) {
      resetCodeCheck()
      return
    }
    checkCodeDebounced(next, { excludeRef: props.dept?.id ?? null })
  }
)

const onCodeBlur = () => {
  if (props.readonly) return
  void checkCodeDebounced(form.code, { excludeRef: props.dept?.id ?? null }, 0)
}

const onSave = async () => {
  // 提交前兜底：输入期可能因为网络失败而"未判定"，权威结论以这一刻为准
  const result = await ensureCodeAvailable(form.code, props.dept?.id ?? null)
  if (!result.ok) return
  emit('save', { ...form })
}

watch(() => props.dept, () => resetCodeCheck())
</script>
