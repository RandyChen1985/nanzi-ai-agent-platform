/**
 * 输入框附件 chip 的读写唯一事实源。
 *
 * 「挂载数据集 / 知识库」在三个地方发生：
 *  1. 知识库面板（`useKnowledgePortal`）逐个拨开关；
 *  2. 数据门户（`useDatasetMount`）勾选 📊 数据集；
 *  3. 加号菜单「数据&知识库」浮层（`DataKnowledgeCascadeMenu` + `ChatInput`）。
 *
 * 三处写出的 chip 必须逐字段一致——发送链（`useChatAttachments`）依赖 `type` 与
 * `url` 解析挂载内容，任何一处自行拼格式都会让「挂了但没带过去」这种问题极难排查。
 * 因此这里只放纯函数，不持有状态、不发请求。
 */

export const KNOWLEDGE_BASE_CHIP_TYPE = "knowledge_base";
export const METADATA_DATASET_CHIP_TYPE = "metadata_dataset";

/** 知识库汇总 chip 的展示文案，与三个入口共用。 */
export const knowledgeBaseChipLabel = (count: number): string => `已选择 ${count} 个知识库`;

const asFiles = (files: unknown): any[] => (Array.isArray(files) ? files : []);

export const splitChipIds = (raw: unknown): string[] =>
  String(raw ?? "")
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);

/** 勾选/取消一个 ID，保持原有顺序（新增追加到末尾）。 */
export const toggleMountId = (ids: unknown, id: string): string[] => {
  const target = String(id ?? "").trim();
  const current = (Array.isArray(ids) ? ids : []).map((item) => String(item ?? "").trim()).filter(Boolean);
  if (!target) return current;
  return current.includes(target)
    ? current.filter((item) => item !== target)
    : [...current, target];
};

/** 从附件里读出已挂载的知识库 ID（一个汇总 chip，url 为逗号分隔）。 */
export const readKnowledgeBaseIds = (files: unknown): string[] => {
  const chip = asFiles(files).find((file) => file?.type === KNOWLEDGE_BASE_CHIP_TYPE);
  return chip?.url ? splitChipIds(chip.url) : [];
};

/**
 * 按 ID 列表重建知识库汇总 chip；列表为空时整块移除。
 * 其余附件保持原顺序，汇总 chip 始终排在末尾（与知识库面板原实现一致）。
 */
export const syncKnowledgeBaseChip = (files: unknown, ids: unknown): any[] => {
  const others = asFiles(files).filter((file) => file?.type !== KNOWLEDGE_BASE_CHIP_TYPE);
  const uniqueIds = Array.from(new Set((Array.isArray(ids) ? ids : []).map((id) => String(id ?? "").trim()).filter(Boolean)));
  if (!uniqueIds.length) return [...others];
  return [
    ...others,
    {
      type: KNOWLEDGE_BASE_CHIP_TYPE,
      url: uniqueIds.join(","),
      filename: knowledgeBaseChipLabel(uniqueIds.length),
      size: 0,
      ext: KNOWLEDGE_BASE_CHIP_TYPE,
    },
  ];
};

/** 从附件里读出已挂载的元数据集 ID（每个数据集一个 chip）。 */
export const readMetadataDatasetIds = (files: unknown): string[] => {
  const ids: string[] = [];
  for (const file of asFiles(files)) {
    if (file?.type !== METADATA_DATASET_CHIP_TYPE) continue;
    for (const id of splitChipIds(file?.url)) {
      if (!ids.includes(id)) ids.push(id);
    }
  }
  return ids;
};

/**
 * 按 ID 列表重建元数据集 chip（每个数据集一个）。
 * 已存在的 chip 保留其展示名（例如数据门户里带过来的 `display_name`），
 * 新挂载的用 `resolveName` 兜底，再退化为 ID。
 */
export const syncMetadataDatasetChips = (
  files: unknown,
  ids: unknown,
  resolveName?: (id: string) => string | undefined,
): any[] => {
  const list = asFiles(files);
  const others = list.filter((file) => file?.type !== METADATA_DATASET_CHIP_TYPE);
  const uniqueIds = Array.from(new Set((Array.isArray(ids) ? ids : []).map((id) => String(id ?? "").trim()).filter(Boolean)));
  const chips = uniqueIds.map((datasetId) => {
    const existingChip = list.find(
      (file) => file?.type === METADATA_DATASET_CHIP_TYPE && splitChipIds(file?.url).includes(datasetId),
    );
    const name = String(existingChip?.filename || resolveName?.(datasetId) || datasetId);
    return {
      type: METADATA_DATASET_CHIP_TYPE,
      url: datasetId,
      filename: name,
      size: 0,
      ext: METADATA_DATASET_CHIP_TYPE,
    };
  });
  return [...others, ...chips];
};
