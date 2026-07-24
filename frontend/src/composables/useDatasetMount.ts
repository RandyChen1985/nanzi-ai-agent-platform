import { ref } from "vue";

export function useDatasetMount() {
  const activeMetadataDatasetIds = ref<string[]>([]);

  const toggleMetadataDatasetActive = (datasetId: string) => {
    const id = String(datasetId || "").trim();
    if (!id) return;
    activeMetadataDatasetIds.value = activeMetadataDatasetIds.value.includes(id)
      ? activeMetadataDatasetIds.value.filter((item) => item !== id)
      : [...activeMetadataDatasetIds.value, id];
  };

  const clearActiveMetadataDatasets = () => {
    activeMetadataDatasetIds.value = [];
  };

  return {
    activeMetadataDatasetIds,
    toggleMetadataDatasetActive,
    clearActiveMetadataDatasets,
  };
}
