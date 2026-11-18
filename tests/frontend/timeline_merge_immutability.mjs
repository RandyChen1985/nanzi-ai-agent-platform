// 验证 mergeTimelineLogs({ clone: true }) 保持输入不可变（避免 Vue computed 内原地改写触发递归更新），
// 以及 chunkLoadRecovery 在动态导入失效时带防死循环冷却的自愈逻辑。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const { loadModule } = createRequire(import.meta.url)('./ts_module_loader.cjs')

const { mergeTimelineLogs } = loadModule('frontend/src/utils/processTimeline.ts')
const {
  buildEmbedStreamSnapshot,
  serializeEmbedStreamSnapshot,
} = loadModule('frontend/src/utils/embedStreamSnapshot.ts')
const { coalesceReplayTextEvents } = loadModule('frontend/src/utils/conversationStreamReplay.ts')
const {
  applyProcessNarrationEvent,
  createBufferedStreamEventPump,
  flushTimelineNarrationDelta,
} = loadModule('frontend/src/utils/agentscopeSseHandlers.ts')
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

// --- 4. clone 模式惰性转发高频字段，不触发 Vue computed 订阅 content/progress ---
{
  const readTextKeys = new Set()
  const readLogKeys = new Set()
  const rawText = {
    kind: 'text',
    id: 'r-1',
    textKind: 'reasoning',
    content: '初始思考',
    pending: true,
  }
  const rawLog = {
    kind: 'log',
    id: 'tool-progress-1',
    title: '调用工具: Bash',
    details: '',
    status: 'pending',
    category: 'tool',
    progress: 1,
    total: 10,
    progressMessage: '1/10',
  }
  const proxiedText = new Proxy(rawText, {
    get(target, prop, receiver) {
      if (typeof prop === 'string') readTextKeys.add(prop)
      return Reflect.get(target, prop, receiver)
    },
  })
  const proxiedLog = new Proxy(rawLog, {
    get(target, prop, receiver) {
      if (typeof prop === 'string') readLogKeys.add(prop)
      return Reflect.get(target, prop, receiver)
    },
  })

  const cloned = mergeTimelineLogs([proxiedText, proxiedLog], [], { clone: true })
  assert.equal(readTextKeys.has('content'), false, 'clone 构建阶段不得读取 text.content，避免依赖爆炸')
  assert.equal(readLogKeys.has('progress'), false, 'clone 构建阶段不得读取 log.progress')
  assert.equal(readLogKeys.has('total'), false, 'clone 构建阶段不得读取 log.total')
  assert.equal(readLogKeys.has('progressMessage'), false, 'clone 构建阶段不得读取 log.progressMessage')

  rawText.content = '初始思考 -> 持续增量'
  rawLog.progress = 5
  assert.equal(cloned[0].content, '初始思考 -> 持续增量', '渲染读取时应透传原对象的最新 content')
  assert.equal(cloned[1].progress, 5, '渲染读取时应透传原对象的最新 progress')

  cloned[0].content = '视图层局部覆盖'
  cloned[1].progress = 99
  assert.equal(rawText.content, '初始思考 -> 持续增量', '修改克隆体 content 不得反向污染原对象')
  assert.equal(rawLog.progress, 5, '修改克隆体 progress 不得反向污染原对象')
}

// --- 5. serializeEmbedStreamSnapshot 复用预算校验时的 JSON 序列化结果 ---
{
  const snap = buildEmbedStreamSnapshot({
    conversationId: 'conv-1',
    traceId: 'trace-1',
    draft: { content: '正文内容', reasoningContent: '思考内容' },
    now: 123456,
  })
  assert.ok(snap)
  const raw = serializeEmbedStreamSnapshot(snap)
  assert.equal(raw, JSON.stringify(snap))
}

// --- 6. coalesceReplayTextEvents 合并连续同类型/同来源文本增量 ---
{
  const events = [
    { _seq: 1, type: 'reasoning_content', content: '思' },
    { _seq: 2, type: 'reasoning_content', content: '考' },
    { _seq: 3, type: 'process_narration', agent_name: 'A', content: '第1句' },
    { _seq: 4, type: 'process_narration', agent_name: 'A', content: '续' },
    { _seq: 5, type: 'process_narration', agent_name: 'B', content: '另起' },
    { _seq: 6, type: 'log', id: 'tool-1', title: '工具' },
    { _seq: 7, type: 'answer_delta', content: '答' },
    { _seq: 8, type: 'answer_delta', content: '案' },
  ]
  const batched = coalesceReplayTextEvents(events)
  assert.deepEqual(
    batched.map((e) => [e.type, e.content || e.title, e._seq]),
    [
      ['reasoning_content', '思考', 2],
      ['process_narration', '第1句续', 4],
      ['process_narration', '另起', 5],
      ['log', '工具', 6],
      ['answer_delta', '答案', 8],
    ],
  )
}

// --- 7. 旁白缓冲不再逐帧直写 processNarrationPending，且支持恢复流缓冲泵 ---
{
  const msg = { content: '', processTimeline: [], processNarrationPending: '' }
  applyProcessNarrationEvent(msg, { type: 'process_narration', content: '刚到一段', agent_name: 'A' })
  // 首次（at=0）会立即触发一次刷入
  assert.equal(msg.processNarrationPending, '刚到一段')
  applyProcessNarrationEvent(msg, { type: 'process_narration', content: '100ms内碎片1', agent_name: 'A' })
  applyProcessNarrationEvent(msg, { type: 'process_narration', content: '碎片2', agent_name: 'A' })
  assert.equal(msg.processNarrationPending, '刚到一段', '100ms 窗口内碎片不得立即写响应式字段')
  flushTimelineNarrationDelta(msg)
  assert.equal(msg.processNarrationPending, '刚到一段100ms内碎片1碎片2')
  assert.equal(msg.processTimeline[0].content, '刚到一段100ms内碎片1碎片2')

  const dispatched = []
  let hasReasoning = false
  let hasContent = false
  const pump = createBufferedStreamEventPump(
    (d) => {
      if (d.type === 'reasoning_content') hasReasoning = true
      if (d.type === 'answer_delta') hasContent = true
      dispatched.push(d)
    },
    { hasReasoning: () => hasReasoning, hasContent: () => hasContent },
  )
  pump.push({ type: 'reasoning_content', content: 'R1' })
  pump.push({ type: 'reasoning_content', content: 'R2' })
  pump.push({ type: 'reasoning_content', content: 'R3' })
  pump.push({ type: 'answer_delta', content: 'A1' })
  pump.push({ type: 'answer_delta', content: 'A2' })
  pump.push({ type: 'retraction', content: '已撤回' })
  pump.push({ type: 'answer_delta', content: 'A3' })
  pump.push({ type: 'answer_delta', content: 'A4' })
  pump.flush()
  assert.deepEqual(
    dispatched.map((e) => [e.type, e.content]),
    [
      ['reasoning_content', 'R1'],
      ['reasoning_content', 'R2R3'],
      ['answer_delta', 'A1'],
      ['retraction', '已撤回'],
      ['answer_delta', 'A3A4'],
    ],
  )
  pump.cancel()
}
