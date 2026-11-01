/**
 * 知识库选择器的过滤与排序。
 *
 * 抽成纯函数而不是留在组件里，是因为这几条规则（时间戳兼容、范围计数、
 * 排序稳定性）都容易被后续改动悄悄破坏，而组件级的 computed 没法直接测。
 */

/** 组件侧传进来的最小字段集，避免让工具模块依赖 API 类型 */
export interface KbDatasetLike {
  id: string;
  name?: string;
  platform_name?: string;
  description?: string;
  platform_description?: string;
  visibility?: string;
  doc_count?: number;
  document_count?: number;
  update_time?: number | string;
  created_by?: string;
  can_write?: boolean;
}

export type KbScope = "public" | "team" | "private";
export type KbUpdatedWithin = "7" | "30" | "older";
export type KbDocsSize = "empty" | "small" | "large";
export type KbWriteAccess = "w" | "ro";
export type KbSortBy = "updated" | "docs" | "name";

export interface KbFilters {
  keyword: string;
  scopes: KbScope[];
  updatedWithin: KbUpdatedWithin | null;
  docsSize: KbDocsSize | null;
  writeAccess: KbWriteAccess | null;
}

/** 可见范围归一：未知/空值按 public 兜底，与后端判定一致 */
export const scopeOf = (ds: KbDatasetLike): KbScope => {
  const token = String(ds.visibility || "").trim().toLowerCase();
  return token === "team" || token === "private" ? token : "public";
};

/**
 * update_time 兼容毫秒时间戳、秒级时间戳与日期字符串。
 * 解析不出来按 0 处理：排到最后，也不会被「近 7 天」误命中。
 */
export const toMs = (value?: number | string): number => {
  if (value === undefined || value === null || value === "") return 0;
  const raw =
    typeof value === "number" ? value : Date.parse(String(value)) || Number(value) || 0;
  if (!Number.isFinite(raw) || raw <= 0) return 0;
  // 秒级时间戳（RAGFlow 部分接口这么给）统一到毫秒
  return raw < 1e12 ? raw * 1000 : raw;
};

export const docCountOf = (ds: KbDatasetLike): number =>
  ds.doc_count ?? ds.document_count ?? 0;

export const writableOf = (ds: KbDatasetLike): boolean => ds.can_write === true;

export const nameOf = (ds: KbDatasetLike): string => ds.platform_name || ds.name || "";

/** 关键词一次匹配名称、描述、创建人 */
export const matchesKeyword = (ds: KbDatasetLike, keyword: string): boolean => {
  const q = keyword.trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    ds.platform_name,
    ds.name,
    ds.platform_description,
    ds.description,
    ds.created_by,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(q);
};

/** 折叠在「更多筛选」里的条件 */
export const matchesAdvanced = (
  ds: KbDatasetLike,
  filters: Pick<KbFilters, "updatedWithin" | "docsSize" | "writeAccess">,
  now: number = Date.now(),
): boolean => {
  if (filters.updatedWithin) {
    const updatedMs = toMs(ds.update_time);
    if (!updatedMs) return false;
    const ageDays = (now - updatedMs) / 86400000;
    if (filters.updatedWithin === "older") {
      if (ageDays <= 30) return false;
    } else if (ageDays > Number(filters.updatedWithin)) {
      return false;
    }
  }
  if (filters.docsSize) {
    const docs = docCountOf(ds);
    if (filters.docsSize === "empty" && docs !== 0) return false;
    if (filters.docsSize === "small" && (docs < 1 || docs > 50)) return false;
    if (filters.docsSize === "large" && docs <= 50) return false;
  }
  if (filters.writeAccess) {
    const writable = writableOf(ds);
    if (filters.writeAccess === "w" ? !writable : writable) return false;
  }
  return true;
};

/**
 * 过滤 + 排序。
 *
 * 排序统一带 id 兜底，保证同样输入永远同样顺序——否则时间戳相同的两项在
 * 不同浏览器下会乱跳，用户刚点的位置就跑了。
 */
export const filterAndSortDatasets = <T extends KbDatasetLike>(
  rows: T[],
  filters: KbFilters,
  sortBy: KbSortBy,
): T[] => {
  const filtered = rows.filter((ds) => {
    if (filters.scopes.length && !filters.scopes.includes(scopeOf(ds))) return false;
    return matchesKeyword(ds, filters.keyword) && matchesAdvanced(ds, filters);
  });
  return filtered.sort((a, b) => {
    let diff = 0;
    if (sortBy === "docs") diff = docCountOf(b) - docCountOf(a);
    else if (sortBy === "name") diff = nameOf(a).localeCompare(nameOf(b), "zh");
    else diff = toMs(b.update_time) - toMs(a.update_time);
    return diff !== 0 ? diff : a.id.localeCompare(b.id);
  });
};

/**
 * 各可见范围的数量角标。
 *
 * 故意**不**把当前 scopes 选择算进去：勾了「TEAM 级」之后另外两个若显示 0，
 * 用户就无法判断换个范围能筛出多少。
 */
export const countByScope = (
  rows: KbDatasetLike[],
  filters: KbFilters,
): Record<KbScope, number> => {
  const counts: Record<KbScope, number> = { public: 0, team: 0, private: 0 };
  rows.forEach((ds) => {
    if (!matchesKeyword(ds, filters.keyword)) return;
    if (!matchesAdvanced(ds, filters)) return;
    counts[scopeOf(ds)] += 1;
  });
  return counts;
};

/** 是否有任何筛选条件生效，决定空态应该说「没有匹配」还是「暂无数据」 */
export const hasAnyFilter = (filters: KbFilters): boolean =>
  Boolean(filters.keyword.trim()) ||
  filters.scopes.length > 0 ||
  filters.updatedWithin !== null ||
  filters.docsSize !== null ||
  filters.writeAccess !== null;
