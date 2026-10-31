/**
 * 工具导出：两个 tab 共用的「拼请求 → 下载 → 提示」。
 *
 * 抽出来是因为 API 工具与脚本工具的导出行为必须完全一致（同样的文件名规则、
 * 同样的错误解析）；各写一份迟早出现「一边能下载、另一边点了没反应」。
 */
import { ref } from 'vue'
import { toolBundleApi, type ToolBundleKind } from '@/api/tool'
import {
  defaultBundleFilename,
  downloadBlob,
  readBundleError,
  resolveDownloadFilename,
} from '@/utils/toolBundle'
import { useToast } from '@/composables/useToast'

/**
 * GET 导出把 ID 拼在 URL 上，超过这个数量就可能撞 8KB 的请求行上限。
 * 与其让浏览器报一个看不懂的 414，不如让用户先缩小筛选范围。
 */
const MAX_EXPORT_IDS_IN_QUERY = 150

export function useToolBundleExport() {
  const { showToast } = useToast()
  const exporting = ref(false)

  async function exportBundle(kind: ToolBundleKind, ids: string[]) {
    if (!ids.length) {
      showToast('没有可导出的工具', 'warning')
      return false
    }
    if (ids.length > MAX_EXPORT_IDS_IN_QUERY) {
      showToast(
        `当前范围有 ${ids.length} 个工具，超过单次导出上限（${MAX_EXPORT_IDS_IN_QUERY} 个），请先缩小筛选范围或只勾选要导出的工具`,
        'warning',
      )
      return false
    }
    exporting.value = true
    try {
      const res = await toolBundleApi.exportBundle({ kind, ids })
      const disposition = res.headers?.['content-disposition'] as string | undefined
      const filename = resolveDownloadFilename(disposition, defaultBundleFilename(kind))
      downloadBlob(res.data as Blob, filename)
      showToast(`已导出 ${ids.length} 个工具，文件名 ${filename}`, 'success')
      return true
    } catch (error: any) {
      showToast(`导出失败：${await readBundleError(error)}`, 'error')
      return false
    } finally {
      exporting.value = false
    }
  }

  return { exporting, exportBundle }
}
