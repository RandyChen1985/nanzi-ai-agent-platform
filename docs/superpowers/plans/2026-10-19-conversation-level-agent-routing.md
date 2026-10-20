# 会话级专家选择 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把输入框的专家选择从账号级下沉到会话级：每个会话记住自己最后选的专家，切回时自动恢复，新建会话沿用账号默认。

**Architecture:** 新增一个会话级 Redis 存储服务（完全对齐现有 `ConversationResourceService` 的键与 TTL 模式），在 chat 路由旁新增一对 GET/PUT 端点，前端在切会话时读取、切专家时写入。不新增数据库表，不改 chat 主链路的配置解析。

**Tech Stack:** FastAPI + Pydantic 2 + Redis（服务端）；Vue 3 + TypeScript + axios（前端）；pytest + pytest-asyncio（测试）。

**Spec:** `docs/superpowers/specs/2026-10-19-conversation-level-agent-routing-design.md`

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `app/services/conversation_routing_service.py`（新建） | 会话级专家记录的读写与键/TTL 约定 |
| `app/api/v1/endpoints/chat.py`（修改） | 两个 HTTP 端点 + 专家可用性校验 |
| `frontend/src/views/EmbedChat.vue`（修改） | `loadConversationRouting` / `saveConversationRouting` 与 4 处接入 |
| `tests/services/test_conversation_routing_service.py`（新建） | 存储层单测（mock Redis） |
| `tests/api/v1/test_conversation_routing_endpoint.py`（新建） | 端点契约测试（不依赖 Redis/DB） |
| `tests/frontend/test_conversation_routing_contract.py`（新建） | 前端接线契约测试（读源码断言） |

---

## Task 1: 会话级专家存储服务

**Files:**
- Create: `app/services/conversation_routing_service.py`
- Test: `tests/services/test_conversation_routing_service.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/services/test_conversation_routing_service.py`：

```python
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.conversation_routing_service import (
    TTL_SECONDS,
    ConversationRoutingService,
)


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_get_returns_none_when_no_record():
    """没记录必须返回 None：前端要区分「无记录」与「记录为 auto」。"""
    redis = AsyncMock()
    redis.get.return_value = None
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        assert await ConversationRoutingService.get(7, "conv-1") is None


@pytest.mark.asyncio
async def test_replace_uses_shared_ttl_and_conversation_key_shape():
    redis = AsyncMock()
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        await ConversationRoutingService.replace(7, "conv-1", "expert", "agent-1")

    assert TTL_SECONDS == 2592000
    assert ConversationRoutingService.TTL_SECONDS == TTL_SECONDS
    assert redis.set.await_args.kwargs["ex"] == TTL_SECONDS
    assert redis.set.await_args.args[0] == "conversation:7:conv-1:routing_v1"


@pytest.mark.asyncio
async def test_auto_mode_clears_expert_agent_id():
    redis = AsyncMock()
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        payload = await ConversationRoutingService.replace(7, "conv-1", "auto", "agent-1")

    assert payload == {"routing_mode": "auto", "expert_agent_id": ""}


@pytest.mark.asyncio
async def test_get_decodes_expert_record():
    redis = AsyncMock()
    redis.get.return_value = json.dumps(
        {"routing_mode": "expert", "expert_agent_id": " agent-1 "}
    )
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        record = await ConversationRoutingService.get(7, "conv-1")

    assert record == {"routing_mode": "expert", "expert_agent_id": "agent-1"}


@pytest.mark.asyncio
async def test_expert_without_agent_id_is_treated_as_missing():
    """脏数据（expert 但没有专家 ID）不能把界面锁死在一个空专家上。"""
    redis = AsyncMock()
    redis.get.return_value = json.dumps({"routing_mode": "expert", "expert_agent_id": ""})
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        assert await ConversationRoutingService.get(7, "conv-1") is None


@pytest.mark.asyncio
async def test_redis_unavailable_degrades_without_raising():
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        side_effect=RuntimeError("redis down"),
    ):
        assert await ConversationRoutingService.get(7, "conv-1") is None
        payload = await ConversationRoutingService.replace(7, "conv-1", "expert", "agent-1")
        assert payload == {"routing_mode": "expert", "expert_agent_id": "agent-1"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/services/test_conversation_routing_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.conversation_routing_service'`

- [ ] **Step 3: 实现服务**

创建 `app/services/conversation_routing_service.py`：

```python
"""会话级专家选择：记录每个会话最后选择的专家与路由模式。

与会话历史（``conversation:{uid}:{cid}:history``）和会话资源范围
（``conversation:{uid}:{cid}:resource_scope_v1``）共用同一套键前缀与存活期，
会话过期后被自然回收，不需要额外的清理任务。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from app.core.redis import get_redis
from app.services.ai.conversation_identity import require_user_id

logger = logging.getLogger(__name__)

#: 会话相关键的统一存活期（30 天），与会话历史保持一致。
TTL_SECONDS = 2592000


class ConversationRoutingService:
    KEY_PREFIX = "conversation"
    SUFFIX = "routing_v1"
    TTL_SECONDS = TTL_SECONDS

    @classmethod
    def _key(cls, user_id: Any, conversation_id: str) -> str:
        return f"{cls.KEY_PREFIX}:{require_user_id(user_id)}:{conversation_id}:{cls.SUFFIX}"

    @classmethod
    def _decode(cls, raw: Any) -> Optional[Dict[str, str]]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        mode = "expert" if value.get("routing_mode") == "expert" else "auto"
        agent_id = str(value.get("expert_agent_id") or "").strip()
        if mode == "expert" and not agent_id:
            # expert 却没有专家 ID 属于脏数据：当作没有记录，让调用方回落账号默认，
            # 否则界面会稳定地停在一个永远选不中的专家上。
            return None
        return {"routing_mode": mode, "expert_agent_id": agent_id if mode == "expert" else ""}

    @classmethod
    async def get(cls, user_id: Any, conversation_id: str) -> Optional[Dict[str, str]]:
        """读取会话级专家记录；没有记录或 Redis 不可用时返回 None。"""
        if not str(conversation_id or "").strip():
            return None
        try:
            redis = await get_redis()
        except Exception:
            redis = None
        if not redis:
            return None
        try:
            raw = await redis.get(cls._key(user_id, conversation_id))
        except Exception:
            logger.warning("Failed to read conversation routing: %s", conversation_id)
            return None
        return cls._decode(raw)

    @classmethod
    async def replace(
        cls,
        user_id: Any,
        conversation_id: str,
        routing_mode: str,
        expert_agent_id: str = "",
    ) -> Dict[str, str]:
        """覆盖写入会话级专家记录；写入失败不抛异常（前端本地切换已生效）。"""
        mode = "expert" if routing_mode == "expert" else "auto"
        agent_id = str(expert_agent_id or "").strip() if mode == "expert" else ""
        payload = {"routing_mode": mode, "expert_agent_id": agent_id}
        if not str(conversation_id or "").strip():
            return payload
        try:
            redis = await get_redis()
            if redis:
                await redis.set(
                    cls._key(user_id, conversation_id),
                    json.dumps(payload, ensure_ascii=False),
                    ex=cls.TTL_SECONDS,
                )
        except Exception:
            logger.warning("Failed to persist conversation routing: %s", conversation_id)
        return payload
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/services/test_conversation_routing_service.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交（需用户确认后再执行）**

```bash
git add app/services/conversation_routing_service.py tests/services/test_conversation_routing_service.py
git commit -m "feat(chat): add conversation-level agent routing store"
```

> 本项目规范：Agent 不自动执行 `git commit`，此步由用户决定。

---

## Task 2: 会话级路由端点

**Files:**
- Modify: `app/api/v1/endpoints/chat.py`（在 `resource-scope` 端点之后，约 3262 行）
- Test: `tests/api/v1/test_conversation_routing_endpoint.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/api/v1/test_conversation_routing_endpoint.py`：

```python
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.main import app


pytestmark = pytest.mark.no_infrastructure


def _override_auth(user_id: int = 7):
    app.dependency_overrides[require_api_key] = lambda: {
        "user_id": user_id,
        "user_name": f"user-{user_id}",
        "role": "user",
    }


async def _override_db():
    yield None


@pytest.fixture(autouse=True)
def _clean_overrides():
    app.dependency_overrides[get_db_session] = _override_db
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_returns_null_when_no_record():
    _override_auth()
    with patch(
        "app.api.v1.endpoints.chat.ConversationRoutingService.get",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/chat/conversation/conv-1/routing")

    assert resp.status_code == 200
    assert resp.json()["data"] is None


@pytest.mark.asyncio
async def test_get_returns_existing_record():
    _override_auth()
    record = {"routing_mode": "expert", "expert_agent_id": "agent-1"}
    with patch(
        "app.api.v1.endpoints.chat.ConversationRoutingService.get",
        new=AsyncMock(return_value=record),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/chat/conversation/conv-1/routing")

    assert resp.status_code == 200
    assert resp.json()["data"] == record


@pytest.mark.asyncio
async def test_put_rejects_expert_mode_without_agent():
    _override_auth()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/api/v1/chat/conversation/conv-1/routing",
            json={"routing_mode": "expert", "expert_agent_id": ""},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_rejects_unpublished_agent_with_404():
    _override_auth()
    with patch(
        "app.api.v1.endpoints.chat.AgentManagerService.resolve_embed_agent_access",
        new=AsyncMock(side_effect=LookupError("agent_not_ready")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/v1/chat/conversation/conv-1/routing",
                json={"routing_mode": "expert", "expert_agent_id": "agent-1"},
            )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_rejects_forbidden_agent_with_403():
    _override_auth()
    with patch(
        "app.api.v1.endpoints.chat.AgentManagerService.resolve_embed_agent_access",
        new=AsyncMock(side_effect=PermissionError("forbidden")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/v1/chat/conversation/conv-1/routing",
                json={"routing_mode": "expert", "expert_agent_id": "agent-1"},
            )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_auto_mode_writes_empty_agent():
    _override_auth()
    replace = AsyncMock(return_value={"routing_mode": "auto", "expert_agent_id": ""})
    with patch(
        "app.api.v1.endpoints.chat.ConversationRoutingService.replace",
        new=replace,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/v1/chat/conversation/conv-1/routing",
                json={"routing_mode": "auto", "expert_agent_id": "agent-1"},
            )

    assert resp.status_code == 200
    assert resp.json()["data"] == {"routing_mode": "auto", "expert_agent_id": ""}
    assert replace.await_args.args[2] == "auto"
    assert replace.await_args.args[3] == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/api/v1/test_conversation_routing_endpoint.py -v`
Expected: FAIL — 404（路由不存在），且 `app.api.v1.endpoints.chat.ConversationRoutingService` 不存在

- [ ] **Step 3: 实现端点**

在 `app/api/v1/endpoints/chat.py` 顶部导入区（第 25 行 `ConversationResourceService` 之后）加：

```python
from app.services.conversation_routing_service import ConversationRoutingService
```

在 `update_conversation_resource_scope` 函数之后（约 3262 行）加：

```python
class ConversationRoutingUpdateRequest(BaseModel):
    routing_mode: str = Field(default="auto", description="auto | expert")
    expert_agent_id: Optional[str] = Field(default="", description="专家模式必填")


@router.get(
    "/conversation/{conversation_id}/routing",
    summary="获取会话级专家选择",
    description="无记录时返回 data=null，由前端回落账号默认。",
)
async def get_conversation_routing(
    conversation_id: str,
    user_info: dict = Depends(require_api_key),
):
    user_id = _require_chat_user_id(user_info)
    routing = await ConversationRoutingService.get(user_id, conversation_id)
    return StandardResponse(data=routing)


@router.put(
    "/conversation/{conversation_id}/routing",
    summary="更新会话级专家选择",
    description="专家模式会校验智能体可用性与当前用户权限，与账号级路由偏好保持一致。",
)
async def update_conversation_routing(
    conversation_id: str,
    body: ConversationRoutingUpdateRequest,
    user_info: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = _require_chat_user_id(user_info)
    mode = "expert" if body.routing_mode == "expert" else "auto"
    agent_id = str(body.expert_agent_id or "").strip()

    if mode == "expert":
        if not agent_id:
            raise HTTPException(
                status_code=422,
                detail="专家模式必须选择智能体",
            )
        try:
            agent = await AgentManagerService.resolve_embed_agent_access(
                db, agent_id, user_info
            )
            active_config = await AgentManagerService.get_active_agent_config(
                db, agent_id=str(agent.id)
            )
            if not active_config:
                raise LookupError("agent_not_ready")
        except LookupError:
            raise HTTPException(
                status_code=404,
                detail="智能体不存在、已停用或尚未发布",
            ) from None
        except PermissionError:
            raise HTTPException(status_code=403, detail="无权使用该智能体") from None
        agent_id = str(agent.id)
    else:
        agent_id = ""

    routing = await ConversationRoutingService.replace(
        user_id, conversation_id, mode, agent_id
    )
    return StandardResponse(data=routing)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/api/v1/test_conversation_routing_endpoint.py -v`
Expected: 6 passed

- [ ] **Step 5: 跑一次相邻端点回归（确认没影响原有会话接口）**

Run: `pytest tests/api/v1/test_active_conversation.py tests/services/test_conversation_resource_service.py -v`
Expected: 全部 passed

- [ ] **Step 6: 提交（需用户确认后再执行）**

```bash
git add app/api/v1/endpoints/chat.py tests/api/v1/test_conversation_routing_endpoint.py
git commit -m "feat(chat): expose conversation routing endpoints"
```

---

## Task 3: 前端读写函数

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`（`switchToAuto` 附近，约 4152 行前）

- [ ] **Step 1: 加入两个函数**

在 `const switchToAuto = () => {` 之前插入：

```ts
/** 会话级专家读取序号：连续切会话时只认最后一次响应，避免旧响应覆盖新会话。 */
let conversationRoutingLoadSequence = 0;

/** 读取会话自己的专家选择；无记录（data=null）时保持当前值，即账号默认。 */
const loadConversationRouting = async (cid: string) => {
  const normalized = String(cid || "").trim();
  if (!normalized || isRoutingSettingsLocked.value) return;
  const sequence = ++conversationRoutingLoadSequence;
  try {
    const res = await axios.get(
      `/api/v1/chat/conversation/${encodeURIComponent(normalized)}/routing`,
      { headers: embedAuthHeaders() },
    );
    if (sequence !== conversationRoutingLoadSequence) return;
    if (conversationId.value !== normalized) return;
    const routing = res.data?.data;
    if (!routing) return;
    if (routing.routing_mode === "expert" && routing.expert_agent_id) {
      config.routingMode = "expert";
      config.expertAgentId = String(routing.expert_agent_id);
    } else {
      config.routingMode = "auto";
      config.expertAgentId = "";
    }
  } catch (e) {
    console.warn("[Routing] Failed to load conversation routing:", e);
  }
};

/** 写入会话自己的专家选择；失败只告警——本地切换已经生效，不打断用户。 */
const saveConversationRouting = async (
  cid: string,
  mode: "auto" | "expert",
  agentId = "",
) => {
  const normalized = String(cid || "").trim();
  if (!normalized || isRoutingSettingsLocked.value) return;
  try {
    await axios.put(
      `/api/v1/chat/conversation/${encodeURIComponent(normalized)}/routing`,
      {
        routing_mode: mode,
        expert_agent_id: mode === "expert" ? String(agentId || "").trim() : "",
      },
      { headers: embedAuthHeaders() },
    );
  } catch (e) {
    console.warn("[Routing] Failed to save conversation routing:", e);
  }
};
```

- [ ] **Step 2: 同步更新账号默认的本地缓存**

`saveRoutingPreference` 目前只 PUT，不更新 `savedRoutingPreference`，会导致“新建会话沿用账号默认”取到旧值。把函数改成：

```ts
const saveRoutingPreference = async (mode: "auto" | "expert", agentId = "") => {
    if (isRoutingSettingsLocked.value) return;
    const normalizedAgentId = mode === "expert" ? String(agentId || "").trim() : "";
    // 新建会话要沿用账号默认，本地缓存必须先同步，否则会拿到上一次的旧值。
    savedRoutingPreference.value = {
        routing_mode: mode,
        expert_agent_id: normalizedAgentId,
        routing_configured: true,
    };
    try {
        await axios.put("/api/portal/portal-prefs/routing", {
            routing_mode: mode,
            expert_agent_id: normalizedAgentId,
        });
    } catch (error) {
        console.error("Failed to save routing preference to Redis", error);
        showToast("路由偏好保存失败，请稍后重试", "error");
    }
};
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增错误（允许存在与本次改动无关的既有报错，需逐条核对不是这两个函数引起的）

- [ ] **Step 4: 提交（需用户确认后再执行）**

```bash
git add frontend/src/views/EmbedChat.vue
git commit -m "feat(embed): add conversation routing load/save helpers"
```

---

## Task 4: 前端四处接入

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`

- [ ] **Step 1: 切专家时写会话记录**

`switchToExpert` 内，把：

```ts
    config.expertAgentId = normalizedAgentId;
    config.routingMode = "expert";
    void saveRoutingPreference("expert", normalizedAgentId);
    // URL 深链锁定初始化时不提示，避免首屏弹出
```

改为：

```ts
    config.expertAgentId = normalizedAgentId;
    config.routingMode = "expert";
    void saveRoutingPreference("expert", normalizedAgentId);
    void saveConversationRouting(conversationId.value, "expert", normalizedAgentId);
    // URL 深链锁定初始化时不提示，避免首屏弹出
```

- [ ] **Step 2: 切到智能委派时写会话记录**

`switchToAuto` 内，把：

```ts
    config.routingMode = "auto";
    config.expertAgentId = "";
    void saveRoutingPreference("auto");
    showToast("已切换为智能委派模式", "success");
```

改为：

```ts
    config.routingMode = "auto";
    config.expertAgentId = "";
    void saveRoutingPreference("auto");
    void saveConversationRouting(conversationId.value, "auto");
    showToast("已切换为智能委派模式", "success");
```

- [ ] **Step 3: 切会话时恢复该会话的专家**

在会话切换入口（`conversationId.value = target;` 之后）把：

```ts
    conversationId.value = target;
    persistConversationId(target);
    updateActiveConversationOnServer(target);
```

改为：

```ts
    conversationId.value = target;
    persistConversationId(target);
    updateActiveConversationOnServer(target);
    // 恢复该会话自己的专家选择；无记录时 loadConversationRouting 会保持账号默认。
    void loadConversationRouting(target);
```

- [ ] **Step 4: 初始化时恢复**

把初始化流程第 7 步：

```ts
    // 7. Load history if exists
    if (conversationId.value) {
      fetchConversationHistory(false, initGeneration)
```

改为：

```ts
    // 7. 恢复该会话自己的专家选择（无记录时保持账号默认），再加载历史
    if (conversationId.value) {
      await loadConversationRouting(conversationId.value);
    }

    // 8. Load history if exists
    if (conversationId.value) {
      fetchConversationHistory(false, initGeneration)
```

- [ ] **Step 5: 新会话固化账号默认**

`generateNewConversation` 内，把：

```ts
  if (options.persist !== false) {
    persistConversationId(conversationId.value);
    updateActiveConversationOnServer(conversationId.value);
  }
  loadResourceScope();
```

改为：

```ts
  if (options.persist !== false) {
    persistConversationId(conversationId.value);
    updateActiveConversationOnServer(conversationId.value);
    // 新会话从第一天起就有自己的专家记录：用账号默认，而不是上一个会话恢复出来的专家。
    const accountRouting = savedRoutingPreference.value;
    const accountMode: RoutingMode =
      accountRouting.routing_mode === "expert" && accountRouting.expert_agent_id
        ? "expert"
        : "auto";
    void saveConversationRouting(
      conversationId.value,
      accountMode,
      accountMode === "expert" ? accountRouting.expert_agent_id : "",
    );
  }
  loadResourceScope();
```

- [ ] **Step 6: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 7: 提交（需用户确认后再执行）**

```bash
git add frontend/src/views/EmbedChat.vue
git commit -m "feat(embed): restore and persist agent routing per conversation"
```

---

## Task 5: 前端接线契约测试

**Files:**
- Test: `tests/frontend/test_conversation_routing_contract.py`

- [ ] **Step 1: 写契约测试**

创建 `tests/frontend/test_conversation_routing_contract.py`：

```python
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _embed() -> str:
    return (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")


def test_embed_defines_conversation_routing_helpers():
    source = _embed()
    assert "const loadConversationRouting = async (cid: string)" in source
    assert "const saveConversationRouting = async (" in source
    assert "/api/v1/chat/conversation/${encodeURIComponent(normalized)}/routing" in source
    assert "routing_mode" in source
    assert "expert_agent_id" in source


def test_switching_conversation_restores_its_routing():
    source = _embed()
    assert "void loadConversationRouting(target);" in source
    assert "await loadConversationRouting(conversationId.value);" in source


def test_switching_expert_persists_conversation_routing():
    source = _embed()
    assert 'void saveConversationRouting(conversationId.value, "expert", normalizedAgentId);' in source
    assert 'void saveConversationRouting(conversationId.value, "auto");' in source


def test_new_conversation_seeds_account_default_routing():
    source = _embed()
    assert "const accountMode: RoutingMode =" in source
    assert "void saveConversationRouting(\n      conversationId.value,\n      accountMode," in source


def test_url_locked_agent_never_reads_or_writes_routing():
    """锁定链接的专家由 URL 决定，读写会话记录会破坏锁定语义。"""
    source = _embed()
    assert source.count("if (!normalized || isRoutingSettingsLocked.value) return;") >= 2


def test_account_default_cache_is_updated_on_save():
    """新建会话沿用账号默认，缓存不同步会拿到旧值。"""
    source = _embed()
    assert "savedRoutingPreference.value = {\n        routing_mode: mode," in source
```

- [ ] **Step 2: 运行测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_conversation_routing_contract.py -v`
Expected: 6 passed

> 若某条断言因换行/缩进与实现不完全一致而失败，**改测试里的匹配串**去适配真实代码，不要为了迁就测试而改实现逻辑。

- [ ] **Step 3: 提交（需用户确认后再执行）**

```bash
git add tests/frontend/test_conversation_routing_contract.py
git commit -m "test(embed): lock conversation routing wiring"
```

---

## Task 6: 全量回归与手工验收

**Files:** 无（只运行验证）

- [ ] **Step 1: 后端回归**

Run: `pytest tests/services/test_conversation_routing_service.py tests/api/v1/test_conversation_routing_endpoint.py tests/services/test_conversation_resource_service.py tests/api/v1/test_active_conversation.py -q`
Expected: 全部 passed

- [ ] **Step 2: 前端契约回归**

Run: `pytest --confcutdir=tests/frontend tests/frontend -q`
Expected: 全部 passed

- [ ] **Step 3: 前端类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 4: 手工验收（由用户在本机控制台执行，Agent 不代跑 `./dev.sh`）**

在浏览器里按顺序验证：

1. 新开两个窗口，都进入对话页，分别切到不同专家。
2. 在窗口 A 切到「知识库助手」并发送一条消息；刷新窗口 B，确认 B 显示的是账号默认（不是 A 的专家）。
3. 在窗口 B 切到「数据智能助手」；回到 A 点历史里的**同一个会话**，确认输入框变成「数据智能助手」。
4. 在 A 点「新建对话」，确认输入框回到账号默认专家。
5. 用带 `agent_id` 的锁定链接打开，确认切换专家仍被禁用，且刷回原会话后专家不被改写。

- [ ] **Step 5: 更新测试清单**

在 `tests/CHECKLIST.md` 中补充这三条测试文件与覆盖场景（项目 PR 模板要求）。

---

## 自审记录

- **Spec 覆盖**：存储（Task 1）、接口（Task 2）、前端读写与 4 处接入（Task 3-4）、边界中的锁定链接与无 cid（Task 4 的守卫与 Task 5 断言）、测试（Task 1/2/5/6）。设计文档第 6 节“智能体随后被停用”由既有初始化逻辑 `switchToAuto()` 兜底（`EmbedChat.vue` 初始化分支已有该降级），本计划不新增逻辑。
- **占位符**：无 TBD/TODO，所有代码步骤均给出完整实现。
- **类型一致性**：`ConversationRoutingService.get/replace` 签名在 Task 1 定义、Task 2 调用一致；前端 `loadConversationRouting(cid)` / `saveConversationRouting(cid, mode, agentId)` 在 Task 3 定义、Task 4 调用一致；`savedRoutingPreference` 字段名沿用既有 `RoutingPreference` 类型。
- **未覆盖项（有意）**：多标签实时同步、会话列表展示专家名、历史消息反推专家——均属设计文档第 3 节非目标。
