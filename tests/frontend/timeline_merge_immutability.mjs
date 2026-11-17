// 验证 mergeTimelineLogs({ clone: true }) 保持输入不可变（避免 Vue computed 内原地改写触发递归更新），
// 以及 chunkLoadRecovery 在动态导入失效时带防死循环冷却的自愈逻辑。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const { loadModule } = createRequire(import.meta.url)('./ts_module_loader.cjs')

const { mergeTimelineLogs } = loadModule('frontend/src/utils/processTimeline.ts')
const {
  isDynamicImportChunkError,
  recoverFromChunkError,
  notifyChunkRecoveryFallback,
  onChunkRecoveryFallback,
  clearChunkRecoveryFallback,
  _resetChunkRecoveryMemoryForTests,
} = loadModule('frontend/src/utils/chunkLoadRecovery.ts')

// --- 1. mergeTimelineLogs({ clone: true }) 不改写原 timeline/logs 对象及嵌套结构 ---
{
  const originalChildTool = {
    kind: 'log',
    id: 'tool-1',
    title: '调用工具: search',
    details: 'query=sales',
    status: 'pending',
    category: 'tool',
    expanded: false,
    file_metadata: {
      operation: 'edit',
      path: 'a.py',
      target_type: 'file',
      changes: { lines: 12 },
    },
    user_question: {
      question_id: 'q1',
      question: '继续？',
      options: [{ id: 'yes', label: '是' }],
    },
  }
  const originalContainer = {
    kind: 'log',
    id: 'subagent_run-99',
    title: '委派智能体 · 数据分析专家',
    details: '委派中',
    status: 'pending',
    category: 'agent',
    expanded: false,
    childrenExpanded: false,
    children: [originalChildTool],
  }
  const originalTimeline = [originalContainer]

  const incomingLogs = [
    {
      id: 'subagent_reasoning:run-99:1',
      parent_id: 'subagent_run-99',
      title: '子代理思考',
      details: '思考分析步骤',
      status: 'success',
      category: 'reasoning',
      reasoning: true,
    },
    {
      id: 'tool-1',
      parent_id: 'subagent_run-99',
      title: '工具完成: search',
      details: '10 rows',
      status: 'success',
      category: 'tool',
    },
  ]

  const timelineSnapshotBefore = JSON.stringify(originalTimeline)
  const logsSnapshotBefore = JSON.stringify(incomingLogs)

  const merged = mergeTimelineLogs(originalTimeline, incomingLogs, { clone: true })

  // 原对象和数组全程保持未改写
  assert.equal(JSON.stringify(originalTimeline), timelineSnapshotBefore, 'props.timeline 原对象不得被原地修改')
  assert.equal(JSON.stringify(incomingLogs), logsSnapshotBefore, 'props.logs 原对象不得被原地修改')
  assert.notEqual(merged, originalTimeline, 'clone 模式必须返回新的数组引用')
  assert.notEqual(merged[0], originalContainer, 'clone 模式必须克隆顶层节点对象')
  assert.equal(merged[0].children.length, 2, '合并结果应包含工具节点与子代理思考节点')

  // 对渲染视图克隆结果做 UI 交互修改及深层字段改写，不应反向污染原始输入
  merged[0].expanded = true
  merged[0].userToggled = true
  merged[0].children[0].file_metadata.changes.lines = 999
  merged[0].children[0].user_question.options[0].label = '篡改'
  assert.equal(originalContainer.expanded, false, '克隆副本的 UI 折叠态不能写回 props.timeline')
  assert.equal(originalContainer.userToggled, undefined, '克隆副本的 userToggled 不能写回 props.timeline')
  assert.equal(originalChildTool.file_metadata.changes.lines, 12, '深层 file_metadata 不得受克隆修改影响')
  assert.equal(originalChildTool.user_question.options[0].label, '是', '深层 user_question 不得受克隆修改影响')
}

// --- 2. 默认不传 clone 时维持既有向后兼容行为 ---
{
  const container = {
    kind: 'log',
    id: 'subagent_compat',
    title: '委派智能体 · 测试',
    details: '',
    status: 'pending',
    category: 'agent',
    children: [],
  }
  const timeline = [container]
  const merged = mergeTimelineLogs(timeline, [
    {
      id: 'tool-compat',
      parent_id: 'subagent_compat',
      title: '调用工具: Bash',
      status: 'success',
      category: 'tool',
    },
  ])
  assert.equal(merged[0], container, '未传 { clone: true } 时保持原地合并复用，兼容既有契约')
}

// --- 3. 动态导入失效判断 & 防死循环冷却自愈 ---
{
  assert.equal(
    isDynamicImportChunkError(new TypeError('Failed to fetch dynamically imported module: https://x/assets/Users-123.js')),
    true,
  )
  assert.equal(isDynamicImportChunkError('Importing a module script failed.'), true)
  assert.equal(isDynamicImportChunkError(new Error('ChunkLoadError: Loading chunk 42 failed.')), true)
  assert.equal(isDynamicImportChunkError('Unable to preload CSS for /assets/app.css'), true)
  assert.equal(isDynamicImportChunkError(new TypeError("Cannot read properties of null (reading 'id')")), false)
  assert.equal(isDynamicImportChunkError(new Error('Request failed with status code 500')), false)

  _resetChunkRecoveryMemoryForTests()
  const store = new Map()
  const mockStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { store.set(k, v) },
  }
  let nowTs = 100_000
  const assigned = []
  let reloaded = 0

  const first = recoverFromChunkError('/dashboard/users', {
    now: () => nowTs,
    getStorage: () => mockStorage,
    currentLocation: () => '/dashboard/chat',
    assign: (url) => assigned.push(url),
    reload: () => { reloaded += 1 },
  })
  assert.equal(first, true, '首次命中 chunk 失效必须触发跳转重载')
  assert.deepEqual(assigned, ['/dashboard/users'])
  assert.equal(reloaded, 0)

  // 15 秒冷却窗内再次出错，拒绝死循环重载
  nowTs += 5_000
  const blocked = recoverFromChunkError('/dashboard/roles', {
    now: () => nowTs,
    getStorage: () => mockStorage,
    currentLocation: () => '/dashboard/chat',
    assign: (url) => assigned.push(url),
    reload: () => { reloaded += 1 },
  })
  assert.equal(blocked, false, '15 秒冷却期内必须拦截连续自动刷新，防止死循环')
  assert.equal(assigned.length, 1)

  // 超过 15 秒且目标与当前同路径时改调 reload()
  nowTs += 16_000
  const afterCooldown = recoverFromChunkError('/dashboard/users', {
    now: () => nowTs,
    getStorage: () => mockStorage,
    currentLocation: () => '/dashboard/users',
    assign: (url) => assigned.push(url),
    reload: () => { reloaded += 1 },
  })
  assert.equal(afterCooldown, true, '冷却期过后再次命中同路径应触发 reload() 拿新 index.html')
  assert.equal(reloaded, 1)

  // 冷却期通知与清除机制（解决 router.onError 冷却期静默无反馈）
  _resetChunkRecoveryMemoryForTests()
  const notices = []
  const unsub = onChunkRecoveryFallback((n) => notices.push(n))
  notifyChunkRecoveryFallback('/dashboard/skills')
  assert.equal(notices.length, 1)
  assert.equal(notices[0].targetUrl, '/dashboard/skills')
  clearChunkRecoveryFallback()
  const noticesAfterClear = []
  const unsub2 = onChunkRecoveryFallback((n) => noticesAfterClear.push(n))
  assert.equal(noticesAfterClear.length, 0, '清除之后新订阅不应再收到旧冷却期提示')
  unsub()
  unsub2()
}
