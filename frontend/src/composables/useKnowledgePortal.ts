import { ref, watch } from "vue";
import axios from "@/utils/axios";

export interface KnowledgeDataset {
  id: string;
  name: string;
  description: string;
  doc_count?: number;
  word_count?: number;
  status?: string;
  [key: string]: any;
}

export interface RecommendationQuestion {
  label: string;
  query: string;
}

export interface RecommendationPayload {
  questions: RecommendationQuestion[];
  loading?: boolean;
}

export interface UseKnowledgePortalOptions {
  showToast: (message: string, type?: "success" | "error" | "info") => void;
  onOpenAnotherPortal?: () => void;
}

export const isMobileViewport = () =>
  typeof window !== "undefined" && window.matchMedia("(max-width: 639px)").matches;

const INITIAL_RECOMMENDATION_PREFETCH_LIMIT = 3;

const readStoredBoolean = (storageKey: string, defaultVal: boolean): boolean => {
  const val = localStorage.getItem(storageKey);
  if (val === "1") return true;
  if (val === "0") return false;
  return defaultVal;
};

export function useKnowledgePortal(options: UseKnowledgePortalOptions) {
  const showKnowledgePortal = ref(false);
  const knowledgePinned = ref(!isMobileViewport() && readStoredBoolean("knowledge_portal_pinned", false));
  const knowledgeKeepOpenOnQuestion = ref(readStoredBoolean("knowledge_portal_keep_open", !isMobileViewport()));

  const datasets = ref<KnowledgeDataset[]>([]);
  const loadingDatasets = ref(false);
  const datasetLoadError = ref(false);
  const activeDatasetIds = ref<string[]>([]);
  const datasetRecommendations = ref<Record<string, RecommendationPayload>>({});

  // 本地持久化配置
  watch(knowledgePinned, (val) => {
    localStorage.setItem("knowledge_portal_pinned", val ? "1" : "0");
  });
  watch(knowledgeKeepOpenOnQuestion, (val) => {
    localStorage.setItem("knowledge_portal_keep_open", val ? "1" : "0");
  });

  // NLI 事实一致性检测开关（默认关闭）
  const hallucinationCheckEnabled = ref(readStoredBoolean("knowledge_hallucination_check", false));
  watch(hallucinationCheckEnabled, (val) => {
    localStorage.setItem("knowledge_hallucination_check", val ? "1" : "0");
  });

  // 知识库高级检索参数：相似度阈值、向量权重、文档分块 Top-K
  const knowledgeSimilarityThreshold = ref<number>(
    parseFloat(localStorage.getItem("knowledge_ragflow_similarity_threshold") || "0.20")
  );
  const knowledgeVectorWeight = ref<number>(
    parseFloat(localStorage.getItem("knowledge_ragflow_vector_weight") || "0.30")
  );
  const knowledgeMetadataTopK = ref<number>(
    parseInt(localStorage.getItem("knowledge_ragflow_metadata_top_k") || "5")
  );
  const knowledgeGeneratedAt = ref<string>(
    localStorage.getItem("knowledge_portal_generated_at") || ""
  );

  watch(knowledgeSimilarityThreshold, (val) => {
    localStorage.setItem("knowledge_ragflow_similarity_threshold", val.toString());
  });
  watch(knowledgeVectorWeight, (val) => {
    localStorage.setItem("knowledge_ragflow_vector_weight", val.toString());
  });
  watch(knowledgeMetadataTopK, (val) => {
    localStorage.setItem("knowledge_ragflow_metadata_top_k", val.toString());
  });

  const rawPrefs = ref<any>(null);

  const prefetchInitialRecommendations = () => {
    const pinned = datasets.value.filter((ds) => pinnedDatasetIds.value.includes(ds.id));
    const unpinned = datasets.value.filter((ds) => !pinnedDatasetIds.value.includes(ds.id));
    [...pinned, ...unpinned]
      .slice(0, INITIAL_RECOMMENDATION_PREFETCH_LIMIT)
      .forEach((ds) => {
        fetchRecommendations(ds.id, false);
      });
  };

  const fetchDatasets = async () => {
    if (loadingDatasets.value) return;
    loadingDatasets.value = true;
    datasetLoadError.value = false;
    try {
      // 异步读取 Redis 中的数据门户/知识库通用个人偏好设置
      try {
        const prefRes = await axios.get("/api/portal/portal-prefs");
        if (prefRes.data && prefRes.data.code === 0 && prefRes.data.data) {
          rawPrefs.value = prefRes.data.data;
          if (Array.isArray(prefRes.data.data.pinned_kb_dataset_ids)) {
            pinnedDatasetIds.value = prefRes.data.data.pinned_kb_dataset_ids;
          }
        }
      } catch (prefErr) {
        console.error("Failed to load portal prefs in knowledge portal", prefErr);
      }

      const response = await axios.get("/api/portal/ragflow/datasets", {
        params: { page: 1, page_size: 100, include_missing: false }
      });
      if (response.data && response.data.code === 0) {
        datasets.value = response.data.data.datasets || response.data.data || [];
        const nowStr = new Date().toLocaleString("zh-CN", { hour12: false });
        knowledgeGeneratedAt.value = nowStr;
        localStorage.setItem("knowledge_portal_generated_at", nowStr);
        prefetchInitialRecommendations();
      } else {
        datasets.value = response.data.data || [];
      }
    } catch (error: any) {
      console.error("Failed to load knowledge bases", error);
      datasetLoadError.value = true;
      const isConnectionErr = error.response?.status === 502 || error.response?.status === 503 || error.message?.includes("Network Error");
      if (isConnectionErr) {
        options.showToast("知识库服务暂时不可用，请稍后重试", "error");
      } else {
        options.showToast("加载知识库列表失败，请稍后重试", "error");
      }
    } finally {
      loadingDatasets.value = false;
    }
  };

  const fetchRecommendations = async (datasetId: string, refresh: boolean = false) => {
    if (!datasetId) return;
    const currentRec = datasetRecommendations.value[datasetId];
    if (!refresh && (
      (currentRec?.questions || []).length > 0 ||
      (currentRec?.custom_questions || []).length > 0
    )) return;

    if (!datasetRecommendations.value[datasetId]) {
      datasetRecommendations.value[datasetId] = { questions: [], custom_questions: [], loading: true };
    } else {
      datasetRecommendations.value[datasetId].loading = true;
    }

    try {
      const params: Record<string, any> = {};
      if (refresh && (datasetRecommendations.value[datasetId]?.questions || []).length > 0) {
        const queries = datasetRecommendations.value[datasetId].questions.map((q: any) => q.query);
        params.exclude = queries.join(",");
      }

      const response = await axios.get(`/api/portal/ragflow/datasets/${datasetId}/portal`, { params });
      if (response.data && response.data.code === 0) {
        datasetRecommendations.value[datasetId] = {
          questions: response.data.data.questions || [],
          custom_questions: response.data.data.custom_questions || [],
          loading: false
        };
      } else {
        datasetRecommendations.value[datasetId].loading = false;
      }
    } catch (error) {
      console.error("Failed to fetch recommendations for " + datasetId, error);
      datasetRecommendations.value[datasetId].loading = false;
    }
  };

  // 从当前输入框附件中提取已激活的知识库
  const syncActiveDatasetsFromInput = (chatInputRef: any) => {
    if (!chatInputRef || !chatInputRef.uploadedFiles) {
      activeDatasetIds.value = [];
      return;
    }
    const files = chatInputRef.uploadedFiles || [];
    const kbFile = files.find((f: any) => f.type === "knowledge_base");
    if (kbFile && kbFile.url) {
      activeDatasetIds.value = kbFile.url.split(",").map((id: string) => id.trim()).filter(Boolean);
    } else {
      activeDatasetIds.value = [];
    }
  };

  // 开关切换激活状态
  const toggleDatasetActive = (datasetId: string, chatInputRef: any) => {
    if (!chatInputRef) return;
    if (!chatInputRef.uploadedFiles) {
      chatInputRef.uploadedFiles = [];
    }

    const files = chatInputRef.uploadedFiles || [];
    const otherFiles = files.filter((f: any) => f.type !== "knowledge_base");
    const kbFile = files.find((f: any) => f.type === "knowledge_base");

    let currentIds = kbFile && kbFile.url
      ? kbFile.url.split(",").map((id: string) => id.trim()).filter(Boolean)
      : [];

    if (currentIds.includes(datasetId)) {
      currentIds = currentIds.filter((id: string) => id !== datasetId);
    } else {
      currentIds.push(datasetId);
    }

    activeDatasetIds.value = currentIds;

    if (currentIds.length > 0) {
      const newKbFile = {
        type: "knowledge_base",
        url: currentIds.join(","),
        filename: `已选择 ${currentIds.length} 个知识库`,
        size: 0,
        ext: "knowledge_base",
      };
      chatInputRef.uploadedFiles = [...otherFiles, newKbFile];
    } else {
      chatInputRef.uploadedFiles = otherFiles;
    }
  };

  // 置顶状态管理
  const pinnedDatasetIds = ref<string[]>([]);

  const toggleDatasetPinned = async (datasetId: string) => {
    if (pinnedDatasetIds.value.includes(datasetId)) {
      pinnedDatasetIds.value = pinnedDatasetIds.value.filter(id => id !== datasetId);
    } else {
      pinnedDatasetIds.value.push(datasetId);
    }

    try {
      const currentPrefs = rawPrefs.value || {
        pinned_group_ids: [],
        card_order: [],
        expanded_group_ids: [],
        question_clicks: {},
      };
      await axios.put("/api/portal/portal-prefs", {
        ...currentPrefs,
        pinned_kb_dataset_ids: pinnedDatasetIds.value
      });
      if (!rawPrefs.value) {
        rawPrefs.value = currentPrefs;
      }
      rawPrefs.value.pinned_kb_dataset_ids = pinnedDatasetIds.value;
    } catch (error) {
      console.error("Failed to save portal prefs in knowledge portal", error);
      options.showToast("保存置顶状态失败，请稍后重试", "error");
    }
  };

  // 关联文档数据管理
  const datasetDocuments = ref<Record<string, { docs: any[]; loading: boolean }>>({});

  const fetchDatasetDocuments = async (datasetId: string) => {
    if (!datasetId) return;
    if ((datasetDocuments.value[datasetId]?.docs || []).length > 0) return;

    datasetDocuments.value[datasetId] = { docs: [], loading: true };
    try {
      const response = await axios.get(`/api/portal/ragflow/datasets/${datasetId}/documents`, {
        params: { page: 1, page_size: 100 }
      });
      if (response.data && response.data.code === 0) {
        datasetDocuments.value[datasetId] = {
          docs: response.data.data || [],
          loading: false
        };
      } else {
        datasetDocuments.value[datasetId] = { docs: [], loading: false };
      }
    } catch (error) {
      console.error("Failed to load dataset documents", error);
      datasetDocuments.value[datasetId] = { docs: [], loading: false };
    }
  };

  // 单文件专属推荐提问管理
  const documentRecommendations = ref<Record<string, { questions: RecommendationQuestion[]; loading: boolean }>>({});

  const fetchDocumentRecommendations = async (datasetId: string, documentId: string) => {
    if (!datasetId || !documentId) return;
    if ((documentRecommendations.value[documentId]?.questions || []).length > 0) return;

    documentRecommendations.value[documentId] = { questions: [], loading: true };
    try {
      const response = await axios.get(`/api/portal/ragflow/datasets/${datasetId}/documents/${documentId}/portal`);
      if (response.data && response.data.code === 0) {
        documentRecommendations.value[documentId] = {
          questions: response.data.data.questions || [],
          loading: false
        };
      } else {
        documentRecommendations.value[documentId] = { questions: [], loading: false };
      }
    } catch (error) {
      console.error("Failed to fetch document recommendations for " + documentId, error);
      documentRecommendations.value[documentId] = { questions: [], loading: false };
    }
  };

  const openKnowledgePortal = async () => {
    if (options.onOpenAnotherPortal) {
      options.onOpenAnotherPortal();
    }
    showKnowledgePortal.value = true;
    await fetchDatasets();
  };

  const closeKnowledgePortal = () => {
    showKnowledgePortal.value = false;
  };

  return {
    showKnowledgePortal,
    knowledgePinned,
    knowledgeKeepOpenOnQuestion,
    hallucinationCheckEnabled,
    knowledgeSimilarityThreshold,
    knowledgeVectorWeight,
    knowledgeMetadataTopK,
    knowledgeGeneratedAt,
    datasets,
    loadingDatasets,
    datasetLoadError,
    activeDatasetIds,
    datasetRecommendations,
    pinnedDatasetIds,
    datasetDocuments,
    documentRecommendations,
    toggleDatasetPinned,
    fetchDatasetDocuments,
    fetchDocumentRecommendations,
    fetchDatasets,
    fetchRecommendations,
    syncActiveDatasetsFromInput,
    toggleDatasetActive,
    openKnowledgePortal,
    closeKnowledgePortal
  };
}
