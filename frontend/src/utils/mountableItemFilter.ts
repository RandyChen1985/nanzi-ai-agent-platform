/**
 * 「数据&知识库」级联菜单的过滤与排序。
 *
 * 这个菜单同时承载两种资源（元数据集 / 知识库），字段并不一样：数据集有
 * 数据源与表数，知识库有可见范围与文档数。用同一份实现是因为两者对用户
 * 来说都是「可挂载的资源」，筛选维度按是否为空各自生效，不必分两套。
 */

import { toMs } from "./kbDatasetFilter";

export interface MountableItemLike {
  id: string;
  name: string;
  description?: string;
  /** 元数据集专有 */
  dataSource?: string;
  tags?: string[];
  tableCount?: number;
  /** 知识库专有 */
  docCount?: number;
  visibility?: string;
  updatedAt?: string | number;
}

export type MountableSortBy = "name" | "size" | "updated";

export interface MountableFilters {
  keyword: string;
  /** 空串表示不限数据源 */
  dataSource: string;
  /** 空串表示不限可见范围 */
  visibility: string;
}

/**
 * 关键词匹配名称、ID、描述、数据源与标签。
 *
 * 比原来的「名称 + ID + 描述」多了数据源和标签：用户常按「postgresql」这种
 * 库名或「生产」这类标签找，只搜名称会漏。
 */
export const matchesItemKeyword = (item: MountableItemLike, keyword: string): boolean => {
  const q = keyword.trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    item.name,
    item.id,
    item.description,
    item.dataSource,
    ...(item.tags || []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(q);
};

const visibilityOf = (item: MountableItemLike): string => {
  const token = String(item.visibility || "").trim().toLowerCase();
  return token === "team" || token === "private" ? token : "public";
};

/** 规模：数据集看表数，知识库看文档数——同一排序项对两个页签都成立 */
export const sizeOf = (item: MountableItemLike): number => item.tableCount ?? item.docCount ?? 0;

export const filterAndSortMountableItems = <T extends MountableItemLike>(
  rows: T[],
  filters: MountableFilters,
  sortBy: MountableSortBy,
): T[] => {
  const filtered = rows.filter((item) => {
    if (!matchesItemKeyword(item, filters.keyword)) return false;
    if (filters.dataSource && (item.dataSource || "") !== filters.dataSource) return false;
    if (filters.visibility && visibilityOf(item) !== filters.visibility) return false;
    return true;
  });
  return filtered.sort((a, b) => {
    let diff = 0;
    if (sortBy === "size") diff = sizeOf(b) - sizeOf(a);
    else if (sortBy === "updated") diff = toMs(b.updatedAt) - toMs(a.updatedAt);
    else diff = a.name.localeCompare(b.name, "zh");
    return diff !== 0 ? diff : a.id.localeCompare(b.id);
  });
};

/** 数据源下拉选项：去空、去重、按名称排序 */
export const listMountableDataSources = (rows: MountableItemLike[]): string[] => {
  const names = new Set<string>();
  rows.forEach((item) => {
    const name = (item.dataSource || "").trim();
    if (name) names.add(name);
  });
  return Array.from(names).sort((a, b) => a.localeCompare(b, "zh"));
};

/** 当前页签下有多少条处于某个可见范围，用于下拉项上的数量提示 */
export const countByVisibility = (rows: MountableItemLike[]): Record<string, number> => {
  const counts: Record<string, number> = { public: 0, team: 0, private: 0 };
  rows.forEach((item) => {
    counts[visibilityOf(item)] += 1;
  });
  return counts;
};

export const hasAnyMountableFilter = (filters: MountableFilters): boolean =>
  Boolean(filters.keyword.trim()) || Boolean(filters.dataSource) || Boolean(filters.visibility);
