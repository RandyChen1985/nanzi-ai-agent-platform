import { normalizeAttachmentExt } from "./attachmentImages";

export type FileTypeCategory =
  | "folder"
  | "image"
  | "document"
  | "spreadsheet"
  | "presentation"
  | "code"
  | "archive"
  | "video"
  | "audio"
  | "data"
  | "unknown";

export interface FileTypeVisual {
  category: FileTypeCategory;
  ext: string;
  icon: string;
  label: string;
  iconBg: string;
  iconRing: string;
  badgeBg: string;
  badgeText: string;
}

const EXT_CATEGORY: Record<string, FileTypeCategory> = {
  png: "image",
  jpg: "image",
  jpeg: "image",
  webp: "image",
  gif: "image",
  svg: "image",
  bmp: "image",
  pdf: "document",
  doc: "document",
  docx: "document",
  txt: "document",
  md: "document",
  rtf: "document",
  pages: "document",
  xls: "spreadsheet",
  xlsx: "spreadsheet",
  csv: "spreadsheet",
  numbers: "spreadsheet",
  ppt: "presentation",
  pptx: "presentation",
  key: "presentation",
  js: "code",
  ts: "code",
  jsx: "code",
  tsx: "code",
  py: "code",
  java: "code",
  go: "code",
  rs: "code",
  cpp: "code",
  c: "code",
  h: "code",
  json: "code",
  yaml: "code",
  yml: "code",
  xml: "code",
  html: "code",
  css: "code",
  sh: "code",
  sql: "code",
  zip: "archive",
  rar: "archive",
  "7z": "archive",
  tar: "archive",
  gz: "archive",
  mp4: "video",
  mov: "video",
  avi: "video",
  mkv: "video",
  webm: "video",
  mp3: "audio",
  wav: "audio",
  flac: "audio",
  aac: "audio",
  parquet: "data",
  db: "data",
  sqlite: "data",
};

const CATEGORY_STYLE: Record<
  FileTypeCategory,
  Omit<FileTypeVisual, "category" | "ext">
> = {
  folder: {
    icon: "📁",
    label: "文件夹",
    iconBg: "bg-amber-100 dark:bg-amber-500/20",
    iconRing: "ring-amber-200/60 dark:ring-amber-500/30",
    badgeBg: "bg-amber-50 dark:bg-amber-500/15",
    badgeText: "text-amber-700 dark:text-amber-300",
  },
  image: {
    icon: "🖼️",
    label: "图片",
    iconBg: "bg-violet-100 dark:bg-violet-500/20",
    iconRing: "ring-violet-200/60 dark:ring-violet-500/30",
    badgeBg: "bg-violet-50 dark:bg-violet-500/15",
    badgeText: "text-violet-700 dark:text-violet-300",
  },
  document: {
    icon: "📄",
    label: "文档",
    iconBg: "bg-blue-100 dark:bg-blue-500/20",
    iconRing: "ring-blue-200/60 dark:ring-blue-500/30",
    badgeBg: "bg-blue-50 dark:bg-blue-500/15",
    badgeText: "text-blue-700 dark:text-blue-300",
  },
  spreadsheet: {
    icon: "📊",
    label: "表格",
    iconBg: "bg-emerald-100 dark:bg-emerald-500/20",
    iconRing: "ring-emerald-200/60 dark:ring-emerald-500/30",
    badgeBg: "bg-emerald-50 dark:bg-emerald-500/15",
    badgeText: "text-emerald-700 dark:text-emerald-300",
  },
  presentation: {
    icon: "📽️",
    label: "演示",
    iconBg: "bg-orange-100 dark:bg-orange-500/20",
    iconRing: "ring-orange-200/60 dark:ring-orange-500/30",
    badgeBg: "bg-orange-50 dark:bg-orange-500/15",
    badgeText: "text-orange-700 dark:text-orange-300",
  },
  code: {
    icon: "💻",
    label: "代码",
    iconBg: "bg-slate-200 dark:bg-slate-600/40",
    iconRing: "ring-slate-300/60 dark:ring-slate-500/30",
    badgeBg: "bg-slate-100 dark:bg-slate-700/50",
    badgeText: "text-slate-700 dark:text-slate-300",
  },
  archive: {
    icon: "🗜️",
    label: "压缩包",
    iconBg: "bg-yellow-100 dark:bg-yellow-500/20",
    iconRing: "ring-yellow-200/60 dark:ring-yellow-500/30",
    badgeBg: "bg-yellow-50 dark:bg-yellow-500/15",
    badgeText: "text-yellow-800 dark:text-yellow-300",
  },
  video: {
    icon: "🎬",
    label: "视频",
    iconBg: "bg-rose-100 dark:bg-rose-500/20",
    iconRing: "ring-rose-200/60 dark:ring-rose-500/30",
    badgeBg: "bg-rose-50 dark:bg-rose-500/15",
    badgeText: "text-rose-700 dark:text-rose-300",
  },
  audio: {
    icon: "🎵",
    label: "音频",
    iconBg: "bg-pink-100 dark:bg-pink-500/20",
    iconRing: "ring-pink-200/60 dark:ring-pink-500/30",
    badgeBg: "bg-pink-50 dark:bg-pink-500/15",
    badgeText: "text-pink-700 dark:text-pink-300",
  },
  data: {
    icon: "🗃️",
    label: "数据",
    iconBg: "bg-cyan-100 dark:bg-cyan-500/20",
    iconRing: "ring-cyan-200/60 dark:ring-cyan-500/30",
    badgeBg: "bg-cyan-50 dark:bg-cyan-500/15",
    badgeText: "text-cyan-700 dark:text-cyan-300",
  },
  unknown: {
    icon: "📎",
    label: "其他",
    iconBg: "bg-gray-100 dark:bg-gray-700/60",
    iconRing: "ring-gray-200/60 dark:ring-gray-600/40",
    badgeBg: "bg-gray-100 dark:bg-gray-800",
    badgeText: "text-gray-600 dark:text-gray-400",
  },
};

const EXT_LABEL: Record<string, string> = {
  pdf: "PDF",
  doc: "Word",
  docx: "Word",
  xls: "Excel",
  xlsx: "Excel",
  csv: "CSV",
  ppt: "PPT",
  pptx: "PPT",
  md: "Markdown",
  txt: "文本",
  json: "JSON",
  py: "Python",
  ts: "TypeScript",
  js: "JavaScript",
  png: "PNG",
  jpg: "JPG",
  jpeg: "JPEG",
  webp: "WebP",
  gif: "GIF",
  zip: "ZIP",
  mp4: "MP4",
};

export function resolveFileTypeVisual(
  filename: string,
  isDir = false,
): FileTypeVisual {
  if (isDir) {
    return { category: "folder", ext: "", ...CATEGORY_STYLE.folder };
  }

  const ext = normalizeAttachmentExt(undefined, filename);
  const category = EXT_CATEGORY[ext] || "unknown";
  const style = CATEGORY_STYLE[category];
  const label = EXT_LABEL[ext] || style.label;

  return {
    category,
    ext,
    ...style,
    label,
  };
}

/** 图例用：去重后的主要类型（不含 unknown） */
export const FILE_TYPE_LEGEND: FileTypeCategory[] = [
  "folder",
  "image",
  "document",
  "spreadsheet",
  "presentation",
  "code",
  "archive",
  "video",
  "audio",
  "data",
];

export function getCategoryStyle(category: FileTypeCategory) {
  return CATEGORY_STYLE[category];
}
