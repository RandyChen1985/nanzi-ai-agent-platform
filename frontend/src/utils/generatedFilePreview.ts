/**
 * AI 产物 → 画布内容的取数层。
 *
 * 判定在 ./generatedFilePreviewPlan.ts（零运行时依赖、可被 Node 直接加载做行为测试）；
 * 本文件只负责「按判定去取内容」，因此允许依赖 axios。
 *
 * 与 workspaceFilePreview.openWorkspaceFileInCanvas 对称：那边处理工作区路径
 * （`/api/v1/chat/fs/preview?path=`），这边处理产物能力链接
 * （`/api/v1/chat/generated-files/{id}?token=`）。
 *
 * 两个关键口径：
 *  - Office / PDF **不预取 Blob**，只把鉴权 URL 交给 DocumentViewer（Blob 进响应式
 *    状态会带来无谓开销，也会打乱画布的单槽对象 URL 机制）；
 *  - 图片 / CSV 取 Blob 后把对象 URL 写回调用方的单槽 ref，回收由调用方负责。
 */
import axios from '@/utils/axios'
import type { CanvasPanelData } from '@/types/canvas'
import { resolveDocumentViewerMime } from '@/utils/documentPreviewFormats'
import { resolveGeneratedPreviewPlan } from '@/utils/generatedFilePreviewPlan'

type ShowToast = (message: string, type?: 'success' | 'error' | 'warning' | 'info') => void

export interface OpenGeneratedFileOptions {
  /** 已绑定当前页面 Host 的产物鉴权地址 */
  url: string
  /** 产物文件名：用于展示，也是画布按扩展名选择渲染器的依据 */
  name: string
  showToast: ShowToast
  onOpen: (data: CanvasPanelData) => void
  /** 画布的单槽对象 URL，由调用方统一回收 */
  activeBlobUrlRef?: { value: string }
}

const asString = (value: unknown) => (typeof value === 'string' ? value : String(value ?? ''))

/** 复用后端 detail 文案；404 明确提示过期（token TTL 30 天） */
export const describeGeneratedFileError = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (error?.response?.status === 404) return '文件不存在或已过期'
  return error?.message || fallback
}

/**
 * 在画布中打开一个 AI 产物。
 *
 * @returns true = 已打开预览；false = 未打开（不支持预览 / 缺地址 / 取内容失败），
 *          调用方据此决定是否改走下载。
 */
export async function openGeneratedFileInCanvas(
  options: OpenGeneratedFileOptions,
): Promise<boolean> {
  const { url, name, showToast, onOpen, activeBlobUrlRef } = options
  const plan = resolveGeneratedPreviewPlan(name)

  if (!url) {
    showToast('该产出物缺少下载地址', 'warning')
    return false
  }

  if (plan.kind === 'download-only') {
    // 抽屉已在 emit 前拦掉这一类（见设计 4.1）；此处是给其他调用方的防御性兜底
    showToast('该格式暂不支持预览，已为你下载原文件', 'info')
    return false
  }

  try {
    if (plan.kind === 'url-document') {
      const isPdf = plan.canvasType === 'pdf'
      onOpen({
        type: isPdf ? 'pdf' : 'document',
        title: name,
        content: url,
        documentMeta: {
          filename: name,
          mime: isPdf ? 'application/pdf' : resolveDocumentViewerMime(name),
        },
      })
      return true
    }

    if (plan.kind === 'blob') {
      const response = await axios.get(url, { responseType: 'blob' })
      // 采用新对象 URL 之前先回收旧值：与 openWorkspaceFileInCanvas 同一顺序，
      // 避免单槽 ref 被覆盖后旧对象 URL 永久泄漏。
      if (activeBlobUrlRef?.value) {
        try {
          URL.revokeObjectURL(activeBlobUrlRef.value)
        } catch {
          /* ignore */
        }
        activeBlobUrlRef.value = ''
      }
      const blobUrl = URL.createObjectURL(response.data as Blob)
      if (activeBlobUrlRef) activeBlobUrlRef.value = blobUrl
      onOpen({
        type: plan.canvasType === 'csv' ? 'csv' : 'image',
        title: name,
        content: blobUrl,
      })
      return true
    }

    const response = await axios.get(url, { responseType: 'text' })
    onOpen({
      type: plan.canvasType === 'html' ? 'html' : 'code',
      title: name,
      content: asString(response.data),
    })
    return true
  } catch (error: any) {
    console.error('加载 AI 产物失败:', error)
    showToast(describeGeneratedFileError(error, '加载产出物失败'), 'error')
    return false
  }
}
