# ChatBI 元数据集本轮挂载 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持本轮 `metadata_dataset_ids` 限定 ChatBI 问数范围，并在 EmbedChat / AgentDebug 数据门户提供本轮勾选与「固定到会话」。

**Architecture:** 聊天 API 增加本轮字段；服务端按「本轮非空优先，否则会话 `resource_scope.datasets`」合并后写入 `AgentContext`；`tool_gate_wrapper` 用该列表注入 `get_dataset_schema` / SQL 白名单。前端新增 `useDatasetMount`，在数据门户列出可访问数据集供勾选，固定到会话复用现有 PUT resource-scope。

**Tech Stack:** FastAPI, AgentContext, ChatBI tool_gate_wrapper, Vue 3 composables, pytest / 前端契约测试

**Spec:** `docs/superpowers/specs/2026-07-24-metadata-dataset-per-turn-mount-design.md`

---

## File map

| File | Responsibility |
|---|---|
| `app/services/ai/metadata_dataset_scope.py` (create) | `resolve_effective_metadata_dataset_ids` 纯函数 |
| `app/core/context.py` | `AgentContext.metadata_dataset_ids` |
| `app/services/ai/context_manager.py` | `setup_context` 接收并写入 |
| `app/api/v1/endpoints/chat.py` | 请求字段 + 合并 + 传入 agent_service |
| `app/services/ai/agent_service.py` | 透传 `metadata_dataset_ids` |
| `app/services/ai/runners/chatbi/tool_gate_wrapper.py` | 从 context 读 effective，注入 schema/SQL |
| `frontend/src/composables/useDatasetMount.ts` (create) | 本轮勾选状态 |
| `frontend/src/components/chatbi/DatasetPortalDrawer.vue` | 挂载区 UI |
| `frontend/src/views/EmbedChat.vue` | 发送字段 + 固定到会话 |
| `frontend/src/views/AgentDebug.vue` | 对齐 EmbedChat |
| `tests/ai/test_metadata_dataset_scope.py` (create) | 合并规则单测 |
| `tests/ai/runners/test_tool_gate_metadata_scope.py` (create 或扩展现有) | gate 注入 |
| `tests/frontend/test_*` | 契约：请求体字段 / 抽屉文案 |
| `tests/CHECKLIST.md` | 清单更新 |

---

### Task 1: 合并规则纯函数 + 单测

**Files:**
- Create: `app/services/ai/metadata_dataset_scope.py`
- Create: `tests/ai/test_metadata_dataset_scope.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/ai/test_metadata_dataset_scope.py
import pytest
from app.services.ai.metadata_dataset_scope import resolve_effective_metadata_dataset_ids

pytestmark = pytest.mark.no_infrastructure


def test_request_wins_over_session():
    assert resolve_effective_metadata_dataset_ids(
        request_ids=["1", "2"],
        session_ids=["9"],
    ) == ["1", "2"]


def test_empty_request_falls_back_to_session():
    assert resolve_effective_metadata_dataset_ids(
        request_ids=[],
        session_ids=["9", "8"],
    ) == ["9", "8"]


def test_none_when_both_empty():
    assert resolve_effective_metadata_dataset_ids(
        request_ids=None,
        session_ids=[],
    ) is None


def test_normalizes_and_dedupes_preserve_order():
    assert resolve_effective_metadata_dataset_ids(
        request_ids=[" 1 ", "1", "2", ""],
        session_ids=["3"],
    ) == ["1", "2"]
```

- [ ] **Step 2: Run tests — expect FAIL (module missing)**

```bash
pytest tests/ai/test_metadata_dataset_scope.py -v
```

- [ ] **Step 3: Implement**

```python
# app/services/ai/metadata_dataset_scope.py
from __future__ import annotations
from typing import Any, Optional


def _normalize_id_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, (str, int)):
        raw = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def resolve_effective_metadata_dataset_ids(
    *,
    request_ids: Any = None,
    session_ids: Any = None,
) -> Optional[list[str]]:
    """本轮非空优先，否则会话挂载；皆空返回 None（不注入白名单）。"""
    request = _normalize_id_list(request_ids)
    if request:
        return request
    session = _normalize_id_list(session_ids)
    return session or None
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/ai/test_metadata_dataset_scope.py -v
```

- [ ] **Step 5: Commit**

```bash
# 中文备注；若环境注入 --trailer 则用 commit-tree
git add app/services/ai/metadata_dataset_scope.py tests/ai/test_metadata_dataset_scope.py
git commit -m "$(cat <<'EOF'
feat(ai): 新增元数据集本轮/会话范围合并函数

EOF
)"
```

---

### Task 2: AgentContext + setup_context 透传

**Files:**
- Modify: `app/core/context.py`
- Modify: `app/services/ai/context_manager.py`
- Modify: `tests/` 中已有 context_manager 相关测试（若无则在 `tests/ai/test_metadata_dataset_scope.py` 增补 setup 断言，或新建轻量测试）

- [ ] **Step 1: 在 `AgentContext` 增加字段**

```python
metadata_dataset_ids: List[str] = Field(
    default_factory=list,
    description="本轮生效的 MetaDataset ID（已合并本轮优先/会话回退）",
)
```

- [ ] **Step 2: `setup_context` 增加参数并赋值**

签名增加 `metadata_dataset_ids: Optional[List[str]] = None`，构造 `AgentContext` 时：

```python
metadata_dataset_ids=list(metadata_dataset_ids or []),
```

- [ ] **Step 3: 单测或手工断言 context 字段存在且默认为 `[]`**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(ai): AgentContext 支持 metadata_dataset_ids"
```

---

### Task 3: Chat API 合并并传入 AgentService

**Files:**
- Modify: `app/api/v1/endpoints/chat.py`
- Modify: `app/services/ai/agent_service.py`（`chat_completion_stream` / `_run_stream` / `chat_completion` 签名与 `setup_context` 调用）

- [ ] **Step 1: `ChatCompletionRequest` 增加**

```python
metadata_dataset_ids: Optional[List[str]] = Field(
    default=None,
    description="本轮结构化指定的 MetaDataset ID 列表（优先于会话挂载；仍需通过权限校验）",
)
```

- [ ] **Step 2: 在 completions 处理中合并（紧挨 knowledge 合并处）**

```python
from app.services.ai.metadata_dataset_scope import resolve_effective_metadata_dataset_ids

session_dataset_ids = [
    str(item.get("id"))
    for item in conversation_scope.get("datasets", []) or []
    if item.get("id")
]
effective_metadata_dataset_ids = resolve_effective_metadata_dataset_ids(
    request_ids=completion_request.metadata_dataset_ids,
    session_ids=session_dataset_ids,
)
```

仍保留：会话非空时把 `conversation_scope` 写入 `effective_debug_options["resource_scope"]`（兼容旧 gate 路径）。  
**本轮非空时**额外保证后续 context 带本轮列表（以 context 为准覆盖 gate）。

- [ ] **Step 3: 调用 `agent_service.chat_completion_stream(..., metadata_dataset_ids=effective_metadata_dataset_ids)`**  
同步非流式路径同样传。

- [ ] **Step 4: `agent_service` 透传到 `AgentContextManager.setup_context(..., metadata_dataset_ids=...)`**

- [ ] **Step 5: 增加 API/服务层测试（可 mock stream，断言 setup_context 收到本轮优先列表）**

示例断言场景：
- request=`["1"]`, session=`["9"]` → context `["1"]`
- request=`[]`/`None`, session=`["9"]` → context `["9"]`

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(ai): 聊天请求支持本轮 metadata_dataset_ids 并优先于会话挂载"
```

---

### Task 4: tool_gate_wrapper 从 AgentContext 注入

**Files:**
- Modify: `app/services/ai/runners/chatbi/tool_gate_wrapper.py`
- Create or extend: `tests/ai/runners/test_data_agent_runner.py` / 新建 `tests/ai/runners/test_tool_gate_metadata_scope.py`

- [ ] **Step 1: 写失败测试** — mock runner + context，调用 wrapped `get_dataset_schema`，断言 kwargs 含本轮 ID，且本轮覆盖 `debug_options.resource_scope.datasets`

- [ ] **Step 2: 在 `wrap_tools_with_schema_gate` 顶部解析 effective**

```python
from app.core.context import get_current_agent_context

def _effective_metadata_dataset_ids() -> list[str] | None:
    ctx = get_current_agent_context()
    if ctx and ctx.metadata_dataset_ids:
        return [str(x) for x in ctx.metadata_dataset_ids if str(x).strip()]
    # 回退：会话 resource_scope（与现网一致）
    scope = (getattr(runner, "debug_options", {}) or {}).get("resource_scope", {}) or {}
    mounted = [
        str(item.get("id")).strip()
        for item in (scope.get("datasets", []) or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    return mounted or None
```

- [ ] **Step 3: `invoke_schema_controlled` 中**

```python
effective_ids = _effective_metadata_dataset_ids()
if effective_ids:
    kwargs["metadata_dataset_ids"] = effective_ids
```

（替换或优先于仅读 `mounted_dataset_ids` 的旧逻辑。）

- [ ] **Step 4: SQL 闸门与 schema 一致**  

最小正确路径：
1. schema 注入 ID 列表（`fetch_dataset_schema_core` 已按 ID 过滤）— 必须
2. SQL：`has_mounted_dataset_scope` 在 effective 非空时为 True；`mounted_dataset_names()` 从 `resource_scope.datasets` 中筛出 effective ID 对应的 `dataset_name`/`name`；若本轮仅有 ID、scope 中无条目，则允许把 ID 也纳入允许 token，或依赖「schema 已过滤 → SQL 用错库会失败」。本迭代测试至少覆盖 **schema kwargs 注入**。

- [ ] **Step 5: 测试 PASS 后 Commit**

```bash
git commit -m "feat(ai): ChatBI 工具闸门按 AgentContext 注入元数据集范围"
```

---

### Task 5: 前端 useDatasetMount + 数据门户挂载区

**Files:**
- Create: `frontend/src/composables/useDatasetMount.ts`
- Modify: `frontend/src/components/chatbi/DatasetPortalDrawer.vue`

- [ ] **Step 1: 实现 composable**

```ts
// frontend/src/composables/useDatasetMount.ts
import { ref, computed } from "vue";

export function useDatasetMount() {
  const activeMetadataDatasetIds = ref<string[]>([]);

  const toggleMetadataDatasetActive = (datasetId: string) => {
    const id = String(datasetId || "").trim();
    if (!id) return;
    if (activeMetadataDatasetIds.value.includes(id)) {
      activeMetadataDatasetIds.value = activeMetadataDatasetIds.value.filter((x) => x !== id);
    } else {
      activeMetadataDatasetIds.value = [...activeMetadataDatasetIds.value, id];
    }
  };

  const clearActiveMetadataDatasets = () => {
    activeMetadataDatasetIds.value = [];
  };

  const hasActiveMetadataDatasets = computed(
    () => activeMetadataDatasetIds.value.length > 0,
  );

  return {
    activeMetadataDatasetIds,
    hasActiveMetadataDatasets,
    toggleMetadataDatasetActive,
    clearActiveMetadataDatasets,
  };
}
```

- [ ] **Step 2: Drawer 增加「本轮限定数据集」区块**

Props 增加：
- `mountableDatasets: Array<{ id: string; name: string; description?: string }>`
- `activeDatasetIds: string[]`
- `sessionDatasetIds: string[]`（来自 resourceScope.datasets，用于展示「已固定到会话」）

Emits：
- `toggle-active: [id: string]`
- `pin-to-session: [ids: string[]]` — 将当前本轮选中固定到会话
- `unpin-from-session: [id: string]` — 可选单项移除

UI 文案：
- 勾选：本轮启用（多选）
- 按钮：「将勾选固定到会话」
- 标注：已在会话中固定 / 本轮已启用
- 提示：「本轮勾选优先于会话挂载」

数据源：父组件传入 `resourceOptions.datasets`（与资源范围弹窗同一 `/api/portal/metadata/datasets/accessible` 列表）。

- [ ] **Step 3: 前端契约测试**（字符串断言源码含 `metadata_dataset_ids`、固定到会话文案）

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(frontend): 数据门户支持本轮勾选元数据集"
```

---

### Task 6: EmbedChat 接线（发送 + 固定到会话）

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`

- [ ] **Step 1: 引入 `useDatasetMount`，传给 `DatasetPortalDrawer`**

- [ ] **Step 2: 发送 completion 时**

```ts
const turnIds = activeMetadataDatasetIds.value.filter(Boolean);
// ...
body: {
  // ...
  ...(turnIds.length ? { metadata_dataset_ids: turnIds } : {}),
}
```

不要把本轮 ID 塞进客户端 `debug_options.resource_scope`（服务端会丢弃）。

- [ ] **Step 3: `pin-to-session` 处理**

复用现有 `persistResourceScope` / `resourceScope`：

```ts
async function pinActiveDatasetsToSession(ids: string[]) {
  const selected = (resourceOptions.datasets || []).filter((d) => ids.includes(String(d.id)));
  // merge into resourceScope.datasets (dedupe by id), then PUT
  await persistResourceScope({ ...resourceScope, datasets: merged });
}
```

确保 `conversation_id` 已存在；若无则先创建会话（沿用现有发消息前逻辑）。

- [ ] **Step 4: 输入区可选芯片** — 展示本轮已启用数据集名称，点击可取消（调用 toggle）。

- [ ] **Step 5: 契约测试更新 EmbedChat 发送体

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(frontend): EmbedChat 发送本轮 metadata_dataset_ids 并支持固定到会话"
```

---

### Task 7: AgentDebug 对齐

**Files:**
- Modify: `frontend/src/views/AgentDebug.vue`

- [ ] **Step 1: 同样挂载 `useDatasetMount` + Drawer props/events（若 Debug 已有 DatasetPortalDrawer；否则最小：请求体支持同一 composable）**

- [ ] **Step 2: completion 请求带上 `metadata_dataset_ids`

- [ ] **Step 3: 契约测试或源码断言**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(frontend): AgentDebug 对齐本轮元数据集挂载"
```

---

### Task 8: CHECKLIST + 回归

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 增加清单项**

- 本轮 `metadata_dataset_ids` 优先于会话挂载
- 空本轮回退会话 `resource_scope.datasets`
- `get_dataset_schema` 注入有效 ID 列表
- EmbedChat / AgentDebug 发送字段与固定到会话

- [ ] **Step 2: 跑相关测试**

```bash
pytest tests/ai/test_metadata_dataset_scope.py tests/ai/runners/test_tool_gate_metadata_scope.py tests/ai/tools/test_data_api.py -v
pytest tests/frontend/ -k "dataset or embed or metadata" -v
```

- [ ] **Step 3: Commit**

```bash
git commit -m "docs(test): 更新清单覆盖元数据集本轮挂载"
```

---

## Spec coverage check

| Spec 要求 | Task |
|---|---|
| API `metadata_dataset_ids` | 3 |
| 本轮优先合并 | 1, 3 |
| AgentContext | 2 |
| tool_gate 注入 schema/SQL | 4 |
| 数据门户本轮勾选 | 5, 6 |
| 固定到会话 PUT | 6 |
| EmbedChat + AgentDebug | 6, 7 |
| 不改 DataPortalHome | （未列入） |
| CHECKLIST | 8 |

## Out of scope (do not implement)

- `DataPortalHome` 整页挂载
- 向 LLM 暴露 `dataset_id` 工具参数
- 改知识库「会话优先」语义
