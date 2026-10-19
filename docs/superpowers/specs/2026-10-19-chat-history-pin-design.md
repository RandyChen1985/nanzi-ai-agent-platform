# 会话历史侧栏「置顶」功能

- 日期：2026-10-19
- 涉及：`app/api/portal/endpoints/portal_prefs.py`、`frontend/src/composables/chat/usePinnedConversations.ts`（新增）、`frontend/src/composables/chat/useChatHistoryGroups.ts`、`frontend/src/composables/chat/useHistoryFilters.ts`、`frontend/src/components/ChatHistorySidebar.vue`、`frontend/src/views/EmbedChat.vue`、`frontend/src/views/AgentDebug.vue`、`tests/api/portal/test_pinned_conversations.py`（新增）、`tests/frontend/pinned_history_groups.mjs`（新增）、`tests/frontend/test_pinned_conversations_contract.py`（新增）、`tests/CHECKLIST.md`

## 背景

会话历史侧栏（`ChatHistorySidebar.vue`，聊天页与 AgentDebug 共用）的数据流是：

1. `fetchHistory()` 调 `GET /api/v1/chat/history?group_by_conversation=true`，每页 20 条**会话聚合行**（含最新一轮的 `query`、`created_at`、`agent_name`、`turn_count`）；
2. `aggregatedHistoryList` 按 `conversation_id` 去重；
3. `groupChatHistoryByDate()` 按时间分成 今天 / 昨天 / 3天前 / 7天前 / 更早。

排序完全由后端 `created_at` 倒序驱动，用户无法把常用会话固定在上方。

项目已有的账号级偏好在 `GET/PUT /api/portal/portal-prefs`（Redis，key 为 `agent:portal_prefs:{user_id}`），其中 `pinned_group_ids`、`pinned_kb_dataset_ids` 已证明「列表型置顶偏好」可行。但会话置顶没有对应字段，也没有会话实体表（会话只是历史行的 `conversation_id`）。

## 需求

在历史条目的 hover 动作区（导出/日志/删除三个图标的左侧）加「置顶」按钮：

- 置顶后会话出现在侧栏**顶部独立的「置顶」分组**，固定第一位、默认展开，**不在日期分组里重复出现**；
- 置顶组内部按**置顶时间倒序**（最近置顶在最上）；
- 置顶保存到**账号级偏好**，换浏览器/设备仍在；
- 上限 **20** 个。

## 方案对比

| 方案 | 做法 | 评价 |
|---|---|---|
| **A. 扩展 portal_prefs（选中）** | Redis 偏好新增 `pinned_conversations`，加一个只动置顶的原子写接口 | 复用既有账号级偏好设施，零表结构/零迁移；专用接口避免全量回写覆盖数据门户偏好 |
| B. 新建 DB 表 | `chat_conversation_pins(user_id, conversation_id, pinned_at)` | 强一致、可审计；但要写 MySQL + PG 两套迁移，且会话无实体表，对轻量 UI 偏好偏重 |
| C. 复用 `pinned_group_ids` | 把会话 ID 塞进现有列表 | 语义混淆：与数据门户卡片共用 50 上限，清理逻辑互相误伤，**不采用** |

采纳 A。三个决策点：账号级存储、顶部独立分组且不重复、置顶时间倒序 + 上限 20。

## 设计

### 后端：数据结构

`portal_prefs.py` 新增：

```python
MAX_PINNED_CONVERSATIONS = 20

class PinnedConversation(BaseModel):
    conversation_id: str
    title: str = ""          # 会话标题快照（聚合行的 query）
    created_at: str = ""     # ISO 字符串，原样保留
    agent_name: str = ""
    turn_count: int = 0

class PortalPrefs(BaseModel):
    ...
    pinned_conversations: List[PinnedConversation] = Field(default_factory=list)

class PortalPrefsUpdate(BaseModel):
    ...
    # None = 本次请求不改置顶（旧页面/数据门户全量回写时必须保留）
    pinned_conversations: Optional[List[PinnedConversation]] = None
```

**为什么存快照而不只存 ID**：首屏只加载 20 条会话，置顶会话很可能不在其中。只存 ID 会导致刷新后置顶项无法渲染、表现为「置顶丢失」。快照提供 `title / created_at / agent_name / turn_count`，让置顶组在任何分页窗口下都能稳定展示；列表里出现该会话时以最新数据覆盖。

### 后端：接口契约

1. **`GET /api/portal/portal-prefs`**：`prefs.model_dump()` 自动带出 `pinned_conversations`；旧 JSON 无该字段时 `_parse_portal_prefs` 走 Pydantic 默认值 `[]`，无需迁移。
2. **全量 `PUT /api/portal/portal-prefs`**：
   ```python
   pinned_conversations=(
       existing.pinned_conversations
       if body.pinned_conversations is None
       else _normalize_pinned_conversations(body.pinned_conversations)
   )
   ```
   数据门户等旧调用方不带该字段，因此**不会清空**置顶。
3. **新增 `PUT /api/portal/portal-prefs/pinned-conversations`**（专用原子接口）：
   - 请求：`{ "conversations": [PinnedConversation, ...] }`；
   - 读 existing → 只替换置顶字段 → 写回 → 返回 `{ code: 0, data: { pinned_conversations: [...] } }`；
   - Redis 不可用返回 503（与现有 PUT 一致）；
   - 清洗规则（`_normalize_pinned_conversations`）：去掉空白/空 ID、按 `conversation_id` 去重（保留首次出现 ⇒ 列表首位就是最近置顶）、`title` 截断 200、`agent_name` 截断 100、`created_at` 截断 40、`turn_count` 归一到非负整数、超出 20 条截断；
   - **不做会话归属校验**：置顶只保存用户自己提交的快照文本，不读服务端会话内容；打开会话时 `checkConversationAdoption` 与 `/chat/history` 仍按用户过滤，因此不存在越权读取。

### 前端：分组纯函数

在 `useChatHistoryGroups.ts` 新增（保持现有 `groupChatHistoryByDate` 不动）：

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
  options?: { includeMissingPins?: boolean; now?: Date },
): { pinnedGroup: ChatHistoryDateGroup<T> | null; dateGroups: ChatHistoryDateGroup<T>[] }
```

- 置顶组：按 `pins` 顺序；在 `history` 中能按 `conversation_id` 找到的用**最新数据**并标记 `__pinned: true`；找不到且 `includeMissingPins` 为真时用快照构造条目（`query = title`、`created_at`、`agent_name`、`turn_count`，标记 `__pinned: true`、`is_snapshot: true`）；
- 日期组：`history` 中**未被置顶**的项，仍走 `groupChatHistoryByDate`；
- 置顶组 id 固定 `pinned`、title `置顶`、order `0`；无置顶项时返回 `null`，不渲染该组。

### 前端：状态与接线

新增 `usePinnedConversations.ts`：

| 能力 | 说明 |
|---|---|
| `pinned` | `ref<PinnedConversationSnapshot[]>` |
| `load()` | `GET /api/portal/portal-prefs` 取字段；失败静默降级为 `[]`（置顶是增强功能，不能拖垮聊天）。请求头由调用方注入：EmbedChat 传 `Authorization`/`X-API-Key`，AgentDebug 用默认 axios 凭证 |
| `isPinned(cid)` | 判定 |
| `toggle(item)` | 已置顶→移除；未置顶→上限检查后**插入首位**；返回 `{ ok, message? }` 供页面 toast |
| `removeMany(ids)` | 删除会话后清掉对应置顶 |

- 写请求走专用 `PUT /api/portal/portal-prefs/pinned-conversations`；采用**乐观更新**，失败回滚并返回错误信息；
- 快照映射：`{ conversation_id, title: item.query || item.summary || "未命名对话", created_at, agent_name, turn_count }`（兜底文案与侧栏现有显示一致）。

`EmbedChat.vue`：

- 身份就绪后 `load()`；`resetSessionForIdentityChange` 中清空并重新 `load()`（防止上一位用户的置顶串到新身份）；
- `groupedHistoryList` 改为：`splitHistoryByPin(aggregatedHistoryList.value, pinned.value, { includeMissingPins: historyKeyword.value.trim() === "" && isDefaultHistoryFilters(historyFilters.value) })`；
- 在 `useHistoryFilters.ts` 新增 `isDefaultHistoryFilters(filters)`（与 `DEFAULT_HISTORY_FILTERS` 逐字段比较），作为「默认视图」的唯一判定，避免各处手写字段比较漂移；
- `handleTogglePin(item)`：调用 `toggle`，按返回结果 toast（上限提示「置顶数量已达上限（20）」、失败「置顶失败，请稍后重试」）；
- 单条删除、分组删除、批量删除成功后调用 `removeMany`。

`AgentDebug.vue`：复用同一侧栏组件，同样接入 `usePinnedConversations` 与 `handleTogglePin`（否则会出现「按钮点了没反应」）。它没有运行时换身份的入口（身份只在 `bootstrapDebugConversation` 读一次），因此在拿到身份后 `load()` 即可，无需额外的身份切换清理。

### 前端：侧栏 UI

`ChatHistorySidebar.vue`：

- 新增 emit `toggle-pin(item)`；
- 动作区**最左侧**加 📌 按钮（位于导出按钮左边）：`@click.stop`；
  - 已置顶（`item.__pinned`）：实心 + `text-primary`，**不依赖 hover 常显**；
  - 未置顶：描边，沿用 hover 显隐；移动端（`isMobile`）动作区常显，避免触达不到；
  - `title`：`置顶该会话` / `取消置顶`；
- 置顶组 header **不显示「删除整组」按钮**（`v-if="group.id !== 'pinned'"`），避免误删置顶分组下所有会话；折叠箭头保留；
- 置顶项与普通项点击行为一致（`emit('load-chat')`），快照项同样可点开（`handleHistoryClick` 会按 `conversation_id` 拉历史）。

## 边界与清理

| 场景 | 行为 |
|---|---|
| 置顶已达 20 | 前端拦截 + toast；后端同样截断兜底 |
| 删除会话（单条/整组/批量） | 成功后从置顶移除并回写；回写失败仅 `console.warn`，不打断删除 |
| 置顶项不在当前分页 | 默认视图用快照渲染；**搜索/筛选激活时不渲染快照项**（避免「搜 A 却冒出 B」） |
| 置顶项已被删除 | 点击后历史为空 → 现有空态提示；置顶条目保留，由用户手动取消（不自动删除，避免误清理） |
| 切换身份 | 清空本地置顶并重新加载 |
| Redis 不可用 | 读取降级为空列表；写入失败回滚 + toast，不影响聊天 |
| 多标签页同时置顶 | 后写覆盖先写（PUT 全量列表），不实时同步；下次进入页面以服务端为准 |

## 明确不做

- 不做拖拽自定义排序（固定「最近置顶在前」）；
- 不做多端实时同步（无 WebSocket/轮询推送）；
- 不做置顶重命名、置顶备注；
- 不改 `/chat/history` 的后端排序，也不新增会话实体表；
- 不在置顶组内提供「删除整组」。

## 测试策略

- **后端**（`tests/api/portal/test_pinned_conversations.py`，沿用 `test_portal_prefs.py` 的 `FakeRedis` 写法）：
  - 清洗：空 ID 丢弃、去重保留首次出现、title/agent_name 截断、`turn_count` 归一到非负、超 20 截断；
  - 全量 PUT 不带 `pinned_conversations` 时保留原值；显式传 `[]` 时清空；
  - 专用端点只改置顶，不动 `pinned_group_ids` / `markdown_theme` 等字段；Redis 不可用 503；
  - 旧 JSON（无该字段）解析为 `[]`。
- **前端纯函数**（`tests/frontend/pinned_history_groups.mjs`，由 pytest 以 `node --experimental-strip-types` 运行，参考 `conversation_history_request.mjs`）：
  - 置顶顺序与 `pins` 一致、置顶项不在日期组重复；
  - 缺失快照在 `includeMissingPins=true` 时出现、为 false 时消失；
  - 空置顶返回 `pinnedGroup === null`；
  - `isDefaultHistoryFilters`：默认值返回 true，任一字段改动（含 keyword 非空）返回 false。
- **前端源码契约**（`tests/frontend/test_pinned_conversations_contract.py`）：
  - 侧栏按钮 `@click.stop` 且 emit `toggle-pin`；置顶组 header 无删除按钮；
  - EmbedChat / AgentDebug 均已接线；EmbedChat 身份切换路径清空并重载；
  - 删除路径调用 `removeMany`；搜索/筛选激活时 `includeMissingPins` 为假；
  - 写请求使用专用端点，不调用全量 `/api/portal/portal-prefs` PUT。
- **类型检查**：`vue-tsc --noEmit`。
- 按项目约定在 `tests/CHECKLIST.md` 登记本次改动。

## 风险

- **快照过期**：会话有新消息后，若不在当前列表，置顶组显示旧标题/时间；一旦出现在列表即以最新数据为准，属于可接受降级。
- **多标签覆盖**：两个标签同时置顶，后写覆盖先写；影响仅限置顶列表，不做乐观合并。
- **Redis 单点**：偏好读写失败降级为空/回滚，不阻塞聊天与历史加载。
- **侧栏组件共用**：EmbedChat 与 AgentDebug 必须同步接线，否则会出现无响应按钮；由契约测试锁住两处。
