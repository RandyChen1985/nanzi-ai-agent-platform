/**
 * 折叠态「深度思考」摘要与字数口径。
 *
 * 摘要完全由思考正文现场派生：不落库、不走后端、不调模型。规则集中在这里，
 * 便于用仓库既有的 Node harness 直接单测（见
 * tests/frontend/test_reasoning_preview_behavior.py）。
 */

/** 摘要正文字符上限。 */
export const REASONING_PREVIEW_MAX_CHARS = 80;
/** 允许回退到句读的最小长度：短于此长度宁可硬截，也不摘出半行。 */
export const REASONING_PREVIEW_MIN_CHARS = 40;
/** 低于该字数不显示「N 字」胶囊，避免开流瞬间闪一个「1 字」。 */
export const REASONING_CHAR_COUNT_MIN_VISIBLE = 10;

/** 句读字符：头部摘要优先收在这些位置之后。 */
const SENTENCE_BREAKS = "。！？；!?;";
/**
 * 结论句标记词。
 *
 * 思考正文的开头往往是「先明确口径」这类铺垫，真正有信息量的是中途的转折与结论。
 * 命中这些词的句子比首句更值得当摘要，但只在「长度像一句话」时才采用（见下面两个边界），
 * 过长过短都退回定长截取，避免摘出半句或整段。
 */
const CONCLUSION_MARKERS = ["所以", "结论", "改成", "卡点", "因此", "关键是", "问题在", "说明", "等于"];
/** 结论句可取用的最短长度：太短（「所以呢。」）没有信息量。 */
const CONCLUSION_MIN_CHARS = 12;
/** 结论句可取用的最长长度：超过就当整段，退回定长截取。 */
const CONCLUSION_MAX_CHARS = 46;
/** 尾部跟随的边界字符：在其之后开切，避免从半个词中间切入。 */
const TAIL_BOUNDARY = /[\s，。；！？、,;!?]/;
/** 尾部跟随的边界搜索窗口（字符数）。 */
const TAIL_BOUNDARY_WINDOW = 12;

export interface ReasoningPreview {
  /** 折叠态展示文本；无可展示内容时为空串。 */
  text: string;
  /** 压平后的可见字符数（已去空白与 Markdown 标记）。 */
  charCount: number;
  /** 摘要是否被截断（决定是否加省略号、是否值得挂 title）。 */
  truncated: boolean;
}

/** 剥离块级 Markdown 标记；围栏行、分割线与表格分隔行整行丢弃。 */
function stripBlockMarkers(line: string): string {
  const trimmed = line.trim();
  if (!trimmed) return "";
  // ``` / ~~~ 代码围栏行
  if (/^(`{3,}|~{3,})/.test(trimmed)) return "";
  // --- / *** / ___ 分割线，以及 | --- | --- | 表格分隔行
  if (/^[|\s:-]*-[|\s:-]*$/.test(trimmed)) return "";
  return trimmed
    .replace(/^#{1,6}\s+/, "")
    .replace(/^[-*+]\s+/, "")
    .replace(/^\d+[.)]\s+/, "")
    .replace(/^>\s?/, "");
}

/**
 * 剥离行内 Markdown 标记，只留文字。
 *
 * 刻意不处理 `_` 与单个 `*`：思考正文里的 `analyze_md_charts.py`、`__init__.py`、
 * `*.md` 远比 `_斜体_` / `*斜体*` 常见，剥离下划线系标记会把标识符改写掉。
 */
function stripInlineMarkers(text: string): string {
  return text
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\|/g, " ");
}

/** 压平 Markdown：去标记、折叠空白，得到一行可读纯文本。 */
export function flattenReasoningText(raw: string): string {
  const source = String(raw ?? "");
  if (!source.trim()) return "";
  return source
    .split(/\r?\n/)
    .map((line) => stripInlineMarkers(stripBlockMarkers(line)))
    .filter((line) => line !== "")
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

/** 可见字符数（压平后去掉全部空白）。 */
export function reasoningCharCount(raw: string): number {
  return flattenReasoningText(raw).replace(/\s/g, "").length;
}

/** 头部截取：写满上限，并在下界以上回退到最近的句读之后。 */
function headPreview(flat: string): string {
  const head = flat.slice(0, REASONING_PREVIEW_MAX_CHARS);
  for (let index = head.length - 1; index >= REASONING_PREVIEW_MIN_CHARS; index -= 1) {
    if (SENTENCE_BREAKS.includes(head.charAt(index))) return head.slice(0, index + 1);
  }
  return head;
}

/** 尾部截取：跟随最新内容，并在窗口内回退到边界字符之后。 */
function tailPreview(flat: string): string {
  const tail = flat.slice(-REASONING_PREVIEW_MAX_CHARS);
  const limit = Math.min(TAIL_BOUNDARY_WINDOW, tail.length - 1);
  for (let index = 0; index < limit; index += 1) {
    if (TAIL_BOUNDARY.test(tail.charAt(index))) return tail.slice(index + 1);
  }
  return tail;
}

/** 按句读切句（保留标点），用于挑「结论句」。 */
function splitSentences(flat: string): string[] {
  const sentences: string[] = [];
  let buffer = "";
  for (const char of flat) {
    buffer += char;
    if (SENTENCE_BREAKS.includes(char)) {
      if (buffer.trim()) sentences.push(buffer.trim());
      buffer = "";
    }
  }
  if (buffer.trim()) sentences.push(buffer.trim());
  return sentences;
}

/**
 * 挑一句当摘要：命中标记词、且长度像一句话的句子。
 *
 * 只做「挑句」，不做兜底截断——没有合适的句子就返回 null，由 `headPreview` 按定长截。
 * 两条规则互不叠加，这样「摘出的是完整句」和「实在没好句子就定长截」各自可测。
 */
function conclusionPreview(flat: string): string | null {
  const sentences = splitSentences(flat);
  if (sentences.length < 2) return null;
  for (const sentence of sentences) {
    if (sentence.length < CONCLUSION_MIN_CHARS || sentence.length > CONCLUSION_MAX_CHARS) continue;
    if (CONCLUSION_MARKERS.some((marker) => sentence.includes(marker))) return sentence;
  }
  return null;
}

/** 折叠态摘要：pending 时取尾部（跟随最新内容），否则取头部并回退句读。 */
export function resolveReasoningPreview(
  raw: string,
  options: { pending?: boolean } = {},
): ReasoningPreview {
  const flat = flattenReasoningText(raw);
  if (!flat) return { text: "", charCount: 0, truncated: false };
  const charCount = flat.replace(/\s/g, "").length;
  if (flat.length <= REASONING_PREVIEW_MAX_CHARS) {
    return { text: flat, charCount, truncated: false };
  }
  if (options.pending) {
    return { text: `…${tailPreview(flat)}`, charCount, truncated: true };
  }
  const conclusion = conclusionPreview(flat);
  if (conclusion) {
    return { text: `${conclusion}…`, charCount, truncated: true };
  }
  return { text: `${headPreview(flat)}…`, charCount, truncated: true };
}
