/**
 * 工作空间的文件类型图标。
 *
 * 形状负责区分「目录 / 文档 / 表格 / 图片 / 代码 / 压缩包…」，颜色只做弱提示——
 * 列表里是密排的，颜色一多就显乱，所以只有目录用琥珀色抢一点注意力。
 *
 * 工作空间独立页和聊天侧「浏览工作空间」抽屉共用这一份：它们访问的是同一套
 * `/chat/fs/*` 接口，图标长歪了会很明显（一边新一边旧）。
 */
import type { Component } from "vue"
import {
  ArchiveBoxIcon,
  CircleStackIcon,
  CodeBracketIcon,
  DocumentIcon,
  DocumentTextIcon,
  FilmIcon,
  FolderIcon,
  PhotoIcon,
  PresentationChartBarIcon,
  SpeakerWaveIcon,
  TableCellsIcon,
} from "@heroicons/vue/24/outline"
import { resolveFileTypeVisual } from "./fileTypeVisual"

const ICON_BY_CATEGORY: Record<string, Component> = {
  folder: FolderIcon,
  image: PhotoIcon,
  document: DocumentTextIcon,
  spreadsheet: TableCellsIcon,
  presentation: PresentationChartBarIcon,
  code: CodeBracketIcon,
  archive: ArchiveBoxIcon,
  video: FilmIcon,
  audio: SpeakerWaveIcon,
  data: CircleStackIcon,
  unknown: DocumentIcon,
}

const TONE_BY_CATEGORY: Record<string, string> = {
  folder: "text-amber-500",
  image: "text-sky-500",
  document: "text-slate-400",
  spreadsheet: "text-emerald-500",
  presentation: "text-orange-500",
  code: "text-violet-500",
  archive: "text-amber-600",
  video: "text-rose-500",
  audio: "text-teal-500",
  data: "text-primary",
  unknown: "text-gray-400",
}

/** 按文件名（或「是目录」）取图标组件 */
export const workspaceFileIcon = (name: string, isDir = false): Component =>
  ICON_BY_CATEGORY[resolveFileTypeVisual(name, isDir).category] || DocumentIcon

/** 配套的文字色调 */
export const workspaceFileTone = (name: string, isDir = false): string =>
  TONE_BY_CATEGORY[resolveFileTypeVisual(name, isDir).category] || "text-gray-400"
