import { onUnmounted, ref } from "vue";

export interface ConversationRunStatus {
  active: boolean;
  trace_id?: string | null;
  ttl_seconds?: number | null;
  /**
   * 后端是否给出了可信结论。
   *
   * Redis 抖动 / 配置读取失败时后端 `get_status` 会 fail-open 返回 active=false——
   * 那和「本轮确实已结束」在旧协议里完全一样，前端会据此提前收手，把仍被占用的
   * 输入框解锁、并停止恢复器。带 available=false 后前端可以保留上次锁态并退避重试。
   */
  available?: boolean;
}

export type ConversationRunStatusFetcher = (
  conversationId: string,
) => Promise<ConversationRunStatus | { data?: ConversationRunStatus | { data?: ConversationRunStatus } }>;

const EMPTY_STATUS: ConversationRunStatus = {
  active: false,
  trace_id: null,
  ttl_seconds: null,
  available: true,
};

/**
 * 查不到运行态时的重试间隔。
 *
 * 查询失败不等于运行结束：必须保留上一次的锁态并继续重试，否则一次网络抖动就会让
 * 恢复器/输入锁一起消失。间隔递增，避免后端故障时打成风暴。
 */
const RETRY_DELAYS_MS = [1500, 3000, 5000, 8000, 10000];

const normalizeStatus = (
  response: ConversationRunStatus | { data?: ConversationRunStatus | { data?: ConversationRunStatus } },
): ConversationRunStatus => {
  const first = (response as { data?: unknown })?.data ?? response;
  const raw = ((first as { data?: unknown })?.data ?? first) as Partial<ConversationRunStatus> | null | undefined;
  // 只有明确带 active 布尔值的载荷才算「结论」；空对象/异常包络一律视为未知。
  const hasActive = typeof raw?.active === "boolean";
  return {
    active: hasActive ? raw!.active === true : false,
    trace_id: typeof raw?.trace_id === "string" ? raw.trace_id : null,
    ttl_seconds: typeof raw?.ttl_seconds === "number" ? raw.ttl_seconds : null,
    available: hasActive ? raw?.available !== false : false,
  };
};

export function createConversationRunStatusController(
  fetchStatus: ConversationRunStatusFetcher,
  pollIntervalMs = 1500,
) {
  // 锁真相：只反映后端会话运行锁。输出完成后它仍要为「收敛落库终态」保留这条通道，
  // 因此不得被 outputCompleted 压制——否则恢复器与 run-status 轮询会一起停摆。
  const remoteRunActive = ref(false);
  // 输入是否仍被本轮占住：与锁真相解耦。输出完成即可解锁输入，但收敛照常进行。
  const remoteRunBusy = ref(false);
  /** 后端是否给出过可信结论；false 表示查询失败 / Redis 不可用，当前锁态是旧值。 */
  const statusUnknown = ref(true);
  const status = ref<ConversationRunStatus>({ ...EMPTY_STATUS });
  // 输出已结束但后台仍可能在做历史持久化时，允许前端准备下一轮输入。
  let outputCompleted = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let requestSequence = 0;
  let currentConversationId = "";
  let consecutiveFailures = 0;

  const stopPolling = () => {
    requestSequence += 1;
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const scheduleNextPoll = (conversationId: string) => {
    if (timer || !status.value.active || conversationId !== currentConversationId) return;
    timer = setTimeout(() => {
      timer = null;
      void refresh(conversationId);
    }, pollIntervalMs);
  };

  const scheduleRetry = (conversationId: string) => {
    if (timer || !conversationId || conversationId !== currentConversationId) return;
    const delay = RETRY_DELAYS_MS[Math.min(Math.max(consecutiveFailures - 1, 0), RETRY_DELAYS_MS.length - 1)];
    timer = setTimeout(() => {
      timer = null;
      void refresh(conversationId);
    }, delay);
  };

  const markUnknown = (conversationId: string) => {
    consecutiveFailures += 1;
    statusUnknown.value = true;
    status.value = { ...status.value, available: false };
    // 关键：不清 remoteRunActive / remoteRunBusy。查询失败不是「运行结束」的证据。
    scheduleRetry(conversationId);
  };

  const refresh = async (conversationId: string): Promise<boolean> => {
    const normalizedConversationId = String(conversationId || "").trim();
    currentConversationId = normalizedConversationId;
    const sequence = ++requestSequence;
    if (!normalizedConversationId) {
      stopPolling();
      remoteRunActive.value = false;
      remoteRunBusy.value = false;
      statusUnknown.value = false;
      consecutiveFailures = 0;
      status.value = { ...EMPTY_STATUS };
      return false;
    }

    try {
      const nextStatus = normalizeStatus(await fetchStatus(normalizedConversationId));
      if (sequence !== requestSequence || normalizedConversationId !== currentConversationId) {
        return remoteRunActive.value;
      }
      if (nextStatus.available === false) {
        markUnknown(normalizedConversationId);
        return remoteRunActive.value;
      }
      consecutiveFailures = 0;
      statusUnknown.value = false;
      status.value = nextStatus;
      if (!nextStatus.active) outputCompleted = false;
      remoteRunActive.value = nextStatus.active;
      remoteRunBusy.value = nextStatus.active && !outputCompleted;
      if (nextStatus.active) scheduleNextPoll(normalizedConversationId);
      else stopPolling();
      return nextStatus.active;
    } catch {
      if (sequence === requestSequence && normalizedConversationId === currentConversationId) {
        markUnknown(normalizedConversationId);
      }
      return remoteRunActive.value;
    }
  };

  const startPolling = (conversationId: string) => {
    stopPolling();
    outputCompleted = false;
    remoteRunBusy.value = false;
    consecutiveFailures = 0;
    void refresh(conversationId);
  };

  /**
   * 输出已完成（收到 run_status 终态帧 / 重试耗尽兜底）。
   *
   * 只解锁输入：后端此刻往往还在落库（finalize 先发终态帧、之后才写库），所以
   * `remoteRunActive` 必须保持锁真相，让恢复器继续按 run-status 轮询收敛终态。
   * 早前把两者一起置 false，导致切回页面后恢复器在续显重放到终态帧的瞬间自断，
   * 落库慢于最后一次历史同步时界面永久停在草稿、只能刷新页面。
   */
  const markOutputCompleted = () => {
    outputCompleted = true;
    remoteRunBusy.value = false;
  };

  /** 新一轮开始：乐观置锁，并复位上一轮的「输出已完成」，避免它压制本轮恢复。 */
  const markRunStarted = () => {
    outputCompleted = false;
    statusUnknown.value = false;
    remoteRunActive.value = true;
    remoteRunBusy.value = true;
  };

  /**
   * 整体复位运行态（切换身份 / 清空会话）。
   *
   * 必须连 outputCompleted 一起清掉：它标记的是「本轮输出已完成」，而重置之后会有
   * 新一轮——或切页面回来后仍在跑的那一轮——需要重新锁住输入。若这里复用
   * markOutputCompleted，完成标记会残留到新会话上，使后续每次「仍在跑」的查询都
   * 把输入锁判成 false：恢复器活着（思考卡片在动）、输入框却始终不锁。
   */
  const resetRunStatus = () => {
    outputCompleted = false;
    remoteRunActive.value = false;
    remoteRunBusy.value = false;
    statusUnknown.value = true;
    consecutiveFailures = 0;
    status.value = { ...EMPTY_STATUS };
  };

  const isPolling = () => timer !== null;

  return {
    remoteRunActive,
    remoteRunBusy,
    statusUnknown,
    status,
    refresh,
    startPolling,
    markOutputCompleted,
    markRunStarted,
    resetRunStatus,
    stopPolling,
    isPolling,
  };
}

export function useConversationRunStatus(fetchStatus: ConversationRunStatusFetcher) {
  const controller = createConversationRunStatusController(fetchStatus);
  onUnmounted(controller.stopPolling);
  return controller;
}
