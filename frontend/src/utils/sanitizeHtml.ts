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
    // `<style>` 标签也要禁：DOMPurify 的 html profile 是**包含**它的，模型输出
    // `<style>body{display:none}</style>` 会原样生效、把整页搞乱。但 `style`
    // **属性**必须留着——KaTeX 的 `katex-html` 那份完全靠内联 style 定位。
    FORBID_TAGS: ['iframe', 'object', 'embed', 'form', 'input', 'textarea', 'select', 'style'],
    FORBID_ATTR: ['srcdoc', 'formaction'],
    // mathMl profile 不含 `semantics` / `annotation`，而 KaTeX 的 MathML 副本正需要
    // 这对包装标签；缺了会让 `<annotation>` 里的 LaTeX 源码变成裸文本，读屏软件念出来。
    ADD_TAGS: ['semantics', 'annotation'],
    // DOMPurify 不只看标签/属性，还会按协议过滤**属性值**：不匹配 `ALLOWED_URI_REGEXP`
    // 就把整个 `href` 删掉。markdown-it 那层早有 `validateLink` 放行 `quick:`，消毒层
    // 不跟上就会出现「按钮渲染出来了但点了没反应」——点击处理读的是
    // `href.startsWith('quick:')`，href 被删后整段跳过。`canvas://` 的预览链接同理。
    // 以下是 DOMPurify 默认白名单 + 两个业务自定义协议（默认里本来就没有 javascript:）。
    ALLOWED_URI_REGEXP:
      /^(?:(?:(?:f|ht)tps?|mailto|tel|callto|sms|cid|xmpp|quick|canvas):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
  })
}
