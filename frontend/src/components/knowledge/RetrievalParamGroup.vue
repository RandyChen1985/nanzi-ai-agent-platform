<script setup lang="ts">
/**
 * 检索测试的参数组：前 n 条 / 相似度阈值 / 向量相似度权重。
 *
 * 语义固定为「默认值来自系统配置，页面改动只对本次检索生效」：
 * 每个参数右侧显示来源（系统配置 / 已修改），改过之后可以一键恢复。
 * 单独的组件是为了让检索测试页在 A/B 对照模式下渲染两组而不复制三份模板。
 */
import { computed } from 'vue'

type RetrievalParams = {
  topK: number
  threshold: number
  weight: number
}

const props = defineProps<{
  modelValue: RetrievalParams
  defaults: RetrievalParams
  disabled?: boolean
}>()

const emit = defineEmits<{ (e: 'update:modelValue', value: RetrievalParams): void }>()

// RAGFlow 控制台只给这几档，页面保持一致；配置里若是别的值（例如 7）会追加进去，避免选中态丢失
const TOP_K_OPTIONS = [5, 10, 20, 30, 50]
const topKOptions = computed(() => {
  const values = new Set<number>(TOP_K_OPTIONS)
  if (Number.isInteger(props.modelValue.topK) && props.modelValue.topK > 0) {
    values.add(props.modelValue.topK)
  }
  return Array.from(values).sort((a, b) => a - b)
})

const EPSILON = 1e-9
const isDirty = (current: number, fallback: number) => Math.abs(Number(current) - Number(fallback)) > EPSILON

const topKDirty = computed(() => isDirty(props.modelValue.topK, props.defaults.topK))
const thresholdDirty = computed(() => isDirty(props.modelValue.threshold, props.defaults.threshold))
const weightDirty = computed(() => isDirty(props.modelValue.weight, props.defaults.weight))

const patch = (key: keyof RetrievalParams, value: number) => {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

const reset = (key: keyof RetrievalParams) => patch(key, props.defaults[key])

const clamp = (value: number) => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 0
  return Math.min(1, Math.max(0, parsed))
}

const onNumberInput = (key: 'threshold' | 'weight', raw: string) => {
  // 输入框清空时 v-model.number 会得到空串，回落成 0 而不是 NaN
  const parsed = raw === '' ? 0 : Number(raw)
  patch(key, Number.isFinite(parsed) ? clamp(parsed) : 0)
}

// 参数说明走自定义气泡，不用原生 title：原生提示延迟不可控、样式不可控、
// 移动端不触发，而且这里需要放两三行解释，title 撑不住。
const HINTS = {
  topK: '最终返回的片段条数（对应 RAGFlow page_size）。调大提高覆盖率，也增加 Token 与噪声。',
  threshold: '低于该分值的片段直接丢弃（0~1）。调高更保守，但可能漏召回。',
  weight: '向量检索得分权重，其余给关键词匹配（和为 1）。语义问句调高，编号查询调低。',
}

const fillPercent = (value: number) => `${Math.min(100, Math.max(0, Number(value) * 100))}%`
// full-text 是互补权重：RAGFlow 里两者之和恒为 1
const fullTextWeight = computed(() => Math.max(0, 1 - Number(props.modelValue.weight)))
</script>

<template>
  <div class="param-group">
    <!-- 前 n 条 -->
    <div class="param">
      <div class="param-head">
        <span class="param-name">前 n 条</span>
        <span class="param-help-wrap">
          <span class="param-help" tabindex="0" role="img" :aria-label="`前 n 条：${HINTS.topK}`">?</span>
          <span class="param-bubble" role="tooltip" aria-hidden="true">{{ HINTS.topK }}</span>
        </span>
        <span class="param-src" :class="{ 'is-dirty': topKDirty }">{{ topKDirty ? '已修改' : '系统配置' }}</span>
        <button v-if="topKDirty" type="button" class="param-reset" :disabled="disabled" @click="reset('topK')">恢复</button>
      </div>
      <select
        :value="modelValue.topK"
        :disabled="disabled"
        class="param-select"
        aria-label="前 n 条"
        @change="patch('topK', Number(($event.target as HTMLSelectElement).value))"
      >
        <option v-for="item in topKOptions" :key="item" :value="item">前 {{ item }} 条</option>
      </select>
    </div>

    <!-- 相似度阈值 -->
    <div class="param">
      <div class="param-head">
        <span class="param-name">相似度阈值</span>
        <span class="param-help-wrap">
          <span class="param-help" tabindex="0" role="img" :aria-label="`相似度阈值：${HINTS.threshold}`">?</span>
          <span class="param-bubble" role="tooltip" aria-hidden="true">{{ HINTS.threshold }}</span>
        </span>
        <span class="param-src" :class="{ 'is-dirty': thresholdDirty }">{{ thresholdDirty ? '已修改' : '系统配置' }}</span>
        <button v-if="thresholdDirty" type="button" class="param-reset" :disabled="disabled" @click="reset('threshold')">恢复</button>
      </div>
      <div class="param-row">
        <input
          type="range" min="0" max="1" step="0.01"
          class="param-range"
          :style="{ background: `linear-gradient(90deg, #12b5a5 ${fillPercent(modelValue.threshold)}, #e5e7eb ${fillPercent(modelValue.threshold)})` }"
          :value="modelValue.threshold"
          :disabled="disabled"
          aria-label="相似度阈值"
          @input="patch('threshold', clamp(Number(($event.target as HTMLInputElement).value)))"
        />
        <input
          type="number" min="0" max="1" step="0.01"
          class="param-number"
          :value="modelValue.threshold"
          :disabled="disabled"
          aria-label="相似度阈值数值"
          @input="onNumberInput('threshold', ($event.target as HTMLInputElement).value)"
        />
      </div>
    </div>

    <!-- 向量相似度权重 -->
    <div class="param">
      <div class="param-head">
        <span class="param-name">向量相似度权重</span>
        <span class="param-help-wrap">
          <span class="param-help" tabindex="0" role="img" :aria-label="`向量相似度权重：${HINTS.weight}`">?</span>
          <span class="param-bubble" role="tooltip" aria-hidden="true">{{ HINTS.weight }}</span>
        </span>
        <span class="param-src" :class="{ 'is-dirty': weightDirty }">{{ weightDirty ? '已修改' : '系统配置' }}</span>
        <button v-if="weightDirty" type="button" class="param-reset" :disabled="disabled" @click="reset('weight')">恢复</button>
      </div>
      <div class="weight-legend">
        <span class="weight-label">vector <span class="weight-pill">{{ Number(modelValue.weight).toFixed(2) }}</span></span>
        <span class="weight-label">full-text <span class="weight-pill">{{ fullTextWeight.toFixed(2) }}</span></span>
      </div>
      <div class="param-row">
        <input
          type="range" min="0" max="1" step="0.01"
          class="param-range"
          :style="{ background: `linear-gradient(90deg, #12b5a5 ${fillPercent(modelValue.weight)}, #e5e7eb ${fillPercent(modelValue.weight)})` }"
          :value="modelValue.weight"
          :disabled="disabled"
          aria-label="向量相似度权重"
          @input="patch('weight', clamp(Number(($event.target as HTMLInputElement).value)))"
        />
        <input
          type="number" min="0" max="1" step="0.01"
          class="param-number"
          :value="modelValue.weight"
          :disabled="disabled"
          aria-label="向量相似度权重数值"
          @input="onNumberInput('weight', ($event.target as HTMLInputElement).value)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.param + .param {
  border-top: 1px dashed #f3f4f6;
}

.param {
  padding: 12px 0;
}

.param-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 9px;
}

.param-name {
  font-size: 13px;
  color: #374151;
  font-weight: 500;
}

.param-help-wrap {
  position: relative;
  display: inline-flex;
  flex: none;
}

.param-help {
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 1px solid #d1d5db;
  color: #9ca3af;
  font-size: 10px;
  line-height: 13px;
  text-align: center;
  cursor: help;
  flex: none;
  user-select: none;
  transition: border-color 0.14s ease, color 0.14s ease;
}

.param-help-wrap:hover .param-help,
.param-help:focus-visible {
  border-color: #12b5a5;
  color: #0f766e;
}

.param-help:focus-visible {
  outline: 2px solid rgba(18, 181, 165, 0.3);
  outline-offset: 1px;
}

/* 气泡向下展开并右对齐图标左缘：向左展开会被卡片圆角裁掉 */
.param-bubble {
  position: absolute;
  top: calc(100% + 7px);
  left: -3px;
  width: 212px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #1f2937;
  color: #f9fafb;
  font-size: 11.5px;
  font-weight: 400;
  line-height: 1.65;
  text-align: left;
  white-space: normal;
  box-shadow: 0 8px 22px rgba(17, 24, 39, 0.22);
  opacity: 0;
  visibility: hidden;
  transform: translateY(-3px);
  transition: opacity 0.14s ease 0.06s, transform 0.14s ease 0.06s, visibility 0.14s 0.06s;
  pointer-events: none;
  z-index: 40;
}

.param-bubble::before {
  content: '';
  position: absolute;
  top: -4px;
  left: 10px;
  width: 8px;
  height: 8px;
  border-radius: 1px;
  background: #1f2937;
  transform: rotate(45deg);
}

.param-help-wrap:hover .param-bubble,
.param-help-wrap:focus-within .param-bubble {
  opacity: 1;
  visibility: visible;
  transform: translateY(0);
}

.param-src {
  margin-left: auto;
  font-size: 10.5px;
  padding: 1px 7px;
  border-radius: 999px;
  background: #e6f7f5;
  color: #0f766e;
  border: 1px solid #cdf0ec;
  white-space: nowrap;
}

.param-src.is-dirty {
  background: #fff7ed;
  color: #b45309;
  border-color: #fde7c7;
}

.param-reset {
  font-size: 10.5px;
  color: #6b7280;
  text-decoration: underline;
  background: none;
  border: 0;
  padding: 0;
  cursor: pointer;
  flex: none;
}

.param-reset:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.param-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.param-range {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 4px;
  border-radius: 999px;
  outline: none;
  flex: 1;
}

.param-range::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  border: 2px solid #12b5a5;
  box-shadow: 0 0 0 3px rgba(18, 181, 165, 0.12);
  cursor: grab;
}

.param-range::-moz-range-thumb {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  border: 2px solid #12b5a5;
  cursor: grab;
}

.param-range:disabled::-webkit-slider-thumb {
  border-color: #d1d5db;
  box-shadow: none;
  cursor: not-allowed;
}

.param-number {
  width: 66px;
  flex: none;
  text-align: center;
  border: 1px solid #e5e7eb;
  border-radius: 9px;
  padding: 7px 4px;
  font-size: 13px;
  color: #111827;
  background: #fff;
}

.param-number:focus {
  outline: 2px solid rgba(18, 181, 165, 0.18);
  border-color: #12b5a5;
}

.param-number:disabled,
.param-select:disabled {
  background: #f9fafb;
  cursor: not-allowed;
}

.param-select {
  width: 100%;
  appearance: none;
  border: 1px solid #e5e7eb;
  border-radius: 9px;
  padding: 9px 34px 9px 12px;
  font-size: 13px;
  color: #374151;
  background: #fff
    url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239ca3af' stroke-width='2.5'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")
    no-repeat right 12px center;
  cursor: pointer;
}

.param-select:focus {
  outline: 2px solid rgba(37, 99, 235, 0.15);
  border-color: #2563eb;
}

.weight-legend {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 0 78px 8px 0;
  color: #6b7280;
  font-size: 12px;
}

.weight-label {
  font-style: italic;
}

.weight-pill {
  display: inline-block;
  background: #f3f4f6;
  border-radius: 6px;
  padding: 1px 7px;
  font-size: 11.5px;
  color: #374151;
  margin-left: 5px;
  font-variant-numeric: tabular-nums;
}
</style>
