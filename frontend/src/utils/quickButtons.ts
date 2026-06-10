/** Quick 行动按钮：[标签](quick:命令) → 带 quick-action-btn 的 HTML 链接 */

export function encodeQuickTarget(target: string): string {
  const trimmed = target.trim();
  return trimmed.includes("%") ? trimmed : encodeURIComponent(trimmed);
}

export function buildQuickButtonHtml(label: string, target: string): string {
  const encodedTarget = encodeQuickTarget(target);
  return `<a class="quick-action-btn" href="quick:${encodedTarget}">${label.trim()}</a>`;
}

export function parseQuickButtons(text: string): string {
  if (!text) return "";

  let processed = text;

  // [label](<quick:...>) — 复杂命令（含 >、引号等）推荐此写法
  processed = processed.replace(
    /(?:\[|【)([^\]】]+?)(?:\]|】)\s*\(<quick:([\s\S]+?)>\)/gi,
    (_match, label, target) => buildQuickButtonHtml(label, target),
  );

  // [label](quick:...) — 匹配到闭合 ) 为止，允许 >、引号、空格
  processed = processed.replace(
    /(?:\[|【)([^\]】]+?)(?:\]|】)\s*\(quick:([^)]+)\)/gi,
    (_match, label, target) => buildQuickButtonHtml(label, target),
  );

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

/** 修复 Markdown 引擎转义后的 quick 链接 */
export function postProcessQuickButtonHtml(html: string): string {
  if (!html) return "";
  return html.replace(
    /&lt;a\s+[\s\S]*?href=(?:&quot;|")quick:([\s\S]*?)(?:&quot;|")[\s\S]*?&gt;([\s\S]*?)&lt;\/a&gt;/gi,
    (_match, target, label) => buildQuickButtonHtml(label, target),
  );
}
