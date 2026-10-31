// CSV 单元格渲染的 XSS 回归：用 Node 真跑 `highlightCsvCell`，而不是断言源码里
// 有没有出现 escapeHtml —— 后者在函数被重写时会静默失效。
//
// 背景：ChatCanvas 的表格用 `v-html` 渲染单元格以高亮搜索命中，旧实现
// `text.replace(regex, '<mark>$1</mark>')` 把未命中的原文原样带进 DOM，
// 于是模型输出 / 用户上传 CSV 里的 `<img onerror=...>` 会直接执行。
import assert from 'node:assert/strict'

import { escapeHtml, escapeRegExp, highlightCsvCell } from '../../frontend/src/utils/csvHighlight.ts'

const MARK_OPEN =
  '<mark class="bg-yellow-200 dark:bg-yellow-800/60 dark:text-white px-0.5 rounded">'
const mark = (inner) => `${MARK_OPEN}${inner}</mark>`

// --- 1. 无搜索词：恶意标签必须被整体转义，不得留下可执行标签 ---
const xss = '<img src=x onerror="alert(1)">'
const plain = highlightCsvCell(xss, '')
assert.equal(plain, '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;')
assert.equal(plain.includes('<img'), false, '不得输出未转义的 <img 标签')

// --- 2. 有搜索词：命中段高亮，命中段之外同样转义 ---
const withQuery = highlightCsvCell('a<b>cell</b>', 'cell')
assert.equal(withQuery, `a&lt;b&gt;${mark('cell')}&lt;/b&gt;`)
assert.equal(withQuery.includes('<b>'), false)

// --- 3. 搜索词本身含 HTML 特殊字符：能命中，且高亮内容也被转义 ---
const ltQuery = highlightCsvCell('x < y and y > z', '<')
assert.equal(ltQuery, `x ${mark('&lt;')} y and y &gt; z`)
const ampQuery = highlightCsvCell('Tom & Jerry', '&')
assert.equal(ampQuery, `Tom ${mark('&amp;')} Jerry`)

// --- 4. 属性逃逸：引号必须被转义，不能闭合 v-html 之外的任何属性 ---
const quoteAttack = highlightCsvCell('"><script>alert(1)</script>', '')
assert.equal(quoteAttack.includes('"'), false, '原文引号应转义为 &quot;')
assert.equal(quoteAttack.includes('<script>'), false)

// --- 5. 正常文本：大小写不敏感高亮，且保留原文大小写 ---
assert.equal(highlightCsvCell('Hello World', 'world'), `Hello ${mark('World')}`)

// --- 6. 多次命中：每一处都高亮，段间文本不丢 ---
assert.equal(
  highlightCsvCell('a1b2a3', 'a'),
  `${mark('a')}1b2${mark('a')}3`,
)

// --- 7. 搜索词为正则元字符时按字面匹配，不误伤相邻文本 ---
assert.equal(highlightCsvCell('a.b.axb', '.b'), `a${mark('.b')}.axb`)

// --- 8. 转义/正则工具的边界 ---
assert.equal(escapeHtml("&<>\"'"), '&amp;&lt;&gt;&quot;&#39;')
assert.equal(escapeRegExp('a+b'), 'a\\+b')
assert.equal(highlightCsvCell('', 'x'), '')
assert.equal(highlightCsvCell('anything', '   '), 'anything')

console.log('csv_highlight: all assertions passed')
