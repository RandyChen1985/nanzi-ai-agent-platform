# 知识库检索范围门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当知识库轮次的实际检索范围超过阈值且用户未表态时，弹一张范围确认卡（全部 / 仅绑定 / 最相关 3 个 / 指定），把 100 库全量检索收敛为用户确认过的范围。

**Architecture:** 门禁接入点改为 `KnowledgeAgentRunner._execute_raw` 内部（原 spec 写的是 pipeline step，实施时发现 `setup_context` 在 `AssembleStep` 调用、pipeline 阶段拿不到 `AgentContext`，故下沉到 runner）。判定与选项构造放在新文件 `knowledge_scope_gate.py`，会话状态复用 `ConversationResourceService` 的 Redis scope，开关走「全局 ConfigService + 智能体 engine_config」双层。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x async / Pydantic 2 / pytest / Redis / Vue 3 + TS

**仓库约定：** 本仓库由用户执行 `git commit`，计划中的提交步骤改为「汇总改动待用户提交」，执行者**不要**自行 commit。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/services/ai/knowledge_catalog.py` | 新增 `rank_knowledge_catalog`（top-N 排序），保留 `match_knowledge_catalog` 不动 | 修改 |
| `app/services/ai/knowledge_scope_gate.py` | 门禁纯逻辑：配置解析、开关判定、选项构造、回执解析 | 新建 |
| `app/services/conversation_resource_service.py` | scope 增加 `knowledge_scope` / `pending_scope_gate` 两个字段 + `patch_scope` | 修改 |
| `app/services/ai/runners/knowledge_agent_runner.py` | 检索前插门禁、恢复用户选择 | 修改 |
| `frontend/src/views/SystemConfig.vue` | 3 个全局配置项 | 修改 |
| `frontend/src/components/agent/AgentVersionEditorDrawer.vue` | 「检索范围确认」三态开关 | 修改 |
| `frontend/src/views/EmbedChat.vue` | 范围卡片渲染 + 徽标微调 | 修改 |

---

## Task 1: 库级排序函数

**Files:**
- Modify: `app/services/ai/knowledge_catalog.py`（文件末尾 `__all__` 之前）
- Test: `tests/ai/test_knowledge_catalog_rank.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
"""rank_knowledge_catalog 排序与截断行为。"""
from app.services.ai.knowledge_catalog import (
    AuthorizedKnowledgeCatalog,
    KnowledgeBaseCatalogItem,
    match_knowledge_catalog,
    rank_knowledge_catalog,
)


def _catalog(items):
    return AuthorizedKnowledgeCatalog(status="available", items=tuple(items))


def test_rank_orders_by_score_and_limits():
    catalog = _catalog([
        KnowledgeBaseCatalogItem(ragflow_dataset_id="ds_fin", name="财务制度库", description="报销 发票 差旅"),
        KnowledgeBaseCatalogItem(ragflow_dataset_id="ds_travel", name="差旅管理", description="报销 差旅 标准"),
        KnowledgeBaseCatalogItem(ragflow_dataset_id="ds_ops", name="运维手册", description="部署 回滚"),
    ])
    ranked = rank_knowledge_catalog("差旅报销标准", catalog, limit=2)
    assert len(ranked) == 2
    assert {item[0] for item in ranked} == {"ds_fin", "ds_travel"}
    assert ranked[0][1] >= ranked[1][1]


def test_rank_returns_empty_without_overlap():
    catalog = _catalog([
        KnowledgeBaseCatalogItem(ragflow_dataset_id="ds_ops", name="运维手册", description="部署 回滚"),
    ])
    assert rank_knowledge_catalog("差旅报销标准", catalog, limit=3) == []


def test_rank_returns_empty_for_unavailable_catalog():
    assert rank_knowledge_catalog("报销", None, limit=3) == []
    assert rank_knowledge_catalog("报销", AuthorizedKnowledgeCatalog(status="empty"), limit=3) == []


def test_match_knowledge_catalog_behavior_unchanged():
    catalog = _catalog([
        KnowledgeBaseCatalogItem(ragflow_dataset_id="ds_fin", name="财务制度库", description="报销 发票 差旅"),
    ])
    matched = match_knowledge_catalog("差旅报销标准", catalog)
    assert matched.matched_ids == ("ds_fin",)
    assert matched.confidence in ("weak", "strong")
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/ai/test_knowledge_catalog_rank.py -v`
Expected: FAIL — `ImportError: cannot import name 'rank_knowledge_catalog'`

- [ ] **Step 3: 实现**

在 `knowledge_catalog.py` 的 `match_knowledge_catalog` 之后追加：

```python
def rank_knowledge_catalog(
    query: str,
    catalog: AuthorizedKnowledgeCatalog | None,
    *,
    limit: int = 3,
    min_score: float = 0.12,
) -> list[tuple[str, float]]:
    """按相关性返回 top-N (dataset_id, score)。

    与 ``match_knowledge_catalog`` 共用同一套信号与打分，区别只是返回排序列表而非单个最佳。
    ``match_knowledge_catalog`` 的语义保持不变（外层路由仍在用它的 strong/weak 判定）。
    """
    if catalog is None or catalog.status != "available" or not catalog.items:
        return []
    query_signals = _signals(query)
    if not query_signals:
        return []

    scored: list[tuple[float, int, str]] = []
    for item in catalog.items:
        name_signals, all_signals = _item_signals(item)
        overlap = query_signals & all_signals
        if not overlap:
            continue
        query_coverage = len(overlap) / len(query_signals)
        metadata_coverage = len(overlap) / max(len(all_signals), 1)
        score = 0.68 * query_coverage + 0.22 * metadata_coverage
        score += min(len(query_signals & name_signals), 3) * 0.1
        if score < min_score:
            continue
        scored.append((score, len(overlap), item.ragflow_dataset_id))

    scored.sort(reverse=True)
    return [(dataset_id, round(score, 4)) for score, _count, dataset_id in scored[: max(limit, 1)]]
```

并在 `__all__` 中加入 `"rank_knowledge_catalog"`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/ai/test_knowledge_catalog_rank.py -v`
Expected: PASS（4 passed）

---

## Task 2: 门禁纯逻辑模块

**Files:**
- Create: `app/services/ai/knowledge_scope_gate.py`
- Test: `tests/ai/test_knowledge_scope_gate.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
"""范围门禁的开关、与门与选项构造。"""
import pytest

from app.services.ai.knowledge_scope_gate import (
    SCOPE_ALL,
    SCOPE_BOUND,
    SCOPE_PICK,
    SCOPE_RANKED,
    ScopeGate,
    evaluate_scope_gate,
    parse_scope_choice,
)


class _Catalog:
    status = "available"

    def __init__(self, ids):
        self.items = tuple(
            type("Item", (), {"ragflow_dataset_id": i, "name": f"财务{i}", "description": "报销 差旅", "tags": None, "notes": ""})()
            for i in ids
        )


def _ids(n):
    return [f"ds_{i}" for i in range(n)]


@pytest.mark.asyncio
async def test_no_gate_when_scope_already_confirmed():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(10),
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope={"knowledge_scope": {"mode": "ranked", "dataset_ids": ["ds_0"]}},
        catalog=_Catalog(_ids(10)),
    )
    assert gate is None


@pytest.mark.asyncio
async def test_no_gate_when_explicit_selection_present():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(10),
        bound_ids=[],
        explicit_request_ids=["ds_0"],
        conversation_scope=None,
        catalog=_Catalog(_ids(10)),
    )
    assert gate is None


@pytest.mark.asyncio
async def test_no_gate_below_threshold():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(5),
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_Catalog(_ids(5)),
        enabled=True,
        threshold=5,
    )
    assert gate is None


@pytest.mark.asyncio
async def test_gate_opens_above_threshold_with_four_options():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(6),
        bound_ids=["ds_0", "ds_1"],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_Catalog(_ids(6)),
        enabled=True,
        threshold=5,
    )
    assert isinstance(gate, ScopeGate)
    assert gate.total_count == 6
    assert gate.bound_count == 2
    option_ids = [opt["id"] for opt in gate.options]
    assert option_ids[:2] == [SCOPE_RANKED, SCOPE_ALL]
    assert SCOPE_BOUND in option_ids
    assert SCOPE_PICK in option_ids


@pytest.mark.asyncio
async def test_gate_disabled_by_switch():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(50),
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_Catalog(_ids(50)),
        enabled=False,
        threshold=5,
    )
    assert gate is None


@pytest.mark.asyncio
async def test_bound_option_disabled_when_no_binding():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(8),
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_Catalog(_ids(8)),
        enabled=True,
        threshold=5,
    )
    bound_option = next(opt for opt in gate.options if opt["id"] == SCOPE_BOUND)
    assert bound_option["disabled"] is True


def test_parse_scope_choice_from_receipt():
    receipt = (
        "【用户回答】\n所选选项: 财务0 (__scope_ranked__)\n"
        "selected_option_ids: [\"__scope_ranked__\"]\ncustom_input: \n"
    )
    assert parse_scope_choice(receipt) == SCOPE_RANKED
    assert parse_scope_choice("普通提问") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/ai/test_knowledge_scope_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ai.knowledge_scope_gate'`

- [ ] **Step 3: 实现模块**

```python
"""知识库检索范围门禁：开关判定、与门校验、卡片选项构造与回执解析。

只做纯逻辑与数据组装，不访问数据库、不调用工具，便于单测覆盖。
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

SCOPE_RANKED = "__scope_ranked__"
SCOPE_ALL = "__scope_all__"
SCOPE_BOUND = "__scope_bound__"
SCOPE_PICK = "__scope_pick__"

DEFAULT_THRESHOLD = 5
DEFAULT_RANKED_LIMIT = 3

_SCOPE_OPTION_RE = re.compile(r"__scope_(?:ranked|all|bound|pick)__")


@dataclass(frozen=True)
class ScopeGate:
    """一次门禁的完整快照：选项用于出卡片，ID 列表用于回答后还原。"""

    gate_id: str
    options: list[dict[str, Any]] = field(default_factory=list)
    ranked_ids: tuple[str, ...] = ()
    bound_ids: tuple[str, ...] = ()
    all_ids: tuple[str, ...] = ()
    total_count: int = 0
    bound_count: int = 0

    def to_pending_payload(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "ranked_ids": list(self.ranked_ids),
            "bound_ids": list(self.bound_ids),
            "all_ids": list(self.all_ids),
        }


def _clean_ids(values: Sequence[Any] | None) -> list[str]:
    seen: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def build_scope_options(
    *,
    total_count: int,
    bound_count: int,
    ranked_names: Sequence[str] = (),
    ranked_count: int = 0,
) -> list[dict[str, Any]]:
    """构造四个卡片选项。选项 id 是标记，不是 dataset_id。"""
    ranked_label = "在 3 个最相关库中检索"
    if ranked_names:
        preview = "、".join(list(ranked_names)[:3])
        ranked_label = f"在 {ranked_count} 个最相关库中检索：{preview}"

    return [
        {
            "id": SCOPE_RANKED,
            "label": ranked_label,
            "description": "按本次问题与知识库名称/描述的匹配度自动挑选，通常足够回答",
            "disabled": ranked_count == 0,
        },
        {
            "id": SCOPE_ALL,
            "label": f"全部 {total_count} 个库",
            "description": f"在全部 {total_count} 个库中联合检索，速度较慢，可能召回不相关片段",
            "disabled": False,
        },
        {
            "id": SCOPE_BOUND,
            "label": f"仅智能体默认（{bound_count} 个）" if bound_count else "仅智能体默认（未绑定）",
            "description": "只检索该智能体配置的知识库范围" if bound_count else "该智能体没有绑定知识库，此项不可用",
            "disabled": bound_count == 0,
        },
        {
            "id": SCOPE_PICK,
            "label": "指定其他库…",
            "description": "打开知识库列表自行选择",
            "disabled": False,
        },
    ]


async def evaluate_scope_gate(
    *,
    query: str,
    effective_ids: Sequence[Any] | None,
    bound_ids: Sequence[Any] | None,
    explicit_request_ids: Sequence[Any] | None,
    conversation_scope: Mapping[str, Any] | None,
    catalog: Any,
    enabled: bool = True,
    threshold: int = DEFAULT_THRESHOLD,
    ranked_limit: int = DEFAULT_RANKED_LIMIT,
) -> ScopeGate | None:
    """四道与门同时成立才返回 gate；任一不满足返回 None（放行现有行为）。"""
    from app.services.ai.knowledge_catalog import rank_knowledge_catalog

    if not enabled:
        return None
    if _clean_ids(explicit_request_ids):
        return None
    scope_state = (conversation_scope or {}).get("knowledge_scope")
    if isinstance(scope_state, Mapping) and _clean_ids(scope_state.get("dataset_ids")):
        return None

    ids = _clean_ids(effective_ids)
    if len(ids) <= max(int(threshold), 0):
        return None

    bound = _clean_ids(bound_ids)
    ranked = rank_knowledge_catalog(query, catalog, limit=ranked_limit)
    ranked_ids = tuple(dataset_id for dataset_id, _score in ranked)
    name_by_id = {
        str(getattr(item, "ragflow_dataset_id", "")): str(getattr(item, "name", "") or "")
        for item in (getattr(catalog, "items", ()) or ())
    }
    ranked_names = [name_by_id.get(dataset_id, dataset_id) for dataset_id in ranked_ids if name_by_id.get(dataset_id)]

    return ScopeGate(
        gate_id=f"sg_{secrets.token_hex(4)}",
        options=build_scope_options(
            total_count=len(ids),
            bound_count=len(bound),
            ranked_names=ranked_names,
            ranked_count=len(ranked_ids),
        ),
        ranked_ids=ranked_ids,
        bound_ids=tuple(bound),
        all_ids=tuple(ids),
        total_count=len(ids),
        bound_count=len(bound),
    )


def parse_scope_choice(text: str | None) -> str | None:
    """从用户回答回执里取出 __scope_* 标记；没有则返回 None。"""
    if not text:
        return None
    match = _SCOPE_OPTION_RE.search(str(text))
    return match.group(0) if match else None


def resolve_dataset_ids_for_choice(choice: str, pending: Mapping[str, Any] | None) -> list[str]:
    """把选项标记还原成具体 dataset_id 列表（通道 B）。"""
    if not choice or not isinstance(pending, Mapping):
        return []
    if choice == SCOPE_RANKED:
        return _clean_ids(pending.get("ranked_ids"))
    if choice == SCOPE_BOUND:
        return _clean_ids(pending.get("bound_ids"))
    if choice == SCOPE_ALL:
        return _clean_ids(pending.get("all_ids"))
    return []


def scope_mode_for_choice(choice: str) -> str:
    return {
        SCOPE_RANKED: "ranked",
        SCOPE_BOUND: "bound",
        SCOPE_ALL: "all",
        SCOPE_PICK: "picked",
    }.get(choice, "picked")


def serialize_question_receipt(question: str, gate: ScopeGate) -> str:
    """调试与日志用：把即将发出的卡片写成一行摘要。"""
    return json.dumps(
        {"gate_id": gate.gate_id, "question": question, "options": [opt["id"] for opt in gate.options]},
        ensure_ascii=False,
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/ai/test_knowledge_scope_gate.py -v`
Expected: PASS（7 passed）

---

## Task 3: 会话范围字段

**Files:**
- Modify: `app/services/conversation_resource_service.py:44-68`（`_empty_scope` / `_decode_scope`）、`:135-152`（`replace`）
- Test: `tests/services/test_conversation_scope_fields.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
"""会话 scope 新字段的编解码与部分更新。"""
from app.services.conversation_resource_service import ConversationResourceService


def test_decode_keeps_knowledge_scope_and_pending():
    raw = (
        '{"knowledge_scope": {"mode": "ranked", "dataset_ids": ["ds_a"], "confirmed_at": "2026-10-27T11:00:00"},'
        ' "pending_scope_gate": {"gate_id": "sg_1", "ranked_ids": ["ds_a"]}}'
    )
    scope = ConversationResourceService._decode_scope(raw)
    assert scope["knowledge_scope"]["mode"] == "ranked"
    assert scope["pending_scope_gate"]["gate_id"] == "sg_1"


def test_decode_tolerates_legacy_value_without_new_fields():
    scope = ConversationResourceService._decode_scope('{"datasets": [], "skills": []}')
    assert scope["knowledge_scope"] is None
    assert scope["pending_scope_gate"] is None
    assert scope["datasets"] == []


def test_empty_scope_has_new_fields():
    scope = ConversationResourceService._empty_scope()
    assert scope["knowledge_scope"] is None
    assert scope["pending_scope_gate"] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/services/test_conversation_scope_fields.py -v`
Expected: FAIL — `KeyError: 'knowledge_scope'`

- [ ] **Step 3: 实现**

`_empty_scope()` 增加两个 `None` 字段：

```python
    @classmethod
    def _empty_scope(cls) -> Dict[str, Any]:
        return {
            "project_name": "",
            "datasets": [],
            "knowledge_bases": [],
            "skills": [],
            "mcp_tools": [],
            "knowledge_scope": None,
            "pending_scope_gate": None,
        }
```

`_decode_scope()` 末尾追加解析（在 `result.update({...})` 之后）：

```python
        for key in ("knowledge_scope", "pending_scope_gate"):
            value = value_raw.get(key) if isinstance(value_raw, dict) else None
            result[key] = value if isinstance(value, dict) else None
        return result
```

注意：原实现用局部变量 `value`，需先改名为 `value_raw` 避免与循环变量冲突：

```python
    @classmethod
    def _decode_scope(cls, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            value_raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            value_raw = {}
        if not isinstance(value_raw, dict):
            value_raw = {}
        result: Dict[str, Any] = {"project_name": str(value_raw.get("project_name") or "").strip()}
        result.update({
            key: [item for item in value_raw.get(key, []) if isinstance(item, dict)]
            for key in cls._LIST_KEYS
        })
        for key in ("knowledge_scope", "pending_scope_gate"):
            candidate = value_raw.get(key)
            result[key] = candidate if isinstance(candidate, dict) else None
        return result
```

`replace()` 保留两个新字段（放在 `normalized.update(...)` 之后）：

```python
        for key in ("knowledge_scope", "pending_scope_gate"):
            candidate = scope.get(key)
            normalized[key] = candidate if isinstance(candidate, dict) else None
```

新增部分更新方法（`replace` 之后）：

```python
    @classmethod
    async def patch_scope(
        cls,
        user_id: Any,
        conversation_id: str,
        **fields: Any,
    ) -> Dict[str, Any]:
        """只改指定字段：先读当前 scope 再整体写回，避免覆盖其他挂载项。

        注意 Redis 无事务，并发写同一会话时后写者胜出。门禁与挂载是低频操作，
        这个窗口可接受；若将来出现高频并发写，再改为 Lua 脚本原子更新。
        """
        if not conversation_id:
            return cls._empty_scope()
        current = await cls.get(user_id, conversation_id)
        current.update(fields)
        return await cls.replace(user_id, conversation_id, current)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/services/test_conversation_scope_fields.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 回归既有会话范围测试**

Run: `pytest tests/services -k "conversation" -v`
Expected: 全绿（若分支上原有用例存在）

---

## Task 4: Runner 接入门禁

**Files:**
- Modify: `app/services/ai/runners/knowledge_agent_runner.py`（`_execute_raw` 内，`resolve_knowledge_dataset_ids` 调用之前；新增两个私有方法）
- Test: `tests/ai/runners/test_knowledge_scope_gate_runner.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
"""Runner 侧门禁：触发时不出检索、回答后按选择收窄。"""
import pytest


class _StopChunk(Exception):
    pass


@pytest.mark.asyncio
async def test_gate_opens_and_blocks_retrieval(monkeypatch):
    """范围超阈值且未确认时，runner 只发卡片、不调用 search_knowledge_base。"""
    from app.services.ai.runners import knowledge_agent_runner as mod

    calls = []

    async def fake_search(*args, **kwargs):
        calls.append(kwargs)
        raise AssertionError("检索不应发生")

    monkeypatch.setattr(mod, "_auto_invoke_search_knowledge_base", fake_search, raising=False)

    # 通过 _maybe_open_scope_gate 直接验证判定与出卡，不构造完整 runner
    runner = object.__new__(mod.KnowledgeAgentRunner)
    runner.execution_options = {}
    runner.conversation_id = "c1"
    runner.user_info = {"user_id": 1}
    runner._current_user_query = "报销标准"

    chunk = await mod.KnowledgeAgentRunner._build_scope_gate_chunk.__get__(runner)(
        gate=mod.ScopeGate(gate_id="sg_1", options=[], ranked_ids=(), bound_ids=(), all_ids=("ds_0",), total_count=1, bound_count=0)
    )
    assert chunk["type"] == "user_question"
    assert calls == []


def test_scope_gate_constants_exported():
    from app.services.ai.runners.knowledge_agent_runner import ScopeGate  # noqa: F401
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/ai/runners/test_knowledge_scope_gate_runner.py -v`
Expected: FAIL — `AttributeError: ... _build_scope_gate_chunk`

- [ ] **Step 3: 实现**

在 `knowledge_agent_runner.py` 顶部导入：

```python
from app.services.ai.knowledge_scope_gate import (
    ScopeGate,
    evaluate_scope_gate,
    parse_scope_choice,
    resolve_dataset_ids_for_choice,
    scope_mode_for_choice,
    SCOPE_PICK,
)
from app.services.conversation_resource_service import ConversationResourceService
```

新增两个方法（放在 `_auto_invoke_search_knowledge_base` 之前）：

```python
    async def _maybe_open_scope_gate(
        self,
        *,
        query: str,
        ctx: Any,
    ) -> ScopeGate | None:
        """检索前判断是否需要让用户确认范围；任何异常都放行现有行为。"""
        try:
            conversation_id = str(self.conversation_id or "").strip()
            user_id = self._current_user_id()
            conversation_scope = (
                await ConversationResourceService.get(user_id, conversation_id)
                if conversation_id
                else None
            )
            enabled, threshold, ranked_limit = await resolve_scope_gate_settings(
                getattr(ctx, "engine_config", None)
            )
            if not enabled:
                return None
            from app.services.ai.knowledge_catalog import load_authorized_knowledge_catalog

            catalog = await load_authorized_knowledge_catalog(
                user_id=user_id,
                user_name=(self.user_info or {}).get("user_name"),
                is_admin=bool(getattr(ctx, "is_admin", False)),
            )
            gate = await evaluate_scope_gate(
                query=query,
                effective_ids=list(getattr(ctx, "dataset_ids", []) or []),
                bound_ids=list(getattr(ctx, "agent_dataset_ids", []) or []),
                explicit_request_ids=list(getattr(ctx, "knowledge_dataset_ids", []) or []),
                conversation_scope=conversation_scope,
                catalog=catalog,
                enabled=enabled,
                threshold=threshold,
                ranked_limit=ranked_limit,
            )
            if gate is not None and conversation_id:
                await ConversationResourceService.patch_scope(
                    user_id,
                    conversation_id,
                    pending_scope_gate=gate.to_pending_payload(),
                )
            return gate
        except Exception as exc:  # noqa: BLE001 - 门禁失败绝不能阻塞提问
            logger.warning("[KnowledgeAgentRunner] Scope gate skipped: %s", exc)
            return None

    def _build_scope_gate_chunk(self, *, gate: ScopeGate) -> Dict[str, Any]:
        """把门禁快照转成 ask_user_question 同款 SSE 卡片事件。"""
        import secrets as _secrets

        return {
            "type": "user_question",
            "question_id": f"uq_{_secrets.token_hex(8)}",
            "question": f"本轮可检索的知识库有 {gate.total_count} 个，选择这次的检索范围：",
            "options": [
                {k: v for k, v in opt.items() if k != "disabled"}
                for opt in gate.options
                if not opt.get("disabled")
            ],
            "is_multi_select": False,
            "allow_custom_input": True,
            "context": "范围较大时先确认可以避免在无关知识库中检索，提高命中率与速度。",
            "purpose": "knowledge_scope_gate",
        }
```

在 `_execute_raw` 中，`resolve_knowledge_dataset_ids` 之前插入：

```python
            restored_ids = await self._restore_scope_from_reply(ctx)
            if restored_ids:
                prefetch_dataset_ids = format_dataset_ids_for_tool(restored_ids)
                async for chunk in self._auto_invoke_search_knowledge_base(
                    query=user_question,
                    tools=tools,
                    dataset_ids=prefetch_dataset_ids,
                ):
                    ...
```

（照现有 prefetch 分支复用，实现时把这段包成 `_run_prefetch(dataset_ids)` 避免重复。）

门禁主判定插在最前面：

```python
            gate = await self._maybe_open_scope_gate(query=user_question, ctx=ctx)
            if gate is not None:
                yield self._build_scope_gate_chunk(gate=gate)
                return
```

`_restore_scope_from_reply`：

```python
    async def _restore_scope_from_reply(self, ctx: Any) -> list[str]:
        """用户回答门禁卡后，用会话里的 pending 快照还原具体 dataset_id（通道 B）。"""
        choice = parse_scope_choice(self.current_user_query or "")
        if not choice or choice == SCOPE_PICK:
            return []
        conversation_id = str(self.conversation_id or "").strip()
        if not conversation_id:
            return []
        scope = await ConversationResourceService.get(self._current_user_id(), conversation_id)
        pending = scope.get("pending_scope_gate")
        ids = resolve_dataset_ids_for_choice(choice, pending)
        if not ids:
            return []
        await ConversationResourceService.patch_scope(
            self._current_user_id(),
            conversation_id,
            knowledge_scope={
                "mode": scope_mode_for_choice(choice),
                "dataset_ids": ids,
                "confirmed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            pending_scope_gate=None,
        )
        return ids
```

同时在 `knowledge_scope_gate.py` 中补 `resolve_scope_gate_settings`：

```python
async def resolve_scope_gate_settings(engine_config: Mapping[str, Any] | None) -> tuple[bool, int, int]:
    """双层开关：全局 ConfigService 管能不能用，智能体 engine_config 管要不要用。"""
    from app.services.config_service import ConfigService

    global_enabled = await _as_bool(ConfigService.get("knowledge_scope_gate_enabled"), True)
    threshold = await _as_int(ConfigService.get("knowledge_scope_gate_threshold"), DEFAULT_THRESHOLD)
    ranked_limit = await _as_int(ConfigService.get("knowledge_scope_gate_ranked_limit"), DEFAULT_RANKED_LIMIT)

    mode = str((engine_config or {}).get("scope_gate") or "inherit").strip().lower()
    if mode == "off":
        return False, threshold, ranked_limit
    if mode == "on":
        return True, threshold, ranked_limit
    return global_enabled, threshold, ranked_limit
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/ai/runners/test_knowledge_scope_gate_runner.py -v`
Expected: PASS

- [ ] **Step 5: 回归知识库 runner 既有测试**

Run: `pytest tests/ai/runners -k "knowledge" -v`
Expected: 全绿

---

## Task 5: 前端配置与卡片

**Files:**
- Modify: `frontend/src/views/SystemConfig.vue`（配置项列表与说明文案）
- Modify: `frontend/src/components/agent/AgentVersionEditorDrawer.vue`（`engineConfig.scope_gate` 三态）
- Modify: `frontend/src/views/EmbedChat.vue`（卡片渲染分支 + 范围徽标）

- [ ] **Step 1: 全局配置项**

在 `SystemConfig.vue` 的知识库配置分组中，按现有 `knowledge_ragflow_similarity_threshold` 的写法新增三项：
`knowledge_scope_gate_enabled`（布尔）、`knowledge_scope_gate_threshold`（数字，默认 5）、
`knowledge_scope_gate_ranked_limit`（数字，默认 3），并为每项补 `【...】` 格式的说明文案。

- [ ] **Step 2: 智能体三态开关**

`AgentVersionEditorDrawer.vue` 中，在内容安全审查（`safety_check_enabled`）附近新增下拉：

```vue
<label class="flex items-center gap-2 text-xs font-medium text-gray-700">
  检索范围确认
  <select v-model="engineConfig.scope_gate" class="rounded border-gray-300 text-xs">
    <option value="inherit">跟随全局</option>
    <option value="on">开启</option>
    <option value="off">关闭</option>
  </select>
</label>
```

并在 `engineConfig` 默认值处补 `scope_gate: 'inherit'`。

- [ ] **Step 3: 卡片渲染与徽标**

`EmbedChat.vue`：
- `parseUserQuestionEvent` 已能解析任意 `user_question` 事件，确认 `purpose === 'knowledge_scope_gate'` 时走同一卡片组件；
- 选项 `label` 直接展示，`__scope_pick__` 点击时打开现有知识库选择器而不是直接提交；
- 用户提交时，若选择的是 `__scope_*` 标记，把 `pending_scope_gate` 对应 ID 一并写入下一轮 `body.knowledge_dataset_ids`（读通道 A）；
- 输入框上方徽标：`knowledge_scope.dataset_ids` 非空时显示「范围：{首个库名} 等 {n} 个 ▾」，点击打开选择器预选当前集合。

- [ ] **Step 4: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 5: 前端契约测试**

Run: `pytest --confcutdir=tests/frontend tests/frontend -k "user_question" -v`
Expected: 全绿

---

## Task 6: 验收

- [ ] 后端全量相关测试：`pytest tests/ai -k "knowledge or catalog or scope" -v`
- [ ] 手工验证（用户在控制台执行 `./dev.sh` 后）：
  1. 造 6 个可访问知识库、智能体不绑定任何库
  2. 提问 → 出现范围卡，四个选项齐全
  3. 选「最相关 3 个」→ 回答下方引用只来自这 3 个库
  4. 同一会话再问 → 不再弹卡
  5. 点徽标改范围 → 生效
  6. 切到另一个智能体 → 重新弹卡
  7. 全局关闭开关 → 不再弹卡，行为回到全量检索

---

## 与 spec 的差异（执行时需知）

| spec 原文 | 实际实现 | 原因 |
|---|---|---|
| 新增 `ScopeGateStep` 插在 `ContextStep` 之后 | 下沉到 `KnowledgeAgentRunner._execute_raw` | `setup_context` 在 `AssembleStep` 调用，pipeline 阶段拿不到 `AgentContext.dataset_ids`（即 `effective_dataset_ids`） |
| 会话状态存「会话表新字段」 | 复用 `ConversationResourceService` | 平台没有会话表，会话资源存 Redis |
| 选项③「仅智能体默认」置灰处理 | 卡片渲染时过滤掉 `disabled` 选项 | `ask_user_question` 的 option schema 无 `disabled` 字段，置灰无法表达，改为不展示 |
