/**
 * 「绑定数据集」弹窗的过滤与排序。
 *
 * 与 kbDatasetFilter 分开是因为字段完全不同（这里是元数据集：数据源、标签、
 * 表数），但时间戳解析复用同一份实现，避免两处对秒/毫秒的判断再次跑偏。
 */

import { toMs } from "./kbDatasetFilter";

/** 组件侧传进来的最小字段集，避免让工具模块依赖 API 类型 */
export interface MetadataDatasetLike {
  id: number;
  name?: string;
  display_name?: string;
  description?: string;
  tags?: string[];
  data_source?: string;
  table_count?: number;
  updated_at?: string;
  created_at?: string;
}

export type MetadataSortBy = "name" | "tables" | "updated";

export interface MetadataFilters {
  keyword: string;
  /** 空串表示「全部数据源」 */
  dataSource: string;
}

export const labelOf = (ds: MetadataDatasetLike): string =>
  ds.display_name || ds.name || `#${ds.id}`;

export const tableCountOf = (ds: MetadataDatasetLike): number => ds.table_count ?? 0;

/**
 * 关键词一次匹配显示名、内部名、描述、数据源与全部标签。
 *
 * 用户找数据集时给的线索很杂：可能是「订单」，可能是「postgresql_demo」，
 * 也可能是某个标签——只搜名称会漏掉一大半。
 */
export const matchesKeyword = (ds: MetadataDatasetLike, keyword: string): boolean => {
  const q = keyword.trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    ds.display_name,
    ds.name,
    ds.description,
    ds.data_source,
    ...(ds.tags || []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(q);
};

export const matchesDataSource = (ds: MetadataDatasetLike, dataSource: string): boolean =>
  !dataSource || (ds.data_source || "") === dataSource;

/** 排序统一带 id 兜底，保证同样输入永远同样顺序 */
export const filterAndSortMetadataDatasets = <T extends MetadataDatasetLike>(
  rows: T[],
  filters: MetadataFilters,
  sortBy: MetadataSortBy,
): T[] => {
  const filtered = rows.filter(
    (ds) =>
      matchesDataSource(ds, filters.dataSource) && matchesKeyword(ds, filters.keyword),
  );
  return filtered.sort((a, b) => {
    let diff = 0;
    if (sortBy === "tables") diff = tableCountOf(b) - tableCountOf(a);
    else if (sortBy === "updated") {
      diff = toMs(b.updated_at || b.created_at) - toMs(a.updated_at || a.created_at);
    } else diff = labelOf(a).localeCompare(labelOf(b), "zh");
    return diff !== 0 ? diff : a.id - b.id;
  });
};

/** 数据源下拉选项：按名称排序、去重，空值统一归为「默认数据源」 */
export const listDataSourceOptions = (rows: MetadataDatasetLike[]): string[] => {
  const names = new Set<string>();
  rows.forEach((ds) => {
    const name = (ds.data_source || "").trim();
    if (name) names.add(name);
  });
  return Array.from(names).sort((a, b) => a.localeCompare(b, "zh"));
};

/** 每个数据源各有多少条，用于在下拉里给出数量提示 */
export const countByDataSource = (rows: MetadataDatasetLike[]): Record<string, number> => {
  const counts: Record<string, number> = {};
  rows.forEach((ds) => {
    const name = (ds.data_source || "").trim() || "默认数据源";
    counts[name] = (counts[name] || 0) + 1;
  });
  return counts;
};

export const hasAnyMetadataFilter = (filters: MetadataFilters): boolean =>
  Boolean(filters.keyword.trim()) || Boolean(filters.dataSource);
