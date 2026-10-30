<script setup lang="ts">
/**
 * Python 代码编辑器（CodeMirror 6）。
 *
 * 为什么不用 `<textarea>`：脚本工具的核心内容就是代码，纯文本框没有行号、没有
 * 高亮、缩进全靠手敲，长脚本里定位一行都很费劲。项目里已经有 CodeMirror（SQL
 * 查看器在用），这里复用同一套基础设施，只把语言换成 Python。
 *
 * 职责克制：只负责「把字符串渲染成可编辑代码」，校验、保存、草稿都在调用方。
 */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { basicSetup } from 'codemirror'
import { python } from '@codemirror/lang-python'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags as t } from '@lezer/highlight'

const props = withDefaults(defineProps<{
  modelValue: string
  readonly?: boolean
  minHeight?: string
  maxHeight?: string
  ariaLabel?: string
}>(), {
  readonly: false,
  minHeight: '14rem',
  maxHeight: '26rem',
  ariaLabel: 'Python 脚本代码',
})

const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()

const host = ref<HTMLDivElement | null>(null)
const view = shallowRef<EditorView | null>(null)
/** 只读状态用 Compartment 动态切换：直接重配 Facet 的 API 并不存在 */
const readonlyConfig = new Compartment()

/** 深色编辑器配浅色默认高亮会发灰，这里给一套对比度够用的配色。 */
const darkHighlight = HighlightStyle.define([
  { tag: t.keyword, color: '#c792ea' },
  { tag: [t.string, t.special(t.string)], color: '#c3e88d' },
  { tag: t.comment, color: '#7c8b9c', fontStyle: 'italic' },
  { tag: [t.number, t.bool, t.null], color: '#f78c6c' },
  { tag: t.function(t.variableName), color: '#82aaff' },
  { tag: t.definition(t.variableName), color: '#ffcb6b' },
  { tag: t.propertyName, color: '#89ddff' },
  { tag: t.operator, color: '#89ddff' },
])

const theme = EditorView.theme(
  {
    '&': {
      backgroundColor: '#0b1220',
      color: '#e2e8f0',
      borderRadius: '0.5rem',
      border: '1px solid #1e293b',
      fontSize: '0.75rem',
    },
    '&.cm-focused': { outline: 'none', borderColor: '#1677ff' },
    '.cm-scroller': {
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      lineHeight: '1.6',
      maxHeight: props.maxHeight,
      overflow: 'auto',
    },
    '.cm-content': { padding: '0.5rem 0' },
    '.cm-gutters': {
      backgroundColor: '#0b1220',
      color: '#475569',
      border: 'none',
      borderRight: '1px solid #1e293b',
    },
    '.cm-activeLine': { backgroundColor: 'rgba(148, 163, 184, 0.08)' },
    '.cm-activeLineGutter': { backgroundColor: 'rgba(148, 163, 184, 0.08)' },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': {
      backgroundColor: 'rgba(22, 119, 255, 0.35)',
    },
    '.cm-cursor': { borderLeftColor: '#e2e8f0' },
  },
  { dark: true },
)

function buildState(doc: string): EditorState {
  return EditorState.create({
    doc,
    extensions: [
      basicSetup,
      python(),
      theme,
      syntaxHighlighting(darkHighlight),
      EditorView.lineWrapping,
      readonlyConfig.of([
        EditorState.readOnly.of(props.readonly),
        EditorView.editable.of(!props.readonly),
      ]),
      EditorView.updateListener.of((update) => {
        if (!update.docChanged) return
        const next = update.state.doc.toString()
        if (next !== props.modelValue) emit('update:modelValue', next)
      }),
    ],
  })
}

onMounted(() => {
  if (!host.value) return
  view.value = new EditorView({ state: buildState(props.modelValue), parent: host.value })
})

onBeforeUnmount(() => {
  view.value?.destroy()
  view.value = null
})

// 外部改值（插入最小模板、切工具）时同步进编辑器；内容一致就不动，避免光标跳到行首
watch(() => props.modelValue, (next) => {
  const current = view.value
  if (!current) return
  const doc = current.state.doc.toString()
  if (doc === next) return
  current.dispatch({
    changes: { from: 0, to: doc.length, insert: next },
  })
})

// 只读状态可能在挂载后变化（例如权限刷新）
watch(() => props.readonly, (readonly) => {
  view.value?.dispatch({
    effects: readonlyConfig.reconfigure([
      EditorState.readOnly.of(readonly),
      EditorView.editable.of(!readonly),
    ]),
  })
})
</script>

<template>
  <div
    ref="host"
    class="overflow-hidden rounded-lg"
    :style="{ minHeight }"
    :aria-label="ariaLabel"
    :aria-readonly="readonly ? 'true' : undefined"
  ></div>
</template>
