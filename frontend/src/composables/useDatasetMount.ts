import { ref } from "vue";
import { readMetadataDatasetIds, syncMetadataDatasetChips, toggleMountId } from "@/utils/sessionMountChips";

export function useDatasetMount() {
  const activeMetadataDatasetIds = ref<string[]>([]);

  // 从当前输入框附件中提取所有已激活的元数据集 ID 列表（支持单数据集多 Chip 独立拆分）
  const syncActiveMetadataDatasetsFromInput = (chatInputRef: any) => {
    activeMetadataDatasetIds.value = chatInputRef?.uploadedFiles
      ? readMetadataDatasetIds(chatInputRef.uploadedFiles)
      : [];
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

    const nextIds = toggleMountId(activeMetadataDatasetIds.value, id);
    activeMetadataDatasetIds.value = nextIds;

    // 如果传入了 chatInputRef，按数据集拆分为独立的提示 Chip
    if (!chatInputRef) return;
    const files = Array.isArray(chatInputRef.uploadedFiles) ? chatInputRef.uploadedFiles : [];
    const options = Array.isArray(datasetNameOrOptions) ? datasetNameOrOptions : undefined;
    const explicitName = typeof datasetNameOrOptions === "string" ? datasetNameOrOptions : undefined;

    chatInputRef.uploadedFiles = syncMetadataDatasetChips(files, nextIds, (dsId) =>
      explicitName && dsId === id ? explicitName : getDatasetName(dsId, options),
    );
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
