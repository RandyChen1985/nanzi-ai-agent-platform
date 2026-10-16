# EmbedChat「进行中的会话」入口与强制终止 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 EmbedChat 顶部加一个「进行中的会话」按钮与浮层，让用户能看到、进入并终止自己所有正在运行的会话（含续约已停的疑似僵死锁）。

**Architecture:** 后端新增一个纯只读接口，`SCAN` 当前用户在 Redis 里的会话 lane 锁并按「剩余 TTL 是否低于续约窗口」标注 `active` / `stale`；前端用一个 composable 负责轮询与状态、一个纯渲染组件负责浮层、EmbedChat 只做接线；终止统一复用已有的 `/api/v1/chat/cancel`（有 handle 时真取消、无 handle 时只强释放锁）。

**Tech Stack:** Python 3.11 / FastAPI / Pydantic 2 / pytest；Vue 3 + TypeScript + Vite 7 + Tailwind 3；redis-py asyncio；`vue-tsc --noEmit`。

**设计文档：** `docs/superpowers/specs/2026-10-16-active-runs-popover-design.md`

> **⚠️ 实施后审查修正（2026-10-16，以 spec 的「实施后审查修复」小节为准）**：本计划的
> 部分代码块已被实施后审查推翻，照抄会重新引入缺陷——
> ① Task 1 里 `stale_before = max(1, int(ttl_seconds * 2 / 3))` **必须改为
> `max(1, int(ttl_seconds * 0.5))`**（`2/3·ttl` 与正常续约下限零距离，抖动即误报，
> 而用户点「强制释放」会硬取消正在正常运行的 run）；相应断言从 `== 400` 改为 `== 300`。
> ② Task 4 的 `stop()` 只有 `clearTimeout`，**必须补 `stopped` 标志**并在 `schedule()`
> 内检查，否则 in-flight 请求的 `finally` 会复活轮询（hidden 后仍每 15s 请求）。
> ③ Task 3 的结果对象 **必须补 `responded`**，兼容入口按 `responded` 判定，否则会话
> 恰好自然结束时会弹假警告。
> ④ Task 1 的 `scan_iter` **必须转义 uid 里的 glob 元字符**并用 `partition` 二次校验
> uid 段。详见 spec 的同名小节与本计划末尾「实施中修正」。

## 执行约束（重要）

- **不要执行 `git commit`**。项目约定提交由用户决定：每个任务结束时**停下汇报**改了什么、验证结果，等用户处理提交。计划里的「停下汇报」就是原本的 commit 步骤。
- 不要执行 `./dev.sh`、部署脚本或生产数据库操作。
- 后端测试命令统一为 `python -m pytest`；纯前端契约测试必须带 `--confcutdir=tests/frontend`（否则会拉起基础设施 conftest）。
- 所有新增代码注释、文案使用中文。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/services/ai/runtime/session_run_lane.py` | 修改 | 新增只读方法 `list_active()`：枚举本人 lane 锁并标注状态 |
| `app/api/v1/endpoints/chat.py` | 修改 | 新增 `GET /active-runs` 端点与两个响应模型 |
| `tests/ai/runtime/test_session_run_lane.py` | 修改 | `list_active` 单测（复用文件内既有 `FakeRedis`） |
| `tests/api/test_chat_active_runs_endpoint.py` | 新建 | 端点契约单测 |
| `frontend/src/utils/cancelConversationRun.ts` | 修改 | 新增 `cancelConversationRunDetailed()`，原函数委托它 |
| `frontend/src/composables/chat/useActiveRuns.ts` | 新建 | 列表状态 + 打开 3s / 关闭 15s 轮询 + 失败保留旧数据 |
| `frontend/src/components/chat/ActiveRunsPopover.vue` | 新建 | 纯渲染浮层（锚定 / 移动端模态两种变体） |
| `frontend/src/views/EmbedChat.vue` | 修改 | 顶部栏入口与角标、标题映射、点击进入、终止；`handleHistoryClick` 补 `resetRunStatus()` |
| `tests/frontend/test_active_runs_contract.py` | 新建 | 前端契约测试 |
| `tests/CHECKLIST.md` | 修改 | 按项目约定登记本次改动 |

---

### Task 1: 后端 lane 只读扫描 `list_active`

**Files:**
- Modify: `app/services/ai/runtime/session_run_lane.py`（在 `get_status` 方法之后、`hold` 之前插入）
- Test: `tests/ai/runtime/test_session_run_lane.py`（文件末尾追加）

- [ ] **Step 1: 写失败的测试**

在 `tests/ai/runtime/test_session_run_lane.py` 末尾追加（`FakeRedis` 已有 `scan_iter`/`get`/`ttl`/`set`，无需改动）：

```python
@pytest.mark.asyncio
async def test_list_active_marks_stale_when_renewal_stopped(monkeypatch):
    """续约间隔是 ttl/3：剩余 TTL 跌破 2/3·ttl 说明续约已停，应判为 stale。"""
    fake = FakeRedis()
    lane = ConversationRunLane()
    fresh = f"conv-fresh-{uuid.uuid4().hex}"
    stale = f"conv-stale-{uuid.uuid4().hex}"
    fake.store[lane._lock_key("u1", fresh)] = str(uuid.uuid4())
    fake.store[lane._lock_key("u1", stale)] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u1", fresh)] = 590
    fake.ttls[lane._lock_key("u1", stale)] = 60

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        if key == "agent_session_run_lock_enabled":
            return "true"
        if key == "agent_session_run_lock_ttl_seconds":
            return "600"
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    snapshot = await lane.list_active(user_id="u1")

    assert snapshot["ttl_seconds"] == 600
    assert snapshot["stale_before_ttl_seconds"] == 300
    states = {item["conversation_id"]: item["state"] for item in snapshot["items"]}
    assert states[fresh] == "active"
    assert states[stale] == "stale"
    assert set(states) == {fresh, stale}


@pytest.mark.asyncio
async def test_list_active_ignores_other_users_and_expired_keys(monkeypatch):
    """只返回本人会话；TTL=-2（已过期）跳过，TTL=-1（无过期）保守判活跃。"""
    fake = FakeRedis()
    lane = ConversationRunLane()
    mine = f"conv-mine-{uuid.uuid4().hex}"
    other = f"conv-other-{uuid.uuid4().hex}"
    expired = f"conv-expired-{uuid.uuid4().hex}"
    no_expiry = f"conv-noexp-{uuid.uuid4().hex}"
    fake.store[lane._lock_key("u1", mine)] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u1", mine)] = 500
    fake.store[lane._lock_key("u2", other)] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u2", other)] = 500
    fake.store[lane._lock_key("u1", expired)] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u1", expired)] = -2
    fake.store[lane._lock_key("u1", no_expiry)] = str(uuid.uuid4())
    fake.ttls.pop(lane._lock_key("u1", no_expiry), None)

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        if key == "agent_session_run_lock_enabled":
            return "true"
        if key == "agent_session_run_lock_ttl_seconds":
            return "600"
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    snapshot = await lane.list_active(user_id="u1")
    ids = {item["conversation_id"] for item in snapshot["items"]}

    assert mine in ids
    assert no_expiry in ids
    assert other not in ids
    assert expired not in ids
    by_id = {item["conversation_id"]: item for item in snapshot["items"]}
    assert by_id[no_expiry]["state"] == "active"
    assert by_id[no_expiry]["ttl_seconds"] is None


@pytest.mark.asyncio
async def test_list_active_returns_empty_when_disabled(monkeypatch):
    """锁功能关闭时列表为空，且不触碰 Redis。"""
    fake = FakeRedis()
    lane = ConversationRunLane()
    fake.store[lane._lock_key("u1", "conv-x")] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u1", "conv-x")] = 500

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        return "false" if key == "agent_session_run_lock_enabled" else default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    snapshot = await lane.list_active(user_id="u1")
    assert snapshot["items"] == []


@pytest.mark.asyncio
async def test_list_active_returns_empty_when_redis_unavailable(monkeypatch):
    """Redis 不可用时返回空列表而不是抛异常。"""
    lane = ConversationRunLane()

    async def _redis():
        return None

    async def _config_get(key, default=None):
        return "true" if key == "agent_session_run_lock_enabled" else default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    snapshot = await lane.list_active(user_id="u1")
    assert snapshot["items"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/ai/runtime/test_session_run_lane.py -k list_active -v`
Expected: 4 个用例 FAIL，报 `AttributeError: 'ConversationRunLane' object has no attribute 'list_active'`

- [ ] **Step 3: 实现 `list_active`**

在 `app/services/ai/runtime/session_run_lane.py` 的 `get_status` 方法结束后（`hold` 之前）插入：

```python
    async def list_active(
        self,
        *,
        user_id: str | int | None,
    ) -> dict[str, object]:
        """列出该用户当前仍持有 lane 锁的会话（纯只读，不写不删任何 key）。

        `state` 判定：lane 锁由 `renew_lock_during_hold` 以 `ttl/3` 为间隔续约，
        所以正常活跃的锁，其剩余 TTL 必然落在 `(2/3·ttl, ttl]`；低于该阈值说明
        续约已停（进程崩溃 / pod 被杀 / 任务卡死），判为 `stale`（疑似僵死或孤儿锁）。
        """
        try:
            enabled = await self._is_enabled()
        except Exception as exc:
            logger.warning("[ConversationRunLane] list_active config check failed: %s", exc)
            enabled = False

        ttl_seconds = DEFAULT_LOCK_TTL_SECONDS
        try:
            ttl_seconds = await self._ttl_seconds()
        except Exception as exc:
            logger.warning("[ConversationRunLane] list_active ttl read failed: %s", exc)

        # 阈值取 ttl/2（不是 2/3·ttl）：留半个续约周期容错，否则抖动会误报，
        # 而用户对 stale 条目点「强制释放」会硬取消正在正常运行的 run。
        stale_before = max(1, int(ttl_seconds * 0.5))
        empty: dict[str, object] = {
            "ttl_seconds": ttl_seconds,
            "stale_before_ttl_seconds": stale_before,
            "items": [],
        }
        if not enabled:
            return empty

        from app.core.redis import get_redis

        try:
            redis = await get_redis()
        except Exception as exc:
            logger.warning("[ConversationRunLane] list_active Redis unavailable: %s", exc)
            return empty
        if redis is None:
            return empty

        uid = require_user_id(user_id)
        prefix = f"nanzi:conv_run:{uid}:"
        items: list[dict[str, object]] = []
        try:
            async for raw_key in redis.scan_iter(match=f"{prefix}*", count=200):
                key = (
                    raw_key.decode("utf-8", errors="ignore")
                    if isinstance(raw_key, bytes)
                    else str(raw_key)
                )
                remainder = key[len(prefix):]
                if ":" not in remainder:
                    continue
                _, conversation_id = remainder.split(":", 1)
                if not conversation_id:
                    continue

                raw_token = await redis.get(key)
                if raw_token is None:
                    continue
                if isinstance(raw_token, bytes):
                    raw_token = raw_token.decode("utf-8", errors="ignore")

                remaining: int | None = None
                ttl_reader = getattr(redis, "ttl", None)
                if ttl_reader is not None:
                    try:
                        parsed_ttl = int(await ttl_reader(key))
                    except (TypeError, ValueError):
                        parsed_ttl = -1
                    if parsed_ttl == -2:
                        # key 已过期，跳过
                        continue
                    # -1（无过期，理论不该出现）保守按活跃处理
                    remaining = parsed_ttl if parsed_ttl >= 0 else None

                state = "active" if remaining is None or remaining >= stale_before else "stale"
                items.append(
                    {
                        "conversation_id": conversation_id,
                        # 锁值只回传服务端 UUID trace token，异常内容一律丢弃
                        "trace_id": self._safe_trace_id(raw_token),
                        "ttl_seconds": remaining,
                        "state": state,
                    }
                )
        except Exception as exc:
            logger.warning("[ConversationRunLane] list_active scan failed: %s", exc)
            return empty

        def _sort_key(item: dict[str, object]) -> tuple[int, int]:
            ttl_value = item.get("ttl_seconds")
            return (
                0 if item.get("state") == "active" else 1,
                ttl_value if isinstance(ttl_value, int) else 10 ** 9,
            )

        items.sort(key=_sort_key)
        return {
            "ttl_seconds": ttl_seconds,
            "stale_before_ttl_seconds": stale_before,
            "items": items,
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/ai/runtime/test_session_run_lane.py -v`
Expected: 全部 PASS（含既有用例，确认没有回归）

- [ ] **Step 5: 停下汇报**（不提交）

汇报：`session_run_lane.py` 新增了什么、4 个新用例与既有用例的通过情况。

---

### Task 2: 后端端点 `GET /api/v1/chat/active-runs`

**Files:**
- Modify: `app/api/v1/endpoints/chat.py`（在 `ConversationRunStatusResponse` 之后加模型；在 `run-status` 端点之后加端点）
- Test: `tests/api/test_chat_active_runs_endpoint.py`（新建）

- [ ] **Step 1: 写失败的测试**

新建 `tests/api/test_chat_active_runs_endpoint.py`：

```python
import pytest

from app.api.v1.endpoints.chat import list_active_conversation_runs
from app.services.ai.runtime.session_run_lane import ConversationRunLane

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_active_runs_endpoint_passes_user_id_and_returns_items(monkeypatch):
    """端点必须用稳定用户 ID 查锁，并把 lane 的快照原样封装成标准响应。"""
    seen: dict[str, object] = {}

    async def _list_active(self, *, user_id):
        seen["user_id"] = user_id
        return {
            "ttl_seconds": 600,
            "stale_before_ttl_seconds": 400,
            "items": [
                {
                    "conversation_id": "conv-1",
                    "trace_id": None,
                    "ttl_seconds": 512,
                    "state": "active",
                },
                {
                    "conversation_id": "conv-2",
                    "trace_id": None,
                    "ttl_seconds": 30,
                    "state": "stale",
                },
            ],
        }

    monkeypatch.setattr(ConversationRunLane, "list_active", _list_active)

    response = await list_active_conversation_runs(user_info={"user_id": "u-1"})

    assert seen["user_id"] == "u-1"
    assert response.code == 200
    assert response.data.ttl_seconds == 600
    assert response.data.stale_before_ttl_seconds == 400
    assert [item.conversation_id for item in response.data.items] == ["conv-1", "conv-2"]
    assert [item.state for item in response.data.items] == ["active", "stale"]


@pytest.mark.asyncio
async def test_active_runs_endpoint_defaults_to_empty_items(monkeypatch):
    """lane 返回空快照时也要给出可渲染的结构，而不是 null。"""

    async def _list_active(self, *, user_id):
        return {"ttl_seconds": 600, "stale_before_ttl_seconds": 400, "items": []}

    monkeypatch.setattr(ConversationRunLane, "list_active", _list_active)

    response = await list_active_conversation_runs(user_info={"user_id": "u-2"})

    assert response.data.items == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/api/test_chat_active_runs_endpoint.py -v`
Expected: FAIL，`ImportError: cannot import name 'list_active_conversation_runs'`

- [ ] **Step 3: 加响应模型**

在 `app/api/v1/endpoints/chat.py` 的 `ConversationRunStatusResponse` 定义之后插入：

```python
class ActiveConversationRunItem(BaseModel):
    """一条仍在运行的会话记录。"""

    conversation_id: str
    trace_id: Optional[str] = None
    ttl_seconds: Optional[int] = None
    # active=续约正常；stale=续约已停（疑似僵死或孤儿锁）
    state: str = "active"


class ActiveConversationRunsResponse(BaseModel):
    """当前用户进行中的会话列表。"""

    ttl_seconds: int = 0
    # 剩余 TTL 低于该值即判为 stale，随锁 TTL 配置变化，前端不自行推算
    stale_before_ttl_seconds: int = 0
    items: List[ActiveConversationRunItem] = Field(default_factory=list)
```

> 若文件里还没有 `Field` / `List` 的导入，按文件顶部既有导入风格补齐（`from pydantic import BaseModel, Field`、`from typing import List`）；本文件已使用 `Optional`/`Dict`/`Any`。

- [ ] **Step 4: 加端点**

在 `run-status` 端点函数结束之后、`stream-events` 端点之前插入：

```python
@router.get(
    "/active-runs",
    response_model=StandardResponse[ActiveConversationRunsResponse],
    summary="列出当前用户进行中的会话",
    description=(
        "读取当前身份在 Redis 中的会话运行锁，返回仍在运行的会话列表，"
        "并区分「续约正常」与「疑似僵死（续约已停的孤儿锁）」。"
        "供 EmbedChat 顶部「进行中的会话」面板展示与强制终止。纯只读，不修改任何锁。"
    ),
)
async def list_active_conversation_runs(
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.ai.runtime.session_run_lane import conversation_run_lane

    # 注意路径必须是 /chat/active-runs：写成 /conversation/active-runs 会被
    # /conversation/{conversation_id} 抢先匹配。
    user_id = _require_chat_user_id(user_info)
    snapshot = await conversation_run_lane.list_active(user_id=user_id)
    return StandardResponse(data=ActiveConversationRunsResponse(**snapshot))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/api/test_chat_active_runs_endpoint.py -v`
Expected: 2 个用例 PASS

- [ ] **Step 6: 确认路由没有冲突**

Run: `python -c "from app.main import app; print([r.path for r in app.routes if 'active' in r.path])"`
Expected: 输出里包含 `/api/v1/chat/active-runs`（若该命令因环境依赖报错，改为在 Step 5 的 pytest 中用 `app.routes` 断言，见 Task 8 的契约测试）

- [ ] **Step 7: 停下汇报**（不提交）

---

### Task 3: 前端 `cancelConversationRunDetailed`

**Files:**
- Modify: `frontend/src/utils/cancelConversationRun.ts`
- Test: `tests/frontend/test_active_runs_contract.py`（新建，本任务先加第 1 个用例）

- [ ] **Step 1: 写失败的测试**

新建 `tests/frontend/test_active_runs_contract.py`：

```python
import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
CANCEL_UTIL = ROOT / "frontend/src/utils/cancelConversationRun.ts"
ACTIVE_RUNS_COMPOSABLE = ROOT / "frontend/src/composables/chat/useActiveRuns.ts"
ACTIVE_RUNS_POPOVER = ROOT / "frontend/src/components/chat/ActiveRunsPopover.vue"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
CHAT_ENDPOINT = ROOT / "app/api/v1/endpoints/chat.py"


def test_cancel_util_exposes_detailed_result():
    """终止结果必须能区分「真取消」与「仅强释放」，否则文案只能含糊其辞。"""
    source = CANCEL_UTIL.read_text(encoding="utf-8")

    assert "cancelConversationRunDetailed" in source
    assert "runCancelled" in source
    assert "run_cancelled" in source
    # 既有调用方继续拿到 boolean，签名不能变
    assert "Promise<boolean>" in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_active_runs_contract.py -v`
Expected: FAIL（`cancelConversationRunDetailed` 不存在）

- [ ] **Step 3: 实现**

先读 `frontend/src/utils/cancelConversationRun.ts` 现有内容，然后改为（保留原有 export 名称与 `Promise<boolean>` 签名）：

```ts
export interface CancelConversationRunResult {
  success: boolean
  runCancelled: boolean
  canvasStopped: number
  laneReleased: boolean
  sessionLocksReleased: number
}

const failedResult: CancelConversationRunResult = {
  success: false,
  runCancelled: false,
  canvasStopped: 0,
  laneReleased: false,
  sessionLocksReleased: 0,
}

/**
 * 终止会话运行并释放锁，返回结构化结果。
 *
 * 后端语义：有运行 handle 时真取消任务并强释放两把锁；handle 已不在
 * （僵死 / 孤儿锁）时退化为只强释放锁——两种情况 success 都为 true，
 * 用 runCancelled 区分，供界面给出准确文案。
 */
export async function cancelConversationRunDetailed(
  conversationId: string,
  options?: CancelConversationRunOptions
): Promise<CancelConversationRunResult> {
  const cid = (conversationId || '').trim()
  if (!cid) return failedResult
  try {
    const res = await axios.post(
      '/api/v1/chat/cancel',
      { conversation_id: cid, trace_id: options?.traceId || undefined },
      options?.headers ? { headers: options.headers } : undefined
    )
    const body = (res.data?.data || {}) as Record<string, unknown>
    return {
      success: body.success === true,
      runCancelled: body.run_cancelled === true,
      canvasStopped: Number(body.canvas_stopped || 0) || 0,
      laneReleased: body.lane_released === true,
      sessionLocksReleased: Number(body.session_locks_released || 0) || 0,
    }
  } catch (e) {
    console.warn('[Chat] 终止会话运行失败:', cid, e)
    return failedResult
  }
}

export async function cancelConversationRun(
  conversationId: string,
  options?: CancelConversationRunOptions
): Promise<boolean> {
  return (await cancelConversationRunDetailed(conversationId, options)).success
}
```

> 保留文件里原有的 `CancelConversationRunOptions` 类型与 `axios` 导入；上面只替换函数体部分。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_active_runs_contract.py -v`
Expected: PASS

- [ ] **Step 5: 停下汇报**（不提交）

---

### Task 4: `useActiveRuns` composable

**Files:**
- Create: `frontend/src/composables/chat/useActiveRuns.ts`
- Test: `tests/frontend/test_active_runs_contract.py`（追加用例）

- [ ] **Step 1: 写失败的测试**

在 `tests/frontend/test_active_runs_contract.py` 追加：

```python
def test_active_runs_poll_interval_switches_with_panel_state():
    """面板打开时快轮询、关闭时慢轮询，隐藏页面由调用方停表。"""
    source = ACTIVE_RUNS_COMPOSABLE.read_text(encoding="utf-8")

    assert "OPEN_POLL_INTERVAL_MS = 3000" in source
    assert "IDLE_POLL_INTERVAL_MS = 15000" in source
    assert "setOpen" in source


def test_active_runs_refresh_failure_keeps_previous_items():
    """刷新失败必须保留上一次的数据：清零角标会让用户彻底失去「还有会话在跑」的感知。"""
    source = ACTIVE_RUNS_COMPOSABLE.read_text(encoding="utf-8")

    assert "failed" in source
    catch_block = re.search(r"catch\s*\{([\s\S]*?)\}", source)
    assert catch_block, "composable 里必须有 catch 分支处理刷新失败"
    assert "items.value = []" not in catch_block.group(1)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_active_runs_contract.py -v`
Expected: 新增两个用例 FAIL（文件不存在 → `FileNotFoundError`）

- [ ] **Step 3: 实现**

新建 `frontend/src/composables/chat/useActiveRuns.ts`：

```ts
import { computed, ref } from 'vue'

export type ActiveRunState = 'active' | 'stale'

export interface ActiveRunItem {
  conversation_id: string
  trace_id: string | null
  ttl_seconds: number | null
  state: ActiveRunState
}

export interface ActiveRunsSnapshot {
  ttlSeconds: number
  staleBeforeTtlSeconds: number
  items: ActiveRunItem[]
}

export type ActiveRunsFetcher = () => Promise<ActiveRunsSnapshot>

/** 面板打开时的刷新间隔：用户正盯着列表，需要及时看到终止结果。 */
export const OPEN_POLL_INTERVAL_MS = 3000
/** 面板关闭时的低频刷新：只为更新顶部角标，能提示「还有会话在跑」即可。 */
export const IDLE_POLL_INTERVAL_MS = 15000

/**
 * 「进行中的会话」列表控制器。
 *
 * 只负责取数与轮询节奏；页面可见性由调用方通过 start()/stop() 控制，
 * 这样模块本身不依赖 document，便于契约测试与复用。
 */
export function createActiveRunsController(fetchRuns: ActiveRunsFetcher) {
  const items = ref<ActiveRunItem[]>([])
  const loading = ref(false)
  const failed = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  let inflight = false
  let open = false

  const intervalMs = () => (open ? OPEN_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS)

  const stop = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  const schedule = () => {
    stop()
    timer = setTimeout(() => {
      timer = null
      void refresh()
    }, intervalMs())
  }

  const refresh = async (): Promise<void> => {
    // 单飞去重：并发请求不会让列表更快，只会放大后端压力
    if (inflight) return
    inflight = true
    loading.value = true
    try {
      const snapshot = await fetchRuns()
      items.value = snapshot.items
      failed.value = false
    } catch (error) {
      // 关键：失败保留上一次数据。清零会让角标消失，用户就再也发现不了
      // 「锁还在、界面却没有停止入口」的会话——这正是本功能要兜住的场景。
      console.warn('[ActiveRuns] 刷新进行中会话失败，保留上次结果:', error)
      failed.value = true
    } finally {
      inflight = false
      loading.value = false
      schedule()
    }
  }

  const start = () => {
    void refresh()
  }

  /** 面板开合时切换轮询节奏；打开时立即刷新一次，不等下一个周期。 */
  const setOpen = (value: boolean) => {
    open = value
    if (value) {
      void refresh()
    } else {
      schedule()
    }
  }

  const reset = () => {
    stop()
    items.value = []
    failed.value = false
  }

  const count = computed(() => items.value.length)
  const staleCount = computed(() => items.value.filter((item) => item.state === 'stale').length)

  return {
    items,
    count,
    staleCount,
    loading,
    failed,
    refresh,
    start,
    stop,
    setOpen,
    reset,
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_active_runs_contract.py -v`
Expected: PASS

- [ ] **Step 5: 停下汇报**（不提交）

---

### Task 5: `ActiveRunsPopover` 组件

**Files:**
- Create: `frontend/src/components/chat/ActiveRunsPopover.vue`
- Test: `tests/frontend/test_active_runs_contract.py`（追加用例）

- [ ] **Step 1: 写失败的测试**

追加：

```python
def test_active_runs_popover_is_pure_renderer_with_expected_events():
    """浮层只负责渲染与抛事件，网络请求留在 EmbedChat，便于独立验证交互。"""
    source = ACTIVE_RUNS_POPOVER.read_text(encoding="utf-8")

    assert "defineEmits" in source
    for event in ("'select'", "'terminate'", "'refresh'", "'close'"):
        assert event in source, f"浮层缺少 {event} 事件"
    # 浮层自己不发请求
    assert "axios" not in source
    assert "/api/v1/chat/active-runs" not in source


def test_active_runs_popover_separates_stale_group_and_labels():
    """疑似僵死的条目要单独成组，且按钮文案为「强制释放」。"""
    source = ACTIVE_RUNS_POPOVER.read_text(encoding="utf-8")

    assert "疑似僵死" in source
    assert "强制释放" in source
    assert "当前没有进行中的会话" in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_active_runs_contract.py -v`
Expected: 新增用例 FAIL（文件不存在）

- [ ] **Step 3: 实现组件**

新建 `frontend/src/components/chat/ActiveRunsPopover.vue`：

```vue
<template>
  <Teleport to="body" :disabled="variant !== 'modal'">
    <div v-if="open && variant === 'modal'" class="fixed inset-0 z-40 bg-black/20" @click="emit('close')"></div>
    <div
      v-if="open"
      class="z-50 origin-top-right"
      :class="
        variant === 'modal'
          ? 'fixed left-1/2 top-16 w-[min(22rem,calc(100vw-2rem))] -translate-x-1/2'
          : 'absolute right-0 top-full mt-2 w-80'
      "
    >
      <div class="rounded-xl border border-gray-200 bg-white shadow-xl dark:border-gray-700 dark:bg-gray-800">
        <div class="flex items-center justify-between border-b border-gray-100 px-3 py-2 dark:border-gray-700">
          <span class="text-[12px] font-semibold text-gray-700 dark:text-gray-200">
            {{ items.length }} 个进行中的会话
          </span>
          <div class="flex items-center gap-1">
            <button
              type="button"
              class="rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-primary disabled:opacity-50 dark:hover:bg-gray-700"
              :disabled="loading"
              title="刷新列表"
              @click="emit('refresh')"
            >
              <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v6h6M20 20v-6h-6M20 9A8 8 0 006.6 5.6M4 15a8 8 0 0013.4 3.4" />
              </svg>
            </button>
            <button
              type="button"
              class="rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-primary dark:hover:bg-gray-700"
              title="关闭"
              @click="emit('close')"
            >
              <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <p v-if="failed" class="border-b border-amber-100 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-700 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
          刷新失败，正在重试（显示的是上一次结果）
        </p>

        <div v-if="!items.length" class="px-3 py-6 text-center text-[12px] text-gray-400">
          当前没有进行中的会话
        </div>

        <template v-else>
          <div v-if="activeItems.length" class="py-1">
            <div class="px-3 py-1 text-[11px] font-medium text-gray-400">进行中 {{ activeItems.length }}</div>
            <div
              v-for="item in activeItems"
              :key="item.conversation_id"
              class="flex items-center gap-2 px-3 py-2 transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/40"
            >
              <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"></span>
              <button type="button" class="min-w-0 flex-1 text-left" :title="item.title" @click="emit('select', item.conversation_id)">
                <span class="block truncate text-[12px] text-gray-700 dark:text-gray-200">{{ item.title }}</span>
                <span class="block truncate text-[10px] text-gray-400">
                  {{ ttlText(item) }}<template v-if="item.conversation_id === activeConversationId"> · 当前会话</template>
                </span>
              </button>
              <button
                type="button"
                class="shrink-0 rounded px-2 py-1 text-[11px] font-medium text-red-600 transition-colors hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
                @click.stop="emit('terminate', item)"
              >
                终止
              </button>
            </div>
          </div>

          <div v-if="staleItems.length" class="border-t border-gray-100 py-1 dark:border-gray-700">
            <div class="px-3 py-1 text-[11px] font-medium text-amber-500">疑似僵死 {{ staleItems.length }}</div>
            <div
              v-for="item in staleItems"
              :key="item.conversation_id"
              class="flex items-center gap-2 px-3 py-2 transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/40"
            >
              <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400"></span>
              <button type="button" class="min-w-0 flex-1 text-left" :title="item.title" @click="emit('select', item.conversation_id)">
                <span class="block truncate text-[12px] text-gray-700 dark:text-gray-200">{{ item.title }}</span>
                <span class="block truncate text-[10px] text-amber-500">续约已停，{{ ttlText(item) }}</span>
              </button>
              <button
                type="button"
                class="shrink-0 rounded px-2 py-1 text-[11px] font-medium text-amber-600 transition-colors hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-950/40"
                @click.stop="emit('terminate', item)"
              >
                强制释放
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface ActiveRunView {
  conversation_id: string
  trace_id: string | null
  ttl_seconds: number | null
  state: 'active' | 'stale'
  title: string
}

const props = withDefaults(
  defineProps<{
    open: boolean
    items: ActiveRunView[]
    loading?: boolean
    failed?: boolean
    activeConversationId?: string
    /** anchored=桌面端锚定在按钮下方；modal=移动端居中卡片（带遮罩） */
    variant?: 'anchored' | 'modal'
  }>(),
  {
    loading: false,
    failed: false,
    activeConversationId: '',
    variant: 'anchored',
  }
)

const emit = defineEmits<{
  (e: 'select', conversationId: string): void
  (e: 'terminate', item: ActiveRunView): void
  (e: 'refresh'): void
  (e: 'close'): void
}>()

const activeItems = computed(() => props.items.filter((item) => item.state !== 'stale'))
const staleItems = computed(() => props.items.filter((item) => item.state === 'stale'))

const ttlText = (item: ActiveRunView): string =>
  item.ttl_seconds === null ? '未设置过期时间' : `剩余 ${item.ttl_seconds}s`
</script>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_active_runs_contract.py -v`
Expected: PASS

- [ ] **Step 5: 停下汇报**（不提交）

---

### Task 6: EmbedChat 接线（入口、角标、点击、终止）

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`（模板：顶部栏；脚本：导入、控制器、处理函数、可见性）

- [ ] **Step 1: 加导入**

在 `frontend/src/views/EmbedChat.vue` 里已有的 `import { createConversationId } from "@/utils/conversationId";` 附近加入：

```ts
import ActiveRunsPopover from "@/components/chat/ActiveRunsPopover.vue";
import { createActiveRunsController, type ActiveRunItem } from "@/composables/chat/useActiveRuns";
```

并把已有的 `import { cancelConversationRun } from "@/utils/cancelConversationRun";` 改为：

```ts
import { cancelConversationRun, cancelConversationRunDetailed } from "@/utils/cancelConversationRun";
```

- [ ] **Step 2: 加控制器与处理函数**

在 `handleHistoryClick` 定义之前（`const handleHistoryClick = async (item: any) => {` 上方）插入：

```ts
// —— 顶部「进行中的会话」：跨标签/跨设备的运行态唯一入口 ——
const showActiveRuns = ref(false);
const activeRunsRef = ref<HTMLElement | null>(null);

const fetchActiveRuns = async () => {
  const res = await axios.get("/api/v1/chat/active-runs", { headers: embedAuthHeaders() });
  const body = (res.data?.data || {}) as Record<string, any>;
  const rawItems = Array.isArray(body.items) ? body.items : [];
  return {
    ttlSeconds: Number(body.ttl_seconds || 0) || 0,
    staleBeforeTtlSeconds: Number(body.stale_before_ttl_seconds || 0) || 0,
    items: rawItems
      .map((raw: any) => ({
        conversation_id: String(raw?.conversation_id || ""),
        trace_id: typeof raw?.trace_id === "string" ? raw.trace_id : null,
        ttl_seconds: typeof raw?.ttl_seconds === "number" ? raw.ttl_seconds : null,
        state: raw?.state === "stale" ? ("stale" as const) : ("active" as const),
      }))
      .filter((item: ActiveRunItem) => item.conversation_id),
  };
};

const {
  items: activeRunItems,
  count: activeRunsCount,
  staleCount: activeRunsStaleCount,
  loading: activeRunsLoading,
  failed: activeRunsFailed,
  refresh: refreshActiveRuns,
  start: startActiveRunsWatch,
  stop: stopActiveRunsWatch,
  setOpen: setActiveRunsOpen,
} = createActiveRunsController(fetchActiveRuns);

// 标题尽力映射：活跃会话通常就在已加载的会话列表里；映射不到则退化为 ID 短码。
const activeRunTitleIndex = computed(() => {
  const index = new Map<string, string>();
  for (const item of aggregatedHistoryList.value as any[]) {
    const cid = String(item?.conversation_id || "");
    if (!cid || index.has(cid)) continue;
    const title = String(item?.query || item?.summary || "").trim();
    if (title) index.set(cid, title);
  }
  return index;
});

const activeRunViews = computed(() =>
  activeRunItems.value.map((item) => ({
    ...item,
    title: activeRunTitleIndex.value.get(item.conversation_id) || `会话 ${item.conversation_id.slice(0, 8)}`,
  }))
);

const activeRunsAriaLabel = computed(() =>
  activeRunsStaleCount.value
    ? `进行中的会话（${activeRunsCount.value} 个，其中 ${activeRunsStaleCount.value} 个疑似僵死）`
    : `进行中的会话（${activeRunsCount.value} 个）`
);

const toggleActiveRuns = () => {
  showActiveRuns.value = !showActiveRuns.value;
};

watch(showActiveRuns, (open) => {
  setActiveRunsOpen(open);
});

const handleActiveRunSelect = async (targetConversationId: string) => {
  showActiveRuns.value = false;
  if (!targetConversationId || targetConversationId === conversationId.value) return;
  await handleHistoryClick({ conversation_id: targetConversationId });
};

const handleActiveRunTerminate = async (item: { conversation_id: string; trace_id: string | null }) => {
  const cid = String(item?.conversation_id || "");
  if (!cid) return;
  // 终止当前会话必须走 stopGeneration：本地 isProcessing、消息标记与恢复卡片
  // 需要一起收敛，只发一个 HTTP 请求会让界面停在「生成中」。
  if (cid === conversationId.value) {
    stopGeneration({ notifySuccess: true });
    return;
  }
  const result = await cancelConversationRunDetailed(cid, {
    traceId: item?.trace_id || undefined,
    headers: embedAuthHeaders(),
  });
  if (!result.success) {
    showToast("释放失败，请重试", "warning");
  } else if (result.runCancelled) {
    showToast("已终止运行并释放锁", "success");
  } else {
    showToast("已强制释放（该会话任务未在运行）", "success");
  }
  void refreshActiveRuns();
};
```

> 说明：`stopGeneration` 定义在文件后部（约 8958 行），`handleHistoryClick` 也在后部；`const` 提升到模块作用域时，**模板事件在渲染后才触发，运行时不会踩到 TDZ**——`handleHistoryClick` 的调用方本来就遵循这个模式。若 `vue-tsc` 报「used before declaration」，把 `handleActiveRunSelect` 里的直接调用改为 `void handleHistoryClick({ conversation_id: targetConversationId })` 所在函数的声明顺序调整：将上面这段整体移动到 `handleHistoryClick` 定义**之后**（`handleDeleteHistory` 之前）。

- [ ] **Step 3: 挂载与可见性**

在 `onMounted`（文件里已有，搜索 `onMounted(`）里追加：

```ts
  startActiveRunsWatch();
```

在 `onUnmounted`（搜索 `onUnmounted(`）里追加：

```ts
  stopActiveRunsWatch();
```

在已有的 `visibilitychange` 处理里（搜索 `document.visibilityState === 'hidden'` 所在的 handler）追加两处：可见分支加 `startActiveRunsWatch();`，隐藏分支加 `stopActiveRunsWatch();`。

- [ ] **Step 4: 点击外部关闭**

在已有的 document click 处理里（搜索 `showHeaderMore.value && headerMoreRef.value`）追加：

```ts
    if (showActiveRuns.value && activeRunsRef.value && !activeRunsRef.value.contains(event.target as Node)) {
      showActiveRuns.value = false;
    }
```

- [ ] **Step 5: 模板：桌面端按钮 + 浮层**

在顶部栏左侧标题区容器结束后、右侧按钮组 `<div class="flex items-center space-x-2">` 之前插入：

```vue
        <!-- 进行中的会话：跨标签/跨设备的运行态入口，兜住「输入框没有停止按钮」的僵死会话 -->
        <div v-if="!isMobile" ref="activeRunsRef" class="relative inline-flex items-center">
          <button
            type="button"
            class="relative rounded-lg p-2 text-gray-400 transition-all hover:bg-gray-100 hover:text-primary dark:hover:bg-gray-800"
            :class="showActiveRuns ? 'bg-primary/10 text-primary' : ''"
            :aria-label="activeRunsAriaLabel"
            :aria-expanded="showActiveRuns"
            @click="toggleActiveRuns"
          >
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l2.5 2.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span
              v-if="activeRunsCount"
              class="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full px-1 text-[9px] font-semibold text-white ring-2 ring-white dark:ring-gray-900"
              :class="activeRunsStaleCount ? 'bg-amber-500' : 'bg-primary'"
            >
              {{ activeRunsCount > 99 ? '99+' : activeRunsCount }}
            </span>
          </button>
          <ActiveRunsPopover
            :open="showActiveRuns"
            :items="activeRunViews"
            :loading="activeRunsLoading"
            :failed="activeRunsFailed"
            :active-conversation-id="conversationId"
            @select="handleActiveRunSelect"
            @terminate="handleActiveRunTerminate"
            @refresh="refreshActiveRuns"
            @close="showActiveRuns = false"
          />
        </div>
```

- [ ] **Step 6: 模板：移动端入口**

在「更多」弹层里（搜索 `@click="runHeaderMoreBrowser"` 的那个按钮之后、账号类入口的分隔线之前）插入：

```vue
                    <button
                      type="button"
                      class="flex w-full items-center gap-3 px-3 py-2 text-sm text-gray-700 transition-all duration-150 hover:bg-primary/10 hover:text-primary dark:text-gray-200 dark:hover:bg-primary/20"
                      @click="runHeaderMoreActiveRuns"
                    >
                      <svg class="h-5 w-5 shrink-0 text-rose-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l2.5 2.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                      <span class="flex-1 text-left font-medium">进行中的会话</span>
                      <span v-if="activeRunsCount" class="shrink-0 rounded-full px-1.5 text-[10px] font-semibold text-white" :class="activeRunsStaleCount ? 'bg-amber-500' : 'bg-primary'">{{ activeRunsCount > 99 ? '99+' : activeRunsCount }}</span>
                    </button>
```

并在 `runHeaderMoreBrowser` 函数旁边加：

```ts
const runHeaderMoreActiveRuns = () => {
  showHeaderMore.value = false;
  showActiveRuns.value = true;
};
```

移动端浮层用 modal 变体：在「更多」入口之外、模板末尾（`</div>` 关闭根容器之前，搜索文件末尾的根容器闭合处）插入：

```vue
    <ActiveRunsPopover
      v-if="isMobile"
      :open="showActiveRuns"
      :items="activeRunViews"
      :loading="activeRunsLoading"
      :failed="activeRunsFailed"
      :active-conversation-id="conversationId"
      variant="modal"
      @select="handleActiveRunSelect"
      @terminate="handleActiveRunTerminate"
      @refresh="refreshActiveRuns"
      @close="showActiveRuns = false"
    />
```

- [ ] **Step 7: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增错误（若报 TDZ/声明顺序问题，按 Step 2 的说明调整代码块位置）

- [ ] **Step 8: 停下汇报**（不提交）

---

### Task 7: 修复 `handleHistoryClick` 的运行态残留

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`（`handleHistoryClick` 内部）

- [ ] **Step 1: 写失败的测试**

在 `tests/frontend/test_active_runs_contract.py` 追加：

```python
def test_switching_conversation_resets_remote_run_status():
    """切会话必须先清掉上一轮的「输出已完成」标记。

    outputCompleted 只在轮询到 active=false 时复位；若它从上一个会话残留下来，
    remoteRunBusy 会被压制，切进一个仍在运行的会话时既没有「正在处理」也没有
    停止按钮。换身份/清空会话路径已经修过同一个坑，普通切换路径此前漏了。
    """
    source = EMBED_CHAT.read_text(encoding="utf-8")
    match = re.search(r"const handleHistoryClick = async \(item: any\) => \{([\s\S]*?)\n\};", source)
    assert match, "未找到 handleHistoryClick 定义"
    body = match.group(1)

    assert "resetRunStatus();" in body, "handleHistoryClick 未重置远程运行态"
    assert "conversationId.value = target;" in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_active_runs_contract.py -k switching -v`
Expected: FAIL（`handleHistoryClick` 内没有 `resetRunStatus();`）

- [ ] **Step 3: 实现**

在 `frontend/src/views/EmbedChat.vue` 的 `handleHistoryClick` 里，`resetEmbedThinkingOverrides();` 之后、`conversationId.value = target;` 之前插入：

```ts
    // 目标会话可能正是「仍在跑」的那一轮：先清掉上一轮残留的「输出已完成」标记，
    // 否则 remoteRunBusy 会被它压制——进来后既没有「正在处理」也没有停止按钮。
    // 用 resetRunStatus 而非 markOutputCompleted，理由同换身份路径。
    resetRunStatus();
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_active_runs_contract.py -v`
Expected: PASS

- [ ] **Step 5: 停下汇报**（不提交）

---

### Task 8: 契约测试补全、端到端验证与登记

**Files:**
- Modify: `tests/frontend/test_active_runs_contract.py`（追加接口与接线断言）
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 追加接线与接口断言**

在 `tests/frontend/test_active_runs_contract.py` 追加：

```python
def test_backend_exposes_active_runs_route_without_shadowing_conversation_route():
    """接口必须是 /chat/active-runs：/conversation/active-runs 会被 {conversation_id} 抢先匹配。"""
    source = CHAT_ENDPOINT.read_text(encoding="utf-8")

    assert '"/active-runs"' in source
    assert '"/conversation/active-runs"' not in source
    assert "def list_active_conversation_runs" in source
    assert "class ActiveConversationRunsResponse" in source


def test_lane_list_active_is_read_only():
    """列表实现必须是纯只读：不能顺手删锁或改锁。"""
    lane_source = (ROOT / "app/services/ai/runtime/session_run_lane.py").read_text(encoding="utf-8")
    match = re.search(r"async def list_active\(([\s\S]*?)\n    async def ", lane_source)
    assert match, "未找到 list_active 实现"
    body = match.group(1)

    assert "scan_iter" in body
    assert "force_release" not in body
    assert "await redis.set(" not in body
    assert "delete" not in body


def test_embed_chat_wires_active_runs_entry_and_termination():
    """入口、标题映射与终止分支都必须接上，且终止当前会话走 stopGeneration。"""
    source = EMBED_CHAT.read_text(encoding="utf-8")

    assert "ActiveRunsPopover" in source
    assert "/api/v1/chat/active-runs" in source
    assert "createActiveRunsController" in source
    assert "handleActiveRunTerminate" in source
    assert "cancelConversationRunDetailed" in source
    assert "stopGeneration({ notifySuccess: true });\n    return;" in source
    # 移动端入口不能漏
    assert "runHeaderMoreActiveRuns" in source
```

- [ ] **Step 2: 跑全部契约测试**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_active_runs_contract.py -v`
Expected: 全部 PASS

- [ ] **Step 3: 跑后端测试**

Run: `python -m pytest tests/ai/runtime/test_session_run_lane.py tests/api/test_chat_active_runs_endpoint.py -v`
Expected: 全部 PASS

- [ ] **Step 4: 全量回归（后端锁与取消相关）**

Run: `python -m pytest tests/ai/runtime tests/api -q`
Expected: 无新增失败

- [ ] **Step 5: 前端类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无错误

- [ ] **Step 6: 更新 `tests/CHECKLIST.md`**

按该文件既有格式登记本次改动：新增接口 `GET /api/v1/chat/active-runs`、新增两个响应模型、新增 `ConversationRunLane.list_active`、新增前端 composable/组件、修 `handleHistoryClick` 运行态残留、新增两个测试文件。

- [ ] **Step 7: 停下汇报**（不提交）

汇报内容：改动的文件清单、每个测试命令与实际结果、以及一份建议的 commit message 草稿。

---

## Self-Review

**Spec 覆盖检查：**

| Spec 要求 | 对应任务 |
|---|---|
| 只读接口 + `SCAN` + 状态判定（`active`/`stale`、`TTL=-1` 保守、`-2` 跳过、禁用返回空） | Task 1、Task 2 |
| 响应含 `ttl_seconds` / `stale_before_ttl_seconds` / `items` | Task 2 |
| 路径不与 `/conversation/{id}` 冲突 | Task 2 Step 6、Task 8 Step 1 |
| 纯渲染浮层 + 独立 composable + EmbedChat 只接线 | Task 4、Task 5、Task 6 |
| 打开 3s / 关闭 15s / hidden 暂停 / 单飞 / 失败保留旧数据 | Task 4、Task 6 Step 3 |
| 顶部栏按钮 + 角标（stale 转琥珀）+ 移动端收入「更多」 | Task 6 Step 5、Step 6 |
| 标题映射（映射不到用 ID 短码） | Task 6 Step 2 |
| 点击进入会话：`resetRunStatus` → `handleHistoryClick` → 关闭浮层 | Task 6 Step 2、Task 7 |
| 终止：当前会话 `stopGeneration`，其他会话 `cancelConversationRunDetailed` + 三种文案 | Task 3、Task 6 Step 2 |
| 契约测试与 CHECKLIST 登记 | Task 8 |

**占位符扫描：** 无 TBD/TODO；每个代码步骤都给了完整代码；命令都带预期输出。

**类型一致性：** 后端 `state` 为 `str`（`"active"`/`"stale"`），前端 `ActiveRunState` 为 `'active' | 'stale'`；`cancelConversationRunDetailed` 返回的 `runCancelled` 与后端 `run_cancelled` 字段对应；`ActiveRunView` 由 `ActiveRunItem` 加 `title` 得到，与组件 props 一致；`setOpen`/`start`/`stop`/`refresh` 在 composable 与 EmbedChat 中的命名一致。

---

## 实施中修正（TDD 循环内发现 + 实施后审查，2026-10-16）

计划定稿后，实施过程与独立审查各发现若干问题（均已**先写失败测试**再改实现）：

| # | 位置 | 计划原文 | 实际实施 | 原因 |
|---|---|---|---|---|
| 1 | Task 1 key 解析 | `remainder` 再 `split(":", 1)` | `partition(":")` + 逐条比对 uid 段 | 首版把已含 uid 的前缀又拆一次 → 恒定返回空集（4 个单测立刻报空）；二次校验同时拦住 match 扩散 |
| 2 | Task 1 阈值 | `int(ttl * 2 / 3)` | `int(ttl * 0.5)` | 与正常续约下限零距离：抖动即误报「疑似僵死」，用户点「强制释放」会硬取消**正在正常运行**的 run |
| 3 | Task 2 断言 | `code == 0` | `code == 200` | `StandardResponse.code` 默认 200，不是 0 |
| 4 | Task 1 SCAN | `match=f"...{uid}:*"` | `glob_escape(uid)` 后再拼 | uid 含 `*?[]\` 时模式扩散，可能列出他人会话 ID（信息泄漏） |
| 5 | Task 4 `stop()` | 只 `clearTimeout` | 补 `stopped` 标志 + `schedule()` 内检查 | in-flight 请求的 `finally` 会复活轮询，hidden 后仍每 15s 请求 |
| 6 | Task 3 结果对象 | 无 `responded` | 新增 `responded`，兼容入口按它判定 | 会话恰好自然结束时后端返回 `success=false`（无 handle、无锁可删），原口径会弹假警告 |

计划里 Task 1/3/4 的代码块已按上表就地更新，测试断言同步收紧（`stale_before` → 300、
`list_active` 返回精确集合、新增抖动/字节 key/glob 转义/停表恢复/`responded` 等断言）。
审查同时提到的「`list_active` 只读断言、路由断言、CHECKLIST 未登记」为**基于中间快照的
误判**——它们在审查进行时已补齐；详见 spec 的「实施后审查修复」小节。
