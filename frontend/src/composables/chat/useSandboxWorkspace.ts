import { ref, computed, watch, onUnmounted, type Ref } from "vue";
import axios from "axios";

export type SandboxWorkspaceStatus = "idle" | "starting" | "stopping" | "running" | "error";

export interface UseSandboxWorkspaceOptions {
  conversationId: Ref<string | null | undefined>;
  contextUsage: Ref<{ sandbox_policy?: string; sandbox_auto_warm?: boolean } | null | undefined>;
  /** 凭据渲染回调；门户认证已由同源 HttpOnly Cookie 承载，调用方可不再提供 */
  authHeaders?: () => Record<string, string> | undefined;
  isProcessing?: Ref<boolean>;
  remoteRunActive?: Ref<boolean>;
  showToast: (msg: string, type?: "success" | "error" | "info" | "warning") => void;
}

export function useSandboxWorkspace(options: UseSandboxWorkspaceOptions) {
  const {
    conversationId,
    contextUsage,
    authHeaders,
    isProcessing = ref(false),
    remoteRunActive = ref(false),
    showToast,
  } = options;

  const sandboxWorkspaceStatus = ref<SandboxWorkspaceStatus>("idle");
  const sandboxWorkspaceStatusLoaded = ref(false);
  const sandboxWorkspaceError = ref("");
  const sandboxWorkspaceInstanceId = ref<string | null>(null);
  const sandboxWorkspaceStartedAt = ref<string | null>(null);
  const sandboxWorkspaceUptimeSeconds = ref<number | null>(null);
  const showSandboxStopConfirm = ref(false);
  const showDockerTerminal = ref(false);
  const showK8sTerminal = ref(false);

  const effectiveSandboxPolicy = computed(() =>
    String(contextUsage.value?.sandbox_policy || "").trim().toLowerCase(),
  );

  const sandboxBackend = computed<"docker" | "k8s">(() =>
    effectiveSandboxPolicy.value === "k8s" ? "k8s" : "docker",
  );

  const isSandboxWorkspacePolicy = computed(() => {
    const policy = effectiveSandboxPolicy.value;
    return policy === "docker" || policy === "k8s";
  });

  const sandboxWorkspaceBaseEndpoint = computed(() =>
    sandboxBackend.value === "k8s"
      ? "/api/v1/sandbox/k8s/workspace"
      : "/api/v1/sandbox/docker/workspace",
  );

  const instanceIdFromData = (data: any): string | null =>
    sandboxBackend.value === "k8s"
      ? (data?.pod_name ?? null)
      : (data?.container_id ?? null);

  const mapSandboxStatus = (raw: string): SandboxWorkspaceStatus => {
    if (raw === "running") return "running";
    if (raw === "starting") return "starting";
    if (raw === "stopping") return "stopping";
    if (raw === "error") return "error";
    return "idle";
  };

  const resetSandboxWorkspaceState = () => {
    sandboxWorkspaceStatus.value = "idle";
    sandboxWorkspaceStatusLoaded.value = false;
    sandboxWorkspaceError.value = "";
    sandboxWorkspaceInstanceId.value = null;
    sandboxWorkspaceStartedAt.value = null;
    sandboxWorkspaceUptimeSeconds.value = null;
  };

  let sandboxStatusRefreshInFlight = false;

  const refreshSandboxWorkspaceStatus = async (showFeedback = false) => {
    if (!isSandboxWorkspacePolicy.value || !conversationId.value) return;
    if (sandboxStatusRefreshInFlight) return;
    sandboxStatusRefreshInFlight = true;
    const requestedConversationId = conversationId.value;
    try {
      const response = await axios.get(
        `${sandboxWorkspaceBaseEndpoint.value}/status`,
        {
          params: { conversation_id: requestedConversationId },
          headers: authHeaders?.(),
        },
      );
      if (conversationId.value !== requestedConversationId) return;
      const data = response.data?.data ?? response.data;
      sandboxWorkspaceInstanceId.value = instanceIdFromData(data);
      sandboxWorkspaceStartedAt.value = data?.started_at || null;
      sandboxWorkspaceUptimeSeconds.value = typeof data?.uptime_seconds === "number" ? data.uptime_seconds : null;
      sandboxWorkspaceStatus.value = mapSandboxStatus(String(data?.status || "idle"));
      sandboxWorkspaceError.value = "";
      if (showFeedback) {
        if (sandboxWorkspaceStatus.value === "running") {
          const shortId = (sandboxWorkspaceInstanceId.value || "").slice(0, 12);
          showToast(shortId ? `沙箱运行中 (${shortId})` : "沙箱运行中", "success");
        } else if (sandboxWorkspaceStatus.value === "starting") {
          showToast("沙箱启动中，请稍候...", "info");
        } else {
          showToast("沙箱状态已刷新：尚未启动", "info");
        }
      }
    } catch (error: any) {
      if (conversationId.value !== requestedConversationId) return;
      const detail = error?.response?.data?.detail;
      sandboxWorkspaceError.value = typeof detail === "string"
        ? detail
        : String(detail?.message || error?.message || "沙箱状态查询失败");
      sandboxWorkspaceStatus.value = "error";
      sandboxWorkspaceStartedAt.value = null;
      sandboxWorkspaceUptimeSeconds.value = null;
      if (showFeedback) {
        showToast(sandboxWorkspaceError.value, "error");
      }
    } finally {
      if (conversationId.value === requestedConversationId) {
        sandboxWorkspaceStatusLoaded.value = true;
      }
      sandboxStatusRefreshInFlight = false;
    }
  };

  let isPollingRunning = false;

  const pollSandboxWorkspaceUntilRunning = async (
    cid: string,
    opts: { readyToast?: boolean; readyMessage?: string } = {},
  ) => {
    if (isPollingRunning) return;
    isPollingRunning = true;
    const { readyToast = true, readyMessage = "沙箱已就绪" } = opts;
    const MAX_ATTEMPTS = 30; // 60s
    try {
      for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        if (conversationId.value !== cid) return;
        try {
          const response = await axios.get(
            `${sandboxWorkspaceBaseEndpoint.value}/status`,
            { params: { conversation_id: cid }, headers: authHeaders?.() },
          );
          const data = response.data?.data ?? response.data;
          const mapped = mapSandboxStatus(String(data?.status || "idle"));
          sandboxWorkspaceStatus.value = mapped;
          sandboxWorkspaceInstanceId.value = instanceIdFromData(data);
          sandboxWorkspaceStartedAt.value = data?.started_at || null;
          sandboxWorkspaceUptimeSeconds.value = typeof data?.uptime_seconds === "number"
            ? data.uptime_seconds
            : null;
          if (mapped === "running") {
            if (readyToast) {
              showToast(readyMessage, "success");
            }
            return;
          }
          if (mapped === "error") {
            return;
          }
        } catch {
          // 单次查询失败不中断轮询
        }
      }
      if (conversationId.value === cid && sandboxWorkspaceStatus.value !== "running") {
        if (readyToast) {
          showToast("Pod 仍在创建中（可能镜像拉取较慢），可稍后点「刷新」查看", "info");
        }
      }
    } finally {
      isPollingRunning = false;
    }
  };

  let autoWarmedConversationKey = "";

  const maybeAutoWarmSandbox = async () => {
    if (!isSandboxWorkspacePolicy.value || !conversationId.value) return;
    if (isProcessing.value || remoteRunActive.value) return;
    if (!sandboxWorkspaceStatusLoaded.value) return;
    if (contextUsage.value?.sandbox_auto_warm === false) return;
    const status = sandboxWorkspaceStatus.value;
    if (status === "running" || status === "starting" || status === "error") return;
    const key = `${conversationId.value}::${sandboxBackend.value}`;
    if (autoWarmedConversationKey === key) return;
    autoWarmedConversationKey = key;
    try {
      const response = await axios.post(
        `${sandboxWorkspaceBaseEndpoint.value}/ensure`,
        { conversation_id: conversationId.value, auto_warm: true },
        { headers: authHeaders?.() },
      );
      const data = response.data?.data ?? response.data;
      if (data?.auto_warm_disabled) {
        sandboxWorkspaceStatus.value = "idle";
        return;
      }
      const mapped = mapSandboxStatus(String(data?.status || "idle"));
      sandboxWorkspaceStatus.value = mapped;
      sandboxWorkspaceInstanceId.value = instanceIdFromData(data);
      sandboxWorkspaceStartedAt.value = data?.started_at || null;
      sandboxWorkspaceUptimeSeconds.value = typeof data?.uptime_seconds === "number"
        ? data.uptime_seconds
        : null;
      if (mapped === "running") {
        showToast("沙箱环境已预热就绪", "success");
      } else {
        showToast("正在预热沙箱运行环境…", "info");
        void pollSandboxWorkspaceUntilRunning(String(conversationId.value), {
          readyToast: true,
          readyMessage: "沙箱环境已预热就绪",
        });
      }
    } catch {
      // 自动预热静默
    }
  };

  const ensureSandboxWorkspace = async () => {
    if (!isSandboxWorkspacePolicy.value || !conversationId.value) return;
    if (sandboxWorkspaceStatus.value === "starting") return;
    const requestedConversationId = conversationId.value;
    sandboxWorkspaceStatus.value = "starting";
    sandboxWorkspaceError.value = "";
    try {
      const response = await axios.post(
        `${sandboxWorkspaceBaseEndpoint.value}/ensure`,
        { conversation_id: requestedConversationId },
        { headers: authHeaders?.() },
      );
      if (conversationId.value !== requestedConversationId) return;
      const data = response.data?.data ?? response.data;
      const mapped = mapSandboxStatus(String(data?.status || "idle"));
      if (mapped === "error") {
        throw new Error("沙箱未返回运行中状态");
      }
      sandboxWorkspaceInstanceId.value = instanceIdFromData(data);
      sandboxWorkspaceStartedAt.value = data?.started_at || null;
      sandboxWorkspaceUptimeSeconds.value = typeof data?.uptime_seconds === "number" ? data.uptime_seconds : 0;
      sandboxWorkspaceStatus.value = mapped;
      showToast(mapped === "starting" ? "沙箱启动中..." : "沙箱已启动", mapped === "starting" ? "info" : "success");
      if (mapped !== "running") {
        void pollSandboxWorkspaceUntilRunning(requestedConversationId);
      }
    } catch (error: any) {
      if (conversationId.value !== requestedConversationId) return;
      const detail = error?.response?.data?.detail;
      sandboxWorkspaceError.value = typeof detail === "string"
        ? detail
        : String(detail?.message || error?.message || "沙箱启动失败");
      sandboxWorkspaceStatus.value = "error";
      showToast(sandboxWorkspaceError.value, "error");
    }
  };

  const openDockerTerminal = () => {
    if (sandboxWorkspaceStatus.value !== "running") {
      showToast("沙箱未在运行中，请先启动", "warning");
      return;
    }
    if (sandboxBackend.value === "k8s") {
      showK8sTerminal.value = true;
      return;
    }
    if (sandboxBackend.value === "docker") {
      showDockerTerminal.value = true;
      return;
    }
    showToast("当前沙箱不支持终端", "warning");
  };

  const stopSandboxWorkspace = async () => {
    if (!isSandboxWorkspacePolicy.value || !conversationId.value) return;
    if (sandboxWorkspaceStatus.value === "stopping" || sandboxWorkspaceStatus.value === "starting") return;
    const requestedConversationId = conversationId.value;
    sandboxWorkspaceStatus.value = "stopping";
    sandboxWorkspaceError.value = "";
    try {
      await axios.post(
        `${sandboxWorkspaceBaseEndpoint.value}/stop`,
        { conversation_id: requestedConversationId },
        { headers: authHeaders?.() },
      );
      if (conversationId.value !== requestedConversationId) return;
      sandboxWorkspaceStatus.value = "idle";
      sandboxWorkspaceInstanceId.value = null;
      sandboxWorkspaceStartedAt.value = null;
      sandboxWorkspaceUptimeSeconds.value = null;
      sandboxWorkspaceError.value = "";
      showToast(sandboxBackend.value === "k8s" ? "Kubernetes 沙箱 Pod 已停止" : "Docker 沙箱容器已关机停止", "info");
    } catch (error: any) {
      if (conversationId.value !== requestedConversationId) return;
      const detail = error?.response?.data?.detail;
      const msg = typeof detail === "string"
        ? detail
        : String(detail?.message || error?.message || "停止沙箱失败");
      sandboxWorkspaceStatus.value = "running";
      showToast(msg, "error");
    }
  };

  const handleStopSandboxWorkspaceRequest = () => {
    if (sandboxBackend.value === "k8s") {
      showSandboxStopConfirm.value = true;
    } else {
      void stopSandboxWorkspace();
    }
  };

  const confirmStopSandboxWorkspace = async () => {
    showSandboxStopConfirm.value = false;
    await stopSandboxWorkspace();
  };

  const restartSandboxWorkspace = async () => {
    if (!isSandboxWorkspacePolicy.value || !conversationId.value) return;
    if (sandboxWorkspaceStatus.value === "starting") return;
    const requestedConversationId = conversationId.value;
    sandboxWorkspaceStatus.value = "starting";
    sandboxWorkspaceError.value = "";
    try {
      const response = await axios.post(
        `${sandboxWorkspaceBaseEndpoint.value}/restart`,
        { conversation_id: requestedConversationId },
        { headers: authHeaders?.() },
      );
      if (conversationId.value !== requestedConversationId) return;
      const data = response.data?.data ?? response.data;
      const mapped = mapSandboxStatus(String(data?.status || "idle"));
      sandboxWorkspaceInstanceId.value = instanceIdFromData(data);
      sandboxWorkspaceStartedAt.value = data?.started_at || null;
      sandboxWorkspaceUptimeSeconds.value = typeof data?.uptime_seconds === "number" ? data.uptime_seconds : 0;
      sandboxWorkspaceStatus.value = mapped;
      const shortId = (sandboxWorkspaceInstanceId.value || "").slice(0, 12);
      const restartMsg = sandboxBackend.value === "k8s"
        ? (shortId ? `Kubernetes 沙箱 Pod 已重启 (${shortId})` : "Kubernetes 沙箱 Pod 已重启")
        : (shortId ? `Docker 沙箱已重启 (${shortId})` : "Docker 沙箱已重启");
      showToast(restartMsg, "success");
    } catch (error: any) {
      if (conversationId.value !== requestedConversationId) return;
      const detail = error?.response?.data?.detail;
      sandboxWorkspaceError.value = typeof detail === "string"
        ? detail
        : String(detail?.message || error?.message || "沙箱重启失败");
      sandboxWorkspaceStatus.value = "error";
      showToast(sandboxWorkspaceError.value, "error");
    }
  };

  watch(
    [conversationId, effectiveSandboxPolicy],
    async ([conversation, policy], previous) => {
      const previousConversation = String(previous?.[0] || "");
      const previousPolicy = String(previous?.[1] || "");
      if (
        !isSandboxWorkspacePolicy.value
        || !conversation
        || conversation !== previousConversation
        || policy !== previousPolicy
      ) {
        resetSandboxWorkspaceState();
        if (isSandboxWorkspacePolicy.value && conversation) {
          await refreshSandboxWorkspaceStatus();
          void maybeAutoWarmSandbox();
        }
      }
    },
    { immediate: true },
  );

  onUnmounted(() => {
    isPollingRunning = false;
  });

  return {
    sandboxWorkspaceStatus,
    sandboxWorkspaceStatusLoaded,
    sandboxWorkspaceError,
    sandboxWorkspaceInstanceId,
    sandboxWorkspaceStartedAt,
    sandboxWorkspaceUptimeSeconds,
    effectiveSandboxPolicy,
    sandboxBackend,
    isSandboxWorkspacePolicy,
    sandboxWorkspaceBaseEndpoint,
    showSandboxStopConfirm,
    showDockerTerminal,
    showK8sTerminal,
    resetSandboxWorkspaceState,
    refreshSandboxWorkspaceStatus,
    pollSandboxWorkspaceUntilRunning,
    maybeAutoWarmSandbox,
    ensureSandboxWorkspace,
    openDockerTerminal,
    handleStopSandboxWorkspaceRequest,
    confirmStopSandboxWorkspace,
    stopSandboxWorkspace,
    restartSandboxWorkspace,
  };
}
