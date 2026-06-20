const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "webp", "gif"]);

export function normalizeAttachmentExt(ext?: string, url?: string): string {
  let normalized = (ext || "").toLowerCase().replace(/^\./, "");
  if (!normalized && url) {
    const path = url.split("?")[0] || "";
    const segment = path.split("/").pop() || "";
    const dot = segment.lastIndexOf(".");
    normalized = dot >= 0 ? segment.slice(dot + 1).toLowerCase() : "";
  }
  return normalized;
}

export function isImageAttachment(file: {
  ext?: string;
  url?: string;
}): boolean {
  return IMAGE_EXTS.has(normalizeAttachmentExt(file.ext, file.url));
}

/** 输入框/气泡内图片缩略图 URL（本地上传 vs 服务器文件） */
export function getAttachmentPreviewUrl(file: {
  url?: string;
  type?: string;
  ext?: string;
}): string | null {
  if (!file.url || !isImageAttachment(file)) return null;
  if (file.url.startsWith("/static/uploads/") || /^https?:\/\//.test(file.url)) {
    return file.url;
  }
  if (file.type === "local_file") {
    return `/api/v1/chat/fs/preview?path=${encodeURIComponent(file.url)}`;
  }
  return null;
}
