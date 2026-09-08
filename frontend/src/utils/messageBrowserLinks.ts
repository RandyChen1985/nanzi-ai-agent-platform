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

/**
 * 内部 API 与静态资源路径判定正则：
 * 匹配以 /api/、/static/、/assets/ 开头的路径，例如：
 * - /api/v1/chat/generated-files/...
 * - /api/v1/ch...
 * - /static/uploads/...
 */
const INTERNAL_PATH_PATTERN = /^\/(?:api|static|assets)(?:\/|$)/i;

/**
 * 判断给定的 URL 字符串是否属于平台内部地址或 API 接口
 */
export const isInternalUrl = (value: string | null | undefined): boolean => {
  const raw = String(value || '').trim();
  if (!raw) return false;

  // 1. 相对路径形式（如 /api/v1/chat/...）
  if (raw.startsWith('/') || !/^[a-z][a-z0-9+.-]*:/i.test(raw)) {
    const normalized = raw.startsWith('/') ? raw : `/${raw}`;
    return INTERNAL_PATH_PATTERN.test(normalized);
  }

  // 2. 带有协议的绝对 URL（如 http(s)://...）
  try {
    const url = new URL(raw);
    // 内部 API 或静态资源路径
    if (INTERNAL_PATH_PATTERN.test(url.pathname)) {
      return true;
    }
    // 同源/站内地址（当前站点自身的主机名）
    if (typeof window !== 'undefined' && window.location?.host) {
      if (url.host.toLowerCase() === window.location.host.toLowerCase()) {
        return true;
      }
    }
  } catch {
    return false;
  }

  return false;
};

export const isBrowserOpenableUrl = (value: string | null | undefined) => {
  const raw = String(value || '').trim();
  if (!/^https?:\/\//i.test(raw)) return false;
  try {
    const url = new URL(raw);
    const protocol = url.protocol;
    if (protocol !== 'http:' && protocol !== 'https:') return false;

    // 内部地址（包括内部 API、生成文件下载端点及同源站内地址）禁止在右侧网页预览抽屉中打开
    if (isInternalUrl(raw)) return false;

    return true;
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
