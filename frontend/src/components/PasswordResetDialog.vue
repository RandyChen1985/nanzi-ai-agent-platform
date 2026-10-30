<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-[13000] flex items-center justify-center bg-black/40 p-4"
      @click.self="emit('cancel')"
    >
      <form
        class="w-[min(480px,100vw)] space-y-4 rounded-2xl bg-white p-6 shadow-2xl dark:bg-gray-900"
        @submit.prevent="submit"
      >
        <div class="flex items-center justify-between gap-2">
          <h3 class="flex items-center gap-1.5 text-lg font-bold text-emerald-700 dark:text-emerald-400">
            {{ title }}
          </h3>
          <span
            class="inline-flex items-center gap-1 rounded border border-emerald-200/60 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300"
          >
            <svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
              />
            </svg>
            <span>等保三级规范</span>
          </span>
        </div>

        <p class="text-xs text-gray-500 dark:text-gray-400">
          为 <strong class="font-semibold text-gray-800 dark:text-gray-100">{{ displayName }}</strong> 设置登录密码<span
            v-if="hint"
          >，{{ hint }}</span>
        </p>

        <div class="space-y-1 rounded-lg border border-emerald-100 bg-emerald-50/70 p-3 text-xs dark:border-emerald-500/30 dark:bg-emerald-500/10">
          <div class="flex items-center gap-1.5 font-semibold text-emerald-900 dark:text-emerald-200">
            <svg class="h-4 w-4 shrink-0 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            <span>密码复杂度规则说明</span>
          </div>
          <p class="pl-5 leading-relaxed text-emerald-800/90 dark:text-emerald-300/90">
            长度须为 <strong>8-32 位</strong>，至少包含<strong>大写字母</strong>、<strong>小写字母</strong>、<strong>数字</strong>、<strong>特殊符号</strong>中的
            <strong>3 种</strong>，且不能包含空格或用户名。
          </p>
        </div>

        <div class="space-y-3">
          <div>
            <label class="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-300">新密码</label>
            <input
              ref="passwordInput"
              v-model="password"
              type="password"
              autocomplete="new-password"
              placeholder="输入新密码（8-32位，含3种字符类别）"
              class="w-full rounded-lg border px-3 py-2 text-sm transition-all focus:outline-none focus:ring-2 focus:ring-emerald-500"
              :class="
                password
                  ? policy.valid
                    ? 'border-emerald-400 bg-emerald-50/20'
                    : 'border-amber-300 bg-amber-50/20'
                  : 'border-gray-300 dark:border-gray-700'
              "
            />
          </div>
          <div>
            <label class="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-300">再次输入新密码</label>
            <input
              v-model="confirm"
              type="password"
              autocomplete="new-password"
              placeholder="再次输入新密码"
              class="w-full rounded-lg border px-3 py-2 text-sm transition-all focus:outline-none focus:ring-2 focus:ring-emerald-500"
              :class="
                confirm
                  ? confirm === password
                    ? 'border-emerald-400 bg-emerald-50/20'
                    : 'border-red-300 bg-red-50/20'
                  : 'border-gray-300 dark:border-gray-700'
              "
            />
          </div>
        </div>

        <div
          v-if="password"
          class="space-y-1.5 rounded-lg border border-gray-200/80 bg-gray-50 p-2.5 text-xs dark:border-gray-700 dark:bg-gray-800"
        >
          <div class="flex items-center justify-between gap-2 text-gray-600 dark:text-gray-300">
            <span class="shrink-0 whitespace-nowrap font-medium">合规检测：</span>
            <span
              class="truncate text-right font-medium"
              :class="policy.valid ? 'font-semibold text-emerald-600' : 'text-amber-600'"
            >
              {{ policy.valid ? '✓ 符合等保要求' : policy.shortMessage }}
            </span>
          </div>
          <div class="grid grid-cols-3 gap-1.5 text-[11px]">
            <div
              v-for="item in indicators"
              :key="item.label"
              class="flex items-center gap-1 rounded px-1.5 py-0.5"
              :class="item.ok ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-200/70 text-gray-500'"
            >
              <span>{{ item.ok ? '✓' : '○' }}</span>
              <span>{{ item.label }}</span>
            </div>
          </div>
        </div>

        <div class="flex justify-end gap-3 pt-2">
          <button
            type="button"
            class="cursor-pointer rounded-lg border border-gray-300 px-5 py-2 text-sm font-medium transition-colors hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-800"
            :disabled="submitting"
            @click="emit('cancel')"
          >取消</button>
          <button
            type="submit"
            class="cursor-pointer rounded-lg bg-emerald-600 px-5 py-2 text-sm font-medium text-white transition-all hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="submitting || !policy.valid || password !== confirm"
          >{{ submitting ? '提交中…' : '确认设置' }}</button>
        </div>
      </form>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { checkPasswordPolicy } from '../utils/passwordPolicy'

/**
 * 重置密码弹窗——等保三级规范的**唯一**实现。
 *
 * 用户管理页与部门管理页都必须用它。此前部门页自己写了两个裸输入框：规则函数
 * （`checkPasswordPolicy`）虽然一致，但规则说明、实时合规指标、输入框染色全缺，
 * 管理员会以为部门页的密码要求更宽松——这类"看起来更松"的错觉比真的更松还危险。
 *
 * 组件只负责收集与校验密码，**不调接口**：两边接口路径不同（用户管理走
 * `/management/users/{id}/set-password`，部门成员走同一接口但错误处理上下文不同），
 * 由调用方决定怎么提交。
 */
const props = withDefaults(
  defineProps<{
    /** 弹窗标题 */
    title?: string
    /** 展示给管理员的账号名（批量时为「15 个用户」这类复数描述） */
    displayName: string
    /** 用于「不能包含用户名」校验，默认与 displayName 一致 */
    loginName?: string
    /** 批量场景：密码不能包含其中任一用户名（长度 >= 3 才参与校验） */
    loginNames?: string[]
    /** 提交中（由父级控制，避免重复提交） */
    submitting?: boolean
    /** 补充说明，如「保存后该用户的全部登录会话会立即失效」 */
    hint?: string
  }>(),
  { title: '重置密码', loginName: '', loginNames: () => [], submitting: false, hint: '' }
)

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'submit', password: string): void
}>()

const password = ref('')
const confirm = ref('')
const passwordInput = ref<HTMLInputElement | null>(null)

/**
 * 先跑与用户名无关的规则，再查「密码是否包含用户名」。
 *
 * 批量改密必须查**每一个**目标用户的用户名：同一个密码包含 alice 不合规、
 * 对 bob 却合规。这里把多个用户名合并成一次判定，单用户场景传入一个名字，
 * 行为与原先完全一致（顺序也是先长度/空白/类别、最后才报用户名）。
 */
const policy = computed(() => {
  const base = checkPasswordPolicy(password.value)
  if (!base.valid || !password.value) return base

  const names = (props.loginNames?.length
    ? props.loginNames
    : [props.loginName || props.displayName]
  )
    .map((name) => (name || '').trim())
    .filter((name) => name.length >= 3)

  const hit = names.find((name) =>
    password.value.toLowerCase().includes(name.toLowerCase())
  )
  if (!hit) return base
  return {
    ...base,
    valid: false,
    message: '密码不能包含用户名',
    shortMessage: `不能包含用户名（${hit}）`,
  }
})

const indicators = computed(() => [
  { label: '8-32位', ok: policy.value.hasLength },
  { label: '大写字母', ok: policy.value.hasUpper },
  { label: '小写字母', ok: policy.value.hasLower },
  { label: '数字', ok: policy.value.hasDigit },
  { label: '特殊符号', ok: policy.value.hasSpecial },
  { label: `命中 ${policy.value.categoryCount}/4 类`, ok: policy.value.categoryCount >= 3 },
])

const submit = () => {
  if (!policy.value.valid || password.value !== confirm.value) return
  emit('submit', password.value)
}

onMounted(() => passwordInput.value?.focus())
</script>
