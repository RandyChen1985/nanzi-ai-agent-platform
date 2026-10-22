/**
 * 邮箱脱敏：没有编辑权限的列表只展示 `a***@domain`。
 *
 * 保留域名是有意的——列表里要能看出「这个人属于哪个域」，
 * 脱敏只隐藏本地部分；星号固定三个，不泄露原长度。
 */
export function maskEmail(email?: string | null): string {
  const value = String(email ?? "").trim();
  if (!value) return "";
  const at = value.lastIndexOf("@");
  if (at <= 0 || at === value.length - 1) {
    // 不是能拆的邮箱（例如备注里的纯文本），整体遮掉，避免误当明文展示
    return "***";
  }
  const local = value.slice(0, at);
  const domain = value.slice(at + 1);
  return `${local.slice(0, 1)}***@${domain}`;
}
