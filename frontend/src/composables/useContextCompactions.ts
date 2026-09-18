import { computed, ref } from "vue";
import { agentApi, type ContextCompactionRecord } from "@/api/agent";

export interface RefreshContextCompactionsOptions {
  conversationId?: string | null;
  headers?: Record<string, string>;
}

export interface ManualContextCompactionOptions extends RefreshContextCompactionsOptions {
  retainRatio?: 0.25 | 0.5 | 0.75;
  mode?: "fast" | "smart";
}

/**
 * 一条压缩记录是否应该算作"用户可见的一次压缩"。
 *
 * `stage === "pre_route"` 的压缩是路由前的中间态：它只是为了给路由阶段一个不超窗的
 * 上下文，其窗口随后会被路由后的 `resolved_model` 阶段按目标模型的真实窗口重算——
 * 窗口按"从最新往回取"，阶段②的窗口必然包含阶段①的窗口，所以阶段①的结果永远不会是
 * 最终状态。若把它也计入，同一轮会数出两次压缩（卡片同样会多一张），而真实只压缩了
 * 一次。其余阶段（`resolved_model` / `manual` / `agent_runtime`）都是真实生效的压缩。
 *
 * 记录本身仍然保留（后端排障用），只是不参与用户可见的计数与卡片渲染。
 */
export function isUserVisibleContextCompaction(
  record: Pick<ContextCompactionRecord, "event_type" | "stage">,
): boolean {
  if (record.stage === "pre_route") return false;
  return (
    record.event_type === "context_summarized"
    || record.event_type === "context_compression"
  );
}

/**
 * 读取当前会话的上下文压缩记录。
 *
 * 压缩记录是辅助观测数据，和消息流、上下文使用量相互隔离；请求切换会话
 * 时通过 request id 丢弃旧响应，避免快速切换把上一会话的次数带到当前会话。
 */
export function useContextCompactions() {
  const contextCompactions = ref<ContextCompactionRecord[]>([]);
  const contextCompactionsLoading = ref(false);
  const contextCompactionsError = ref(false);
  const contextCompactionActionLoading = ref(false);
  const contextCompactionsLoadedFor = ref("");
  let latestRequestId = 0;

  const contextCompactionCount = computed(
    () => contextCompactions.value.filter(isUserVisibleContextCompaction).length,
  );

  const refreshContextCompactions = async (
    options: RefreshContextCompactionsOptions = {},
    force = false,
  ) => {
    const conversationId = String(options.conversationId || "").trim();
    const requestId = ++latestRequestId;

    if (!conversationId) {
      contextCompactions.value = [];
      contextCompactionsLoadedFor.value = "";
      contextCompactionsError.value = false;
      contextCompactionsLoading.value = false;
      return;
    }

    if (
      !force
      && contextCompactionsLoadedFor.value === conversationId
      && !contextCompactionsError.value
    ) {
      return;
    }

    contextCompactionsLoading.value = true;
    contextCompactionsError.value = false;
    try {
      const response = await agentApi.getContextCompactions(conversationId, {
        headers: options.headers,
      });
      if (requestId !== latestRequestId) return;
      const records = response.data?.data?.records;
      contextCompactions.value = Array.isArray(records) ? records : [];
      contextCompactionsLoadedFor.value = conversationId;
    } catch (error) {
      if (requestId !== latestRequestId) return;
      console.warn("Failed to fetch context compactions", error);
      contextCompactions.value = [];
      contextCompactionsLoadedFor.value = "";
      contextCompactionsError.value = true;
    } finally {
      if (requestId === latestRequestId) {
        contextCompactionsLoading.value = false;
      }
    }
  };

  const manuallyCompactContext = async (options: ManualContextCompactionOptions = {}) => {
    const conversationId = String(options.conversationId || "").trim();
    if (!conversationId || contextCompactionActionLoading.value) return null;
    contextCompactionActionLoading.value = true;
    try {
      const response = await agentApi.manualContextCompaction(
        conversationId,
        options.retainRatio ?? 0.5,
        options.mode ?? "fast",
        { headers: options.headers },
      );
      await refreshContextCompactions(options, true);
      return response.data?.data || null;
    } finally {
      contextCompactionActionLoading.value = false;
    }
  };

  return {
    contextCompactions,
    contextCompactionCount,
    contextCompactionsLoading,
    contextCompactionsError,
    refreshContextCompactions,
    contextCompactionActionLoading,
    manuallyCompactContext,
  };
}
