/**
 * 工作区文件名的展示处理。
 *
 * 产物（生成的图片、导出件）落盘时由 `publish()` 加了一层 32 位 uuid 前缀：
 *
 *     <uuid>_generated-image-20261121-004916-1-7f3ecd.png
 *
 * 那层前缀是内部去重手段（同一个显示名可以并存多份），对用户没有意义。但在工作空间里
 * 它会把标题挤满、被截断成 `26399a7a80ed41e58b2431a2ca949...` —— 一整页卡片认不出谁是谁。
 *
 * 这里只处理**展示**：实际文件名、`item.path`、重命名输入框一律仍用真实名，
 * 否则改一次名就会把 uuid 前缀写进真实文件、或者改名落到不存在的位置。
 */

/** 32 位十六进制 + 下划线：`uuid.uuid4().hex` 就是 32 位小写 hex。 */
const ARTIFACT_PREFIX_RE = /^[0-9a-f]{32}_/i;

/**
 * 去掉产物 UUID 前缀后的显示名。
 *
 * 剥完什么都不剩时退回原名（例如文件名恰好是 `<32hex>_`），
 * 界面上显示一片空白比显示一个丑名字更糟。
 */
export function displayFileName(name: string | null | undefined): string {
  const raw = String(name ?? '');
  if (!raw) return '';
  const stripped = raw.replace(ARTIFACT_PREFIX_RE, '');
  return stripped || raw;
}

/** 该名字是否带产物 UUID 前缀（便于调用方决定要不要提示「真实名是什么」）。 */
export function hasArtifactPrefix(name: string | null | undefined): boolean {
  return ARTIFACT_PREFIX_RE.test(String(name ?? ''));
}

/** 回收站条目前缀：`<unix时间戳>_<8位hex>_<原文件名>`。 */
const TRASHED_PREFIX_RE = /^(\d+)_[0-9a-f]{8}_(.+)$/i;

export interface ParsedTrashedFileName {
  /** 去掉回收站前缀和产物 UUID 前缀后的可读展示名。 */
  displayName: string;
  /** 去掉回收站前缀后的真实原名（保留产物前缀以便准确匹配）。 */
  originalName: string;
  /** 移入回收站时的秒级时间戳，解析不到时为 null。 */
  deletedAt: number | null;
  /** 是否命中标准回收站命名格式。 */
  isTrashedFormat: boolean;
}

/**
 * 解析回收站条目的真实名称与删除时间。
 *
 * 仅用于回收站视图展示，真实删除/恢复请求仍传完整 `item.path`。
 */
export function parseTrashedFileName(name: string | null | undefined): ParsedTrashedFileName {
  const raw = String(name ?? '');
  const match = TRASHED_PREFIX_RE.exec(raw);
  if (!match) {
    return {
      displayName: displayFileName(raw),
      originalName: raw,
      deletedAt: null,
      isTrashedFormat: false,
    };
  }
  const ts = Number(match[1]);
  const originalName = match[2] || raw;
  return {
    displayName: displayFileName(originalName),
    originalName,
    deletedAt: Number.isFinite(ts) && ts > 0 ? ts : null,
    isTrashedFormat: true,
  };
}
