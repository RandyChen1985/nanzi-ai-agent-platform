/**
 * 工作区「多选」相关的纯逻辑。
 *
 * 单独成模块的原因：半选态、全选的生效范围、只读项跳过是批量操作最容易出错的分支，
 * 抽成无框架依赖的函数后可以在 node 下直接跑行为测试
 * （见 tests/frontend/workspace_batch_selection.mjs）。
 */

export type SelectAllState = 'none' | 'partial' | 'all'

/**
 * 当前列表的全选状态。
 *
 * 只按「当前列表内的路径」判定：切目录/改搜索条件后残留的历史勾选不参与，
 * 否则会出现「看着没选满，但全选框显示已全选」的错觉。
 */
export function selectAllState(
  selectablePaths: readonly string[],
  selectedPaths: ReadonlySet<string>,
): SelectAllState {
  if (selectablePaths.length === 0) return 'none'
  let selectedCount = 0
  for (const path of selectablePaths) {
    if (selectedPaths.has(path)) selectedCount += 1
  }
  if (selectedCount === 0) return 'none'
  return selectedCount === selectablePaths.length ? 'all' : 'partial'
}

/**
 * 全选 / 取消全选。
 *
 * 只增删当前列表内的路径：列表外的历史勾选既不影响判定，也不会被顺手清掉。
 * 半选态下点击视为「补齐全选」，这是文件列表全选框的通行语义。
 */
export function toggleSelectAllPaths(
  selectablePaths: readonly string[],
  selectedPaths: ReadonlySet<string>,
): Set<string> {
  const next = new Set(selectedPaths)
  if (selectAllState(selectablePaths, selectedPaths) === 'all') {
    for (const path of selectablePaths) next.delete(path)
    return next
  }
  for (const path of selectablePaths) next.add(path)
  return next
}

export interface BatchDeletePlan<T> {
  deletable: T[]
  skipped: T[]
}

/**
 * 按可删除判定拆分选中项。
 *
 * 公共只读、系统保护目录、用户根目录本身等不允许删除：批量删除不能因为混入一项就整体失败，
 * 因此把这些项归入 skipped，由确认框说明「将自动跳过」，其余照常删除。
 */
export function planBatchDelete<T>(
  items: readonly T[],
  canDelete: (item: T) => boolean,
): BatchDeletePlan<T> {
  const deletable: T[] = []
  const skipped: T[] = []
  for (const item of items) {
    if (canDelete(item)) deletable.push(item)
    else skipped.push(item)
  }
  return { deletable, skipped }
}
