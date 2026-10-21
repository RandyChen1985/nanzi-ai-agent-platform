import DOMPurify from 'dompurify'

/**
 * 统一清洗 markdown 渲染后的 HTML。
 *
 * 模型输出、知识库正文、数据集字符串都可能进入 `v-html`；markdown-it 开启
 * `html: true` 后原始 HTML 会原样保留，若不消毒，`<img onerror>` 一类注入
 * 可以在终端用户浏览器里执行。这里用 DOMPurify 白名单收敛标签/属性，并
 * 允许业务需要的 data-* 与下载按钮属性。
 */
const ALLOWED_ATTR = [
  'href',
  'src',
  'alt',
  'title',
  'class',
  'style',
  'target',
  'rel',
  'colspan',
  'rowspan',
  'align',
  'width',
  'height',
  'data-generated-download',
  'data-quick',
  'data-citation',
  'data-citation-id',
  'data-citation-index',
  'data-embed',
  'data-lang',
]

export function sanitizeRenderedHtml(html: string): string {
  const input = String(html || '')
  if (!input) return ''
  return DOMPurify.sanitize(input, {
    // 必须显式开 mathMl：`USE_PROFILES` 会把白名单**重置**成所选 profile 的内容
    // （源码里 `ALLOWED_TAGS = addToSet({}, text)` 再按开关追加），只开 html 时
    // KaTeX 输出的 `<math>` / `<mrow>` 会被当未知标签剥掉。视觉上公式由
    // `katex-html` 那份撑着不会坏，但无障碍朗读与「复制公式」会退化。
    USE_PROFILES: { html: true, mathMl: true },
    ADD_ATTR: ALLOWED_ATTR,
    // 画布/图表由组件自行渲染，模型正文不需要 iframe/form。
    FORBID_TAGS: ['iframe', 'object', 'embed', 'form', 'input', 'textarea', 'select'],
    FORBID_ATTR: ['srcdoc', 'formaction'],
  })
}
