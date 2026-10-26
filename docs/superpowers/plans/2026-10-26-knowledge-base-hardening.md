# 知识库管理加固与能力补齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不引入回收站的前提下，把知识库管理的「治理影响面、内容运维、检索质量、列表性能」四类缺口补齐，并修掉三处会真实出错的数据一致性问题。

**Architecture:** 继续以 RAGFlow Dataset 为检索事实源，平台侧 `knowledge_base_metadata` 只存治理元数据。所有变更走「端点 → 既有 Service → RagFlowClient」三层，不新增旁路。权限沿用 `require_dataset_access` / `require_dataset_content_write_access` / `require_dataset_write_access` 三档校验；审计统一走 `record_knowledge_audit`。前端仍以 `KnowledgeBaseManagement.vue` 为主入口，批次 C 才做组件拆分。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x 异步 / Pydantic 2 / Vue 3 + TS + Vite / pytest（契约测试读源码断言）。

---

## 范围

### 批次划分

| 批次 | 主题 | 交付物 | 预计 | 能否独立发布 |
|---|---|---|---|---|
| **A** | 治理与正确性 | 9 个修复/小功能，无新表、无新菜单 | 3–5 天 | 能 |
| **B** | 内容运维与协作 | 文档重命名/启停、同名替换、切片增删改、解析配置、失败通知、跨部门授权、内容来源扩展 | 2 周 | 能（B1/B2/B3 可先发） |
| **C** | 质量、性能与治理 | 列表缓存、检索日志、评测集、治理视图、全局审计、申请审批、配额、前端拆分 | 3 周 | 能（C1 可先发） |

> 本文档是**总纲 + 批次 A 的详细任务分解**。批次 B / C 为路线图：每批开工前，各自复制本模板展开成独立的 TDD 计划（`docs/superpowers/plans/YYYY-MM-DD-<batch>-<topic>.md`），避免一份计划跨三个子系统。

### 明确不做

- **回收站 / 恢复**（用户明确排除）。`status=deleted` 继续只作为「已删除」标记，不提供查询与恢复入口。
- 多租户 / 知识库市场 / 对外公开分享链接。
- 替换 RAGFlow 或引入第二套向量库。

### 全局约定

1. **迁移编号**：MySQL 从 `V191` 起，PG 从 `V91` 起，一一对应（PG = MySQL − 100）。只新增文件，不修改已执行迁移。
2. **新增权限码**必须三步：`app/services/menu_seed.py` 加节点 → `scripts/gen_menu_seed_sql.py` 登记 `INCREMENTAL_MIGRATIONS` 并生成 → 迁移内把旧码授权复制到新码（参考 `db-prod/V176-create_agent_menus.sql:527-542`）。
3. **不执行** `./dev.sh`、不做 `git commit`。每个任务结束只汇报改动与测试结果。
4. **TDD**：先写失败断言（后端单测 / 前端契约），再实现，再跑全绿。
5. **前端契约测试**模式：`pytest --confcutdir=tests/frontend`，直接 `read_text()` 断言源码片段（见 `tests/frontend/test_knowledge_owner_transfer_contract.py`）。
6. **错误文案**：面向用户的中文，带下一步动作；不要把 RAGFlow 原始报错直接透出。

---

## 批次 A：治理与正确性

### Task A1: 建库名唯一 + 不再按名字猜 Dataset ID

**背景：** `create_ragflow_dataset` 在 RAGFlow 未返回 id 时按名称反查列表并取 `matched[0]`（`app/api/portal/endpoints/ragflow.py:527-532`）。存在同名库时可能绑定到别人的库，随后把 `owner_user_id` 写成当前用户 —— 越权写。平台侧也没有任何重名校验。

**Files:**
- Modify: `app/services/knowledge_base_service.py`（新增 `find_active_by_name`）
- Modify: `app/api/portal/endpoints/ragflow.py:510-566`
- Test: `tests/api/portal/test_ragflow_knowledge_platform.py`

- [ ] **Step 1: 写失败测试**

在 `tests/api/portal/test_ragflow_knowledge_platform.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_create_dataset_rejects_duplicate_platform_name(monkeypatch):
    """同名库已存在时必须 409，而不是再建一个同名库（后续按名字反查必然出错）。"""

    class FakeMetaService:
        def __init__(self, db):
            pass

        async def find_active_by_name(self, name):
            return SimpleNamespace(id=7)

        async def upsert_metadata(self, **kwargs):  # pragma: no cover - 不应走到
            raise AssertionError("重名时不应写元数据")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def create_dataset(self, **kwargs):  # pragma: no cover - 不应走到
            raise AssertionError("重名时不应调用 RAGFlow 建库")

    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetaService)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeClient)
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.create_ragflow_dataset(
            payload=ragflow.CreateDatasetRequest(name="制度库"),
            request=_fake_request(),
            user={"role": "user", "user_id": "1", "user_name": "alice"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 409
    assert "已存在" in exc.value.detail


@pytest.mark.asyncio
async def test_create_dataset_does_not_guess_id_when_names_ambiguous(monkeypatch):
    """RAGFlow 未返回 id 且按名字查到多个时，必须失败而不是取第一个。"""

    class FakeMetaService:
        def __init__(self, db):
            pass

        async def find_active_by_name(self, name):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def create_dataset(self, **kwargs):
            return {}

        async def list_datasets(self, **kwargs):
            return [{"id": "ds-a", "name": "制度库"}, {"id": "ds-b", "name": "制度库"}]

    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetaService)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeClient)
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.create_ragflow_dataset(
            payload=ragflow.CreateDatasetRequest(name="制度库"),
            request=_fake_request(),
            user={"role": "user", "user_id": "1", "user_name": "alice"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 502
    assert "Dataset ID" in exc.value.detail
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/api/portal/test_ragflow_knowledge_platform.py -k "duplicate_platform_name or ambiguous" -q`
Expected: FAIL（`AttributeError: find_active_by_name` / 未抛 409）

- [ ] **Step 3: 实现**

`app/services/knowledge_base_service.py` 新增：

```python
    async def find_active_by_name(self, name: str) -> Optional[KnowledgeBaseMetadata]:
        """按名称查未删除的知识库元数据（建库重名校验用）。

        RAGFlow 侧允许同名 dataset，平台侧必须自己兜住：一旦重名，
        「未返回 id 时按名字反查」就会取到任意一个，可能绑到别人的库上。
        """
        token = (name or "").strip()
        if not token:
            return None
        stmt = (
            select(KnowledgeBaseMetadata)
            .where(
                KnowledgeBaseMetadata.name == token,
                KnowledgeBaseMetadata.status != "deleted",
            )
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()
```

`app/api/portal/endpoints/ragflow.py` 的 `create_ragflow_dataset`，在 `client.create_dataset(...)` **之前**插入：

```python
        # 平台侧先做唯一性校验：RAGFlow 允许多个同名 dataset，而本函数在建库
        # 返回缺 id 时会按名字反查列表取第一个 —— 重名等于可能绑到别人的库，
        # 并把 owner_user_id 写成当前用户（越权）。宁可直接拒绝。
        exists = await metadata_service.find_active_by_name(payload.name)
        if exists:
            raise HTTPException(
                status_code=409,
                detail=f"知识库名称「{payload.name}」已存在，请换一个名称",
            )
```

把兜底反查改成「唯一才认」：

```python
        if not dataset_id:
            matched = await client.list_datasets(name=payload.name)
            matched_ids = [str(item.get("id")) for item in matched if item.get("id")]
            if len(matched_ids) == 1:
                dataset_id = matched_ids[0]
            else:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "RAGFlow 创建成功但未能确定 Dataset ID"
                        f"（同名候选 {len(matched_ids)} 个）。"
                        "请到 RAGFlow 核对后手动同步，避免元数据绑错库。"
                    ),
                )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/api/portal/test_ragflow_knowledge_platform.py -q`
Expected: 全绿（含既有用例）

- [ ] **Step 5: 前端提示**

`frontend/src/views/KnowledgeBaseManagement.vue` 的 `createDataset()` catch 分支已走 `extractError`，409 文案会直接显示，无需改动；确认 `showCreateModal` 失败后不关闭即可。

**验收：**
- 用已存在的名称建库 → 弹窗红字「知识库名称「x」已存在」，不产生新库。
- 在 RAGFlow 手工造两个同名 dataset，平台建同名库且 RAGFlow 返回空 id → 报 502，平台元数据不落库。

---

### Task A2: 智能体引用关系可见 + 删除影响面

**背景：** 智能体绑定在 `ai_agents.engine_config.dataset_ids`（JSON，见 `app/services/ai/context_manager.py:272-286`），没有反向索引。详情页与删除确认都无法回答「删了会不会打挂某个智能体」。

**Files:**
- Create: `app/services/knowledge_reference_service.py`
- Modify: `app/api/portal/endpoints/ragflow.py`（列表回填 + 新端点）
- Modify: `frontend/src/views/KnowledgeBaseManagement.vue`（详情头 + 删除弹窗）
- Test: `tests/services/test_knowledge_reference_service.py`（新建）
- Test: `tests/frontend/test_knowledge_agent_reference_contract.py`（新建）

- [ ] **Step 1: 写失败测试（service 纯函数 + 端点）**

`tests/services/test_knowledge_reference_service.py`：

```python
import pytest

from app.services.knowledge_reference_service import (
    KnowledgeReferenceService,
    engine_dataset_ids,
)

pytestmark = pytest.mark.no_infrastructure


def test_engine_dataset_ids_accepts_json_string_and_list():
    assert engine_dataset_ids('{"dataset_ids": ["a", "b"]}') == ["a", "b"]
    assert engine_dataset_ids({"dataset_ids": "a,b"}) == ["a", "b"]
    assert engine_dataset_ids({"dataset_ids": ["a", "a", "b"]}) == ["a", "b"]
    assert engine_dataset_ids(None) == []
    assert engine_dataset_ids("not-json") == []


@pytest.mark.asyncio
async def test_agents_by_dataset_groups_by_dataset():
    class FakeResult:
        def all(self):
            return [
                ("agent-1", "kb_qa", "制度问答", True, {"dataset_ids": ["ds-1", "ds-2"]}),
                ("agent-2", "kb_qa_2", "制度问答二", False, {"dataset_ids": ["ds-1"]}),
                ("agent-3", "other", "无关", True, {"dataset_ids": ["ds-9"]}),
            ]

    class FakeDb:
        async def execute(self, stmt):
            return FakeResult()

    service = KnowledgeReferenceService(db=FakeDb())
    grouped = await service.agents_by_dataset(["ds-1", "ds-2", "ds-3"])

    assert [a["id"] for a in grouped["ds-1"]] == ["agent-1", "agent-2"]
    assert [a["id"] for a in grouped["ds-2"]] == ["agent-1"]
    assert "ds-3" not in grouped
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_knowledge_reference_service.py -q`
Expected: FAIL（`ModuleNotFoundError: app.services.knowledge_reference_service`）

- [ ] **Step 3: 实现 service**

创建 `app/services/knowledge_reference_service.py`：

```python
"""知识库 → 智能体的反向引用查询。

绑定关系存在 ``ai_agents.engine_config.dataset_ids``（JSON）。数量级是
「智能体几百个 × 每个几个库」，实时扫描内存聚合比维护一张双写映射表更可靠：
不存在「保存智能体时忘了写映射」的漂移，也不需要回填历史数据。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AIAgent
from app.services.ai.knowledge_utils import merge_dataset_id_sources


def engine_dataset_ids(engine_config: Any) -> List[str]:
    """从 engine_config 取 dataset_ids，兼容 JSON 字符串、字符串、列表。"""
    config = engine_config
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (TypeError, ValueError):
            return []
    if not isinstance(config, dict):
        return []
    return merge_dataset_id_sources(config.get("dataset_ids"))


class KnowledgeReferenceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def agents_by_dataset(
        self, dataset_ids: Iterable[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        wanted = {str(item).strip() for item in dataset_ids if str(item).strip()}
        if not wanted:
            return {}

        rows = (
            await self.db.execute(
                select(
                    AIAgent.id,
                    AIAgent.name,
                    AIAgent.display_name,
                    AIAgent.is_enabled,
                    AIAgent.engine_config,
                )
            )
        ).all()

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for agent_id, name, display_name, is_enabled, engine_config in rows:
            for dataset_id in engine_dataset_ids(engine_config):
                if dataset_id not in wanted:
                    continue
                grouped.setdefault(dataset_id, []).append(
                    {
                        "id": agent_id,
                        "name": name,
                        "display_name": display_name or name,
                        "is_enabled": bool(is_enabled),
                    }
                )

        # 启用的排前面，其次按显示名，保证列表稳定
        for items in grouped.values():
            items.sort(key=lambda item: (not item["is_enabled"], item["display_name"]))
        return grouped
```

- [ ] **Step 4: 跑 service 测试通过**

Run: `pytest tests/services/test_knowledge_reference_service.py -q`
Expected: PASS

- [ ] **Step 5: 端点实现（列表回填 + 详情）**

`app/api/portal/endpoints/ragflow.py` 顶部 import 增加：

```python
from app.services.knowledge_reference_service import KnowledgeReferenceService
```

新增辅助函数（放在 `_attach_owner_user_names` 之后）：

```python
async def _attach_agent_refs(db: AsyncSession, items: List[Dict[str, Any]]) -> None:
    """给列表项补「被几个智能体引用」，只回前 5 个，避免响应膨胀。"""
    dataset_ids = [
        str(item.get("ragflow_dataset_id") or item.get("id") or "")
        for item in items
    ]
    grouped = await KnowledgeReferenceService(db).agents_by_dataset(dataset_ids)
    for item in items:
        dataset_id = str(item.get("ragflow_dataset_id") or item.get("id") or "")
        agents = grouped.get(dataset_id, [])
        item["agent_ref_count"] = len(agents)
        item["agent_refs"] = agents[:5]
```

`list_ragflow_datasets` 在 `_attach_owner_user_names` 之后调用：

```python
        await _attach_agent_refs(db, merged)
```

新增详情端点（放在 `list_ragflow_datasets` 之后）：

```python
@router.get("/datasets/{dataset_id}/agents")
async def list_dataset_agents(
    dataset_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """该知识库当前被哪些智能体引用（删除/收窄可见范围前的影响面提示）。"""
    await require_dataset_access(user, db, [dataset_id])
    grouped = await KnowledgeReferenceService(db).agents_by_dataset([dataset_id])
    agents = grouped.get(dataset_id, [])
    return {
        "code": 0,
        "data": {
            "total": len(agents),
            "enabled_total": sum(1 for agent in agents if agent["is_enabled"]),
            "items": agents,
        },
    }
```

- [ ] **Step 6: 前端详情头 + 删除弹窗**

`KnowledgeBaseManagement.vue` 类型 `KnowledgeBase` 增加：

```ts
  agent_ref_count?: number
  agent_refs?: { id: string; name: string; display_name: string; is_enabled: boolean }[]
```

详情头元信息条（「切片」与「业务归属部门」之间）插入：

```vue
                <span v-if="selectedDataset.agent_ref_count" class="whitespace-nowrap">
                  被
                  <button
                    type="button"
                    class="font-medium text-primary hover:underline"
                    @click="openDatasetAgents(selectedDataset)"
                  >
                    {{ selectedDataset.agent_ref_count }} 个智能体
                  </button>
                  引用
                </span>
```

新增状态与方法：

```ts
const showAgentRefsModal = ref(false)
const agentRefs = ref<{ id: string; name: string; display_name: string; is_enabled: boolean }[]>([])
const loadingAgentRefs = ref(false)

const openDatasetAgents = async (dataset: KnowledgeBase) => {
  const dsId = dataset.ragflow_dataset_id || dataset.id
  if (!dsId) return
  showAgentRefsModal.value = true
  loadingAgentRefs.value = true
  agentRefs.value = []
  try {
    const res = await axios.get(`/api/portal/ragflow/datasets/${dsId}/agents`)
    agentRefs.value = res.data?.data?.items || []
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    loadingAgentRefs.value = false
  }
}
```

配套 Modal 模板（放在转移归属弹窗之后）：

```vue
    <Modal :show="showAgentRefsModal" title="引用该知识库的智能体" size="max-w-lg" @close="showAgentRefsModal = false">
      <div class="space-y-3">
        <div v-if="loadingAgentRefs" class="py-8 text-center text-sm text-gray-400">加载中…</div>
        <div v-else-if="!agentRefs.length" class="py-8 text-center text-sm text-gray-400">
          暂无智能体引用该知识库
        </div>
        <ul v-else class="divide-y divide-gray-100 max-h-[50vh] overflow-y-auto">
          <li v-for="agent in agentRefs" :key="agent.id" class="flex items-center justify-between py-2.5">
            <div class="min-w-0">
              <p class="text-sm font-medium text-gray-800 truncate">{{ agent.display_name }}</p>
              <p class="text-[11px] text-gray-400 font-mono truncate">{{ agent.name }}</p>
            </div>
            <span
              class="shrink-0 text-[11px] px-2 py-0.5 rounded-full border"
              :class="agent.is_enabled ? 'bg-emerald-50 text-emerald-600 border-emerald-100' : 'bg-gray-50 text-gray-400 border-gray-200'"
            >{{ agent.is_enabled ? '已启用' : '已停用' }}</span>
          </li>
        </ul>
        <p class="text-xs text-gray-400 leading-relaxed">
          引用关系来自智能体的「知识库绑定」配置；删除本库或改为个人私有后，这些智能体将检索不到本库内容。
        </p>
      </div>
    </Modal>
```

删除弹窗（`deleteDatasetMessage` 上方）新增异步引用拉取，并在 `triggerDeleteDataset` 里调用：

```ts
const deleteAgentRefs = ref<{ id: string; display_name: string; is_enabled: boolean }[]>([])

const loadDeleteAgentRefs = async (dataset: KnowledgeBase) => {
  const dsId = dataset.ragflow_dataset_id || dataset.id
  deleteAgentRefs.value = []
  if (!dsId) return
  try {
    const res = await axios.get(`/api/portal/ragflow/datasets/${dsId}/agents`)
    deleteAgentRefs.value = res.data?.data?.items || []
  } catch {
    // 影响面读不到时不阻断删除，但弹窗会提示「引用关系未知」
  }
}
```

`deleteDatasetMessage` 末尾追加：

```ts
  const refs = deleteAgentRefs.value
  const refWarning = refs.length
    ? `（⚠️ 该库正被 ${refs.length} 个智能体引用：${refs
        .map((a) => a.display_name)
        .slice(0, 3)
        .join('、')}${refs.length > 3 ? ' 等' : ''}；删除后这些智能体将检索不到本库内容）`
    : ''
  return baseMsg + refWarning
```

- [ ] **Step 7: 前端契约测试**

新建 `tests/frontend/test_knowledge_agent_reference_contract.py`：

```python
"""知识库被智能体引用的影响面提示契约。

删除/收窄可见范围前必须能回答「会不会打挂某个智能体」。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_detail_header_shows_agent_reference_count(page: str) -> None:
    assert "agent_ref_count" in page
    assert "openDatasetAgents" in page


def test_delete_confirm_lists_referencing_agents(page: str) -> None:
    assert "loadDeleteAgentRefs" in page
    assert "deleteAgentRefs" in page
    assert "检索不到本库内容" in page
```

- [ ] **Step 8: 跑全绿**

Run:
```
pytest tests/services/test_knowledge_reference_service.py tests/api/portal/test_ragflow_knowledge_platform.py -q
pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_agent_reference_contract.py -q
cd frontend && npx vue-tsc --noEmit
```
Expected: 全绿 / 0 错误

**验收：**
- 详情页显示「被 N 个智能体引用」，点开可看名称与启停状态。
- 删除一个有引用的库，确认弹窗红字列出智能体名。
- 没有任何引用的库不显示该字段、弹窗无多余文案。

---

### Task A3: 审计两处失真修复

**背景：**
1. `datasets:sync` 的 payload 没有 `dataset_id`，`KnowledgeAuditService.log` 在 ids 为空时直接 `return False`（`app/services/knowledge_audit_service.py:61-63`）——**同步操作零审计**。
2. 所有 `except Exception` 分支写死 `status_code=500`，RAGFlow 的 4xx 业务错误也被记成 500。

**Files:**
- Modify: `app/services/knowledge_base_service.py:183-222`（返回受影响 id）
- Modify: `app/services/knowledge_audit_service.py`（全局记录常量）
- Modify: `app/api/portal/endpoints/ragflow.py:469-507` 及其余 `except` 分支
- Test: `tests/api/portal/test_knowledge_audit_logs.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_sync_writes_audit_for_touched_datasets(monkeypatch):
    captured = {}

    class FakeMetaService:
        def __init__(self, db):
            pass

        async def sync_from_ragflow_datasets(self, datasets, user_name=None):
            return {"created": 1, "updated": 1, "skipped": 0, "dataset_ids": ["ds-new", "ds-old"]}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def list_datasets(self, **kwargs):
            return [{"id": "ds-new", "name": "新库"}]

    async def fake_record(request, user, action, status_code, payload, error_message=None, started_at=None):
        captured["action"] = action
        captured["dataset_ids"] = payload.get("dataset_ids")
        captured["status_code"] = status_code

    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetaService)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeClient)
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_record)

    await ragflow.sync_ragflow_datasets(
        request=_fake_request(),
        user={"role": "admin", "user_id": "1", "user_name": "admin"},
        db=SimpleNamespace(),
    )

    assert captured["action"] == "datasets:sync"
    assert captured["dataset_ids"] == ["ds-new", "ds-old"]


@pytest.mark.asyncio
async def test_audit_records_upstream_status_code(monkeypatch):
    """RAGFlow 业务错误（RagFlowServiceError.status_code=502）不应被记成 500。"""
    captured = {}

    class FakeMetaService:
        def __init__(self, db):
            pass

        async def update_metadata(self, *args, **kwargs):
            raise RuntimeError("should not reach")

    async def fake_record(request, user, action, status_code, payload, error_message=None, started_at=None):
        captured["status_code"] = status_code

    from app.core.errors import RagFlowServiceError

    class BoomClient:
        def __init__(self, **kwargs):
            pass

        async def create_dataset(self, **kwargs):
            raise RagFlowServiceError("embedding 模型不可用")

    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetaService)
    monkeypatch.setattr(ragflow, "RagFlowClient", BoomClient)
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_record)

    with pytest.raises(RagFlowServiceError):
        await ragflow.create_ragflow_dataset(
            payload=ragflow.CreateDatasetRequest(name="新库"),
            request=_fake_request(),
            user={"role": "user", "user_id": "1", "user_name": "alice"},
            db=SimpleNamespace(),
        )

    assert captured["status_code"] == 502
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/api/portal/test_knowledge_audit_logs.py -k "sync_writes_audit or upstream_status" -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`knowledge_base_service.sync_from_ragflow_datasets` 增加返回值（保持既有键不变）：

```python
        result = {"created": 0, "updated": 0, "skipped": 0, "dataset_ids": []}
        ...
        # 新建与更新都要能追溯：
        result["dataset_ids"].append(dataset_id)
```

`knowledge_audit_service.py` 顶部增加：

```python
#: 无单一库归属的全局操作（如从 RAGFlow 同步）。按库查询不会命中它，
#: 只出现在全局审计视图里，避免与真实 dataset_id 混淆。
GLOBAL_AUDIT_DATASET_ID = "__global__"
```

`ragflow.py` 的 `sync_ragflow_datasets` 改成：

```python
        result = await metadata_service.sync_from_ragflow_datasets(
            datasets,
            user_name=user.get("user_name"),
        )
        touched_ids = result.get("dataset_ids") or []
        audit_payload = {
            "action": "sync_datasets_from_ragflow",
            "created": result["created"],
            "updated": result["updated"],
            "skipped": result["skipped"],
        }
        if touched_ids:
            await record_knowledge_audit(
                request=request,
                user=user,
                action="datasets:sync",
                status_code=200,
                payload={**audit_payload, "dataset_ids": touched_ids},
                started_at=started,
            )
        else:
            # 没同步到任何库也要留一条全局痕：否则「点过同步、什么都没发生」
            # 与「根本没点」在事后无法区分。
            await KnowledgeAuditService.log(
                dataset_ids=[GLOBAL_AUDIT_DATASET_ID],
                action="datasets:sync",
                user=user,
                status_code=200,
                detail=audit_payload,
                client_ip=request.client.host if request.client else None,
            )
```

新增状态码提取工具（放在 `record_knowledge_audit` 之后）：

```python
def _audit_status_code(error: Exception) -> int:
    """审计里的状态码要尽量还原真实原因，不能一律 500。"""
    if isinstance(error, HTTPException):
        return int(error.status_code)
    return int(getattr(error, "status_code", 500) or 500)
```

把 `create / update / delete / upload / delete_documents / parse / retrieval-test` 的 `except` 分支统一改为：

```python
        }, error_message=str(e), started_at=started, status_code=_audit_status_code(e))
```

> 注意：`record_knowledge_audit` 的签名顺序是 `(request, user, action, status_code, payload, error_message, started_at)`，把 `status_code` 作为关键字参数放到最后会与位置参数冲突。统一改成全关键字调用：
> ```python
> await record_knowledge_audit(
>     request=request, user=user, action="datasets:create",
>     status_code=_audit_status_code(e),
>     payload={...}, error_message=str(e), started_at=started,
> )
> ```

- [ ] **Step 4: 跑测试通过**

Run: `pytest tests/api/portal/test_knowledge_audit_logs.py -q && pytest tests/api/portal/test_ragflow_knowledge_platform.py -q`
Expected: 全绿

- [ ] **Step 5: 前端补 action 文案**

`AUDIT_ACTION_LABELS` 增加：

```ts
  'datasets:sync': '从 RAGFlow 同步知识库',
  'datasets:update_layout': '调整文档目录结构',
```

`auditLogText` 增加分支：

```ts
    case 'datasets:sync':
      return `${who} 从 RAGFlow 同步了知识库（新增 ${detail.created ?? 0}、更新 ${detail.updated ?? 0}）`
    case 'datasets:update_layout':
      return `${who} 调整了文档目录结构`
```

**验收：**
- 点一次「同步知识库」，任一受影响库的操作日志多一条「从 RAGFlow 同步…」。
- 无变化时全局范围也能查到该操作（C5 上线后可见）。
- 制造一次 RAGFlow 4xx（如 embedding 未配置）→ 审计状态码为 502 而非 500。

---

### Task A4: 目录结构独立端点 + 删文档清理引用

**背景：** `saveDatasetExtraConfig` 移动一个文档就整份 `PUT /metadata`（name/description/owner/visibility/tags/notes 全量回写，`KnowledgeBaseManagement.vue:1613-1638`），是 lost update 与「挪文档把库改成私有」的同源缺陷。另外删文档后 `folder_structure` 里的 id 不清理，只靠渲染层 filter 遮挡。

**Files:**
- Modify: `app/services/knowledge_base_service.py`（新增 `update_extra_config_key`）
- Modify: `app/api/portal/endpoints/ragflow.py`（新端点）
- Modify: `frontend/src/views/KnowledgeBaseManagement.vue`
- Test: `tests/services/test_knowledge_base_metadata_service.py`
- Test: `tests/frontend/test_knowledge_folder_structure_contract.py`（新建）

- [ ] **Step 1: 写失败测试（service）**

```python
@pytest.mark.asyncio
async def test_update_extra_config_key_preserves_other_keys():
    service = KnowledgeBaseMetadataService(db=None)
    record = KnowledgeBaseMetadata(
        ragflow_dataset_id="ds-1",
        name="库",
        status="active",
        extra_config={"custom_questions": [{"label": "a", "query": "b"}], "folder_structure": {"旧": ["d1"]}},
    )

    async def fake_get(dataset_id):
        return record

    service.get_by_dataset_id = fake_get

    updated = await service.update_extra_config_key(
        "ds-1", key="folder_structure", value={"新": ["d2"]}
    )

    assert updated.extra_config["folder_structure"] == {"新": ["d2"]}
    assert updated.extra_config["custom_questions"] == [{"label": "a", "query": "b"}]
```

- [ ] **Step 2: 跑失败**

Run: `pytest tests/services/test_knowledge_base_metadata_service.py -k extra_config_key -q`
Expected: FAIL（`AttributeError`）

- [ ] **Step 3: 实现 service**

```python
    async def update_extra_config_key(
        self,
        dataset_id: str,
        *,
        key: str,
        value: Any,
        user_name: Optional[str] = None,
    ) -> Optional[KnowledgeBaseMetadata]:
        """只改 extra_config 的一个子键，其余键原样保留。

        虚拟文件夹原先走整份 ``PUT /metadata``：移动一个文档会把 name/visibility
        等前端缓存值一起回写，并发操作就是 lost update。这里把「改目录结构」
        收敛成一个明确的局部写。
        """
        record = await self.get_by_dataset_id(dataset_id)
        if not record or record.status == "deleted":
            return None
        config = dict(record.extra_config or {})
        if value in (None, {}, []):
            config.pop(key, None)
        else:
            config[key] = value
        # 重新赋值而不是原地修改：SQLAlchemy 的 JSON 变更检测依赖引用变化
        record.extra_config = config
        record.updated_by = user_name
        record.updated_at = datetime.now()
        await self.db.flush()
        return record
```

- [ ] **Step 4: 加端点**

```python
class UpdateFolderStructureRequest(BaseModel):
    folder_structure: Dict[str, List[str]] = Field(default_factory=dict)


@router.put("/datasets/{dataset_id}/folder-structure")
async def update_folder_structure(
    dataset_id: str,
    payload: UpdateFolderStructureRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """只更新虚拟文件夹映射，不触碰名称/可见范围等治理字段。"""
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:edit")
    await require_dataset_write_access(user, db, [dataset_id])

    record = await KnowledgeBaseMetadataService(db).update_extra_config_key(
        dataset_id,
        key="folder_structure",
        value=payload.folder_structure,
        user_name=user.get("user_name"),
    )
    if not record:
        raise HTTPException(status_code=404, detail="知识库元数据不存在或已删除")

    await record_knowledge_audit(
        request=request,
        user=user,
        action="datasets:update_layout",
        status_code=200,
        payload={
            "action": "update_folder_structure",
            "dataset_id": dataset_id,
            "folder_count": len(payload.folder_structure),
            "document_count": sum(len(ids) for ids in payload.folder_structure.values()),
        },
        started_at=started,
    )
    return {"code": 0, "data": {"extra_config": record.extra_config or {}}}
```

- [ ] **Step 5: 前端改造**

把 `saveDatasetExtraConfig` 拆成两个明确函数：

```ts
/** 只保存虚拟文件夹映射，不再整份 PUT 元数据（否则会覆盖并发修改的可见范围等）。 */
const saveFolderStructure = async (
  dataset: KnowledgeBase,
  structure: Record<string, string[]>,
) => {
  const datasetId = dataset.ragflow_dataset_id || dataset.id
  const res = await axios.put(
    `/api/portal/ragflow/datasets/${datasetId}/folder-structure`,
    { folder_structure: structure },
  )
  const saved = res.data?.data?.extra_config?.folder_structure || {}
  if (!dataset.local_metadata) dataset.local_metadata = {}
  dataset.local_metadata.extra_config = {
    ...(dataset.local_metadata.extra_config || {}),
    folder_structure: saved,
  }
}

/** 文档删除后清理目录里的悬空引用。 */
const removeDocumentFromFolders = async (dataset: KnowledgeBase, docId: string) => {
  const structure = dataset.local_metadata?.extra_config?.folder_structure
  if (!structure) return
  const next: Record<string, string[]> = {}
  let changed = false
  for (const [folder, ids] of Object.entries(structure)) {
    const filtered = (ids || []).filter((id) => id !== docId)
    if (filtered.length !== (ids || []).length) changed = true
    next[folder] = filtered
  }
  if (!changed) return
  try {
    await saveFolderStructure(dataset, next)
  } catch (err) {
    // 清理失败不影响文档删除本身，但要让用户知道目录里可能有残留
    showToast('文档已删除，但目录引用清理失败：' + extractError(err), 'warning')
  }
}
```

`createFolder / renameFolder / removeFolder / moveDocumentToFolder` 全部改为调 `saveFolderStructure`，并删除 `fetchDatasets()` 全量刷新（本地状态已更新）。以 `confirmCreateFolder` 为例：

```ts
const confirmCreateFolder = async (dataset: KnowledgeBase) => {
  const name = newFolderName.value.trim()
  if (!name) return
  const structure = { ...(dataset.local_metadata?.extra_config?.folder_structure || {}) }
  if (structure[name]) {
    showToast('同名文件夹已存在', 'warning')
    return
  }
  structure[name] = []
  try {
    await saveFolderStructure(dataset, structure)
    showToast('文件夹已创建', 'success')
    newFolderName.value = ''
    newFolderDataset.value = null
  } catch (err) {
    showToast('创建文件夹失败: ' + extractError(err), 'error')
  }
}
```

`renameFolder` 需要迁移 key 对应的 id 数组（`structure[newName] = structure[oldName]; delete structure[oldName]`），`removeFolder` 直接 `delete structure[name]`，两者同样只调 `saveFolderStructure`。

`confirmDeleteDocument` 删除成功后、`fetchDocumentsForDataset` 之前调用：

```ts
    const targetDataset = datasets.value.find(
      (d) => (d.ragflow_dataset_id || d.id) === selectedDatasetId.value,
    )
    if (targetDataset) await removeDocumentFromFolders(targetDataset, docId)
```

- [ ] **Step 6: 前端契约测试**

新建 `tests/frontend/test_knowledge_folder_structure_contract.py`：

```python
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_folder_save_uses_dedicated_endpoint(page: str) -> None:
    assert "/folder-structure" in page
    assert "saveFolderStructure" in page


def test_moving_document_no_longer_puts_whole_metadata(page: str) -> None:
    # 目录操作不得再调用整份 PUT /metadata
    start = page.index("const moveDocumentToFolder")
    end = page.index("const triggerDeleteDataset")
    segment = page[start:end]
    assert "saveDatasetExtraConfig" not in segment
    assert "/metadata" not in segment


def test_deleting_document_cleans_folder_reference(page: str) -> None:
    assert "removeDocumentFromFolders" in page
```

- [ ] **Step 7: 跑全绿**

Run:
```
pytest tests/services/test_knowledge_base_metadata_service.py -q
pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_folder_structure_contract.py -q
cd frontend && npx vue-tsc --noEmit
```

**验收：**
- 移动文档只发一次 `PUT /folder-structure`，不再刷新整页列表。
- 打开另一账号并发改可见范围，再回来移动文档，可见范围不被改回。
- 删除文件夹里的文档后，`extra_config.folder_structure` 里不再有该 id。

---

### Task A5: 上传大小与类型硬限制

**背景：** `upload_dataset_document` 直接 `await file.read()` 全量进内存（`ragflow.py:729`），没有任何大小/类型校验。一个几 GB 文件就能把 worker 打满。

**Files:**
- Modify: `app/api/portal/endpoints/ragflow.py`
- Modify: `frontend/src/views/KnowledgeBaseManagement.vue`
- Test: `tests/api/portal/test_knowledge_upload_limits.py`（新建）
- Test: `tests/frontend/test_knowledge_upload_limit_contract.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
import pytest
from types import SimpleNamespace

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


class _FakeUpload:
    def __init__(self, filename: str, payload: bytes):
        self.filename = filename
        self.content_type = "application/octet-stream"
        self._payload = payload
        self._read = False

    async def read(self, size: int = -1):
        if self._read:
            return b""
        self._read = True
        return self._payload


@pytest.mark.asyncio
async def test_upload_rejects_too_large_file(monkeypatch):
    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)

    big = _FakeUpload("manual.pdf", b"x" * (ragflow.KNOWLEDGE_UPLOAD_MAX_BYTES + 1))

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.upload_dataset_document(
            dataset_id="ds-1",
            request=SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
            file=big,
            user={"role": "user", "user_id": "1", "user_name": "alice"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_extension(monkeypatch):
    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)

    bad = _FakeUpload("payload.exe", b"MZ")

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.upload_dataset_document(
            dataset_id="ds-1",
            request=SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
            file=bad,
            user={"role": "user", "user_id": "1", "user_name": "alice"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 415
```

- [ ] **Step 2: 跑失败**

Run: `pytest tests/api/portal/test_knowledge_upload_limits.py -q`
Expected: FAIL（`AttributeError: KNOWLEDGE_UPLOAD_MAX_BYTES`）

- [ ] **Step 3: 实现**

`ragflow.py` 顶部常量与工具：

```python
#: 平台侧单文件上限 256MB：提前拦住，避免整个文件流到上游才报错。
KNOWLEDGE_UPLOAD_MAX_BYTES = 256 * 1024 * 1024
KNOWLEDGE_UPLOAD_MAX_MB = KNOWLEDGE_UPLOAD_MAX_BYTES // (1024 * 1024)

#: 与 RAGFlow 解析器支持的格式保持一致；不在白名单的直接拒绝，不做静默透传。
KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".txt", ".md", ".csv",
    ".xls", ".xlsx", ".ppt", ".pptx", ".html", ".htm",
    ".epub", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff",
}


def _validate_knowledge_upload(file_name: str, size: int) -> None:
    import os

    ext = os.path.splitext(file_name or "")[1].lower()
    if ext not in KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型「{ext or '无扩展名'}」，支持 PDF/Word/Excel/PPT/Markdown/TXT/HTML/图片/EPUB",
        )
    if size > KNOWLEDGE_UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {KNOWLEDGE_UPLOAD_MAX_MB}MB 上限，请拆分后再上传",
        )
```

`upload_dataset_document` 改为分块读取：

```python
    file_name = file.filename or "upload.bin"
    _validate_knowledge_upload(file_name, int(getattr(file, "size", 0) or 0))

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > KNOWLEDGE_UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过 {KNOWLEDGE_UPLOAD_MAX_MB}MB 上限，请拆分后再上传",
            )
        chunks.append(chunk)
    blob = b"".join(chunks)
```

审计里补 `"size": total`，并去掉原来的 `len(blob)`。

- [ ] **Step 4: 前端预校验**

`onFileChange` 增加：

```ts
const KNOWLEDGE_UPLOAD_MAX_MB = 256
const KNOWLEDGE_UPLOAD_EXTENSIONS = [
  'pdf', 'doc', 'docx', 'txt', 'md', 'csv', 'xls', 'xlsx',
  'ppt', 'pptx', 'html', 'htm', 'epub', 'png', 'jpg', 'jpeg', 'gif', 'bmp', 'tif', 'tiff',
]

const onFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  if (!input.files) return
  const files = Array.from(input.files)
  const rejected: string[] = []
  const accepted = files.filter((f) => {
    const ext = f.name.split('.').pop()?.toLowerCase() || ''
    if (!KNOWLEDGE_UPLOAD_EXTENSIONS.includes(ext)) {
      rejected.push(`${f.name}（类型不支持）`)
      return false
    }
    if (f.size > KNOWLEDGE_UPLOAD_MAX_MB * 1024 * 1024) {
      rejected.push(`${f.name}（超过 ${KNOWLEDGE_UPLOAD_MAX_MB}MB）`)
      return false
    }
    return true
  })
  if (rejected.length) {
    showToast(`已跳过 ${rejected.length} 个文件：${rejected.slice(0, 3).join('、')}${rejected.length > 3 ? ' 等' : ''}`, 'warning')
  }
  const newFiles = accepted.filter((f) => !uploadFiles.value.some((e) => e.name === f.name && e.size === f.size))
  uploadFiles.value = [...uploadFiles.value, ...newFiles]
  input.value = ''
}
```

上传引导文案补上限：

```vue
                <p class="text-xs text-gray-400 mt-1.5">
                  支持多文件上传，类型包含 PDF, DOCX, TXT, MD, XLSX 等，单文件不超过 {{ KNOWLEDGE_UPLOAD_MAX_MB }}MB
                </p>
```

- [ ] **Step 5: 跑全绿**

```
pytest tests/api/portal/test_knowledge_upload_limits.py -q
cd frontend && npx vue-tsc --noEmit
```

**验收：**
- 选 300MB 文件 → 前端直接提示跳过，不发请求。
- 绕过前端直接调接口 → 413 中文提示。
- 上传 `.exe` → 415。

---

### Task A6: 归属人的授权行不可撤销

**背景：** 建库时给创建人写了一条 `ResourcePermission`（`ragflow.py:298-309`），授权名单里会出现「创建人」。点「取消授权」会删掉这条记录，但归属人依然可读（判定走 `owner_user_id`）——一次假操作，且让名单失真。

**Files:**
- Modify: `app/api/portal/endpoints/ragflow.py:853-898, 1663-1720`
- Modify: `frontend/src/views/KnowledgeBaseManagement.vue`
- Test: `tests/api/portal/test_knowledge_grant_owner_guard.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_cannot_revoke_owner_permission(monkeypatch):
    class FakeMetaService:
        def __init__(self, db):
            pass

        async def get_by_dataset_id(self, dataset_id):
            return SimpleNamespace(owner_user_id=42, status="active")

    async def _allow(*args, **kwargs):
        return None

    async def fake_record(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetaService)
    monkeypatch.setattr(ragflow, "require_dataset_write_access", _allow)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_record)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.delete_dataset_permission(
            dataset_id="ds-1",
            payload=ragflow.DeleteDatasetPermissionRequest(target_type="user", target_id=42),
            request=_fake_request(),
            db=SimpleNamespace(),
            user={"role": "admin", "user_id": "1", "user_name": "admin"},
        )

    assert exc.value.status_code == 400
    assert "归属人" in exc.value.detail
```

- [ ] **Step 2: 实现后端**

`delete_dataset_permission` 在删除前插入：

```python
    record = await KnowledgeBaseMetadataService(db).get_by_dataset_id(dataset_id)
    if (
        payload.target_type == "user"
        and record is not None
        and record.owner_user_id == payload.target_id
    ):
        # 归属人的可读可写来自 owner_user_id，与这条授权记录无关；
        # 删掉它只会让名单看起来「撤销成功」，实际仍可访问。
        raise HTTPException(
            status_code=400,
            detail="归属人的访问权不可撤销，如需变更请先把知识库转移给其他同事",
        )
```

`get_dataset_permissions` 返回里补：

```python
    record = await KnowledgeBaseMetadataService(db).get_by_dataset_id(dataset_id)
    return {
        "code": 0,
        "data": {
            "users": granted_users,
            "roles": granted_roles,
            "owner_user_id": record.owner_user_id if record else None,
        },
    }
```

- [ ] **Step 3: 前端标记**

授权列表用户行：

```vue
                    <span v-if="user.id === datasetPermissions.ownerUserId" class="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-semibold">归属人</span>
```

取消按钮禁用：

```vue
                        :disabled="user.id === datasetPermissions.ownerUserId"
                        :title="user.id === datasetPermissions.ownerUserId ? '归属人访问权不可撤销，请先转移归属' : '取消授权'"
```

`datasetPermissions` 类型改为 `{ users: any[]; roles: any[]; ownerUserId: number | null }`，拉取时写入 `ownerUserId: data.owner_user_id ?? null`。

- [ ] **Step 4: 跑测试 + 类型检查**

```
pytest tests/api/portal/test_knowledge_grant_owner_guard.py -q
cd frontend && npx vue-tsc --noEmit
```

**验收：**
- 授权名单里创建人带「归属人」标记、无取消按钮。
- 直接调 DELETE 接口 → 400 中文提示。

---

### Task A7: 批量删库 / 批量解析 / 批量删文档

**背景：** 后端 `DELETE /datasets`、`documents/parse`、`documents` 都接受数组，前端只传单个。库一多，逐个删是纯体力活。

**Files:**
- Modify: `frontend/src/views/KnowledgeBaseManagement.vue`
- Test: `tests/frontend/test_knowledge_bulk_actions_contract.py`（新建）

- [ ] **Step 1: 写失败契约**

```python
def test_dataset_tree_has_bulk_selection(page: str) -> None:
    assert "selectedDatasetIds" in page
    assert "bulkDeleteDatasets" in page
    assert "已选" in page


def test_document_tree_has_bulk_selection(page: str) -> None:
    assert "selectedDocIds" in page
    assert "bulkParseDocuments" in page
    assert "bulkDeleteDocuments" in page
```

- [ ] **Step 2: 实现状态与动作**

```ts
const selectedDatasetIds = ref<string[]>([])
const selectedDocIds = ref<string[]>([])
const bulkWorking = ref(false)

const toggleDatasetSelection = (dataset: KnowledgeBase) => {
  const id = dataset.ragflow_dataset_id || dataset.id
  if (!id || !canManageDataset(dataset)) return
  selectedDatasetIds.value = selectedDatasetIds.value.includes(id)
    ? selectedDatasetIds.value.filter((x) => x !== id)
    : [...selectedDatasetIds.value, id]
}

const bulkDeleteDatasets = async () => {
  if (!selectedDatasetIds.value.length || bulkWorking.value) return
  const ids = [...selectedDatasetIds.value]
  bulkWorking.value = true
  try {
    await axios.delete('/api/portal/ragflow/datasets', { data: { ids } })
    showToast(`已删除 ${ids.length} 个知识库`, 'success')
    selectedDatasetIds.value = []
    await fetchDatasets()
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    bulkWorking.value = false
  }
}

const bulkParseDocuments = async (datasetId: string) => {
  const ids = [...selectedDocIds.value]
  if (!ids.length || !datasetId) return
  bulkWorking.value = true
  try {
    await axios.post(`/api/portal/ragflow/datasets/${datasetId}/documents/parse`, { ids })
    ids.forEach((id) => { parsingDocIds.value = { ...parsingDocIds.value, [id]: true } })
    showToast(`已触发 ${ids.length} 个文档解析`, 'success')
    selectedDocIds.value = []
    await fetchDocumentsForDataset(datasetId)
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    bulkWorking.value = false
  }
}
```

（`bulkDeleteDocuments` 同构，调 `DELETE .../documents` 并清理目录引用。）

- [ ] **Step 3: 模板**

> 批量删除的二次确认复用页面现有的 `ConfirmModal`（参照 `deletingDataset` / `pendingPermissionRemoval` 的写法）：新增 `pendingBulkDelete = ref<string[] | null>(null)`，`bulkDeleteDatasets()` 改为在确认弹窗的「确认」回调里执行，不要用 `window.confirm`（与全站交互不一致，且移动端样式不可控）。

库树节点左侧加 checkbox（仅管理级可勾）：

```vue
                  <input
                    v-if="canEdit && canManageDataset(dataset)"
                    type="checkbox"
                    class="w-3.5 h-3.5 shrink-0 rounded border-gray-300 text-primary focus:ring-primary"
                    :checked="selectedDatasetIds.includes(dataset.ragflow_dataset_id || dataset.id)"
                    :aria-label="`选择 ${dataset.platform_name || dataset.name}`"
                    @click.stop
                    @change="toggleDatasetSelection(dataset)"
                  />
```

左栏 topbar 增加批量条（`selectedDatasetIds.length` 时显示）：

```vue
          <div v-if="selectedDatasetIds.length" class="flex items-center justify-between rounded-xl bg-primary/5 border border-primary/20 px-3 py-2 text-xs">
            <span class="font-semibold text-primary">已选 {{ selectedDatasetIds.length }} 个知识库</span>
            <div class="flex items-center gap-2">
              <button class="text-gray-500 hover:text-gray-700" @click="selectedDatasetIds = []">取消</button>
              <button class="px-2.5 py-1 rounded-lg bg-red-600 text-white font-semibold disabled:opacity-50" :disabled="bulkWorking" @click="bulkDeleteDatasets">批量删除</button>
            </div>
          </div>
```

文档行同样加 checkbox，并在选中时显示「批量解析 / 批量删除」。

- [ ] **Step 4: 跑契约 + 类型检查**

```
pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_bulk_actions_contract.py -q
cd frontend && npx vue-tsc --noEmit
```

**验收：**
- 勾 3 个库 → 批量删除 → 一次请求、一次刷新、审计多 3 条。
- 勾 5 个文档 → 批量解析 → 一次请求。
- 无管理权的库不出现 checkbox。

---

### Task A8: 搜索命中未展开库的文档

**背景：** `filteredDatasets` 的 `docMatch` 只查 `documentsMap`（`KnowledgeBaseManagement.vue:1157-1181`），而文档是展开才懒加载，搜索框却承诺「搜索知识库、文档或业务归属部门」。

**Files:**
- Modify: `frontend/src/views/KnowledgeBaseManagement.vue`
- Test: `tests/frontend/test_knowledge_search_documents_contract.py`（新建）

- [ ] **Step 1: 写失败契约**

```python
def test_search_triggers_lazy_document_loading(page: str) -> None:
    assert "ensureDocumentsForSearch" in page
    assert "searchDocLoading" in page


def test_search_loading_is_debounced_and_bounded(page: str) -> None:
    assert "SEARCH_DOC_CONCURRENCY" in page
    assert "300" in page
```

- [ ] **Step 2: 实现**

```ts
/** 搜索文档名时按需加载；并发上限避免一次打爆 RAGFlow。 */
const SEARCH_DOC_CONCURRENCY = 4
const searchDocLoading = ref(false)
let searchDocTimer: ReturnType<typeof setTimeout> | null = null

const ensureDocumentsForSearch = async () => {
  const targets = visibilityScopeDatasets.value
    .filter((d) => !d.is_missing_in_ragflow)
    .map((d) => d.ragflow_dataset_id || d.id)
    .filter((id) => id && !documentsMap.value[id] && !documentsLoadingMap.value[id])
  if (!targets.length) return
  searchDocLoading.value = true
  try {
    for (let i = 0; i < targets.length; i += SEARCH_DOC_CONCURRENCY) {
      await Promise.all(
        targets.slice(i, i + SEARCH_DOC_CONCURRENCY).map((id) => fetchDocumentsForDataset(id)),
      )
    }
  } finally {
    searchDocLoading.value = false
  }
}

watch(searchQuery, (value) => {
  if (searchDocTimer) clearTimeout(searchDocTimer)
  if (!value.trim()) return
  searchDocTimer = setTimeout(() => { void ensureDocumentsForSearch() }, 300)
})
```

空态与计数区补加载提示：

```vue
          <div v-if="searchDocLoading" class="px-3 py-2 text-[11px] text-gray-400">正在检索文档名…</div>
```

- [ ] **Step 3: 跑契约 + 类型检查**

```
pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_search_documents_contract.py -q
cd frontend && npx vue-tsc --noEmit
```

**验收：**
- 不展开任何库，直接搜某文档名 → 命中所在库。
- 快速连续输入不产生请求风暴（Network 里并发 ≤ 4）。

---

### Task A9: 归属人能看到自己失联的库

**背景：** `include_missing` 只对管理员生效（`ragflow.py:455`），普通归属人的库在 RAGFlow 侧失联后直接从列表消失，看起来像「被别人删了」。

**Files:**
- Modify: `app/services/knowledge_base_service.py:224-282`
- Modify: `app/api/portal/endpoints/ragflow.py:450-461`
- Test: `tests/services/test_knowledge_base_metadata_service.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_merge_with_ragflow_filters_missing_by_allow_ids():
    service = KnowledgeBaseMetadataService(db=None)
    mine = KnowledgeBaseMetadata(ragflow_dataset_id="ds-mine", name="我的库", status="active")
    others = KnowledgeBaseMetadata(ragflow_dataset_id="ds-others", name="别人的库", status="active")

    async def fake_list_metadata(include_deleted=False):
        return [mine, others]

    service.list_metadata = fake_list_metadata

    merged = await service.merge_with_ragflow(
        [], include_missing=True, missing_allow_ids={"ds-mine"}
    )

    assert [item["id"] for item in merged] == ["ds-mine"]
    assert merged[0]["is_missing_in_ragflow"] is True
```

- [ ] **Step 2: 实现**

```python
    async def merge_with_ragflow(
        self,
        ragflow_datasets: List[Dict[str, Any]],
        include_missing: bool = True,
        missing_allow_ids: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
```

失联分支加：

```python
                if missing_allow_ids is not None and dataset_id not in missing_allow_ids:
                    continue
```

端点：

```python
        real_include_missing = include_missing
        if override_url:
            real_include_missing = False
        missing_allow_ids = None if is_admin else (access["accessible_ids"] or set())

        merged = await metadata_service.merge_with_ragflow(
            items,
            include_missing=real_include_missing,
            missing_allow_ids=missing_allow_ids,
        )
```

- [ ] **Step 3: 跑测试**

```
pytest tests/services/test_knowledge_base_metadata_service.py -q
pytest tests/api/portal/test_ragflow_knowledge_platform.py -q
```

**验收：**
- 在 RAGFlow 删除某用户自己的库 → 该用户列表出现「失联」标记，可清理本地残留；其他非管理员看不到。

---

### 批次 A 收尾检查

- [ ] `pytest tests/services tests/api/portal -q` 全绿
- [ ] `pytest --confcutdir=tests/frontend tests/frontend -q` 全绿
- [ ] `cd frontend && npx vue-tsc --noEmit` 0 错误
- [ ] 手工回归：建库 → 传文档 → 解析 → 切片 → 授权 → 转移 → 删除 → 同步
- [ ] 更新 `tests/CHECKLIST.md`（每条一行：问题 / 改动 / 验证 / 日期）

---

## 批次 B：内容运维（路线图）

> 开工前展开为 `docs/superpowers/plans/YYYY-MM-DD-knowledge-content-ops.md`，每项含完整 TDD 步骤。

### B1 文档重命名 / 启用停用
- RAGFlow 能力：`PUT /api/v1/datasets/{id}/documents/{doc_id}`，body 支持 `name`、`enabled`、`chunk_method`、`parser_config`。
- 后端：`PUT /datasets/{id}/documents/{doc_id}`（内容级写 + `element:knowledge:parse_document`），审计 `documents:update`。
- 前端：文档详情加「重命名」与「参与检索」开关；停用文档在树里灰显并标注「已停用」。
- 价值：不必删文档就能把过期内容移出召回。

### B2 同名上传：跳过 / 替换
- 上传前 `GET /documents?name=` 精确查重；冲突时前端弹窗三选一（跳过 / 替换 / 保留两者）。
- 替换流程（避免「旧已删、新失败」）：上传新文件 → `PUT` 改名为旧名 → 删除旧文档 → 触发解析；任一步失败回滚删除新文档。
- 审计 `documents:replace`，detail 记 `old_document_id` / `new_document_id` / `file_name`。
- UI 明确提示：「替换会重新切片，历史引用将指向已删除文档」。

### B3 切片增删改 + 启停
- RAGFlow 能力：`POST/DELETE .../documents/{doc_id}/chunks`、`PUT .../chunks/{chunk_id}`（`content` / `available` / `important_keywords`）。
- 新权限码 `element:knowledge:edit_chunk`（迁移 V191 / PG V91），从 `element:knowledge:parse_document` 复制既有授权。
- 后端三个端点 + 审计 `chunks:create/update/delete`（detail 存 chunk_id + 内容前 120 字）。
- 前端切片抽屉：文本域编辑、启用开关、删除、新增；保存后提示「向量重建需数秒」。
- 价值：召回不准时的最后手段，且全程留痕。

### B4 解析配置可视化
- 建库：`embedding_model`（文本输入 + 常用候选）、`chunk_token_num`、`delimiter`、`layout_recognize`；`CreateDatasetRequest` 扩展，`RagFlowClient.create_dataset` 透传 `parser_config`。
- 建后：`PUT /datasets/{id}/settings` → RAGFlow `PUT /datasets/{id}`；`embedding_model` 仅在 `chunk_count == 0` 时允许改（RAGFlow 硬约束），UI 置灰并说明。
- 改 `chunk_method` 后提示「需重新解析全部文档才生效」，提供一键重解析。
- 风险最高（RAGFlow 0.x / 1.0 契约差异），单独 PR、单独回归。

### B5 切片 ↔ 原文联动
- 切片抽屉每条加「查看原文」，用 `DocumentViewer` 打开 `#page=<page_no>`（字段优先 `page_no`，回退 `positions` 解析）。
- 复用对话侧 `RagPreviewDrawer` 的 `#page=N` 方案，不新写预览器。

### B6 通知扩展（解析失败 / 被授权 / 被转移 / 库被删）
- `parse_dataset_documents` 后挂 `BackgroundTasks`：轮询文档状态（15s × 40 次），失败时 `PortalNotificationService.create` 通知上传者，内容含库名 / 文件名 / `progress_msg`。
- 同一服务复用到三个治理事件：
  - `add_dataset_permissions` → 通知被授权的用户（「你获得了知识库 X 的访问权」）；
  - `transfer_dataset_owner` → 通知新归属人（含「需具备 element:knowledge:edit」提示）与旧归属人；
  - `delete_ragflow_datasets` → 通知曾被授权的用户（「你访问的知识库 X 已被删除」），避免他们反复重试。
- 通知中心已有入口，不新增页面；事件类型加 `knowledge:*` 前缀，便于后续按类型静音。

### B7 跨部门授权（team 库授权给其他部门）
- 现状：team 库只对「归属部门（含下级）」自动只读，跨部门协作只能改成 public 再逐个加人，导致可见范围被永久放宽。
- 数据：`ai_agent_resource_permissions` 增加 `dept_id` 列（V192 / PG V92）+ `(resource_type, resource_id, dept_id)` 索引；`ResourcePermission` 模型同步。
- 判定：`resolve_knowledge_dataset_access` 增加 `granted_dept_ids` 参数，按部门 `path` 前缀展开（含下级），与既有 team 判定复用同一套 path 比较，避免两套口径。
- 端点：`POST/DELETE /datasets/{id}/permissions` 的 `target_type` 增加 `"dept"`；返回体 `departments`。
- 前端：授权弹窗第三个 tab「部门」，展示部门树（复用 `/departments/options`）；名单里显示「部门（含下级）」徽标。
- 审计：`datasets:grant` / `datasets:revoke` 的 `target_type` 记 `dept`，detail 带部门名，便于事后解释「为什么兄弟部门能看」。
- 权限：与现有授权同码 `element:user:edit` + 管理级写权限。

### B8 内容来源扩展
- **手工 QA / 笔记**：复用 B3 的 `POST .../chunks`，前端在切片抽屉「新增切片」里支持 `questions` 字段；无需新端点。
- **网页导入**：`POST /datasets/{id}/documents/from-url`，body `{url, name?}`；后端 httpx 抓取（限制 5MB / 只接受 text/html、text/plain）→ 保存为 `.html`/`.txt` → 走现有上传 + 解析链路。审计 `documents:import_url`，detail 记 URL 与最终文件名。安全：禁止内网地址（复用现有 SSRF 白名单工具，若不存在则校验解析后的 IP 不在私网段）。
- **从元数据生成**：复用 `metadata_rag_service`，提供「把某个数据源的表结构说明生成一个知识库文档」入口；输出 Markdown 后走上传链路。默认关闭，开关放系统配置 `knowledge_metadata_export_enabled`。
- 这三项都不改检索链路，只扩内容入口，风险低。

---

## 批次 C：质量与性能（路线图）

### C1 列表性能（建议第一批做）
- `RagFlowClient.list_datasets`：按响应 `total` 翻页到取完（修 >1000 条截断）；页间并发（`asyncio.Semaphore(4)`）；模块级 TTL 缓存 60s + per-base_url 单飞锁。
- `GET /datasets` 增加 `refresh=1` 绕过缓存（同步/刷新按钮用）。
- 前端 `fetchDatasets` 拆成 `loadDatasets`（挂载/普通刷新）与 `refreshDatasets`（强制刷新），目录操作不再触发全量刷新。
- 降级：RAGFlow 超时/不可用时返回上次成功快照（本地元数据 + 缓存统计），响应带 `"stale": true`，前端顶部提示「引擎连接异常，展示的是 X 分钟前的数据」，而不是整页空白错误。
- 预期：首屏 RAGFlow 请求从 10 次串行降到 0（缓存命中）或 3 次并发。

### C2 检索日志与零结果率
- 新表（V193 / PG V93）：
  - `knowledge_retrieval_logs`：`request_id / user_id / agent_id / conversation_id / query_text(500) / top_k / similarity_threshold / vector_weight / hit_count / avg_score / max_score / latency_ms / cited_count / created_at`
  - `knowledge_retrieval_log_targets`：`log_id / dataset_id`（跨库检索按库统计）
- 埋点：`knowledge_executor` / `knowledge_tool` / `rag_executor` 三处检索统一调 `KnowledgeRetrievalTelemetry.record(...)`；**空结果必须写行**。
- 指标页新增：查询量、零结果率、平均命中数、P95 延迟、Top 零结果 query。
- 保留 90 天，接入既有清理任务；`retrieval-test` 从业务审计迁到本表。

### C3 评测集与召回评估
- 新表（V194 / PG V94）：`knowledge_eval_sets`、`knowledge_eval_items`（query + 期望 doc/chunk）。
- 端点：集合 CRUD、`POST /eval-sets/{id}/run`（逐条检索算 recall@k / MRR / 命中率）。
- 前端：检索测试页「保存为评测集」；运营分析页新增「评测」tab。
- 建议在 C2 上线、有真实 query 积累后再做。

### C4 全局治理视图
- 新表 `knowledge_health_snapshots`（V195 / PG V95）：巡检批次 + 库级状态（失联 / 无归属人 / 空库 / 90 天未更新 / 解析失败文档数）。
- 巡检任务：手动触发 + 每日一次（接 task_center），遍历库时限制并发，结果写快照。
- 页面：运营分析页新增「治理巡检」tab（复用 `menu:knowledge_metrics`，不新增菜单）；支持按问题类型筛选、跳转到对应库。

### C5 全局审计
- `GET /api/portal/ragflow/audit-logs`（admin 或 `menu:system:audit`）：按 action / user / dataset / 时间筛选，含 `__global__` 记录；导出 CSV。
- 前端挂在「审计日志」页新增「知识库」来源，或运营分析页。

### C6 前端拆分
- `components/knowledge/`：`DatasetTree.vue`、`DatasetDetail.vue`、`DocumentPanel.vue`、`UploadPanel.vue`、`ChunkDrawer.vue`、`PermissionPanel.vue`、`AuditPanel.vue`。
- `composables/knowledge/`：`useKnowledgeDatasets.ts`、`useDocumentPolling.ts`、`useDatasetPermissions.ts`、`useKnowledgeAudit.ts`。
- 目标：`KnowledgeBaseManagement.vue` < 800 行；纯重构，契约测试全绿 + 手工回归，单独 PR。

### C7 访问申请与审批
- 现状：列表只返回「我能访问的库」，公共级库对未授权用户完全不可见，也没有申请入口 —— 用户不知道平台有什么，管理员只能靠线下告知。
- 新表（V196 / PG V96）`knowledge_access_requests`：`dataset_id / applicant_user_id / reason / status(pending|approved|rejected|cancelled) / reviewer_user_id / review_comment / reviewed_at / created_at`，唯一约束 `(dataset_id, applicant_user_id, status=pending)`（MySQL 用生成列或应用层兜住）。
- 端点：
  - `GET /datasets/catalog`：**仅名称/描述/归属部门/文档数**的公共级目录（不含文档与切片），供「可申请的知识库」页面使用；
  - `POST /datasets/{id}/access-requests`：申请（同一库 pending 只能一条）；
  - `GET /access-requests/mine` / `GET /access-requests/pending` / `POST /access-requests/{id}/review`（通过 → 写 `ResourcePermission` + 失效缓存 + 通知申请人）。
- 权限：申请只需登录 + `element:knowledge:test_retrieval` 或菜单可见；审批要求 `element:user:edit`（与授权同码）。管理员可批量通过。
- 前端：知识库页新增「发现 / 申请」入口（一个抽屉），个人中心显示「我的申请」；待审列表放用户管理页或知识库页顶部红点。
- 审计：`datasets:apply` / `datasets:approve` / `datasets:reject`，detail 记申请人与理由。

### C8 配额与容量
- 系统配置新增：`knowledge_max_documents_per_dataset`（默认 2000）、`knowledge_max_storage_mb_per_dataset`（默认 5120）、`knowledge_max_datasets_per_user`（默认 50，0 表示不限）。
- 校验点：建库（用户库数）、上传（文档数 / 累计大小）。超限返回 409 + 中文提示；管理员不受用户库数限制。
- 存储量来源：RAGFlow dataset `doc_count` + 文档 `size` 求和（列表接口已有 `size`，无需新表）；每次上传后刷新该库统计缓存（TTL 60s）。
- 前端：详情头显示「文档 N / 上限」与进度条；接近上限时上传区提示。
- 指标页新增「容量 Top 10」区块（为 C4 治理视图提供数据）。

### 待排期小项（backlog，不进批次承诺）

这些是评审中确认「值得做但不影响主线」的体验项，按需捡：

| 项 | 做法 | 影响面 |
|---|---|---|
| 列表排序 | 按更新时间 / 文档数 / 引用量排序；接口加 `sort` 参数，本地元数据 + RAGFlow 字段内存排序 | 前端左栏 |
| 「我创建的 / 共享给我」视角 | 左栏在「可见范围三档」之外加一个「我的」筛选，按 `owner_user_id == me` / 授权来源分组 | 前端左栏 |
| 详情页本库趋势 | 复用 `metrics/summary` 按 dataset 过滤，详情头加 7 日迷你折线 | 详情页 |
| 文档下载原文件 | 复用 `GET /documents/{id}/file`，加 `download=1` 强制附件下载 | 文档详情 |
| 批量改可见范围 / 归属部门 | 后端新增批量元数据端点（逐库校验管理级），或前端串行调用现有 PUT | 左栏批量栏 |
| 知识库复制 / 克隆 | 新建库 + 复制文档（RAGFlow 无跨库复制 API，需逐文档下载再上传，成本高） | 低优先 |
| 上次同步时间 | `extra_config` 或独立键记录 `last_synced_at`，左栏顶部展示 | 小 |
| 失联库一键清理 | C4 治理视图里批量清理本地残留（复用现有 DELETE 对 missing 库的行为） | 小 |
| 审计导出 | C5 端点加 `format=csv`，前端走 `download` | 小 |
| 服务端全局搜索 | `GET /datasets/search?q=` 聚合库名 + 文档名 + 切片关键词（RAGFlow chunk 检索） | 中 |

---

## 迁移清单

| 批次 | 版本 | 内容 | 是否需手工执行 |
|---|---|---|---|
| A | 无 | 无表结构变更 | — |
| B3 | MySQL V191 / PG V91 | 新增 `element:knowledge:edit_chunk` + 从 `parse_document` 复制授权 | 是 |
| B7 | MySQL V192 / PG V92 | `ai_agent_resource_permissions.dept_id` + 索引 | 是 |
| C2 | MySQL V193 / PG V93 | `knowledge_retrieval_logs` + `..._targets` | 是 |
| C3 | MySQL V194 / PG V94 | `knowledge_eval_sets` + `knowledge_eval_items` | 是 |
| C4 | MySQL V195 / PG V95 | `knowledge_health_snapshots` | 是 |
| C7 | MySQL V196 / PG V96 | `knowledge_access_requests` | 是 |
| C8 | 无 | 三项配额走 `system_configs`，不建表 | — |

> B4 不建表：解析配置快照继续放 `knowledge_base_metadata.extra_config.parser_config`。

---

## 测试与验收命令

```bash
# 后端（知识库相关）
pytest tests/services/test_knowledge_base_metadata_service.py \
       tests/services/test_knowledge_access_visibility.py \
       tests/services/test_knowledge_reference_service.py \
       tests/api/portal/test_ragflow_knowledge_platform.py \
       tests/api/portal/test_knowledge_audit_logs.py \
       tests/api/portal/test_knowledge_upload_limits.py -q

# 前端契约
pytest --confcutdir=tests/frontend tests/frontend -q

# 前端类型
cd frontend && npx vue-tsc --noEmit
```

## 风险与回滚

| 变更 | 风险 | 缓解 |
|---|---|---|
| A1 重名校验 | 存量重名库导致无法新建同名库 | 只拦「新建」，不校验存量；报错文案给出改名建议 |
| A2 引用扫描 | 智能体多时列表变慢 | 一次 `SELECT` 全表 + 内存聚合；超 2000 行再考虑加索引表 |
| A4 新端点 | 旧前端仍调整份 PUT | 端点纯新增，旧路径保留；前端灰度后删旧函数 |
| A5 上传上限 | 少数大文件被拒 | 上限 256MB（与前端同一常量口径），文案给拆分建议 |
| A7 批量删除 | 误选多个库 | 二次确认 + 显示数量与名称；只允许勾管理级 |
| B4 解析配置 | RAGFlow 版本差异导致 4xx | 能力探测 + 降级为「仅展示」；单独 PR 便于回滚 |
| C1 缓存 | 同步后看不到新库 | `refresh=1` 强制刷新；TTL 60s |

## 执行顺序建议

1. **A1 → A3 → A4 → A6 → A5**（正确性优先，改动小、可当天验证）
2. **A2 → A9 → A8 → A7**（治理与体验，改前端为主）
3. **B1 → B2 → B3**（内容运维，B3 需迁移）
4. **B7 → B6 → B8**（协作与内容入口；B7 需要权限表迁移，先小范围灰度）
5. **C1 → C2 → C4 → C5 → C3 → C8 → C7 → C6**（性能与治理；C6 前端拆分最后做，避免与其他前端改动冲突）

> 不按 A/B/C 严格串行的原因：B7 的「跨部门授权」与 C7 的「申请审批」共享同一套权限写入路径。先做 B7 能把 `ResourcePermission` 的部门维度打通，C7 审批通过时直接复用写授权逻辑，少一次返工。

## 需要你拍板的 4 个决策

| 决策 | 选项 | 建议 |
|---|---|---|
| 切片编辑权限码 | 新增 `element:knowledge:edit_chunk` / 复用 `element:knowledge:parse_document` | 新增（内容改写比触发解析危险，应可单独收紧；迁移自动继承旧授权，不影响现有角色） |
| 文档替换语义 | 删旧+传新 / 只允许改元数据不允许换内容 | 删旧+传新，UI 明确提示「历史引用会指向已删除文档」；引用历史本身存的是名称快照，不会 404 |
| 申请审批落地位置 | 用户管理页 / 知识库页「发现」抽屉 | 知识库页「发现」抽屉 + 个人中心「我的申请」，审批放用户页（与授权同码，避免再开菜单） |
| 前端拆分时机 | 现在拆 / C 批最后拆 | C 批最后拆（A/B 新功能先落在现有文件，避免边拆边改冲突） |
