import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import axios from "@/utils/axios";

export interface DatasetPortalPayload {
  dataset_count?: number;
  dataset_menu_hash?: string;
  generated_at?: string;
  groups?: unknown[];
  markdown?: string;
  is_fallback?: boolean;
  from_cache?: boolean;
  has_datasets?: boolean;
  _failed_at?: string;
}

type ToastFn = (message: string, type?: "success" | "error" | "info") => void;

export interface UseDatasetPortalOptions {
  getAuthHeaders: () => Record<string, string>;
  showToast: ToastFn;
  lockToDataQueryAgentForDatasetMenu: () => Promise<void>;
  onQuickQuestion: (query: string) => void | Promise<void>;
  findDataQueryAgent?: () => unknown;
  keepOpenStorageKey?: string;
  pinStorageKey?: string;
  /** 门户加载态变化（用于 EmbedChat 加载提示轮播等） */
  onPortalLoadingChange?: (loading: boolean) => void;
}

export const isMobileViewport = () =>
  typeof window !== "undefined" && window.matchMedia("(max-width: 639px)").matches;

const readStoredBoolean = (storageKey: string, defaultWhenUnset: boolean): boolean => {
  const stored = localStorage.getItem(storageKey);
  if (stored === "1") return true;
  if (stored === "0") return false;
  return defaultWhenUnset;
};

export const PORTAL_OPEN_HOTKEY = "Ctrl+Shift+D";

export function useDatasetPortal(options: UseDatasetPortalOptions) {
  const showPortalDrawer = ref(false);
  const portalNavigationPayload = ref<DatasetPortalPayload | null>(null);
  const portalLoading = ref(false);
  const portalSilentRefreshing = ref(false);
  const portalPrefetchInFlight = ref(false);
  const hasSilentlyRefreshed = ref(false);
  let silentRefreshTimer: ReturnType<typeof setTimeout> | null = null;

  const storageKey = options.keepOpenStorageKey || "dataset_portal_keep_open";
  const portalKeepOpenOnQuestion = ref(
    readStoredBoolean(storageKey, !isMobileViewport()),
  );
  watch(portalKeepOpenOnQuestion, (val) => {
    localStorage.setItem(storageKey, val ? "1" : "0");
  });

  const pinStorageKey = options.pinStorageKey || "dataset_portal_pinned";
  const portalPinned = ref(localStorage.getItem(pinStorageKey) === "1");
  watch(portalPinned, (val) => {
    localStorage.setItem(pinStorageKey, val ? "1" : "0");
  });

  const shouldClosePortalAfterQuestion = () =>
    isMobileViewport() || !portalKeepOpenOnQuestion.value;

  const portalBackgroundRefreshing = computed(
    () => portalSilentRefreshing.value || (portalLoading.value && !!portalNavigationPayload.value),
  );

  const setPortalLoading = (loading: boolean) => {
    portalLoading.value = loading;
    options.onPortalLoadingChange?.(loading);
  };

  const fetchDatasetMenuNavigationPayload = async (refresh = false) => {
    const res = await axios.get("/api/v1/chat/dataset-menu", {
      headers: options.getAuthHeaders(),
      params: refresh ? { refresh: true } : undefined,
    });
    return (res.data?.data || {}) as DatasetPortalPayload;
  };

  const recordDatasetMenuQuestionClick = async (
    navigation: DatasetPortalPayload | undefined | null,
    payload: { query: string; label?: string; group_id?: string },
  ) => {
    const datasetMenuHash = navigation?.dataset_menu_hash;
    const query = String(payload?.query || "").trim();
    if (!datasetMenuHash || !query) return;
    try {
      await axios.post(
        "/api/v1/chat/dataset-menu/click",
        {
          dataset_menu_hash: datasetMenuHash,
          query,
          label: payload.label,
          group_id: payload.group_id,
        },
        { headers: options.getAuthHeaders() },
      );
    } catch (error) {
      console.warn("Failed to record dataset menu question click", error);
    }
  };

  const clearNavigationQuestionClickStats = (
    navigation: DatasetPortalPayload | undefined | null,
    query: string,
  ) => {
    const normalized = String(query || "").trim();
    if (!navigation?.groups || !normalized) return;
    for (const group of navigation.groups as Array<{ questions?: Array<{ query?: string; click_count?: number; last_clicked_at?: string }> }>) {
      for (const question of group.questions || []) {
        if (String(question.query || "").trim() !== normalized) continue;
        question.click_count = 0;
        delete question.last_clicked_at;
      }
    }
  };

  const clearDatasetMenuQuestionClick = async (
    navigation: DatasetPortalPayload | undefined | null,
    payload: { query: string },
  ) => {
    const datasetMenuHash = navigation?.dataset_menu_hash;
    const query = String(payload?.query || "").trim();
    if (!datasetMenuHash || !query) return;
    try {
      await axios.post(
        "/api/v1/chat/dataset-menu/click/clear",
        {
          dataset_menu_hash: datasetMenuHash,
          query,
        },
        { headers: options.getAuthHeaders() },
      );
      clearNavigationQuestionClickStats(navigation, query);
      if (navigation === portalNavigationPayload.value && portalNavigationPayload.value) {
        portalNavigationPayload.value = { ...portalNavigationPayload.value };
      }
    } catch (error) {
      console.warn("Failed to clear dataset menu question click", error);
    }
  };

  const fetchPortalNavigationData = async (refresh = false, silent = false) => {
    if (!silent) {
      if (portalLoading.value) return;
      setPortalLoading(true);
    } else if (refresh) {
      portalSilentRefreshing.value = true;
    }
    try {
      const wasFallback = portalNavigationPayload.value?.is_fallback === true;
      const payload = await fetchDatasetMenuNavigationPayload(refresh);
      portalNavigationPayload.value = payload;

      if (payload?.is_fallback && !refresh && !silent && !hasSilentlyRefreshed.value) {
        hasSilentlyRefreshed.value = true;
        if (silentRefreshTimer) clearTimeout(silentRefreshTimer);
        silentRefreshTimer = setTimeout(async () => {
          if (showPortalDrawer.value || portalNavigationPayload.value) {
            await fetchPortalNavigationData(true, true);
          }
        }, 3000);
      }

      if (silent && wasFallback && payload && !payload.is_fallback) {
        options.showToast("数据门户已更新为完整 AI 推荐", "success");
      }

      if (refresh && !silent) {
        options.showToast("数据门户刷新成功", "success");
      }
    } catch (error) {
      console.warn("Failed to load portal navigation data", error);
      if (!silent) {
        options.showToast(
          refresh ? "刷新数据门户失败，请稍后重试" : "加载数据门户失败，请稍后重试",
          "error",
        );
      }
      if (refresh && portalNavigationPayload.value) {
        portalNavigationPayload.value = {
          ...portalNavigationPayload.value,
          _failed_at: new Date().toISOString(),
        };
      }
    } finally {
      if (!silent) {
        setPortalLoading(false);
      } else if (refresh) {
        portalSilentRefreshing.value = false;
      }
    }
  };

  const prefetchPortalNavigationIfEligible = async () => {
    if (portalNavigationPayload.value || portalPrefetchInFlight.value || portalLoading.value) return;
    if (options.findDataQueryAgent && !options.findDataQueryAgent()) return;
    portalPrefetchInFlight.value = true;
    try {
      await fetchPortalNavigationData(false, true);
    } catch {
      // 静默预加载失败不影响主流程
    } finally {
      portalPrefetchInFlight.value = false;
    }
  };

  const openPortalDrawer = async () => {
    showPortalDrawer.value = true;
    hasSilentlyRefreshed.value = false;
    if (silentRefreshTimer) {
      clearTimeout(silentRefreshTimer);
      silentRefreshTimer = null;
    }
    await options.lockToDataQueryAgentForDatasetMenu();
    if (!portalNavigationPayload.value) {
      await fetchPortalNavigationData();
    } else if (portalNavigationPayload.value.is_fallback) {
      await fetchPortalNavigationData(false, false);
    }
  };

  const refreshPortalNavigation = async () => {
    await fetchPortalNavigationData(true);
  };

  const handlePortalQuickQuestion = async (query: string) => {
    if (shouldClosePortalAfterQuestion()) {
      showPortalDrawer.value = false;
    }
    await options.onQuickQuestion(query);
  };

  const handlePortalDrawerKeydown = (event: KeyboardEvent) => {
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && event.shiftKey && key === "d") {
      event.preventDefault();
      if (showPortalDrawer.value) {
        showPortalDrawer.value = false;
      } else {
        void openPortalDrawer();
      }
      return;
    }
    if (event.key === "Escape" && showPortalDrawer.value) {
      showPortalDrawer.value = false;
    }
  };

  watch(showPortalDrawer, (val) => {
    if (!val && silentRefreshTimer) {
      clearTimeout(silentRefreshTimer);
      silentRefreshTimer = null;
    }
  });

  const disposePortalTimers = () => {
    if (silentRefreshTimer) {
      clearTimeout(silentRefreshTimer);
      silentRefreshTimer = null;
    }
  };

  onMounted(() => {
    document.addEventListener("keydown", handlePortalDrawerKeydown);
  });

  onUnmounted(() => {
    document.removeEventListener("keydown", handlePortalDrawerKeydown);
    disposePortalTimers();
  });

  return {
    showPortalDrawer,
    portalNavigationPayload,
    portalLoading,
    portalBackgroundRefreshing,
    portalKeepOpenOnQuestion,
    portalPinned,
    openPortalDrawer,
    refreshPortalNavigation,
    handlePortalQuickQuestion,
    handlePortalDrawerKeydown,
    recordDatasetMenuQuestionClick,
    clearDatasetMenuQuestionClick,
    prefetchPortalNavigationIfEligible,
    fetchDatasetMenuNavigationPayload,
    disposePortalTimers,
  };
}
