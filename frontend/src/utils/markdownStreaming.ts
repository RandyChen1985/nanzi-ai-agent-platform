/**
 * 流式正文的「稳定块」切分。
 *
 * 为什么需要：正文每次变化都会把整篇重新走 markdown-it + highlight.js +
 * DOMParser 清洗，成本随长度线性增长——短回答流畅，长回答/含代码块时卡顿。
 * 切成块后，已完成块的 HTML 可缓存复用，每帧只重算最后一块，视觉结果不变
 * （所有块仍拼回同一个 `v-html` 容器）。
 *
 * 切分原则：**宁可少切，不可多切**。少切只是缓存粒度变粗（退化为整段渲染，
 * 结果一定正确）；多切会改变 Markdown 结构（列表被拆成两个、代码块被腰斩）。
 * 因此只有「空行之后是普通段落/标题」时才断开，凡是以列表、引用、表格、
 * 缩进块、代码围栏开头的行一律并入当前块。
 */

const FENCE_OPEN = /^\s{0,3}(`{3,}|~{3,})/;
const LIST_ITEM = /^\s{0,3}(?:[-*+]|\d{1,9}[.)])\s/;
const QUOTE = /^\s{0,3}>/;
const TABLE = /^\s{0,3}\|/;
const INDENTED = /^(?:\t| {2,})/;

/** 空行之后，下一行是否仍属于当前块（true = 不断开）。 */
function continuesCurrentBlock(next: string): boolean {
  if (!next.trim()) return true; // 连续空行并进当前块
  if (FENCE_OPEN.test(next)) return true;
  if (LIST_ITEM.test(next)) return true;
  if (QUOTE.test(next)) return true;
  if (TABLE.test(next)) return true;
  if (INDENTED.test(next)) return true;
  return false;
}

export function splitStableMarkdownBlocks(text: string): string[] {
  if (!text) return [];

  const lines = text.split("\n");
  // 每行的起始字符偏移：块边界要按字符切，才能保住空行与换行符。
  const lineStarts: number[] = [0];
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === "\n") lineStarts.push(index + 1);
  }

  const blocks: string[] = [];
  let blockStart = 0;
  // 围栏必须记录「字符 + 长度」：```` 包裹的 ``` 是内容，不是闭合。
  let fenceChar = "";
  let fenceLength = 0;

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    const fenceMatch = line.match(FENCE_OPEN);

    if (fenceMatch) {
      const marker = fenceMatch[1] ?? "";
      if (!fenceChar) {
        fenceChar = marker[0] ?? "";
        fenceLength = marker.length;
      } else if (marker[0] === fenceChar && marker.length >= fenceLength) {
        fenceChar = "";
        fenceLength = 0;
      }
      continue; // 围栏行本身既不是空行也不是正文
    }

    if (fenceChar) continue; // 围栏内一律不切

    if (!line.trim()) {
      const next = lines[index + 1];
      if (next !== undefined && !continuesCurrentBlock(next)) {
        const cut = lineStarts[index + 1] ?? text.length;
        blocks.push(text.slice(blockStart, cut));
        blockStart = cut;
      }
      continue;
    }
  }

  if (blockStart < text.length) blocks.push(text.slice(blockStart));
  return blocks;
}
