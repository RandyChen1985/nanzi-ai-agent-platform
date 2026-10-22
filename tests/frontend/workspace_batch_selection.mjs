// 用 Node 真跑批量选择/删除编排的纯逻辑：半选态、列表外勾选保留、只读跳过
// 这些分支用源码字符串断言看不出来，必须调用真实实现。
import assert from 'node:assert/strict'

import {
  planBatchDelete,
  selectAllState,
  toggleSelectAllPaths,
} from '../../frontend/src/utils/workspaceBatchSelection.ts'

// --- 1. 空列表：没有可全选项，判定与切换都不改变现状 ---
assert.equal(selectAllState([], new Set()), 'none')
assert.deepEqual([...toggleSelectAllPaths([], new Set(['/w/a.md']))], ['/w/a.md'])
assert.deepEqual([...toggleSelectAllPaths([], new Set())], [])

// --- 2. 三种状态：未选 / 半选 / 全选 ---
const paths = ['/w/a.md', '/w/b.md', '/w/c.zip']
assert.equal(selectAllState(paths, new Set()), 'none')
assert.equal(selectAllState(paths, new Set(['/w/a.md'])), 'partial')
assert.equal(selectAllState(paths, new Set(paths)), 'all')
// 列表外的历史勾选不参与判定：当前列表选满了就是 all
assert.equal(selectAllState(paths, new Set([...paths, '/w/outside.md'])), 'all')

// --- 3. 半选时点全选：补齐当前列表，不丢已有勾选 ---
assert.deepEqual(
  [...toggleSelectAllPaths(paths, new Set(['/w/b.md']))].sort(),
  [...paths].sort(),
)

// --- 4. 全选时再点：取消当前列表，列表外勾选原样保留 ---
assert.deepEqual(
  [...toggleSelectAllPaths(paths, new Set([...paths, '/w/outside.md']))],
  ['/w/outside.md'],
)

// --- 5. 批量删除计划：只读/受保护项跳过，顺序与列表一致 ---
const plan = planBatchDelete(
  [
    { path: '/w/a.md', kind: 'writable' },
    { path: '/w/pub.md', kind: 'readonly' },
    { path: '/w/dir', kind: 'writable' },
  ],
  (item) => item.kind === 'writable',
)
assert.deepEqual(plan.deletable.map((item) => item.path), ['/w/a.md', '/w/dir'])
assert.deepEqual(plan.skipped.map((item) => item.path), ['/w/pub.md'])

// --- 6. 全部不可删：可删列表为空，组件据此不弹确认框 ---
const allReadonly = planBatchDelete([{ path: '/w/x', kind: 'readonly' }], () => false)
assert.equal(allReadonly.deletable.length, 0)
assert.equal(allReadonly.skipped.length, 1)

console.log('工作区批量选择测试全部通过')
