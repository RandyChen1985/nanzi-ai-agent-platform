/**
 * 会话分享文案：把「主题 + 链接 + 密码」拼成一段可以直接转发的话。
 *
 * 需求来源：只复制链接，对方还得再回来问密码；分开复制两次又容易漏。
 * 这里刻意输出**纯文本**（不带 Markdown / 富文本）：微信、飞书、钉钉里粘贴成
 * 一行行纯文字才不会错位或丢格式。
 *
 * 两个约定：
 * - 公开分享没有密码行，不要留一个空的「密码：」，否则对方会以为漏发了；
 * - 主题取会话首问，超长由后端截到 120 字，这里再做一次空白归一，
 *   避免正文里的换行把文案撑成一大段。
 */

export interface ShareCopyInput {
  /** 会话主题（后端 title，可能为空）。 */
  title?: string | null;
  /** 分享地址，必填；为空时返回空串，调用方据此禁用复制。 */
  url: string;
  /** 访问密码；公开分享传 null/undefined，则不出密码行。 */
  secret?: string | null;
}

/** 主题缺失时的兜底，避免出现「主题：」后面什么都没有。 */
export const FALLBACK_SHARE_TITLE = '未命名会话';

export function buildShareCopyText(input: ShareCopyInput): string {
  const url = String(input.url || '').trim();
  if (!url) return '';

  // 连续空白（含换行、制表）压成一个空格，防止多行主题破坏排版
  const title = String(input.title || '').replace(/\s+/g, ' ').trim() || FALLBACK_SHARE_TITLE;
  const secret = String(input.secret || '').trim();

  const lines = ['快来看我分享的会话', `主题：${title}`, `链接：${url}`];
  if (secret) lines.push(`密码：${secret}`);
  return lines.join('\n');
}
