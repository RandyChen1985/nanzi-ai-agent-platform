/** Quick 行动按钮：[标签](quick:命令) → 带 quick-action-btn 的 HTML 链接 */

export function encodeQuickTarget(target: string): string {
  const trimmed = target.trim();
  return trimmed.includes("%") ? trimmed : encodeURIComponent(trimmed);
}

export function buildQuickButtonHtml(label: string, target: string): string {
  const encodedTarget = encodeQuickTarget(target);
  return `<a class="quick-action-btn" href="quick:${encodedTarget}">${label.trim()}</a>`;
}

function replaceBalancedQuickMarkdownLinks(text: string): string {
  const marker = /(?:\[|【)([^\]】]+?)(?:\]|】)\s*\(\s*quick:/gi;
  let output = "";
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = marker.exec(text)) !== null) {
    const targetStart = marker.lastIndex;
    let depth = 1;
    let quote = "";
    let targetEnd = -1;

    for (let index = targetStart; index < text.length; index += 1) {
      const char = text[index];
      if (char === "\\") {
        index += 1;
        continue;
      }
      if (quote) {
        if (char === quote) quote = "";
        continue;
      }
      if (char === "'" || char === '"' || char === "`") {
        quote = char;
        continue;
      }
      if (char === "(") {
        depth += 1;
      } else if (char === ")") {
        depth -= 1;
        if (depth === 0) {
          targetEnd = index;
          break;
        }
      }
    }

    if (targetEnd < 0) break;

    output += text.slice(cursor, match.index);
    output += buildQuickButtonHtml(match[1] || "", text.slice(targetStart, targetEnd));
    cursor = targetEnd + 1;
    marker.lastIndex = cursor;
  }

  return output + text.slice(cursor);
}

function stripBalancedQuickMarkdownLinks(text: string): string {
  const marker = /(?:\[|【)([^\]】]+?)(?:\]|】)\s*\(\s*quick:/gi;
  let output = "";
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = marker.exec(text)) !== null) {
    const targetStart = marker.lastIndex;
    let depth = 1;
    let quote = "";
    let targetEnd = -1;

    for (let index = targetStart; index < text.length; index += 1) {
      const char = text[index];
      if (char === "\\") {
        index += 1;
        continue;
      }
      if (quote) {
        if (char === quote) quote = "";
        continue;
      }
      if (char === "'" || char === '"' || char === "`") {
        quote = char;
        continue;
      }
      if (char === "(") {
        depth += 1;
      } else if (char === ")") {
        depth -= 1;
        if (depth === 0) {
          targetEnd = index;
          break;
        }
      }
    }

    if (targetEnd < 0) break;

    output += text.slice(cursor, match.index);
    cursor = targetEnd + 1;
    marker.lastIndex = cursor;
  }

  return output + text.slice(cursor);
}

function lineHasQuickButton(line: string): boolean {
  return (
    /(?:\[|【)[^\]】]*?(?:\]|】)\s*\(\s*<?\s*quick:/i.test(line) ||
    /<a\b[^>]*href=(["'])quick:/i.test(line)
  );
}

function isQuickIntroLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed) return false;
  return (
    /^(?:#{2,6}\s*)?(?:\p{Extended_Pictographic}\uFE0F?\s*)?(?:确认完成后[，,]?\s*)?(?:您)?还可以继续\s*[:：]?$/u.test(trimmed) ||
    /^(?:#{2,6}\s*)?(?:\p{Extended_Pictographic}\uFE0F?\s*)?(?:您可能还想了解|您可以这样继续|一键继续)\s*[:：]?$/u.test(trimmed) ||
    /^(?:您可能还想了解|您可以这样继续|接下来您可以|您还可以继续)\s*[:：]?$/u.test(trimmed)
  );
}

function isMarkdownSeparatorLine(line: string): boolean {
  return /^\s*---\s*$/.test(line);
}

function isEmptyListMarkerLine(line: string): boolean {
  return /^\s*(?:[-*•+]|\d+[.)])\s*$/.test(line);
}

function lookAheadHasQuickLine(lines: string[], fromIndex: number): boolean {
  for (let index = fromIndex; index < lines.length; index += 1) {
    const line = lines[index] || "";
    if (!line.trim() || isMarkdownSeparatorLine(line)) continue;
    return lineHasQuickButton(line);
  }
  return false;
}

function stripQuickLinksInText(text: string): string {
  let processed = text;
  processed = processed.replace(
    /(?:\[|【)([^\]】]+?)(?:\]|】)\s*\(<quick:([\s\S]+?)>\)/gi,
    "",
  );
  processed = stripBalancedQuickMarkdownLinks(processed);
  processed = processed.replace(
    /<a\s+[\s\S]*?href=(["'])quick:([\s\S]*?)\1[\s\S]*?>[\s\S]*?<\/a>/gi,
    "",
  );
  return processed;
}

export function parseQuickButtons(text: string): string {
  if (!text) return "";

  let processed = text;

  // [label](<quick:...>) — 复杂命令（含 >、引号等）推荐此写法
  processed = processed.replace(
    /(?:\[|【)([^\]】]+?)(?:\]|】)\s*\(<quick:([\s\S]+?)>\)/gi,
    (_match, label, target) => buildQuickButtonHtml(label, target),
  );

  // [label](quick:...) — 使用括号深度解析，避免 SQL COUNT(...) 等函数截断 quick 目标
  processed = replaceBalancedQuickMarkdownLinks(processed);

  // AI 直接输出的 HTML：<a href="quick:..."> 或单引号
  processed = processed.replace(
    /<a\s+[\s\S]*?href=(["'])quick:([\s\S]*?)\1[\s\S]*?>([\s\S]*?)<\/a>/gi,
    (match, _quote, target, label) => {
      if (!match.includes("quick-action-btn")) {
        return buildQuickButtonHtml(label, target);
      }
      return match;
    },
  );

  return processed;
}

/**
 * 移除 quick 建议整块（含引导文案、列表项与链接），用于同条消息已有业务确认卡等场景。
 * 不只是隐藏按钮，避免留下「您还可以继续:」和空项目符号。
 */
export function stripQuickButtons(text: string): string {
  if (!text) return "";

  const lines = text.split(/\r?\n/);
  const kept: string[] = [];

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] || "";

    if (lineHasQuickButton(line)) {
      continue;
    }

    if (isQuickIntroLine(line) && lookAheadHasQuickLine(lines, index + 1)) {
      continue;
    }

    if (isMarkdownSeparatorLine(line) && lookAheadHasQuickLine(lines, index + 1)) {
      const prev = kept.length > 0 ? (kept[kept.length - 1] || "") : "";
      if (!prev.trim() || isQuickIntroLine(prev)) {
        continue;
      }
    }

    const cleaned = stripQuickLinksInText(line);
    if (isEmptyListMarkerLine(cleaned)) {
      continue;
    }
    kept.push(cleaned);
  }

  return kept
    .join("\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** 修复 Markdown 引擎转义后的 quick 链接 */
export function postProcessQuickButtonHtml(html: string): string {
  if (!html) return "";
  return html.replace(
    /&lt;a\s+[\s\S]*?href=(?:&quot;|")quick:([\s\S]*?)(?:&quot;|")[\s\S]*?&gt;([\s\S]*?)&lt;\/a&gt;/gi,
    (_match, target, label) => buildQuickButtonHtml(label, target),
  );
}
