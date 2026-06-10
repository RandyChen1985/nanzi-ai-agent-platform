/** 剥离流式正文中的推理块 / XML 工具块，避免整段被前端丢弃。 */
export function sanitizeStreamContent(content: string): string {
  if (!content) return "";
  return content
    .replace(/<\s*think\b[^>]*>[\s\S]*?<\s*\/\s*think\s*>/gis, "")
    .replace(/<function_calls>[\s\S]*?<\/function_calls>/gi, "");
}
