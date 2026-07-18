import { ref } from "vue"
import axios from "@/utils/axios"
import type { WorkbenchHomePayload } from "@/types/workbench"

function stableSnapshot(payload: WorkbenchHomePayload | null | undefined) {
  if (!payload) return ""
  const { generated_at: _generatedAt, ...rest } = payload
  return JSON.stringify(rest)
}

export function useWorkbenchHome() {
  const payload = ref<WorkbenchHomePayload | null>(null)
  const loading = ref(false)
  const refreshing = ref(false)
  const error = ref("")
  const lastRefreshedAt = ref("")

  const load = async (options: { silent?: boolean } = {}) => {
    const silent = Boolean(options.silent && payload.value)
    if (silent) {
      if (refreshing.value) return
      refreshing.value = true
    } else {
      loading.value = true
      error.value = ""
    }

    try {
      const response = await axios.get("/api/portal/workbench/home")
      const next = response.data?.data as WorkbenchHomePayload | undefined
      if (!next) return

      lastRefreshedAt.value = next.generated_at || ""
      // 静默刷新时若业务内容未变，保留原对象，避免整页重绘抖动
      if (silent && payload.value && stableSnapshot(payload.value) === stableSnapshot(next)) {
        error.value = ""
        return
      }
      payload.value = next
      error.value = ""
    } catch {
      error.value = payload.value
        ? "工作台暂时无法更新，已保留最近一次成功内容。"
        : "工作台暂时无法加载，请稍后重试。"
    } finally {
      loading.value = false
      refreshing.value = false
    }
  }

  const refresh = () => load({ silent: true })

  return { payload, loading, refreshing, error, lastRefreshedAt, load, refresh }
}
