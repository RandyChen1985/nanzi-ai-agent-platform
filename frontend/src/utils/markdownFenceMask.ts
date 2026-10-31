/**
 * 代码块分段前的「围栏内反引号屏蔽」。
 *
 * 为什么需要：`MessageRenderer` 用非贪婪正则找 ``` 代码块。当代码内容本身包含三反引号
 * （例如演示「如何包裹含反引号的代码」）时，正则会把内容里的 ``` 当成闭合围栏，
 * 于是代码卡片被腰斩、剩余文本错位、下一段标题被当成语言名。
 *
 * 做法：跑正则前把「围栏内部行」的反引号**等长**替换成占位符，匹配到的捕获组再还原。
 * 等长是关键——`match.index` 仍对应原文，`cleanContent.slice(...)` 不会错位。
 *
 * 围栏边界按 CommonMark：开门行可带 info string，闭合行只能是「同字符 + 长度 ≥ 开门
 * + 除围栏外仅空白」，所以 ```` 包裹的 ``` 不会被误判为闭合。
 */

/** 私用区字符：正文里几乎不可能出现，且与反引号等长（1 字符）。 */
const MASK_CHAR = "\uE000";

const FENCE_LINE = /^\s{0,3}(`{3,}|~{3,})(.*)$/;

function maskLine(line: string): string {
  return line.replace(/`/g, MASK_CHAR);
}

export function maskFenceInnerBackticks(text: string): string {
  if (!text) return text;

  let fenceChar = "";
  let fenceLength = 0;
  const lines = text.split("\n").map((line) => {
    const match = line.match(FENCE_LINE);
    if (match) {
      const marker = match[1] ?? "";
      const rest = (match[2] ?? "").trim();
      if (!fenceChar) {
        fenceChar = marker[0] ?? "";
        fenceLength = marker.length;
        return line;
      }
      if (marker[0] === fenceChar && marker.length >= fenceLength && rest === "") {
        fenceChar = "";
        fenceLength = 0;
        return line;
      }
      return maskLine(line); // 围栏内出现的其它围栏标记 → 内容
    }
    return fenceChar ? maskLine(line) : line;
  });

  return lines.join("\n");
}

export function unmaskFenceInnerBackticks(text: string): string {
  if (!text || !text.includes(MASK_CHAR)) return text;
  return text.split(MASK_CHAR).join("`");
}
