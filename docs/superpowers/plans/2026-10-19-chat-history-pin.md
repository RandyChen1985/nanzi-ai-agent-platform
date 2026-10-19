# 会话历史置顶 实施计划

> **执行方式：** 单会话内联执行（用户已确认「开搞」）。按 TDD：先写失败测试 → 跑红 → 最小实现 → 跑绿 → 全量验证。按项目约定，**不执行 `git commit`**，由用户自行提交。

**Goal:** 在会话历史侧栏支持账号级置顶，置顶会话固定显示在顶部「置顶」分组。

**Architecture:** 复用 `/api/portal/portal-prefs`（Redis，按 user_id）存储 `pinned_conversations` 快照列表；新增专用原子写端点；前端用纯函数把历史列表拆成「置顶组 + 日期组」，EmbedChat 与 AgentDebug 共用侧栏组件与 composable。

**Tech Stack:** FastAPI + Pydantic 2 + Redis；Vue 3 + TypeScript + Vite。

**规格：** `docs/superpowers/specs/2026-10-19-chat-history-pin-design.md`

## 文件结构

| 文件 | 职责 |
|---|---|
| `app/api/portal/endpoints/portal_prefs.py` | 数据模型、清洗函数、专用 `PUT /pinned-conversations`、全量 PUT 保留 |
| `frontend/src/composables/chat/usePinnedConversations.ts`（新增） | 置顶状态 + 加载/切换/清理（HTTP 注入，失败回滚） |
| `frontend/src/composables/chat/useChatHistoryGroups.ts` | 新增纯函数 `splitHistoryByPin` |
| `frontend/src/composables/chat/useHistoryFilters.ts` | 新增 `isDefaultHistoryFilters` |
| `frontend/src/components/ChatHistorySidebar.vue` | 📌 按钮 + emit + 置顶组禁止删除整组 + 移动端常显 |
| `frontend/src/views/EmbedChat.vue` | 接线：加载、身份清理、分组、toggle、删除清理 |
| `frontend/src/views/AgentDebug.vue` | 同一套接线（身份只在启动时读取） |
| `tests/api/portal/test_pinned_conversations.py`（新增） | 后端清洗/兼容/端点 |
| `tests/frontend/pinned_history_groups.mjs`（新增） | 分组与默认筛选的运行时断言 |
| `tests/frontend/test_pinned_conversations_contract.py`（新增） | 前端接线源码契约 |

---

### Task 1: 后端偏好字段与专用端点

**Files:**
- Modify: `app/api/portal/endpoints/portal_prefs.py`
- Test: `tests/api/portal/test_pinned_conversations.py`

- [ ] **Step 1: 写失败测试**

覆盖：清洗（空 ID/去重/截断/非负/上限 20）、全量 PUT 保留、专用端点只改置顶、Redis 不可用 503、旧 JSON 兼容。

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/api/portal/test_pinned_conversations.py -q`
Expected: FAIL（`PinnedConversation` 不存在 / 专用端点不存在）

- [ ] **Step 3: 实现**

```python
MAX_PINNED_CONVERSATIONS = 20

class PinnedConversation(BaseModel):
    conversation_id: str
    title: str = ""
    created_at: str = ""
    agent_name: str = ""
    turn_count: int = 0

# PortalPrefs 增加字段
pinned_conversations: List[PinnedConversation] = Field(default_factory=list)

# PortalPrefsUpdate 增加字段（None = 不修改）
pinned_conversations: Optional[List[PinnedConversation]] = None

def _normalize_pinned_conversations(items):
    seen, out = set(), []
    for item in items or []:
        cid = (item.conversation_id or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(PinnedConversation(
            conversation_id=cid,
            title=(item.title or "").strip()[:200],
            created_at=(item.created_at or "").strip()[:40],
            agent_name=(item.agent_name or "").strip()[:100],
            turn_count=max(0, int(item.turn_count or 0)),
        ))
        if len(out) >= MAX_PINNED_CONVERSATIONS:
            break
    return out
```

- 全量 PUT：`pinned_conversations=(existing.pinned_conversations if body.pinned_conversations is None else _normalize_pinned_conversations(body.pinned_conversations))`
- 新端点 `PUT /pinned-conversations`：读 existing → 替换 → 写回 Redis → 返回 `{"code": 0, "data": {"pinned_conversations": [...]}}`；Redis 不可用 503。

- [ ] **Step 4: 跑测试确认绿**

Run: `.venv/bin/python -m pytest tests/api/portal/test_pinned_conversations.py tests/api/portal/test_portal_prefs.py -q`
Expected: 全部 PASS

---

### Task 2: 前端纯函数（分组 + 默认筛选）

**Files:**
- Modify: `frontend/src/composables/chat/useChatHistoryGroups.ts`、`useHistoryFilters.ts`
- Test: `tests/frontend/pinned_history_groups.mjs`、`tests/frontend/test_pinned_conversations_contract.py`

- [ ] **Step 1: 写 `.mjs` 运行时测试**

用 `node --experimental-strip-types` 直接 import 两个 `.ts` 文件，断言：
- 置顶顺序与 `pins` 一致；
- 置顶项不在日期组重复；
- `includeMissingPins` 控制快照项；
- 空置顶返回 `pinnedGroup === null`；
- `isDefaultHistoryFilters` 默认 true、任一字段变化 false。

- [ ] **Step 2: 跑测试确认红**

Run: `node --experimental-strip-types --no-warnings tests/frontend/pinned_history_groups.mjs`
Expected: FAIL（导出不存在）

- [ ] **Step 3: 实现**

```ts
export interface PinnedConversationSnapshot {
  conversation_id: string
  title?: string
  created_at?: string | null
  agent_name?: string
  turn_count?: number
}

export const splitHistoryByPin = <T extends ChatHistoryItem>(
  history: T[],
  pins: PinnedConversationSnapshot[],
  options: { includeMissingPins?: boolean; now?: Date } = {},
): { pinnedGroup: ChatHistoryDateGroup<T> | null; dateGroups: ChatHistoryDateGroup<T>[] } => {
  const byId = new Map(history.map((item) => [String(item.conversation_id ?? ""), item]))
  const pinnedIds = new Set(pins.map((p) => p.conversation_id))
  const pinnedItems: T[] = []
  pins.forEach((pin) => {
    const live = byId.get(pin.conversation_id)
    if (live) {
      pinnedItems.push({ ...live, __pinned: true } as T)
      return
    }
    if (options.includeMissingPins) {
      pinnedItems.push({
        conversation_id: pin.conversation_id,
        query: pin.title || "未命名对话",
        created_at: pin.created_at || null,
        agent_name: pin.agent_name || "",
        turn_count: pin.turn_count ?? 0,
        __pinned: true,
        is_snapshot: true,
      } as T)
    }
  })
  const rest = history.filter((item) => !pinnedIds.has(String(item.conversation_id ?? "")))
  return {
    pinnedGroup: pinnedItems.length
      ? { id: "pinned", title: "置顶", items: pinnedItems, order: 0 }
      : null,
    dateGroups: groupChatHistoryByDate(rest, options.now),
  }
}
```

```ts
export const isDefaultHistoryFilters = (filters: ChatHistoryFilters): boolean =>
  filters.scope === DEFAULT_HISTORY_FILTERS.scope &&
  filters.agentId === DEFAULT_HISTORY_FILTERS.agentId &&
  filters.status === DEFAULT_HISTORY_FILTERS.status &&
  filters.timeRange === DEFAULT_HISTORY_FILTERS.timeRange &&
  filters.startDate === DEFAULT_HISTORY_FILTERS.startDate &&
  filters.endDate === DEFAULT_HISTORY_FILTERS.endDate
```

- [ ] **Step 4: 跑测试确认绿**

Run: `node --experimental-strip-types --no-warnings tests/frontend/pinned_history_groups.mjs`
Expected: PASS

---

### Task 3: `usePinnedConversations` composable

**Files:**
- Create: `frontend/src/composables/chat/usePinnedConversations.ts`

- [ ] **Step 1: 实现（含失败回滚与上限）**

```ts
const ENDPOINT = "/api/portal/portal-prefs/pinned-conversations"

export function usePinnedConversations(options: {
  headers?: () => Record<string, string> | undefined
  onError?: (message: string) => void
} = {}) {
  const pinned = ref<PinnedConversationSnapshot[]>([])
  const isPinned = (cid: string) => pinned.value.some((p) => p.conversation_id === cid)

  const load = async () => {
    try {
      const res = await axios.get("/api/portal/portal-prefs", { headers: options.headers?.() })
      const list = res.data?.data?.pinned_conversations
      pinned.value = Array.isArray(list) ? list : []
    } catch (e) {
      pinned.value = []
    }
  }

  const persist = async (next: PinnedConversationSnapshot[]) => {
    await axios.put(ENDPOINT, { conversations: next }, { headers: options.headers?.() })
  }

  const toggle = async (item: any): Promise<{ ok: boolean; message?: string }> => {
    const cid = String(item?.conversation_id || "")
    if (!cid) return { ok: false, message: "该会话缺少 ID" }
    const previous = [...pinned.value]
    const next = isPinned(cid)
      ? pinned.value.filter((p) => p.conversation_id !== cid)
      : [{ conversation_id: cid, title: item.query || item.summary || "未命名对话",
           created_at: item.created_at || "", agent_name: item.agent_name || "",
           turn_count: Number(item.turn_count || 0) }, ...pinned.value]
    if (next.length > MAX_PINNED) return { ok: false, message: "置顶数量已达上限（20）" }
    pinned.value = next
    try {
      await persist(next)
      return { ok: true }
    } catch (e) {
      pinned.value = previous
      return { ok: false, message: "置顶失败，请稍后重试" }
    }
  }

  const removeMany = async (ids: string[]) => { /* 同 toggle：过滤后乐观写回，失败回滚 */ }

  return { pinned, isPinned, load, toggle, removeMany }
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && node_modules/.bin/vue-tsc --noEmit`
Expected: 0 错误

---

### Task 4: 侧栏 UI

**Files:**
- Modify: `frontend/src/components/ChatHistorySidebar.vue`

- [ ] **Step 1: 新增 emit 与按钮**

- `defineEmits` 增加 `(e: "toggle-pin", item: any): void;`
- 动作区最左侧（导出按钮之前）插入：

```vue
<button
  @click.stop="emit('toggle-pin', item)"
  class="p-1 rounded-md transition-colors"
  :class="item.__pinned ? 'text-primary' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-200'"
  :title="item.__pinned ? '取消置顶' : '置顶该会话'"
  :aria-pressed="item.__pinned ? 'true' : 'false'"
>
  <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" :fill="item.__pinned ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2">
    <path stroke-linecap="round" stroke-linejoin="round" d="M9 4h6l-1 6 4 4H6l4-4-1-6zM12 14v6" />
  </svg>
</button>
```

- 置顶按钮常显：动作区容器加 `:class="isMobile ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'"`；已置顶按钮自身再补 `opacity-100`（`!opacity-100`）。
- 组 header 的删除整组按钮：`v-if="group.id !== 'pinned'"`。

- [ ] **Step 2: 组件编译校验**

Run: `cd frontend && node -e "require('@vue/compiler-sfc').parse(require('fs').readFileSync('src/components/ChatHistorySidebar.vue','utf8'))"`

---

### Task 5: EmbedChat / AgentDebug 接线

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`、`frontend/src/views/AgentDebug.vue`

- [ ] **Step 1: EmbedChat**

- `const { pinned, load: loadPinned, toggle: togglePinned, removeMany: removePinned } = usePinnedConversations({ headers: embedAuthHeaders, onError: (m) => showToast(m, "error") })`
- 初始化身份就绪后 `void loadPinned()`；`resetSessionForIdentityChange` 中 `pinned.value = []` 并 `void loadPinned()`。
- `groupedHistoryList`：
  ```ts
  const groupedHistoryList = computed(() => {
    const { pinnedGroup, dateGroups } = splitHistoryByPin(
      aggregatedHistoryList.value,
      pinned.value,
      { includeMissingPins: !historyKeyword.value.trim() && isDefaultHistoryFilters(historyFilters.value) },
    )
    return pinnedGroup ? [pinnedGroup, ...dateGroups] : dateGroups
  })
  ```
- `handleTogglePin(item)` → `togglePinned(item)`，`ok` 时 toast「已置顶」/「已取消置顶」，否则 toast 返回的 message（warning）。
- 侧栏绑定 `@toggle-pin="handleTogglePin"`。
- 删除成功后 `void removePinned([...])`：`handleDeleteSingleHistory`、`confirmDeleteGroup`（组内 ids）、`handleDeleteHistory`（trace 无 conversation_id 时忽略）。

- [ ] **Step 2: AgentDebug**

- 同样创建 composable（headers 用默认 axios 凭证，不传）。
- `bootstrapDebugConversation` 内 `fetchCurrentUser()` 之后 `void loadPinned()`。
- `groupedHistoryList` 同构；侧栏绑定 `@toggle-pin`；删除路径（若有）调用 `removePinned`。

- [ ] **Step 3: 类型检查**

Run: `cd frontend && node_modules/.bin/vue-tsc --noEmit`
Expected: 0 错误

---

### Task 6: 源码契约测试与全量验证

**Files:**
- Test: `tests/frontend/test_pinned_conversations_contract.py`

- [ ] **Step 1: 写契约测试**

断言：
- 侧栏存在 `emit('toggle-pin', item)` 与 `@click.stop`、`title` 文案两态；
- 置顶组 header 删除按钮带 `group.id !== 'pinned'`；
- EmbedChat/AgentDebug 均含 `usePinnedConversations` 与 `@toggle-pin`；
- EmbedChat `resetSessionForIdentityChange` 内清空并重载；
- 删除路径调用 `removeMany`；
- 请求走 `/api/portal/portal-prefs/pinned-conversations`，且**不**调用全量 PUT `/api/portal/portal-prefs`（在 composable 内不出现无路径的 `axios.put("/api/portal/portal-prefs"`）；
- `groupedHistoryList` 使用 `splitHistoryByPin` 且 `includeMissingPins` 依赖关键词与默认筛选。

- [ ] **Step 2: 全量验证**

Run:
```
.venv/bin/python -m pytest tests/api/portal -q
.venv/bin/python -m pytest tests/frontend --confcutdir=tests/frontend -q
cd frontend && node_modules/.bin/vue-tsc --noEmit
```
Expected: 全部通过 / 0 错误

---

### Task 7: 登记测试清单

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 追加一行**

按现有表格格式追加本次改动：标题、涉及文件、实现说明（含 TDD/变异/验证数字）、`✅ 已完成`、日期 `2026-10-19`。

---

## 自查

- 规格逐节对应：存储/接口 → Task 1；分组纯函数 → Task 2；composable → Task 3；UI → Task 4；两页面接线 → Task 5；测试 → Task 6；清单 → Task 7。
- 无占位符；类型名 `PinnedConversationSnapshot`、`splitHistoryByPin`、`isDefaultHistoryFilters`、`usePinnedConversations` 前后一致。
- 与项目约定一致：不跑 `./dev.sh`、不 `git commit`、前端类型检查用 `vue-tsc --noEmit`。
