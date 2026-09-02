const decodeHtmlAttribute = (value: string) => value
  .replace(/&quot;/gi, '"')
  .replace(/&#39;|&apos;/gi, "'")
  .replace(/&lt;/gi, '<')
  .replace(/&gt;/gi, '>')
  .replace(/&amp;/gi, '&');

const escapeHtmlAttribute = (value: string) => value
  .replace(/&/g, '&amp;')
  .replace(/"/g, '&quot;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;');

const buildBrowserOpenAction = (href: string) => {
  const escaped = escapeHtmlAttribute(href);
  return `<button type="button" class="message-link-open" data-open-browser-url="${escaped}" title="在右侧浏览器打开">打开</button>`;
};

export const isBrowserOpenableUrl = (value: string | null | undefined) => {
  const raw = String(value || '').trim();
  if (!/^https?:\/\//i.test(raw)) return false;
  try {
    const protocol = new URL(raw).protocol;
    return protocol === 'http:' || protocol === 'https:';
  } catch {
    return false;
  }
};

export const appendBrowserOpenActions = (html: string) => html.replace(
  /(<a\b[^>]*>[\s\S]*?<\/a>)(\s*<button\b[^>]*data-open-browser-url=(['"])[\s\S]*?\3[^>]*>[\s\S]*?<\/button>)?/gi,
  (fullMatch, anchor, existingAction = '') => {
    if (existingAction) return fullMatch;
    const match = anchor.match(/\bhref=(['"])(.*?)\1/i);
    const href = decodeHtmlAttribute(match?.[2] || '');
    if (!isBrowserOpenableUrl(href)) return anchor;
    return `${anchor}${buildBrowserOpenAction(href)}`;
  },
);

export const appendBrowserOpenActionsToCode = (html: string) => html.replace(
  /(<code\b[^>]*>)([\s\S]*?)(<\/code>)(\s*<button\b[^>]*data-open-browser-url=(['"])[\s\S]*?\5[^>]*>[\s\S]*?<\/button>)?/gi,
  (fullMatch, opening, inner, closing, existingAction = '') => {
    if (existingAction || /<[^>]+>/.test(inner)) return fullMatch;
    const href = decodeHtmlAttribute(inner.trim());
    if (!isBrowserOpenableUrl(href)) return fullMatch;
    return `${opening}${inner}${closing}${buildBrowserOpenAction(href)}`;
  },
);
