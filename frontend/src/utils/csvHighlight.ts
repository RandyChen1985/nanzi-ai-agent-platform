/**
 * CSV 表格单元格的「转义 + 命中高亮」纯函数。
 *
 * 为什么单独抽出来：单元格内容来自 CSV（模型输出 / 用户上传 / 查询导出），是**不可信
 * 输入**；画布为了高亮搜索命中而使用 `v-html`，因此渲染前必须转义。它曾经直接在
 * `ChatCanvas.vue` 里用 `text.replace(regex, '<mark>$1</mark>')` 拼 HTML —— 未命中的
 * 部分会原样带标签进 `v-html`，构成存储型 XSS。
 *
 * 抽成纯函数还让它可被 Node 真跑断言（见 `tests/frontend/csv_highlight.mjs`），
 * 而不是靠「源码里有没有出现 escapeHtml」这种字符串断言。
 */

/** HTML 转义：覆盖 `& < > " '` 五个字符，足够阻断标签与属性注入。 */
export const escapeHtml = (text: string): string =>
  String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

/** 正则元字符转义：搜索词来自用户输入，不能让 `.` `*` 等改变匹配语义。 */
export const escapeRegExp = (str: string): string =>
  String(str ?? "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const MARK_OPEN =
  '<mark class="bg-yellow-200 dark:bg-yellow-800/60 dark:text-white px-0.5 rounded">';

/**
 * 返回**已转义**的 HTML：命中段包 `<mark>`，其余段原样转义。
 *
 * 分两趟（先按原文切分、再逐段转义）而不是「先转义再匹配」，这样含 `<` `&` 的搜索词
 * 也能正常命中，同时不会因转义把 `&lt;` 拆开而错位。
 */
export const highlightCsvCell = (text: string, query: string): string => {
  const raw = String(text ?? "");
  const q = String(query ?? "").trim();
  if (!q) return escapeHtml(raw);

  const regex = new RegExp(`(${escapeRegExp(q)})`, "gi");
  const html: string[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(raw)) !== null) {
    html.push(escapeHtml(raw.slice(cursor, match.index)));
    html.push(`${MARK_OPEN}${escapeHtml(match[0])}</mark>`);
    cursor = match.index + match[0].length;
    // 零宽命中会原地打转，必须手动推进 lastIndex。
    if (match[0].length === 0) regex.lastIndex += 1;
  }
  html.push(escapeHtml(raw.slice(cursor)));
  return html.join("");
};
