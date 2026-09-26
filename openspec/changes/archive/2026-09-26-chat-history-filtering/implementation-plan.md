# 会话历史多维筛选 实施计划 (chat-history-filtering)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为会话历史侧边栏增加「来源 / 智能体 / 状态 / 时间范围」四个服务端筛选维度，并修复分组模式下关键词与状态只匹配最新一轮的检索缺陷。

**Architecture:** 后端把筛选条件到 SQLAlchemy 查询的构建抽成 `app/services/ai/history_query.py` 纯函数模块（会话级属性过滤分组代表行，轮次级属性以会话键 `IN` 子查询判定存在性），`chat.py` 只做参数解析与编排；前端把筛选类型、默认值与请求参数映射收敛到共享 composable `useHistoryFilters.ts`，`ChatHistorySidebar.vue` 作为受控展示组件，`EmbedChat.vue` 与 `AgentDebug.vue` 两处复用共同接线。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x / Pydantic 2 / pytest；Vue 3 `<script setup>` + TypeScript + Vite + Tailwind CSS 3。

---

## ⚠️ 执行前必读

1. **本计划中的每个「提交点」都必须由用户手动执行。** 依据用户全局指令与 `openspec/project.md:72`（No Auto Commit），Agent 不得运行 `git commit`，只能在提交点准备 commit message 草稿并告知用户。
2. **严禁运行 `./dev.sh` 或任何部署/启动脚本**（用户全局指令）。服务启停由用户在控制台执行。
3. **本变更不产生任何数据库迁移脚本。** 若执行过程中发现需要改表，立即停下并向用户确认，不要自行创建迁移。
4. 后端测试以「编译 SQL 并断言结构」为主要手段（沿用 `tests/services/test_task_execution_history.py:139-141` 的既有做法），无需启动数据库。

---

## 文件结构

| 文件 | 职责 | 本次动作 |
|---|---|---|
| `app/services/ai/history_query.py` | 筛选条件 → SQLAlchemy 查询的唯一构建入口 | **新建** |
| `app/api/v1/endpoints/chat.py` | 参数解析、用户范围、身份回填、编排 | 修改 `get_history` |
| `tests/services/test_history_query.py` | 上述模块的语义单测 | **新建** |
| `frontend/src/composables/chat/useHistoryFilters.ts` | 筛选类型、默认值、请求参数映射 | **新建** |
| `frontend/src/components/ChatHistorySidebar.vue` | 漏斗入口、筛选面板、chip 回显 | 修改 |
| `frontend/src/views/EmbedChat.vue` | 筛选状态与请求接线 | 修改 |
| `frontend/src/views/AgentDebug.vue` | 同上（复用同一组件） | 修改 |
| `tests/frontend/test_chat_history_filters_contract.py` | 前端契约测试 | **新建** |
| `tests/CHECKLIST.md` | 变更登记 | 修改 |

---

## Task 1: 新建查询构建模块

**Files:**
- Create: `app/services/ai/history_query.py`
- Test: `tests/services/test_history_query.py`

- [ ] **Step 1: 先写失败的测试**

新建 `tests/services/test_history_query.py`：

```python
"""会话历史查询构建：来源过滤与轮次级存在性语义。"""

import re
from datetime import datetime

import pytest
from sqlalchemy import select

from app.services.ai.history_query import (
    build_history_query,
    normalize_scope,
    scope_condition,
    turn_level_conversation_keys,
)

pytestmark = pytest.mark.no_infrastructure


def _compile(stmt) -> str:
    """编译为单行 SQL 文本，便于断言结构。"""
    raw = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    return " ".join(raw.split())


def _has_turn_filter_subquery(sql: str) -> bool:
    """判断 SQL 中是否出现轮次筛选子查询。

    两个坑：轮次子查询带 ``DISTINCT``（实际是 ``IN (SELECT DISTINCT ...``）；
    而分组查询的 ``JOIN (SELECT ...)`` 里也含有 ``IN (`` 子串。
    因此必须加词边界 ``\\b``，不能用 ``"IN (SELECT"`` 字面量。
    """
    return re.search(r"\bIN\s*\(\s*SELECT", sql, re.IGNORECASE) is not None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("task", "task"),
        ("TASK", "task"),
        (" chat ", "chat"),
        ("all", "all"),
        (None, "all"),
        ("", "all"),
        ("bogus", "all"),
    ],
)
def test_normalize_scope_falls_back_to_all(raw, expected):
    assert normalize_scope(raw) == expected


def test_scope_condition_task_matches_prefix():
    assert "task_conv_%" in _compile(select(scope_condition("task")))


def test_scope_condition_chat_includes_null_and_excludes_task():
    compiled = _compile(select(scope_condition("chat")))
    assert "IS NULL" in compiled.upper()
    assert "NOT LIKE" in compiled.upper()
    assert "task_conv_%" in compiled


def test_scope_condition_all_returns_none():
    assert scope_condition("all") is None


def test_scope_condition_invalid_value_returns_none():
    assert scope_condition("nonsense") is None


def test_turn_level_keys_merge_keyword_and_status_into_one_subquery():
    stmt = turn_level_conversation_keys(user_id="7", keyword="巡检", status="failed")
    compiled = _compile(stmt)
    # 关键词与状态必须同处一个子查询：只应存在一个 SELECT
    assert compiled.upper().count("SELECT") == 1
    assert "巡检" in compiled
    assert "failed" in compiled
    assert "user_id = '7'" in compiled


def test_grouped_keyword_uses_conversation_key_subquery():
    query, _ = build_history_query(
        user_id="7", keyword="巡检", group_by_conversation=True
    )
    assert _has_turn_filter_subquery(_compile(query))


def test_grouped_status_uses_conversation_key_subquery():
    query, _ = build_history_query(
        user_id="7", status="failed", group_by_conversation=True
    )
    assert _has_turn_filter_subquery(_compile(query))


def test_ungrouped_keyword_filters_current_row_only():
    query, _ = build_history_query(
        user_id="7", keyword="巡检", group_by_conversation=False
    )
    compiled = _compile(query)
    assert not _has_turn_filter_subquery(compiled)
    assert "巡检" in compiled


def test_grouped_query_without_turn_filters_has_no_subquery_filter():
    query, _ = build_history_query(user_id="7", group_by_conversation=True)
    assert not _has_turn_filter_subquery(_compile(query))


def test_time_range_filters_representative_row():
    query, _ = build_history_query(
        user_id="7",
        start_dt=datetime(2026, 9, 1),
        group_by_conversation=True,
    )
    assert "created_at >=" in _compile(query)


def test_count_query_is_built_before_pagination():
    query, count = build_history_query(user_id="7", page=3, page_size=20)
    assert "LIMIT" in _compile(query).upper()
    assert "LIMIT" not in _compile(count).upper()


def test_scope_filter_is_applied_in_grouped_mode():
    query, _ = build_history_query(
        user_id="7", scope="task", group_by_conversation=True
    )
    assert "task_conv_%" in _compile(query)


def test_username_scope_used_when_user_id_absent():
    query, _ = build_history_query(username="alice", group_by_conversation=False)
    assert "username = 'alice'" in _compile(query)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/services/test_history_query.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.services.ai.history_query'`

- [ ] **Step 3: 实现模块**

新建 `app/services/ai/history_query.py`：

```python
"""会话历史查询构建。

把多维度筛选条件收敛为可独立单测的纯函数，避免在 endpoint 内联拼装查询时
遗漏语义分层：会话级属性（来源 / 智能体 / 时间）过滤分组代表行，轮次级属性
（关键词 / 状态）需判定「会话内是否存在满足条件的轮次」。
"""

from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import Select, func, not_, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.models.audit import AgentExecutionHistory as History


TASK_CONVERSATION_PREFIX = "task_conv_"
VALID_SCOPES = ("all", "task", "chat")


def normalize_scope(scope: Optional[str]) -> str:
    """把空值与非法取值统一降级为 all，避免前端灰度期出现硬失败。"""
    value = str(scope or "").strip().lower()
    return value if value in VALID_SCOPES else "all"


def conversation_key(model=History) -> ColumnElement:
    """会话分组键。

    必须与分组使用的键完全一致；``trace_id`` 为 NOT NULL，因此该表达式
    永不为 NULL，可安全用于 ``IN`` 子查询而不受 SQL 三值逻辑影响。
    """
    return func.coalesce(model.conversation_id, model.trace_id)


def scope_condition(scope: Optional[str]) -> Optional[ColumnElement]:
    """会话来源过滤，属于会话级属性，可安全用于分组代表行。"""
    normalized = normalize_scope(scope)
    if normalized == "task":
        return History.conversation_id.like(f"{TASK_CONVERSATION_PREFIX}%")
    if normalized == "chat":
        # NULL 必须显式包含：SQL 中 NULL NOT LIKE 'x' 的结果是 NULL（非真），
        # 若不处理会导致 conversation_id 为空的历史在「普通对话」下消失。
        return or_(
            History.conversation_id.is_(None),
            not_(History.conversation_id.like(f"{TASK_CONVERSATION_PREFIX}%")),
        )
    return None


def turn_level_conversation_keys(
    *,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
) -> Select:
    """返回「存在满足条件轮次」的会话键集合。

    关键词与状态被合并进同一个子查询，因此语义是「同一会话内存在同时满足
    两者的轮次」，而不是「某轮命中关键词」且「另一轮失败」。

    用户约束基于子查询自身的表别名重建：子查询与外层引用同一张表，直接复用
    外层实体表达式会产生同表引用歧义。
    """
    turn = aliased(History)
    conditions = []
    if user_id is not None:
        conditions.append(turn.user_id == user_id)
    elif username:
        conditions.append(turn.username == username)
    if keyword:
        pattern = f"%{keyword}%"
        conditions.append(or_(turn.query.like(pattern), turn.summary.like(pattern)))
    if status:
        conditions.append(turn.status == status)
    return select(conversation_key(turn)).where(*conditions).distinct()


def build_history_query(
    *,
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    agent_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    scope: Optional[str] = None,
    group_by_conversation: bool = False,
) -> Tuple[Select, Select]:
    """构建历史列表查询与总数查询。

    返回 ``(rows_query, count_query)``；``count_query`` 在分页之前构建，
    因此总数统计不受 offset / limit 影响。
    """
    session_filters = []
    if user_id is not None:
        session_filters.append(History.user_id == user_id)
    elif username:
        session_filters.append(History.username == username)

    source = scope_condition(scope)
    if source is not None:
        session_filters.append(source)

    if group_by_conversation:
        grouped = (
            select(
                func.max(History.id).label("max_id"),
                func.count(History.id).label("turn_count"),
            )
            .where(*session_filters)
            .group_by(conversation_key())
            .subquery()
        )
        query = select(History, grouped.c.turn_count).join(
            grouped, History.id == grouped.c.max_id
        )
    else:
        query = select(History)

    if session_filters:
        query = query.where(*session_filters)

    if agent_id:
        query = query.where(History.agent_id == agent_id)
    if conversation_id:
        query = query.where(History.conversation_id == conversation_id)
    if start_dt:
        query = query.where(History.created_at >= start_dt)
    if end_dt:
        query = query.where(History.created_at <= end_dt)

    if group_by_conversation:
        # 轮次级条件：代表行的会话键命中即等价于该会话存在匹配轮次，
        # 因为同一会话的所有行共享同一会话键。
        if keyword or status:
            query = query.where(
                conversation_key().in_(
                    turn_level_conversation_keys(
                        user_id=user_id,
                        username=username,
                        keyword=keyword,
                        status=status,
                    )
                )
            )
    else:
        # 非分组模式每行即一轮，直接过滤当前行，不套用会话级存在性判定。
        if keyword:
            pattern = f"%{keyword}%"
            query = query.where(
                or_(History.query.like(pattern), History.summary.like(pattern))
            )
        if status:
            query = query.where(History.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    query = (
        query.order_by(History.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return query, count_query
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/services/test_history_query.py -v`
Expected: PASS（全部用例通过）

编译断言已按实际 SQLAlchemy 输出校准：轮次筛选子查询带有 `DISTINCT`，实际编译结果是 `IN (SELECT DISTINCT coalesce(...)`，因此必须使用 `_has_turn_filter_subquery` 的正则匹配，不能使用 `"IN (SELECT"` 字面量。同时已确认 `aliased` 会让子查询使用 `ai_agent_execution_history_1` 别名，与外层同表引用不冲突。

- [ ] **Step 5: 提交点（用户执行）**

建议 commit message：

```
feat(history): 新增会话历史查询构建模块，支持来源筛选与轮次级检索语义
```

---

## Task 2: 接入 `get_history`

**Files:**
- Modify: `app/api/v1/endpoints/chat.py:2083-2181`

- [ ] **Step 1: 新增查询参数**

在 `get_history` 签名中，`group_by_conversation` 之后新增一行：

```python
    group_by_conversation: bool = False,
    scope: Optional[str] = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db_session)
):
```

- [ ] **Step 2: 替换查询拼装逻辑**

用下面这段替换 `chat.py` 中从 `# 1. Base Query` 注释开始、到分页语句结束的整段（即原 `2130`–`2179` 行区间的内容）：

```python
    # 1. 查询构建收敛到 app/services/ai/history_query.py：
    #    会话级属性（来源 / 智能体 / 时间）过滤分组代表行，
    #    轮次级属性（关键词 / 状态）以会话键 IN 子查询判定会话内存在性。
    query, count_query = build_history_query(
        page=page,
        page_size=page_size,
        user_id=history_user_id,
        username=username,
        agent_id=agent_id,
        conversation_id=conversation_id,
        keyword=keyword,
        status=status,
        start_dt=start_dt,
        end_dt=end_dt,
        scope=scope,
        group_by_conversation=group_by_conversation,
    )

    # 2. Get Total Count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
```

- [ ] **Step 3: 添加导入**

在文件顶部导入区（`from app.services.ai.memory_service import memory_service` 一行之后）新增：

```python
from app.services.ai.history_query import build_history_query
```

- [ ] **Step 4: 清理死代码与不再使用的导入**

替换查询拼装后会产生两处死代码，必须一并清理，否则会误导后续维护者以为它们仍参与查询：

**(a) 局部导入精简。** 原函数内的 `from sqlalchemy import select, or_, desc, func` 中，`or_` / `desc` / `func` 已无引用，改为：

```python
    from sqlalchemy import select
```

`select` 在后面的管理员分支（`select(User.user_name, User.id)`）仍在用，**不要删除**。

**(b) 删除 `scope_filters` 及其唯一依赖。** 用户范围现在由 `history_query` 内部重建，因此下面这段在替换后不再有任何作用：

```python
    scope_filters = []
    if history_user_id is not None:
        scope_filters.append(AgentExecutionHistory.user_id == history_user_id)
    elif username:
        scope_filters.append(AgentExecutionHistory.username == username)
```

连同函数内仅服务于它的导入一起删除：

```python
    from app.models.audit import AgentExecutionHistory
```

注意：`user_info` 的 401 校验与 `history_user_id` 的计算**必须保留**（后续资源范围查询仍在使用 `history_user_id`），只删除 `scope_filters` 这一段。

Run: `grep -n "scope_filters" app/api/v1/endpoints/chat.py`
Expected: 无输出

Run: `python -m pyflakes app/api/v1/endpoints/chat.py`（若 pyflakes 未安装，退化为 `python -m compileall app/api/v1/endpoints/chat.py`）
Expected: 无 `undefined name`。该文件在改动前已有 4 条 `imported but unused` / `redefinition` 历史告警（`timedelta`、`Response`、`PermissionService`），本次不应新增。

- [ ] **Step 5: 运行相关测试**

Run: `python -m pytest tests/services/test_history_query.py tests/api/v1/ -q`
Expected: PASS

- [ ] **Step 6: 提交点（用户执行）**

建议 commit message：

```
feat(history): get_history 支持 scope 筛选并修复分组模式检索语义
```

---

## Task 3: 前端共享筛选组合式

**Files:**
- Create: `frontend/src/composables/chat/useHistoryFilters.ts`

- [ ] **Step 1: 新建文件**

```ts
/** 会话历史筛选：类型定义、默认值与请求参数映射的唯一来源。 */

export type HistoryFilterScope = "all" | "task" | "chat";
export type HistoryFilterStatus = "" | "success" | "failed";
export type HistoryFilterTimeRange = "" | "today" | "7d" | "30d" | "custom";

export interface ChatHistoryFilters {
  scope: HistoryFilterScope;
  agentId: string;
  status: HistoryFilterStatus;
  timeRange: HistoryFilterTimeRange;
  startDate: string;
  endDate: string;
}

export interface AgentOption {
  id: string;
  display_name: string;
  avatar_url?: string;
}

export const DEFAULT_HISTORY_FILTERS: ChatHistoryFilters = {
  scope: "all",
  agentId: "",
  status: "",
  timeRange: "",
  startDate: "",
  endDate: "",
};

const DAY_MS = 24 * 60 * 60 * 1000;

function startOfToday(now: Date): Date {
  const d = new Date(now.getTime());
  d.setHours(0, 0, 0, 0);
  return d;
}

/** 把时间范围换算为后端 start_date / end_date。 */
export function buildHistoryTimeRange(
  filters: Pick<ChatHistoryFilters, "timeRange" | "startDate" | "endDate">,
  now: Date = new Date()
): { start_date?: string; end_date?: string } {
  switch (filters.timeRange) {
    case "today":
      return {
        start_date: startOfToday(now).toISOString(),
        end_date: now.toISOString(),
      };
    case "7d":
      return {
        start_date: new Date(now.getTime() - 7 * DAY_MS).toISOString(),
        end_date: now.toISOString(),
      };
    case "30d":
      return {
        start_date: new Date(now.getTime() - 30 * DAY_MS).toISOString(),
        end_date: now.toISOString(),
      };
    case "custom": {
      const start = filters.startDate ? new Date(`${filters.startDate}T00:00:00`) : null;
      const end = filters.endDate ? new Date(`${filters.endDate}T23:59:59`) : null;
      if (start && end && start.getTime() > end.getTime()) {
        // 起止颠倒时自动校正，避免出现恒空的结果集
        return { start_date: end.toISOString(), end_date: start.toISOString() };
      }
      return {
        ...(start ? { start_date: start.toISOString() } : {}),
        ...(end ? { end_date: end.toISOString() } : {}),
      };
    }
    default:
      return {};
  }
}

/** 把筛选状态映射为历史接口查询参数；默认值不下发。 */
export function buildHistoryFilterParams(
  filters: ChatHistoryFilters,
  now: Date = new Date()
): Record<string, string> {
  const params: Record<string, string> = {};
  if (filters.scope && filters.scope !== "all") params.scope = filters.scope;
  if (filters.agentId) params.agent_id = filters.agentId;
  if (filters.status) params.status = filters.status;
  Object.assign(params, buildHistoryTimeRange(filters, now));
  return params;
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无该文件相关错误

- [ ] **Step 3: 提交点（用户执行）**

建议 commit message：

```
feat(history): 新增前端会话历史筛选共享组合式
```

---

## Task 4: 侧边栏筛选交互

**Files:**
- Modify: `frontend/src/components/ChatHistorySidebar.vue`

- [ ] **Step 1: 导入筛选类型并扩展 props 与 emits**

**类型必须单一来源**：`ChatHistoryFilters` 与 `AgentOption` 已在 Task 3 的组合式中定义，**不要**在组件内重复声明这两个 interface（重复定义会随时间与实际契约漂移）。在 `<script setup>` 顶部的 vue import 之后新增：

```ts
import type {
  AgentOption,
  ChatHistoryFilters,
} from "@/composables/chat/useHistoryFilters";
```

然后将原有的 `defineProps` / `defineEmits` 整体替换为：

```ts
const props = withDefaults(
  defineProps<{
    visible: boolean;
    loading: boolean;
    loadingMore?: boolean;
    hasMore?: boolean;
    historyList: any[];
    activeTraceId?: string;
    activeConversationId?: string;
    modelValue: string; // keyword
    filters?: ChatHistoryFilters;
    availableAgents?: AgentOption[];
    showAgentFilter?: boolean;
  }>(),
  {
    loadingMore: false,
    hasMore: false,
    activeTraceId: "",
    activeConversationId: "",
    availableAgents: () => [],
    showAgentFilter: false,
    filters: () => ({
      scope: "all",
      agentId: "",
      status: "",
      timeRange: "",
      startDate: "",
      endDate: "",
    }),
  }
);

const emit = defineEmits<{
  (e: "update:visible", value: boolean): void;
  (e: "update:modelValue", value: string): void;
  (e: "update:filters", value: ChatHistoryFilters): void;
  (e: "reset-filters"): void;
  (e: "fetch-history"): void;
  (e: "load-more"): void;
  (e: "load-chat", item: any): void;
  (e: "open-full-logs", traceId: string): void;
  (e: "delete-history", item: any): void;
  (e: "delete-group", group: any): void;
  (e: "new-chat"): void;
  (e: "export-chat", item: any): void;
}>();
```

- [ ] **Step 2: 新增筛选状态与派生逻辑**

在 `// Search keyword with Debounce` 代码块**之前**插入：

```ts
// --- Filter Panel State ---
const filterPanelOpen = ref(false);
const filterPanelRef = ref<HTMLElement | null>(null);
const filterButtonRef = ref<HTMLElement | null>(null);

const scopeOptions: Array<{ value: ChatHistoryFilters["scope"]; label: string }> = [
  { value: "all", label: "全部" },
  { value: "task", label: "任务会话" },
  { value: "chat", label: "普通对话" },
];

const statusOptions: Array<{ value: ChatHistoryFilters["status"]; label: string }> = [
  { value: "", label: "全部" },
  { value: "success", label: "成功" },
  { value: "failed", label: "失败" },
];

const timeOptions: Array<{ value: ChatHistoryFilters["timeRange"]; label: string }> = [
  { value: "", label: "全部" },
  { value: "today", label: "今天" },
  { value: "7d", label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
  { value: "custom", label: "自定义" },
];

const currentFilters = computed<ChatHistoryFilters>(() => ({
  scope: props.filters?.scope ?? "all",
  agentId: props.filters?.agentId ?? "",
  status: props.filters?.status ?? "",
  timeRange: props.filters?.timeRange ?? "",
  startDate: props.filters?.startDate ?? "",
  endDate: props.filters?.endDate ?? "",
}));

const activeFilterChips = computed(() => {
  const f = currentFilters.value;
  const chips: Array<{ key: keyof ChatHistoryFilters; label: string }> = [];
  if (f.scope === "task") chips.push({ key: "scope", label: "任务会话" });
  if (f.scope === "chat") chips.push({ key: "scope", label: "普通对话" });
  if (f.agentId) {
    const matched = props.availableAgents.find((a) => a.id === f.agentId);
    chips.push({ key: "agentId", label: matched?.display_name || "指定智能体" });
  }
  if (f.status === "success") chips.push({ key: "status", label: "成功" });
  if (f.status === "failed") chips.push({ key: "status", label: "失败" });
  if (f.timeRange === "today") chips.push({ key: "timeRange", label: "今天" });
  if (f.timeRange === "7d") chips.push({ key: "timeRange", label: "近 7 天" });
  if (f.timeRange === "30d") chips.push({ key: "timeRange", label: "近 30 天" });
  if (f.timeRange === "custom") chips.push({ key: "timeRange", label: "自定义时间" });
  return chips;
});

const activeFilterCount = computed(() => activeFilterChips.value.length);

const emitFilters = (patch: Partial<ChatHistoryFilters>) => {
  emit("update:filters", { ...currentFilters.value, ...patch });
};

const removeFilter = (key: keyof ChatHistoryFilters) => {
  if (key === "scope") {
    emitFilters({ scope: "all" });
  } else if (key === "agentId") {
    emitFilters({ agentId: "" });
  } else if (key === "status") {
    emitFilters({ status: "" });
  } else if (key === "timeRange") {
    emitFilters({ timeRange: "", startDate: "", endDate: "" });
  }
};

const resetFilters = () => {
  emit("reset-filters");
};

const toggleFilterPanel = () => {
  filterPanelOpen.value = !filterPanelOpen.value;
};

const handleDocumentClick = (event: MouseEvent) => {
  if (!filterPanelOpen.value) return;
  const target = event.target as Node;
  if (filterPanelRef.value?.contains(target)) return;
  if (filterButtonRef.value?.contains(target)) return;
  filterPanelOpen.value = false;
};

const handleEscape = (event: KeyboardEvent) => {
  if (event.key === "Escape") filterPanelOpen.value = false;
};

// 筛选变化时展开全部分组，避免默认折叠的「更早」组隐藏筛选结果
watch(
  () => props.filters,
  () => {
    collapsedGroups.value = { older: false };
  },
  { deep: true }
);
```

- [ ] **Step 3: 注册全局事件监听**

将现有的 `onMounted` / `onUnmounted` 替换为：

```ts
onMounted(() => {
  window.addEventListener("resize", handleResize);
  document.addEventListener("click", handleDocumentClick);
  document.addEventListener("keydown", handleEscape);
});

onUnmounted(() => {
  window.removeEventListener("resize", handleResize);
  document.removeEventListener("click", handleDocumentClick);
  document.removeEventListener("keydown", handleEscape);
});
```

- [ ] **Step 4: 改造搜索行**

将 `<!-- Search Bar with Debounce & Clear -->` 整个 `div` 块替换为：

```html
      <!-- Search Bar with Debounce & Clear + Filter Entry -->
      <div class="px-3 py-2.5 border-b border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 flex-shrink-0 relative">
        <div class="flex items-center gap-1.5">
          <div class="relative flex items-center flex-1 min-w-0">
            <svg
              class="w-3.5 h-3.5 text-gray-400 absolute left-3 pointer-events-none"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
              />
            </svg>
            <input
              v-model="keyword"
              @input="handleSearchInput"
              type="search"
              placeholder="搜索历史记录..."
              class="w-full pl-8 pr-7 py-1.5 text-xs bg-gray-50/80 dark:bg-gray-800/80 border border-gray-200/80 dark:border-gray-700/80 rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all placeholder-gray-400 text-gray-700 dark:text-gray-200"
            />
            <button
              v-if="keyword"
              @click="clearSearch"
              type="button"
              class="absolute right-2.5 p-0.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-full transition-colors"
              title="清空搜索"
            >
              <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <!-- 漏斗入口：置于输入框外部，避免与内嵌清空按钮挤压 -->
          <button
            ref="filterButtonRef"
            type="button"
            @click.stop="toggleFilterPanel"
            class="relative p-1.5 rounded-xl border transition-colors flex-shrink-0"
            :class="
              activeFilterCount > 0
                ? 'border-primary/40 text-primary bg-primary/5'
                : 'border-gray-200/80 dark:border-gray-700/80 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200'
            "
            title="筛选历史会话"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="1.8"
                d="M4 6h16M7 12h10M10 18h4"
              />
            </svg>
            <span
              v-if="activeFilterCount > 0"
              class="absolute -top-1 -right-1 min-w-[14px] h-[14px] px-0.5 rounded-full bg-primary text-white text-[9px] font-bold flex items-center justify-center"
            >
              {{ activeFilterCount }}
            </span>
          </button>
        </div>

        <!-- 激活筛选回显 -->
        <div v-if="activeFilterCount > 0" class="mt-2 flex items-center gap-1.5 overflow-x-auto custom-scrollbar">
          <button
            v-for="chip in activeFilterChips"
            :key="chip.key"
            type="button"
            @click="removeFilter(chip.key)"
            class="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-primary/10 text-primary text-[10px] font-semibold whitespace-nowrap hover:bg-primary/20 transition-colors"
          >
            {{ chip.label }}
            <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- 筛选面板：桌面端浮层，移动端内联展开 -->
        <div
          v-if="filterPanelOpen"
          ref="filterPanelRef"
          class="rounded-2xl border border-gray-200/80 dark:border-gray-700/80 bg-white dark:bg-gray-900 shadow-lg p-3 space-y-3 z-30"
          :class="isMobile ? 'mt-2' : 'absolute left-3 right-3 top-full mt-1'"
        >
          <div>
            <div class="text-[10px] font-bold text-gray-400 mb-1.5">会话来源</div>
            <div class="grid grid-cols-3 gap-1">
              <button
                v-for="opt in scopeOptions"
                :key="opt.value"
                type="button"
                @click="emitFilters({ scope: opt.value })"
                class="px-2 py-1 rounded-lg text-[11px] font-semibold transition-colors border"
                :class="
                  currentFilters.scope === opt.value
                    ? 'bg-primary/10 text-primary border-primary/30'
                    : 'text-gray-500 dark:text-gray-400 border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'
                "
              >
                {{ opt.label }}
              </button>
            </div>
          </div>

          <div v-if="showAgentFilter">
            <div class="text-[10px] font-bold text-gray-400 mb-1.5">智能体</div>
            <select
              :value="currentFilters.agentId"
              @change="emitFilters({ agentId: ($event.target as HTMLSelectElement).value })"
              class="w-full px-2 py-1 text-[11px] bg-gray-50/80 dark:bg-gray-800/80 border border-gray-200/80 dark:border-gray-700/80 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 text-gray-700 dark:text-gray-200"
            >
              <option value="">全部智能体</option>
              <option v-for="agent in availableAgents" :key="agent.id" :value="agent.id">
                {{ agent.display_name }}
              </option>
            </select>
          </div>

          <div>
            <div class="text-[10px] font-bold text-gray-400 mb-1.5">执行状态</div>
            <div class="grid grid-cols-3 gap-1">
              <button
                v-for="opt in statusOptions"
                :key="opt.value"
                type="button"
                @click="emitFilters({ status: opt.value })"
                class="px-2 py-1 rounded-lg text-[11px] font-semibold transition-colors border"
                :class="
                  currentFilters.status === opt.value
                    ? 'bg-primary/10 text-primary border-primary/30'
                    : 'text-gray-500 dark:text-gray-400 border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'
                "
              >
                {{ opt.label }}
              </button>
            </div>
          </div>

          <div>
            <div class="text-[10px] font-bold text-gray-400 mb-1.5">时间范围</div>
            <div class="grid grid-cols-3 gap-1">
              <button
                v-for="opt in timeOptions"
                :key="opt.value"
                type="button"
                @click="emitFilters({ timeRange: opt.value })"
                class="px-2 py-1 rounded-lg text-[11px] font-semibold transition-colors border"
                :class="
                  currentFilters.timeRange === opt.value
                    ? 'bg-primary/10 text-primary border-primary/30'
                    : 'text-gray-500 dark:text-gray-400 border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'
                "
              >
                {{ opt.label }}
              </button>
            </div>
            <div v-if="currentFilters.timeRange === 'custom'" class="mt-2 grid grid-cols-2 gap-1.5">
              <input
                type="date"
                :value="currentFilters.startDate"
                @change="emitFilters({ startDate: ($event.target as HTMLInputElement).value })"
                class="px-2 py-1 text-[11px] bg-gray-50/80 dark:bg-gray-800/80 border border-gray-200/80 dark:border-gray-700/80 rounded-lg text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
              <input
                type="date"
                :value="currentFilters.endDate"
                @change="emitFilters({ endDate: ($event.target as HTMLInputElement).value })"
                class="px-2 py-1 text-[11px] bg-gray-50/80 dark:bg-gray-800/80 border border-gray-200/80 dark:border-gray-700/80 rounded-lg text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
            </div>
          </div>

          <div class="pt-1 border-t border-gray-100 dark:border-gray-800 flex justify-end">
            <button
              type="button"
              @click="resetFilters"
              class="px-2.5 py-1 rounded-lg text-[11px] font-semibold text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
            >
              重置筛选
            </button>
          </div>
        </div>
      </div>
```

- [ ] **Step 5: 区分空结果态文案**

将历史列表中的 `<!-- Empty State -->` 块替换为：

```html
        <!-- Empty State -->
        <div v-else-if="!historyList.length" class="p-8 text-center flex flex-col items-center justify-center h-4/5">
          <div class="w-14 h-14 bg-primary/5 dark:bg-primary/10 rounded-2xl flex items-center justify-center mx-auto mb-3 border border-primary/10">
            <svg
              class="w-7 h-7 text-primary/60"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="1.8"
                d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
              />
            </svg>
          </div>
          <p class="text-xs font-bold text-gray-600 dark:text-gray-300 mb-1">
            {{ activeFilterCount > 0 || keyword ? '无匹配筛选结果' : '暂无会话历史' }}
          </p>
          <p class="text-[11px] text-gray-400 dark:text-gray-500 mb-4 max-w-[180px]">
            {{
              activeFilterCount > 0 || keyword
                ? '试试放宽筛选条件或更换关键词'
                : '开启新对话，即可记录您的灵感与工作流'
            }}
          </p>
          <button
            v-if="activeFilterCount > 0"
            @click="resetFilters"
            class="px-3.5 py-1.5 rounded-lg border border-primary/30 text-primary text-xs font-semibold hover:bg-primary/5 transition-colors"
          >
            重置筛选
          </button>
          <button
            v-else
            @click="emit('new-chat')"
            class="px-3.5 py-1.5 rounded-lg border border-primary/30 text-primary text-xs font-semibold hover:bg-primary/5 transition-colors"
          >
            开启新对话
          </button>
        </div>
```

- [ ] **Step 6: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无 `ChatHistorySidebar.vue` 相关错误

- [ ] **Step 7: 提交点（用户执行）**

建议 commit message：

```
feat(history): 侧边栏新增漏斗筛选面板与激活条件回显
```

---

## Task 5: `EmbedChat` 接线

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`

- [ ] **Step 1: 导入共享组合式**

在 `import ChatHistorySidebar from "@/components/ChatHistorySidebar.vue";`（第 2342 行）之后新增：

```ts
import {
  DEFAULT_HISTORY_FILTERS,
  buildHistoryFilterParams,
  type ChatHistoryFilters,
} from "@/composables/chat/useHistoryFilters";
```

- [ ] **Step 2: 新增筛选状态**

在 `const historyKeyword = ref("");`（第 5379 行）之后新增：

```ts
// --- History Filters ---
const historyFilters = ref<ChatHistoryFilters>({ ...DEFAULT_HISTORY_FILTERS });

const resetHistoryFilters = () => {
  historyFilters.value = { ...DEFAULT_HISTORY_FILTERS };
};

// 集成锁定场景下历史已被限定为单一智能体，智能体筛选无意义
const showAgentFilter = computed(() => !config.agentId);

const historyAgentOptions = computed(() =>
  (allowedAgents.value || []).map((agent: any) => ({
    id: String(agent.id),
    display_name: agent.display_name || agent.name || "未命名智能体",
    avatar_url: agent.avatar_url || "",
  }))
);

watch(
  historyFilters,
  () => {
    fetchHistory();
  },
  { deep: true }
);
```

- [ ] **Step 3: 透传查询参数**

在 `fetchHistory` 中，将 `if (config.agentId) params.agent_id = config.agentId;`（第 5438 行）替换为：

```ts
    Object.assign(params, buildHistoryFilterParams(historyFilters.value));
    // 集成锁定的优先级最高，必须最后覆盖
    if (config.agentId) params.agent_id = config.agentId;
```

- [ ] **Step 4: 组件接线**

将 `<ChatHistorySidebar ... />` 的绑定（第 7-24 行）替换为：

```html
    <ChatHistorySidebar
      v-model:visible="showHistorySidebar"
      v-model="historyKeyword"
      :loading="loadingHistory"
      :loading-more="loadingMoreHistory"
      :has-more="historyHasMore"
      :history-list="groupedHistoryList"
      :active-conversation-id="conversationId"
      :filters="historyFilters"
      :available-agents="historyAgentOptions"
      :show-agent-filter="showAgentFilter"
      @update:filters="historyFilters = $event"
      @reset-filters="resetHistoryFilters"
      @fetch-history="fetchHistory()"
      @load-more="fetchHistory(true)"
      @load-chat="handleHistoryClick"
      @open-full-logs="openTraceLogs"
      @delete-history="handleDeleteSingleHistory"
      @delete-group="handleDeleteGroup"
      @new-chat="handleNewChatFromSidebar"
      @export-chat="handleExportChatFromSidebar"
      class="border-r border-gray-200 dark:border-gray-800"
    />
```

- [ ] **Step 5: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无 `EmbedChat.vue` 相关错误

- [ ] **Step 6: 提交点（用户执行）**

建议 commit message：

```
feat(history): EmbedChat 接入会话历史多维筛选
```

---

## Task 6: `AgentDebug` 接线

**Files:**
- Modify: `frontend/src/views/AgentDebug.vue`

- [ ] **Step 1: 导入共享组合式**

在 `import ChatHistorySidebar from "@/components/ChatHistorySidebar.vue";`（第 7 行）之后新增：

```ts
import {
  DEFAULT_HISTORY_FILTERS,
  buildHistoryFilterParams,
  type ChatHistoryFilters,
} from "@/composables/chat/useHistoryFilters";
```

- [ ] **Step 2: 新增筛选状态**

`AgentDebug.vue` 已有 `const agents = ref<any[]>([]);`（第 377 行，由 `/api/portal/agents/` 填充并已过滤 `is_enabled`）。在该行**之后**新增（放在 `agents` 定义之后，避免对未初始化变量的引用）：

```ts
const historyFilters = ref<ChatHistoryFilters>({ ...DEFAULT_HISTORY_FILTERS });

const resetHistoryFilters = () => {
  historyFilters.value = { ...DEFAULT_HISTORY_FILTERS };
};

// 当前调试上下文已锁定智能体时，历史只能是该智能体的会话，筛选无意义
const showAgentFilter = computed(() => !agentParams.agent_id);

const historyAgentOptions = computed(() =>
  (agents.value || []).map((agent: any) => ({
    id: String(agent.id),
    display_name: agent.display_name || agent.name || "未命名智能体",
    avatar_url: agent.avatar_url || "",
  }))
);

watch(
  historyFilters,
  () => {
    fetchHistory();
  },
  { deep: true }
);
```

- [ ] **Step 3: 透传查询参数**

将 `fetchHistory` 中参数构造的一段（第 346-352 行）替换为：

```ts
    const params: any = { page: 1, page_size: 50, group_by_conversation: true };
    if (historyKeyword.value) {
      params.keyword = historyKeyword.value;
    }
    Object.assign(params, buildHistoryFilterParams(historyFilters.value));
    // 当前调试上下文的优先级最高，必须最后覆盖
    if (agentParams.agent_id) {
      params.agent_id = agentParams.agent_id;
    }
```

- [ ] **Step 4: 组件接线**

将 `<ChatHistorySidebar ... />`（第 4121-4130 行）替换为：

```html
    <ChatHistorySidebar
      v-model:visible="showHistorySidebar"
      v-model="historyKeyword"
      :loading="loadingHistory"
      :history-list="groupedHistoryList"
      :active-trace-id="activeTraceId"
      :filters="historyFilters"
      :available-agents="historyAgentOptions"
      :show-agent-filter="showAgentFilter"
      @update:filters="historyFilters = $event"
      @reset-filters="resetHistoryFilters"
      @fetch-history="fetchHistory"
      @load-chat="openSessionPreview"
      @open-full-logs="openSessionPreview"
    />
```

- [ ] **Step 5: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无 `AgentDebug.vue` 相关错误

- [ ] **Step 6: 提交点（用户执行）**

建议 commit message：

```
feat(history): 智能体调试页同步接入会话历史筛选
```

---

## Task 7: 前端契约测试

**Files:**
- Create: `tests/frontend/test_chat_history_filters_contract.py`

- [ ] **Step 1: 写测试**

```python
"""会话历史筛选：前后端契约与两处接线一致性。"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_shared_composable_is_the_single_source_of_filter_contract():
    shared = _read("frontend/src/composables/chat/useHistoryFilters.ts")
    for contract in (
        "export interface ChatHistoryFilters",
        "export interface AgentOption",
        "export const DEFAULT_HISTORY_FILTERS",
        "export function buildHistoryTimeRange",
        "export function buildHistoryFilterParams",
    ):
        assert contract in shared
    assert '"today"' in shared
    assert '"7d"' in shared
    assert '"30d"' in shared


def test_sidebar_exposes_filter_contract_and_keeps_funnel_outside_input():
    sidebar = _read("frontend/src/components/ChatHistorySidebar.vue")
    for contract in (
        "ChatHistoryFilters",
        "AgentOption",
        "update:filters",
        "reset-filters",
        "showAgentFilter",
        "availableAgents",
        "filterPanelOpen",
        "activeFilterChips",
        "activeFilterCount",
        "filterButtonRef",
        "clearSearch",
    ):
        assert contract in sidebar
    # 类型单一来源：必须从组合式导入，而不是在组件内重复定义
    assert "@/composables/chat/useHistoryFilters" in sidebar
    assert "interface ChatHistoryFilters" not in sidebar
    # 漏斗入口必须在输入框容器之外，避免与内嵌清空按钮挤压。
    # 注意：必须在 template 区间内比较——script 区也声明了这两个标识符
    # （如 `const filterButtonRef = ref(...)`），用全文首次匹配会命中 script 声明。
    template = sidebar[sidebar.index("<template"):]
    assert template.index("filterButtonRef") > template.index("clearSearch")


def test_sidebar_resets_collapsed_groups_on_filter_change():
    sidebar = _read("frontend/src/components/ChatHistorySidebar.vue")
    assert "collapsedGroups.value = { older: false }" in sidebar


def test_sidebar_distinguishes_empty_filter_result():
    sidebar = _read("frontend/src/components/ChatHistorySidebar.vue")
    assert "无匹配筛选结果" in sidebar
    assert "暂无会话历史" in sidebar


def test_embed_chat_maps_filters_into_request_params():
    embed = _read("frontend/src/views/EmbedChat.vue")
    for contract in (
        "useHistoryFilters",
        "historyFilters",
        "buildHistoryFilterParams",
        "resetHistoryFilters",
        "showAgentFilter",
        "@update:filters",
        "@reset-filters",
    ):
        assert contract in embed
    assert "historyPage.value = 1" in embed


def test_agent_debug_is_wired_like_embed_chat():
    debug = _read("frontend/src/views/AgentDebug.vue")
    for contract in (
        "useHistoryFilters",
        "historyFilters",
        "buildHistoryFilterParams",
        "resetHistoryFilters",
        "showAgentFilter",
        "@update:filters",
        "@reset-filters",
    ):
        assert contract in debug
    # 智能体候选复用调试页已有的 agents 数据源，不新增列表请求
    assert "agents.value" in debug
    assert "agentParams.agent_id" in debug


def test_backend_endpoint_uses_shared_query_builder():
    chat = _read("app/api/v1/endpoints/chat.py")
    assert "from app.services.ai.history_query import build_history_query" in chat
    assert "build_history_query(" in chat
    assert "scope: Optional[str] = None" in chat
```

- [ ] **Step 2: 运行测试确认通过**

Run: `python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_history_filters_contract.py -v`
Expected: PASS

若某个断言失败，说明对应实现步骤尚未完成或标识符命名与计划不一致——以本文件定义的契约为准回填实现。

- [ ] **Step 3: 提交点（用户执行）**

建议 commit message：

```
test(history): 补充会话历史筛选契约测试
```

---

## Task 8: 更新测试清单

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 追加变更登记**

按 `tests/CHECKLIST.md` 既有表格格式追加一行，内容需包含：

- 变更名称：会话历史多维筛选（来源 / 智能体 / 状态 / 时间范围）与分组模式检索语义修复
- 涉及文件：`app/services/ai/history_query.py`、`app/api/v1/endpoints/chat.py`、`frontend/src/composables/chat/useHistoryFilters.ts`、`frontend/src/components/ChatHistorySidebar.vue`、`frontend/src/views/EmbedChat.vue`、`frontend/src/views/AgentDebug.vue`
- **行为变更标注（重要）**：`GET /api/v1/chat/history` 在 `group_by_conversation=true` 时，`keyword` 与 `status` 的语义由「仅匹配每个会话的最新一轮」变为「匹配会话内任意轮次」；新增可选参数 `scope`（默认 `all`，向后兼容）
- 数据库变更：无

- [ ] **Step 2: 提交点（用户执行）**

建议 commit message：

```
docs(history): 更新测试清单，登记会话历史筛选变更与行为变更说明
```

---

## Task 9: 端到端验证（用户执行）

**Files:** 无

> 本任务冻结了「完成」的定义。以下验证由用户在控制台执行，因为项目严禁 Agent 运行 `./dev.sh`。

- [ ] **Step 1: 后端测试**

Run: `python -m pytest tests/services/test_history_query.py tests/api/v1/ -q`
Expected: PASS

- [ ] **Step 2: 前端契约测试**

Run: `python -m pytest --confcutdir=tests/frontend tests/frontend/ -q`
Expected: PASS

- [ ] **Step 3: 前端类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无错误

- [ ] **Step 4: 启动服务后人工验证**

由用户执行 `./dev.sh` 后，打开会话历史侧边栏逐项确认：

- 默认状态下侧边栏与改动前视觉一致（搜索框右侧多一个漏斗图标，无其他占位）
- 选择「任务会话」后列表只剩 `task_conv_` 前缀会话；切「普通对话」后反之
- 用一个只出现在某会话早期轮次的关键词搜索，该会话能被搜到（旧行为下搜不到）
- 「成功 / 失败」筛选生效，且能找到「历史上失败过但最近成功」的会话
- 「时间范围」近 7 天 / 近 30 天 / 自定义区间生效
- 切换筛选后分页从第 1 页重新请求，上一次滚动位置不造成漏数据
- 筛选「更早」分组内的结果时，该分组处于展开状态
- 无匹配时显示「无匹配筛选结果」并可一键重置
- 移动端（窄屏）下筛选面板内联展开且不遮挡
- 集成锁定场景（URL 带 `agent_id`）下不出现智能体下拉

- [ ] **Step 5: 确认无数据库迁移产生**

Run: `git status --short db-prod db-prod-pg`
Expected: 输出为空（本变更不涉及任何迁移脚本）

---

## 自检记录

**Spec 覆盖检查**（对照 `specs/chat-history-filtering/spec.md`）：

| Requirement | 覆盖任务 |
|---|---|
| 会话历史来源筛选 (Scope Filter) | Task 1 Step 3（`scope_condition` / `normalize_scope`）、Task 2 Step 1 |
| 会话历史多维度筛选参数 | Task 1 Step 3（`build_history_query` 各参数）、Task 2 Step 1-2 |
| 分组模式下的会话级检索语义 | Task 1 Step 3（`turn_level_conversation_keys` + `group_by_conversation` 分支）、Task 1 Step 1 对应用例 |
| 侧边栏筛选交互与激活态回显 | Task 4 Step 2/4 |
| 筛选变更的状态一致性 | Task 4 Step 2/5（分组展开、空态）、Task 5 Step 2（重置分页并重新拉取） |
| `agent-debug` History Management 扩展 | Task 6 |

**类型与命名一致性检查**：`ChatHistoryFilters` / `AgentOption` / `DEFAULT_HISTORY_FILTERS` / `buildHistoryTimeRange` / `buildHistoryFilterParams` 在 Task 3 定义，在 Task 4/5/6/7 中的引用名称与此完全一致；后端 `build_history_query` / `turn_level_conversation_keys` / `scope_condition` / `normalize_scope` / `conversation_key` 在 Task 1 定义，Task 2 与 Task 7 的引用一致。

**已知取舍**：`EmbedChat` 的智能体候选来自已有的 `allowedAgents`（`/api/portal/agents/allowed`），`AgentDebug` 来自已有的 `agents`（`/api/portal/agents/`），两处均复用既有数据源，**不新增任何智能体列表请求**。两者都在「当前上下文已锁定智能体」时（`config.agentId` / `agentParams.agent_id` 有值）隐藏智能体筛选，与 design 4.1 节的判定一致。

**编译假设实测验证**：Task 1 的模块代码与全部 28 条断言已用等价 SQLAlchemy 模型（SQLAlchemy 2.0.32）离线实测，结果 **28/28 通过**。验证过程抓出并修正了两个会导致实施直接失败的问题：

1. 轮次子查询的编译结果是 `IN (SELECT DISTINCT coalesce(...)`（带 `DISTINCT`），原先用 `"IN (SELECT"` 字面量断言会失败 —— 已改为正则匹配。
2. 分组查询的 `JOIN (SELECT max(...) ...)` 中本身含有 `IN (` 子串，会让正则 `IN \(\s*SELECT` 把「无轮次筛选」误判为「有轮次筛选」—— 已加词边界改为 `\bIN\s*\(\s*SELECT`。

同时确认：`aliased` 会让子查询使用 `ai_agent_execution_history_1` 别名，与外层同表引用不冲突，design 3.4 节「子查询必须基于自身别名重建用户约束」的要求成立且必要。
