import { ref } from "vue";

export function useDatasetMount() {
  const activeMetadataDatasetIds = ref<string[]>([]);

  // 从当前输入框附件中提取所有已激活的元数据集 ID 列表（支持单数据集多 Chip 独立拆分）
  const syncActiveMetadataDatasetsFromInput = (chatInputRef: any) => {
    if (!chatInputRef || !chatInputRef.uploadedFiles) {
      activeMetadataDatasetIds.value = [];
      return;
    }
    const files = chatInputRef.uploadedFiles || [];
    const datasetFiles = files.filter((f: any) => f.type === "metadata_dataset");
    const ids: string[] = [];
    datasetFiles.forEach((f: any) => {
      if (f.url) {
        f.url.split(",").forEach((id: string) => {
          const trimmed = id.trim();
          if (trimmed && !ids.includes(trimmed)) {
            ids.push(trimmed);
          }
        });
      }
    });
    activeMetadataDatasetIds.value = ids;
  };

  const getDatasetName = (datasetId: string, options?: any[]) => {
    const id = String(datasetId || "").trim();
    if (Array.isArray(options)) {
      const found = options.find((item: any) => String(item.id ?? item.dataset_id ?? item.name ?? "").trim() === id);
      if (found) {
        return found.display_name || found.name || found.dataset_name || id;
      }
    }
    return id;
  };

  const toggleMetadataDatasetActive = (
    datasetId: string,
    chatInputRef?: any,
    datasetNameOrOptions?: string | any[]
  ) => {
    const id = String(datasetId || "").trim();
    if (!id) return;

    let currentIds = [...activeMetadataDatasetIds.value];
    if (currentIds.includes(id)) {
      currentIds = currentIds.filter((item) => item !== id);
    } else {
      currentIds.push(id);
    }
    activeMetadataDatasetIds.value = currentIds;

    // 如果传入了 chatInputRef，按数据集拆分为独立的提示 Chip
    if (chatInputRef) {
      if (!chatInputRef.uploadedFiles) {
        chatInputRef.uploadedFiles = [];
      }
      const files = chatInputRef.uploadedFiles || [];
      const otherFiles = files.filter((f: any) => f.type !== "metadata_dataset");

      const options = Array.isArray(datasetNameOrOptions) ? datasetNameOrOptions : undefined;
      const explicitName = typeof datasetNameOrOptions === "string" ? datasetNameOrOptions : undefined;

      const newDatasetChips = currentIds.map((dsId) => {
        const existingChip = files.find((f: any) => f.type === "metadata_dataset" && f.url === dsId);
        const name = explicitName && dsId === id
          ? explicitName
          : existingChip?.filename || getDatasetName(dsId, options);

        return {
          type: "metadata_dataset",
          url: dsId,
          filename: name,
          size: 0,
          ext: "metadata_dataset",
        };
      });

      chatInputRef.uploadedFiles = [...otherFiles, ...newDatasetChips];
    }
  };

  const clearActiveMetadataDatasets = (chatInputRef?: any) => {
    activeMetadataDatasetIds.value = [];
    if (chatInputRef && chatInputRef.uploadedFiles) {
      chatInputRef.uploadedFiles = chatInputRef.uploadedFiles.filter(
        (f: any) => f.type !== "metadata_dataset"
      );
    }
  };

  return {
    activeMetadataDatasetIds,
    syncActiveMetadataDatasetsFromInput,
    toggleMetadataDatasetActive,
    clearActiveMetadataDatasets,
  };
}
