import { ref } from "vue";
import axios from "@/utils/axios";

export interface ContextBreakdown {
  system_prompt_tokens: number;
  tools_tokens: number;
  conversation_tokens: number;
  total_tokens: number;
  estimated: boolean;
  source: string;
}

export interface ContextUsage {
  estimated_current_tokens: number | null;
  estimated_remaining_tokens: number | null;
  context_messages: number | null;
  token_budget: number | null;
  physical_window: number | null;
  history_budget: number | null;
  completion_reserve_tokens: number | null;
  request_input_budget: number | null;
  prompt_overhead_reservation_tokens?: number | null;
  overhead_reservation_tokens?: number | null;
  usage_percentage: number | null;
  context_breakdown?: ContextBreakdown | null;
  sandbox_policy?: string | null;
  sandbox_runtime_env?: string | null;
  sandbox_auto_warm?: boolean | null;
}

export interface RefreshContextUsageOptions {
  conversationId?: string | null;
  modelId?: string | null;
  headers?: Record<string, string>;
}

export const formatContextTokens = (value: number | null | undefined): string => {
  const tokens = Number(value || 0);
  if (!Number.isFinite(tokens) || tokens <= 0) return "0";
  if (tokens < 1000) return Math.round(tokens).toLocaleString();
  // 模型窗口通常按 2 的幂配置（65536 = 64k），其余预算沿用十进制近似，
  // 这样既不会把 64k 显示成 66k，也能保留 49k、41k 这类水位线读法。
  if (tokens >= 65536 && tokens % 1024 === 0) {
    return `${Math.round(tokens / 1024)}k`;
  }
  const digits = tokens >= 10000 ? 0 : 1;
  return `${(tokens / 1000).toFixed(digits).replace(/\.0$/, "")}k`;
};

export function useContextUsage() {
  const contextUsage = ref<ContextUsage | null>(null);
  const contextUsageLoading = ref(false);
  let latestRequestId = 0;

  const refreshContextUsage = async (options: RefreshContextUsageOptions = {}) => {
    const conversationId = String(options.conversationId || "").trim();
    const requestId = ++latestRequestId;

    if (!conversationId) {
      contextUsage.value = null;
      contextUsageLoading.value = false;
      return;
    }

    contextUsageLoading.value = true;
    try {
      const params = options.modelId ? { model_id: options.modelId } : undefined;
      const response = await axios.get(
        `/api/v1/chat/conversation/${encodeURIComponent(conversationId)}/context-usage`,
        { params, headers: options.headers },
      );
      if (requestId === latestRequestId) {
        contextUsage.value = response.data?.data || null;
      }
    } catch (error) {
      if (requestId === latestRequestId) {
        contextUsage.value = null;
      }
      console.warn("[ContextUsage] 获取上下文使用情况失败", error);
    } finally {
      if (requestId === latestRequestId) {
        contextUsageLoading.value = false;
      }
    }
  };

  return {
    contextUsage,
    contextUsageLoading,
    refreshContextUsage,
  };
}
