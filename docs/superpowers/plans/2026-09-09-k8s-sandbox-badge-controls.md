# Kubernetes 沙箱输入框浮标 Pod 状态/启停控制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `sandbox_policy = "k8s"` 时输入框浮标获得与 Docker 对等的 Pod 状态 / 运行时长 / 手动启动 / 刷新 / 重启 / 停止（停止前二次确认）能力；Docker 行为与文案保持不变。

**Architecture:** 后端在 `k8s_workspace.py` 增加真实 Pod 只读查询辅助，在 `workspace.py` docker runtime 族旁新增 k8s runtime 族（ensure/status/stop/restart + metadata），在 `sandbox.py` 增加 4 个用户端点；前端把 `EmbedChat.vue` / `ChatInput.vue` 的 docker 专属状态机与 props 更名泛化为 `sandboxWorkspace*` 并新增 `sandboxBackend` 术语维度，API URL 按 docker|k8s 路由。

**Tech Stack:** Python 3.11 / FastAPI / kubernetes-asyncio / pytest；Vue 3 + TypeScript + Vite + Tailwind（`vue-tsc --noEmit`、纯前端契约测试 `pytest --confcutdir=tests/frontend`）。

**Spec:** `docs/superpowers/specs/2026-09-09-k8s-sandbox-workspace-badge-controls-design.md`

**实现级澄清（相对 spec 的两点落地方案，语义不变）**
1. k8s 停止二次确认放在 `EmbedChat.vue`，复用该文件**已 import 的 `<ConfirmModal>`**（见 `frontend/src/views/EmbedChat.vue:1500-1530` 用法），不在 `ChatInput.vue` 再造局部弹层。ChatInput 照旧 emit `stop-sandbox-workspace`，EmbedChat 在 k8s 后端下先弹确认、确认后再调用停止。
2. 组件文件与组件名保留 `DockerWorkspaceBanner.vue`（其内部 props 已通用：`workspaceStatus/workspaceError/containerId`），仅新增 `backend?: "docker" | "k8s"` prop（默认 `"docker"`，默认分支文案与现有完全一致），避免大范围文件移动与既有契约断言 churn。

---

### Task 1: 后端真实 Pod 只读查询辅助 `read_k8s_sandbox_pod`

**Files:**
- Modify: `app/services/ai/runtime/agentscope/k8s_workspace.py`（文件末尾追加，`check_k8s_rbac_status` 之后）
- Test: `tests/services/test_sandbox_policy_k8s.py`（追加 4 个用例）

- [ ] **Step 1: 写失败测试**

在 `tests/services/test_sandbox_policy_k8s.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_read_k8s_sandbox_pod_running():
    from app.services.ai.runtime.agentscope.k8s_workspace import read_k8s_sandbox_pod

    pod_status = MagicMock()
    pod_status.phase = "Running"
    pod_status.start_time = None
    container_state = MagicMock()
    container_state.ready = True
    pod_status.container_statuses = [container_state]
    pod = MagicMock()
    pod.metadata.creation_timestamp = None
    pod.status = pod_status

    mock_core = MagicMock()
    mock_core.read_namespaced_pod = AsyncMock(return_value=pod)
    mock_client = MagicMock()
    mock_client.CoreV1Api.return_value = mock_core

    class FakeApiClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    mock_client.ApiClient = FakeApiClient
    mock_config = MagicMock()
    mock_config.load_incluster_config.return_value = None
    mock_k8s = MagicMock()
    mock_k8s.client = mock_client
    mock_k8s.config = mock_config

    with patch.dict("sys.modules", {
        "kubernetes_asyncio": mock_k8s,
        "kubernetes_asyncio.client": mock_client,
        "kubernetes_asyncio.config": mock_config,
    }):
        result = await read_k8s_sandbox_pod(namespace="agent-sandboxes", pod_name="as-ws-admin--1")
    assert result["available"] is True
    assert result["found"] is True
    assert result["phase"] == "Running"
    assert result["ready"] is True


@pytest.mark.asyncio
async def test_read_k8s_sandbox_pod_not_found():
    from app.services.ai.runtime.agentscope.k8s_workspace import read_k8s_sandbox_pod

    async def _raise_404(*args, **kwargs):
        exc = Exception("not found")
        exc.status = 404
        raise exc

    mock_core = MagicMock()
    mock_core.read_namespaced_pod = AsyncMock(side_effect=_raise_404)
    mock_client = MagicMock()
    mock_client.CoreV1Api.return_value = mock_core

    class FakeApiClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    mock_client.ApiClient = FakeApiClient
    mock_config = MagicMock()
    mock_config.load_incluster_config.return_value = None
    mock_k8s = MagicMock()
    mock_k8s.client = mock_client
    mock_k8s.config = mock_config

    with patch.dict("sys.modules", {
        "kubernetes_asyncio": mock_k8s,
        "kubernetes_asyncio.client": mock_client,
        "kubernetes_asyncio.config": mock_config,
    }):
        result = await read_k8s_sandbox_pod(namespace="agent-sandboxes", pod_name="as-ws-missing")
    assert result["available"] is True
    assert result["found"] is False
    assert result["phase"] is None


@pytest.mark.asyncio
async def test_read_k8s_sandbox_pod_missing_dependency():
    from app.services.ai.runtime.agentscope.k8s_workspace import read_k8s_sandbox_pod

    with patch.dict("sys.modules", {"kubernetes_asyncio": None}):
        result = await read_k8s_sandbox_pod(namespace="agent-sandboxes", pod_name="as-ws-admin--1")
    assert result["available"] is False
    assert result["found"] is None


@pytest.mark.asyncio
async def test_read_k8s_sandbox_pod_api_error_degrades():
    from app.services.ai.runtime.agentscope.k8s_workspace import read_k8s_sandbox_pod

    async def _raise_forbidden(*args, **kwargs):
        exc = Exception("forbidden")
        exc.status = 403
        raise exc

    mock_core = MagicMock()
    mock_core.read_namespaced_pod = AsyncMock(side_effect=_raise_forbidden)
    mock_client = MagicMock()
    mock_client.CoreV1Api.return_value = mock_core

    class FakeApiClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    mock_client.ApiClient = FakeApiClient
    mock_config = MagicMock()
    mock_config.load_incluster_config.return_value = None
    mock_k8s = MagicMock()
    mock_k8s.client = mock_client
    mock_k8s.config = mock_config

    with patch.dict("sys.modules", {
        "kubernetes_asyncio": mock_k8s,
        "kubernetes_asyncio.client": mock_client,
        "kubernetes_asyncio.config": mock_config,
    }):
        result = await read_k8s_sandbox_pod(namespace="agent-sandboxes", pod_name="as-ws-admin--1")
    assert result["available"] is False
    assert result["found"] is None
    assert result["phase"] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/services/test_sandbox_policy_k8s.py -q`
Expected: `ImportError: cannot import name 'read_k8s_sandbox_pod'`（4 个新用例 fail）。

- [ ] **Step 3: 实现**

在 `app/services/ai/runtime/agentscope/k8s_workspace.py` 末尾（`check_k8s_rbac_status` 之后）追加：

```python
async def read_k8s_sandbox_pod(
    namespace: str,
    pod_name: str,
) -> dict[str, Any]:
    """Read a sandbox Pod's live status without creating or mutating anything.

    Return contract:
    - ``{"available": True, "found": True, "phase": str|None, "start_time": str|None,
       "ready": bool|None}`` when the Pod exists;
    - ``{"available": True, "found": False, ...}`` when the Pod is confirmed
      absent (HTTP 404);
    - ``{"available": False, "found": None, ...}`` when the lookup cannot be
      trusted (missing dependency, no cluster credentials, RBAC/API error).
      Callers must degrade gracefully in that case instead of failing hard.
    """
    try:
        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio import config as k8s_async_config
    except ImportError:
        logger.warning("[k8s_workspace] kubernetes-asyncio is not installed; cannot read pod status")
        return {
            "available": False,
            "found": None,
            "phase": None,
            "start_time": None,
            "ready": None,
        }

    try:
        k8s_async_config.load_incluster_config()
    except Exception:
        try:
            await k8s_async_config.load_kube_config()
        except Exception as exc:
            logger.warning("[k8s_workspace] No cluster credentials to read pod %s: %s", pod_name, exc)
            return {
                "available": False,
                "found": None,
                "phase": None,
                "start_time": None,
                "ready": None,
            }

    try:
        async with k8s_client.ApiClient() as api_client:
            core_v1 = k8s_client.CoreV1Api(api_client)
            pod = await core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            logger.info("[k8s_workspace] Sandbox pod %s/%s not found", namespace, pod_name)
            return {
                "available": True,
                "found": False,
                "phase": None,
                "start_time": None,
                "ready": None,
            }
        logger.warning("[k8s_workspace] Failed to read pod %s/%s: %s", namespace, pod_name, exc)
        return {
            "available": False,
            "found": None,
            "phase": None,
            "start_time": None,
            "ready": None,
        }

    status = getattr(pod, "status", None)
    phase = getattr(status, "phase", None)
    container_ready: bool | None = None
    container_statuses = getattr(status, "container_statuses", None) or []
    if container_statuses:
        container_ready = any(
            bool(getattr(cs, "ready", False)) for cs in container_statuses
        )
    start_time = getattr(status, "start_time", None)
    if start_time is None:
        metadata = getattr(pod, "metadata", None)
        start_time = getattr(metadata, "creation_timestamp", None)
    start_time_text: str | None = None
    if start_time is not None:
        from datetime import datetime, timezone

        if isinstance(start_time, datetime):
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            start_time_text = start_time.astimezone(timezone.utc).isoformat()
        else:
            start_time_text = str(start_time)
    return {
        "available": True,
        "found": True,
        "phase": phase,
        "start_time": start_time_text,
        "ready": container_ready,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/services/test_sandbox_policy_k8s.py -q`
Expected: 全部 PASS（含既有用例）。

- [ ] **Step 5: Commit**

```bash
git add app/services/ai/runtime/agentscope/k8s_workspace.py tests/services/test_sandbox_policy_k8s.py
git commit -m "feat(sandbox): K8s 沙箱 Pod 只读状态查询辅助与降级契约"
```

---

### Task 2: workspace.py k8s runtime 族（status / ensure / stop / restart / metadata）

**Files:**
- Modify: `app/services/ai/runtime/agentscope/workspace.py`
- Test: `tests/services/test_sandbox_policy_k8s.py`（追加 7 个用例）

- [ ] **Step 1: 写失败测试**

在 `tests/services/test_sandbox_policy_k8s.py` 顶部 import 区补充 `import asyncio`，并在文件末尾追加（与既有 `_policy_k8s_workspace` mock 风格一致）：

```python
K8S_RUNTIME_CACHE_KEY = "/data::alice__1::k8s"


async def _patch_k8s_policy(monkeypatch):
    """让 runtime guard 读到 k8s 有效策略，避免触碰真实 Redis。"""
    async def fake_get(key, default=None):
        return "k8s"

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_get,
    )


@pytest.mark.asyncio
async def test_k8s_workspace_status_idle_when_no_workspace_cached(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_module
    from app.services.ai.runtime.agentscope.workspace import k8s_workspace_status

    await _patch_k8s_policy(monkeypatch)

    async def fake_root():
        return "/data"

    monkeypatch.setattr(ws_module, "resolve_workspace_root", fake_root)
    ws_module._k8s_workspace_cache.clear()

    result = await k8s_workspace_status(
        user_id=1,
        user_name="alice",
        conversation_id="conv-1",
    )
    assert result["execution_backend"] == "k8s"
    assert result["status"] == "idle"
    assert result["running"] is False
    assert result["pod_name"] is None


@pytest.mark.asyncio
async def test_k8s_workspace_status_rejects_non_k8s_policy(monkeypatch):
    from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError
    from app.services.ai.runtime.agentscope.workspace import k8s_workspace_status

    async def fake_get(key, default=None):
        return "docker"

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_get,
    )
    with pytest.raises(K8sSandboxUnavailableError) as exc_info:
        await k8s_workspace_status(
            user_id=1,
            user_name="alice",
            conversation_id="conv-1",
        )
    assert exc_info.value.reason_code == "k8s_policy_not_effective"


@pytest.mark.asyncio
async def test_k8s_workspace_status_uses_live_pod_probe(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_module
    from app.services.ai.runtime.agentscope.workspace import k8s_workspace_status

    await _patch_k8s_policy(monkeypatch)

    async def fake_root():
        return "/data"

    monkeypatch.setattr(ws_module, "resolve_workspace_root", fake_root)

    existing = MagicMock()
    existing.is_alive = True
    existing._pod_name = "as-ws-alice__1"
    existing._namespace = "agent-sandboxes"
    existing._platform_execution_backend = "k8s"
    existing.workspace_id = "alice__1"
    existing._platform_started_at = "2026-09-09T10:00:00+00:00"

    async def fake_probe(namespace, pod_name):
        return {
            "available": True,
            "found": True,
            "phase": "Running",
            "start_time": "2026-09-09T10:00:00+00:00",
            "ready": True,
        }

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.k8s_workspace.read_k8s_sandbox_pod",
        fake_probe,
    )
    ws_module._k8s_workspace_cache.clear()
    ws_module._k8s_workspace_cache[K8S_RUNTIME_CACHE_KEY] = existing

    result = await k8s_workspace_status(
        user_id=1,
        user_name="alice",
        conversation_id="conv-1",
    )
    assert result["status"] == "running"
    assert result["running"] is True
    assert result["pod_name"] == "as-ws-alice__1"
    assert result["started_at"] == "2026-09-09T10:00:00+00:00"
    assert isinstance(result["uptime_seconds"], int)


@pytest.mark.asyncio
async def test_k8s_workspace_status_degrades_when_probe_unavailable(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_module
    from app.services.ai.runtime.agentscope.workspace import k8s_workspace_status

    await _patch_k8s_policy(monkeypatch)

    async def fake_root():
        return "/data"

    monkeypatch.setattr(ws_module, "resolve_workspace_root", fake_root)

    existing = MagicMock()
    existing.is_alive = True
    existing._pod_name = "as-ws-alice__1"
    existing._namespace = "agent-sandboxes"
    existing._platform_started_at = "2026-09-09T10:00:00+00:00"

    async def fake_probe(namespace, pod_name):
        return {"available": False, "found": None, "phase": None, "start_time": None}

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.k8s_workspace.read_k8s_sandbox_pod",
        fake_probe,
    )
    ws_module._k8s_workspace_cache.clear()
    ws_module._k8s_workspace_cache[K8S_RUNTIME_CACHE_KEY] = existing

    result = await k8s_workspace_status(
        user_id=1,
        user_name="alice",
        conversation_id="conv-1",
    )
    # 探针不可用 -> 降级为 is_alive 缓存视图
    assert result["status"] == "running"
    assert result["running"] is True
    assert result["started_at"] == "2026-09-09T10:00:00+00:00"


@pytest.mark.asyncio
async def test_k8s_workspace_ensure_reuses_cached_workspace(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_module
    from app.services.ai.runtime.agentscope.workspace import ensure_k8s_workspace, k8s_workspace_metadata

    await _patch_k8s_policy(monkeypatch)

    existing = MagicMock()
    existing.is_alive = True
    existing._pod_name = "as-ws-alice__1"
    existing._namespace = "agent-sandboxes"
    existing._platform_execution_backend = "k8s"
    existing._platform_sandbox_policy = "k8s"
    existing._platform_started_at = None
    existing.workspace_id = "alice__1"

    async def fake_get_local_workspace(**kwargs):
        return (existing, None)

    monkeypatch.setattr(ws_module, "get_local_workspace", fake_get_local_workspace)

    meta = k8s_workspace_metadata(existing)
    assert meta["execution_backend"] == "k8s"
    assert meta["status"] == "running"
    assert meta["pod_name"] == "as-ws-alice__1"

    result = await ensure_k8s_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="conv-1",
    )
    assert result["status"] == "running"
    assert result["pod_name"] == "as-ws-alice__1"
    assert result["started_at"] is not None  # _record_k8s_started_at 已写入


@pytest.mark.asyncio
async def test_k8s_workspace_stop_evicts_cache_and_returns_stopped(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_module
    from app.services.ai.runtime.agentscope.workspace import stop_k8s_workspace

    await _patch_k8s_policy(monkeypatch)

    existing = MagicMock()
    existing.is_alive = True
    existing._pod_name = "as-ws-alice__1"
    existing._namespace = "agent-sandboxes"
    existing.close = AsyncMock()

    async def fake_root():
        return "/data"

    monkeypatch.setattr(ws_module, "resolve_workspace_root", fake_root)
    ws_module._k8s_workspace_cache.clear()
    ws_module._k8s_workspace_locks.pop(K8S_RUNTIME_CACHE_KEY, None)
    ws_module._k8s_workspace_cache[K8S_RUNTIME_CACHE_KEY] = existing
    ws_module._k8s_workspace_locks[K8S_RUNTIME_CACHE_KEY] = asyncio.Lock()

    try:
        result = await stop_k8s_workspace(
            user_id=1,
            user_name="alice",
            conversation_id="conv-1",
        )
        assert result["status"] == "stopped"
        assert result["execution_backend"] == "k8s"
        assert K8S_RUNTIME_CACHE_KEY not in ws_module._k8s_workspace_cache
        existing.close.assert_awaited_once()
    finally:
        ws_module._k8s_workspace_cache.pop(K8S_RUNTIME_CACHE_KEY, None)
        ws_module._k8s_workspace_locks.pop(K8S_RUNTIME_CACHE_KEY, None)


@pytest.mark.asyncio
async def test_k8s_workspace_restart_recreates_via_get_local_workspace(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_module
    from app.services.ai.runtime.agentscope.workspace import restart_k8s_workspace

    await _patch_k8s_policy(monkeypatch)

    recreated = MagicMock()
    recreated.is_alive = True
    recreated._pod_name = "as-ws-alice__1"
    recreated._namespace = "agent-sandboxes"
    recreated._platform_execution_backend = "k8s"
    recreated._platform_sandbox_policy = "k8s"
    recreated._platform_started_at = None
    recreated.workspace_id = "alice__1"

    async def fake_get_local_workspace(**kwargs):
        return (recreated, None)

    async def fake_root():
        return "/data"

    monkeypatch.setattr(ws_module, "get_local_workspace", fake_get_local_workspace)
    monkeypatch.setattr(ws_module, "resolve_workspace_root", fake_root)
    ws_module._k8s_workspace_cache.clear()
    ws_module._k8s_workspace_locks.clear()

    result = await restart_k8s_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="conv-1",
    )
    assert result["status"] == "running"
    assert result["pod_name"] == "as-ws-alice__1"
    assert result["started_at"] is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/services/test_sandbox_policy_k8s.py -q`
Expected: `ImportError`（`k8s_workspace_status` / `k8s_workspace_metadata` / `stop_k8s_workspace` / `restart_k8s_workspace` 未定义）。

- [ ] **Step 3: 实现 —— 在 `def docker_workspace_runtime_metadata(` 定义之前插入以下代码**（保持 docker 族内部顺序不变）

```python
# ---------------------------------------------------------------------------
# Kubernetes sandbox runtime family (mirrors the Docker runtime family above)
# ---------------------------------------------------------------------------


async def _k8s_runtime_guard(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None,
    user_info: dict[str, Any] | None,
    operation: str,
) -> str:
    """Validate session / effective policy / identity for a k8s runtime op.

    Returns the resolved ``sandbox_user_key``. Raises
    ``K8sSandboxUnavailableError`` with user-friendly text otherwise.
    """
    from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

    if not str(conversation_id or "").strip():
        raise K8sSandboxUnavailableError(
            f"K8s sandbox {operation} requires a conversation_id",
            reason_code=f"k8s_workspace_{operation}_failed",
            user_message="缺少会话 ID，无法操作当前用户的 Kubernetes 沙箱。",
        )

    from app.services.config_service import (
        ConfigService,
        resolve_effective_sandbox_policy,
    )

    policy = resolve_effective_sandbox_policy(
        await ConfigService.get("sandbox_policy", SANDBOX_POLICY_LOCAL),
        SANDBOX_POLICY_LOCAL,
    )
    if policy != SANDBOX_POLICY_K8S:
        raise K8sSandboxUnavailableError(
            f"K8s sandbox {operation} requested while effective policy is {policy!r}",
            reason_code="k8s_policy_not_effective",
            user_message="当前不是 Kubernetes 沙箱模式，无需操作沙箱 Pod。",
        )

    user_key = _resolve_sandbox_user_key(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
    )
    if not user_key:
        raise K8sSandboxUnavailableError(
            f"K8s sandbox {operation} requires an authenticated user identity",
            reason_code="k8s_identity_required",
            user_message="缺少当前用户身份，无法操作 Kubernetes 沙箱。",
        )
    return user_key


async def _evict_all_k8s_workspaces_for_user(sandbox_user_key: str, *, reason: str) -> None:
    """Evict the user's K8s workspaces under every workspace root (and close Pods)."""
    matching_keys = [
        k for k in list(_k8s_workspace_cache.keys())
        if f"::{sandbox_user_key}::" in k or k.endswith(f"::{sandbox_user_key}")
    ]
    for key in matching_keys:
        try:
            await _evict_k8s_workspace_cache_entry(key, reason=reason)
        except Exception as exc:
            logger.warning("[workspace] Failed to evict k8s cache key %s: %s", key, exc)


async def _k8s_workspace_pod_identity(workspace: Any) -> tuple[str | None, str | None]:
    """Return (namespace, pod_name) best-effort for a live k8s workspace."""
    from app.services.config_service import ConfigService

    namespace = getattr(workspace, "_namespace", None) or (
        await ConfigService.get("sandbox_k8s_namespace", "agent-sandboxes")
    ).strip() or "agent-sandboxes"
    pod_name = getattr(workspace, "_pod_name", None) or getattr(workspace, "pod_name", None)
    return namespace, pod_name


def _uptime_from_started_at(started_at: str | None) -> int | None:
    """Seconds since ``started_at`` (UTC ISO), or None when unavailable."""
    if not started_at:
        return None
    try:
        from datetime import datetime, timezone

        started = datetime.fromisoformat(str(started_at))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    except Exception:
        return None


def k8s_workspace_metadata(workspace: Any) -> dict[str, Any]:
    """Return safe, user-facing metadata for an initialized K8s workspace.

    ``started_at`` reflects the process-recorded ``_platform_started_at`` set
    by ``_record_k8s_started_at`` during ensure/restart (live Pod start time
    is preferred by ``k8s_workspace_status``, which queries the Pod itself).
    """
    pod_name = getattr(workspace, "_pod_name", None) or getattr(workspace, "pod_name", None)
    started_at = getattr(workspace, "_platform_started_at", None)
    return {
        "status": "running" if getattr(workspace, "is_alive", True) else "stopped",
        "execution_backend": getattr(
            workspace,
            "_platform_execution_backend",
            SANDBOX_POLICY_K8S,
        ),
        "workspace_id": getattr(workspace, "workspace_id", None),
        "pod_name": pod_name,
        "started_at": started_at,
        "uptime_seconds": _uptime_from_started_at(started_at),
    }


def _record_k8s_started_at(workspace: Any) -> None:
    """Best-effort record of the Pod start time on the workspace object.

    Called on ensure/restart success so the running duration stays visible even
    when a live Pod probe is unavailable (mirrors Docker's
    ``_platform_started_at`` semantics).
    """
    from datetime import datetime, timezone

    if getattr(workspace, "_platform_started_at", None):
        return
    workspace._platform_started_at = datetime.now(timezone.utc).isoformat()


async def k8s_workspace_status(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect the current user's K8s workspace Pod without initializing it.

    Read-only: never builds a workspace or creates a Pod. Returns ``idle``
    when no process-local workspace exists for this user. When a workspace is
    cached, prefers the live Pod phase via ``read_k8s_sandbox_pod`` and
    degrades to the cached ``is_alive`` view when the lookup is unavailable.
    """
    user_key = await _k8s_runtime_guard(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
        operation="status",
    )

    root = await resolve_workspace_root()
    cache_key = f"{os.path.abspath(root)}::{user_key}::{SANDBOX_POLICY_K8S}"
    workspace = _k8s_workspace_cache.get(cache_key)
    if workspace is None or getattr(workspace, "is_alive", True) is False:
        return {
            "status": "idle",
            "running": False,
            "execution_backend": SANDBOX_POLICY_K8S,
            "workspace_id": user_key,
            "pod_name": None,
            "started_at": None,
            "uptime_seconds": None,
        }

    namespace, pod_name = await _k8s_workspace_pod_identity(workspace)
    started_at = getattr(workspace, "_platform_started_at", None)

    if pod_name:
        try:
            from app.services.ai.runtime.agentscope.k8s_workspace import (
                read_k8s_sandbox_pod,
            )

            pod_info = await read_k8s_sandbox_pod(
                namespace=namespace or "agent-sandboxes",
                pod_name=pod_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[workspace] k8s status pod probe failed for %s: %s", pod_name, exc)
            pod_info = {"available": False, "found": None, "phase": None, "start_time": None}

        if pod_info.get("available") and pod_info.get("found"):
            phase = pod_info.get("phase")
            live_start = pod_info.get("start_time") or started_at
            if phase == "Running":
                return {
                    "status": "running",
                    "running": True,
                    "execution_backend": SANDBOX_POLICY_K8S,
                    "workspace_id": user_key,
                    "pod_name": pod_name,
                    "started_at": live_start,
                    "uptime_seconds": _uptime_from_started_at(live_start),
                }
            if phase == "Pending":
                return {
                    "status": "starting",
                    "running": False,
                    "execution_backend": SANDBOX_POLICY_K8S,
                    "workspace_id": user_key,
                    "pod_name": pod_name,
                    "started_at": None,
                    "uptime_seconds": None,
                }
            # Succeeded / Failed / Unknown -> stopped view
            return {
                "status": "stopped",
                "running": False,
                "execution_backend": SANDBOX_POLICY_K8S,
                "workspace_id": user_key,
                "pod_name": pod_name,
                "started_at": None,
                "uptime_seconds": None,
            }
        if pod_info.get("available") and pod_info.get("found") is False:
            # Confirmed absent (e.g. deleted out-of-band): idle view.
            return {
                "status": "idle",
                "running": False,
                "execution_backend": SANDBOX_POLICY_K8S,
                "workspace_id": user_key,
                "pod_name": pod_name,
                "started_at": None,
                "uptime_seconds": None,
            }
        # unavailable probe -> degrade to cached view below

    is_alive = bool(getattr(workspace, "is_alive", True))
    return {
        "status": "running" if is_alive else "stopped",
        "running": is_alive,
        "execution_backend": SANDBOX_POLICY_K8S,
        "workspace_id": user_key,
        "pod_name": pod_name,
        "started_at": started_at,
        "uptime_seconds": _uptime_from_started_at(started_at) if is_alive else None,
    }


async def ensure_k8s_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start (warm up) or reuse the current user's K8s sandbox workspace.

    Mirrors ``ensure_docker_workspace``: goes through ``get_local_workspace``
    so the Pod shares the exact cache key with the next chat turn.
    """
    user_key = await _k8s_runtime_guard(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
        operation="ensure",
    )

    workspace_pair = await get_local_workspace(
        user_id=user_id,
        conversation_id=str(conversation_id).strip(),
        user_name=user_name,
        user_info=user_info,
    )
    sandbox_ws, _local_ws = _normalize_workspace_pair(workspace_pair)
    if (
        sandbox_ws is None
        or getattr(sandbox_ws, "_platform_sandbox_policy", None)
        != SANDBOX_POLICY_K8S
    ):
        from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

        raise K8sSandboxUnavailableError(
            "K8s workspace was not bound to the current session",
            reason_code="k8s_workspace_ensure_failed",
            user_message="Kubernetes 沙箱未成功绑定当前会话，请稍后重试。",
        )

    _record_k8s_started_at(sandbox_ws)
    return k8s_workspace_metadata(sandbox_ws)


async def stop_k8s_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stop the current user's K8s sandbox: evict caches and close the Pod."""
    user_key = await _k8s_runtime_guard(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
        operation="stop",
    )
    await _evict_all_k8s_workspaces_for_user(
        user_key,
        reason="user requested sandbox stop",
    )
    return {
        "status": "stopped",
        "execution_backend": SANDBOX_POLICY_K8S,
        "workspace_id": user_key,
        "pod_name": None,
        "started_at": None,
        "uptime_seconds": None,
    }


async def restart_k8s_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    user_name: str | None = None,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Delete the current user's K8s sandbox Pod and recreate it fresh."""
    user_key = await _k8s_runtime_guard(
        user_id=user_id,
        user_name=user_name,
        user_info=user_info,
        conversation_id=conversation_id,
        operation="restart",
    )
    await _evict_all_k8s_workspaces_for_user(
        user_key,
        reason="user requested sandbox restart",
    )

    workspace_pair = await get_local_workspace(
        user_id=user_id,
        conversation_id=str(conversation_id).strip(),
        user_name=user_name,
        user_info=user_info,
    )
    sandbox_ws, _local_ws = _normalize_workspace_pair(workspace_pair)
    if (
        sandbox_ws is None
        or getattr(sandbox_ws, "_platform_sandbox_policy", None)
        != SANDBOX_POLICY_K8S
    ):
        from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

        raise K8sSandboxUnavailableError(
            "K8s workspace was not recreated for the current session",
            reason_code="k8s_workspace_restart_failed",
            user_message="Kubernetes 沙箱重启失败，请稍后重试。",
        )

    _record_k8s_started_at(sandbox_ws)
    return k8s_workspace_metadata(sandbox_ws)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/services/test_sandbox_policy_k8s.py -q`
Expected: 全部 PASS（7 个新用例 + 既有用例）。

- [ ] **Step 5: Commit**

```bash
git add app/services/ai/runtime/agentscope/workspace.py tests/services/test_sandbox_policy_k8s.py
git commit -m "feat(sandbox): K8s 沙箱 runtime 状态/预热/停止/重启与元数据族"
```

---

### Task 3: sandbox.py 新增 k8s workspace 用户端点

**Files:**
- Modify: `app/api/v1/endpoints/sandbox.py`
- Test: `tests/api/v1/test_sandbox_k8s_workspace_ops.py`（新建）

- [ ] **Step 1: 写失败测试（新建文件）**

创建 `tests/api/v1/test_sandbox_k8s_workspace_ops.py`：

```python
import pytest
from app.api.v1.endpoints.sandbox import (
    DockerWorkspaceEnsureRequest,
    ensure_k8s_workspace_endpoint,
    get_k8s_workspace_status_endpoint,
    restart_k8s_workspace_endpoint,
    stop_k8s_workspace_endpoint,
)
from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_get_k8s_workspace_status_endpoint(monkeypatch):
    captured = {}

    async def fake_status(**kwargs):
        captured.update(kwargs)
        return {
            "status": "running",
            "running": True,
            "execution_backend": "k8s",
            "workspace_id": "alice__1",
            "pod_name": "as-ws-alice__1",
            "started_at": "2026-09-09T10:00:00Z",
            "uptime_seconds": 120,
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.k8s_workspace_status_runtime",
        fake_status,
    )
    response = await get_k8s_workspace_status_endpoint(
        conversation_id="conv-1",
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["status"] == "running"
    assert response.data["pod_name"] == "as-ws-alice__1"
    assert captured["conversation_id"] == "conv-1"
    assert captured["user_id"] == 1


@pytest.mark.asyncio
async def test_ensure_k8s_workspace_endpoint(monkeypatch):
    captured = {}

    async def fake_ensure(**kwargs):
        captured.update(kwargs)
        return {
            "status": "running",
            "execution_backend": "k8s",
            "workspace_id": "alice__1",
            "pod_name": "as-ws-alice__1",
            "started_at": "2026-09-09T10:00:00Z",
            "uptime_seconds": 0,
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.ensure_k8s_workspace_runtime",
        fake_ensure,
    )
    response = await ensure_k8s_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="conv-1"),
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["status"] == "running"
    assert captured["conversation_id"] == "conv-1"


@pytest.mark.asyncio
async def test_stop_k8s_workspace_endpoint(monkeypatch):
    async def fake_stop(**kwargs):
        return {
            "status": "stopped",
            "execution_backend": "k8s",
            "workspace_id": "alice__1",
            "pod_name": None,
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.stop_k8s_workspace_runtime",
        fake_stop,
    )
    response = await stop_k8s_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="conv-1"),
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["status"] == "stopped"


@pytest.mark.asyncio
async def test_restart_k8s_workspace_endpoint(monkeypatch):
    async def fake_restart(**kwargs):
        return {
            "status": "running",
            "execution_backend": "k8s",
            "workspace_id": "alice__1",
            "pod_name": "as-ws-alice__1",
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.restart_k8s_workspace_runtime",
        fake_restart,
    )
    response = await restart_k8s_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="conv-1"),
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["status"] == "running"


@pytest.mark.asyncio
async def test_ensure_k8s_workspace_endpoint_policy_not_effective(monkeypatch):
    async def fake_ensure(**kwargs):
        raise K8sSandboxUnavailableError(
            "policy not effective",
            reason_code="k8s_policy_not_effective",
            user_message="当前不是 Kubernetes 沙箱模式，无需操作沙箱 Pod。",
        )

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.ensure_k8s_workspace_runtime",
        fake_ensure,
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await ensure_k8s_workspace_endpoint(
            body=DockerWorkspaceEnsureRequest(conversation_id="conv-1"),
            user_info={"id": 1, "username": "alice"},
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["reason_code"] == "k8s_policy_not_effective"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/api/v1/test_sandbox_k8s_workspace_ops.py -q`
Expected: `ImportError`（端点在 sandbox.py 未定义）。

- [ ] **Step 3: 实现**

`app/api/v1/endpoints/sandbox.py` 修改：

1) 顶部 import（`from app.services.ai.runtime.agentscope.workspace import (...)` 块内，紧接 docker alias 行后）增加：

```python
    ensure_k8s_workspace as ensure_k8s_workspace_runtime,
    k8s_workspace_status as k8s_workspace_status_runtime,
    restart_k8s_workspace as restart_k8s_workspace_runtime,
    stop_k8s_workspace as stop_k8s_workspace_runtime,
```

2) 在 `get_docker_workspace_status_endpoint` 函数结束之后（`class SandboxConnectionTestRequest` 定义之前）插入 4 个端点（与 docker 端点同构；`K8sSandboxUnavailableError` 从 `app.services.ai.runtime.agentscope.k8s_workspace` import）：

```python
@router.post(
    "/sandbox/k8s/workspace/ensure",
    response_model=StandardResponse[Dict[str, Any]],
    summary="启动或复用当前用户的 Kubernetes 沙箱 Pod",
)
async def ensure_k8s_workspace_endpoint(
    body: DockerWorkspaceEnsureRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """只确保当前用户 Pod 运行，不在 Pod 内执行用户命令。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        result = await ensure_k8s_workspace_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except K8sSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "k8s_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=result,
        message="Kubernetes 沙箱 Pod 已运行。",
    )


@router.post(
    "/sandbox/k8s/workspace/stop",
    response_model=StandardResponse[Dict[str, Any]],
    summary="停止当前用户的 Kubernetes 沙箱 Pod",
)
async def stop_k8s_workspace_endpoint(
    body: DockerWorkspaceEnsureRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """停止当前用户的 Kubernetes 沙箱 Pod 并清理缓存。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        result = await stop_k8s_workspace_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except K8sSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "k8s_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=result,
        message="Kubernetes 沙箱 Pod 已停止。",
    )


@router.post(
    "/sandbox/k8s/workspace/restart",
    response_model=StandardResponse[Dict[str, Any]],
    summary="重启当前用户的 Kubernetes 沙箱 Pod（销毁旧 Pod 并拉起新 Pod）",
)
async def restart_k8s_workspace_endpoint(
    body: DockerWorkspaceEnsureRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """销毁旧 Kubernetes 沙箱 Pod 并重新拉起全新的 Pod。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        result = await restart_k8s_workspace_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except K8sSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "k8s_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=result,
        message="Kubernetes 沙箱 Pod 已重启完成。",
    )


@router.get(
    "/sandbox/k8s/workspace/status",
    response_model=StandardResponse[Dict[str, Any]],
    summary="查询当前用户的 Kubernetes 沙箱 Pod 状态",
)
async def get_k8s_workspace_status_endpoint(
    conversation_id: str = Query(..., min_length=1),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """只查询当前用户 Pod，不触发 K8s 沙箱初始化。"""
    conversation_id = conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        status = await k8s_workspace_status_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except K8sSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "k8s_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=status,
        message="Kubernetes 沙箱状态查询完成。",
    )
```

3) 模块顶部 import `K8sSandboxUnavailableError`（`from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError`，放在现有 `docker_prebuild` import 之前）。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/api/v1/test_sandbox_k8s_workspace_ops.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/sandbox.py tests/api/v1/test_sandbox_k8s_workspace_ops.py
git commit -m "feat(sandbox): 新增 K8s 沙箱 workspace 用户端点 status/ensure/stop/restart"
```

---

### Task 4: DockerWorkspaceBanner 泛化（backend 术语维度，默认 docker 行为不变）

**Files:**
- Modify: `frontend/src/components/chat/DockerWorkspaceBanner.vue`
- Test: `tests/frontend/test_chat_sandbox_workspace_contract.py`（追加 k8s 断言）

- [ ] **Step 1: 写失败测试**

在 `tests/frontend/test_chat_sandbox_workspace_contract.py` 追加：

```python
def test_docker_workspace_banner_supports_k8s_backend_copy():
    source = BANNER.read_text(encoding="utf-8")
    assert 'backend?: "docker" | "k8s"' in source
    assert "Kubernetes 沙箱 Pod" in source
    assert "启动我的沙箱 Pod" in source
    assert "关闭沙箱提示" in source
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_sandbox_workspace_contract.py -q`
Expected: `AssertionError`（k8s 文案未出现）。

- [ ] **Step 3: 实现**

`frontend/src/components/chat/DockerWorkspaceBanner.vue`：

1) props 增加 backend（脚本 `const props = defineProps<{...}>()` 内，`containerId` 之后）：

```ts
  /** 沙箱后端术语维度：docker（默认）| k8s */
  backend?: "docker" | "k8s";
```

2) 增加术语计算（放在 `statusCopy` computed 之前）：

```ts
const backendTerm = computed(() => props.backend === "k8s"
  ? {
      subject: "Kubernetes 沙箱 Pod",
      hintSubject: "当前用户沙箱 Pod",
      closeLabel: "关闭沙箱提示",
    }
  : {
      subject: "Docker 沙箱容器",
      hintSubject: "当前用户的 Docker 容器",
      closeLabel: "关闭 Docker 沙箱提示",
    });

const subjectTerms = computed(() => {
  const t = backendTerm.value;
  return {
    startingTitle: `${t.subject}启动中`,
    startingHint: props.backend === "k8s"
      ? "正在创建或复用当前用户的沙箱 Pod"
      : "正在创建或复用当前用户的 Docker 容器",
    stoppingTitle: `${t.subject}停止中`,
    stoppingHint: props.backend === "k8s"
      ? "正在停止并清理当前用户的沙箱 Pod"
      : "正在停止并清理当前用户的 Docker 容器",
    runningTitle: `${t.subject}已运行`,
    runningHint: props.containerId
      ? (props.backend === "k8s" ? `当前用户 Pod：${props.containerId}` : `当前用户容器：${props.containerId}`)
      : (props.backend === "k8s"
          ? "Bash 将绑定到当前用户的沙箱 Pod"
          : "Bash 将绑定到当前用户的 Docker 容器"),
    errorTitle: `${t.subject}启动失败`,
    errorHint: props.workspaceError || (props.backend === "k8s"
      ? "请检查集群网络、RBAC 与镜像拉取状态"
      : "请检查 Docker daemon、镜像和权限"),
    idleTitle: `${t.subject}未启动`,
    idleHint: props.backend === "k8s"
      ? "启动后，Bash 命令将绑定到当前用户的沙箱 Pod"
      : "启动后，Bash 命令将绑定到当前用户的 Docker 容器",
    startLabel: props.backend === "k8s" ? "启动我的沙箱 Pod" : "启动我的 Docker 沙箱",
    retryLabel: "重试启动",
  };
});
```

3) `statusCopy` computed 各分支文案替换为 `subjectTerms.value.*`（start/refresh/close 相关 aria 与按钮文案一并替换）：
   - `starting`：`title: subjectTerms.value.startingTitle`、`hint: subjectTerms.value.startingHint`
   - `stopping`：`stoppingTitle/stoppingHint`
   - `running`：`runningTitle` + `hint: subjectTerms.value.runningHint`
   - `error`：`errorTitle` + `hint: subjectTerms.value.errorHint`
   - default：`idleTitle/idleHint`
   - 启动按钮：`:aria-label="workspaceStatus === 'error' ? subjectTerms.value.retryLabel : subjectTerms.value.startLabel"`，按钮文案 `{{ workspaceStatus === "error" ? subjectTerms.value.retryLabel : subjectTerms.value.startLabel }}`
   - running 刷新按钮 aria 改为 `刷新沙箱状态`
   - 关闭按钮 aria/title 用 `backendTerm.value.closeLabel`（title 保留倒计时提示）
4) template 顶部 `data-testid="docker-workspace-banner"` 保留。

> 注意：`statusCopy` 原实现直接 return 对象字面量；请改为 `return { icon, title: subjectTerms.value.xxx, hint: subjectTerms.value.xxx, box, hintTone }`，box/hintTone 与颜色 class 保持原样。最终 `statusCopy` 仍然是一个 `computed(() => { switch (...) {...} })`。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_sandbox_workspace_contract.py -q`
Expected: 新断言 PASS 且既有 docker 断言仍 PASS。

- [ ] **Step 5: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 0 error。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/chat/DockerWorkspaceBanner.vue tests/frontend/test_chat_sandbox_workspace_contract.py
git commit -m "feat(chat): 沙箱提示横幅支持 k8s 术语（docker 默认行为不变）"
```

---

### Task 5: ChatInput.vue 沙箱状态面板泛化

**Files:**
- Modify: `frontend/src/components/embed/ChatInput.vue`
- Test: `tests/frontend/test_chat_sandbox_workspace_contract.py`（契约同步）

- [ ] **Step 1: 写失败契约测试**

`tests/frontend/test_chat_sandbox_workspace_contract.py` 追加：

```python
def test_chat_input_context_modal_generalizes_sandbox_workspace_controls():
    chat_input_source = CHAT_INPUT.read_text(encoding="utf-8")
    # 更名后的通用命名
    assert "sandboxWorkspaceStatus" in chat_input_source
    assert "sandboxWorkspaceInstanceId" in chat_input_source
    assert "sandboxWorkspaceStartedAt" in chat_input_source
    assert "sandboxWorkspaceUptimeSeconds" in chat_input_source
    assert "sandboxBackend" in chat_input_source
    # 术语由 backend 计算（docker 语义保留）
    assert "启动容器" in chat_input_source
    assert "启动沙箱 Pod" in chat_input_source
    assert "stop-sandbox-workspace" in chat_input_source
    assert "start-sandbox-workspace" in chat_input_source
    assert "refresh-sandbox-workspace" in chat_input_source
    assert "restart-sandbox-workspace" in chat_input_source
    # k8s 菜单不出现终端项：终端只在 docker 渲染
    assert "open-docker-terminal" in chat_input_source
    assert "进入终端" in chat_input_source
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_sandbox_workspace_contract.py -q`
Expected: 断言失败（仍为 dockerWorkspace* 命名）。

- [ ] **Step 3: 实现（机械更名 + 术语化）**

在 `frontend/src/components/embed/ChatInput.vue` 内执行以下**精确替换**（逐条，每条唯一匹配）：

**3.1 script props（`defineProps` 内 5 个 docker 字段）**：分别将
- `dockerWorkspaceStatus?` → `sandboxWorkspaceStatus?`
- `dockerWorkspaceContainerId?` → `sandboxWorkspaceInstanceId?`
- `dockerWorkspaceStartedAt?` → `sandboxWorkspaceStartedAt?`
- `dockerWorkspaceUptimeSeconds?` → `sandboxWorkspaceUptimeSeconds?`
- `dockerWorkspaceError?` → `sandboxWorkspaceError?`

并在 `groundingBlockMode` prop 之后新增：

```ts
  /** 沙箱后端：docker | k8s（决定浮标术语与「操作」菜单项） */
  sandboxBackend?: "docker" | "k8s";
```

**3.2 emits（`defineEmits` 内 4 行）**：
- `(e: 'start-docker-workspace'): void;` → `(e: 'start-sandbox-workspace'): void;`
- `(e: 'refresh-docker-workspace', manualFeedback?: boolean): void;` → `(e: 'refresh-sandbox-workspace', manualFeedback?: boolean): void;`
- `(e: 'stop-docker-workspace'): void;` → `(e: 'stop-sandbox-workspace'): void;`
- `(e: 'restart-docker-workspace'): void;` → `(e: 'restart-sandbox-workspace'): void;`
（`open-docker-terminal` 保留不动。）

**3.3 策略 computed**：将

```ts
const isDockerSandboxPolicy = computed(() => {
  const policy = String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase();
  return policy === "docker";
});
```

替换为：

```ts
const isDockerSandboxPolicy = computed(() => {
  const policy = String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase();
  return policy === "docker";
});

const isSandboxBackendPolicy = computed(() => {
  const policy = String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase();
  return policy === "docker" || policy === "k8s";
});

const sandboxBackend = computed<"docker" | "k8s">(
  () => props.sandboxBackend || (
    String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase() === "k8s" ? "k8s" : "docker"
  ),
);

const sandboxStatusText = computed(() => {
  const status = props.sandboxWorkspaceStatus || "idle";
  if (sandboxBackend.value === "k8s") {
    switch (status) {
      case "running": return "Pod 已运行";
      case "starting": return "Pod 创建中...";
      case "stopping": return "Pod 终止中...";
      case "error": return "Pod 启动失败";
      default: return "Pod 未启动";
    }
  }
  switch (status) {
    case "running": return "容器已运行";
    case "starting": return "容器启动中...";
    case "stopping": return "容器关机中...";
    case "error": return "容器启动失败";
    default: return "容器未启动";
  }
});

const sandboxInstanceLabel = computed(() => {
  const instance = props.sandboxWorkspaceInstanceId;
  if (!instance) return "";
  return sandboxBackend.value === "k8s"
    ? `Pod: ${instance}`
    : `容器 ID: ${instance}`;
});

const sandboxStartButtonLabel = computed(() =>
  props.sandboxWorkspaceStatus === "error"
    ? "重试启动"
    : (sandboxBackend.value === "k8s" ? "启动沙箱 Pod" : "启动容器"),
);

const sandboxReclaimHint = computed(() =>
  sandboxBackend.value === "k8s"
    ? "空闲 30m 自动回收沙箱"
    : "空闲 30m 自动回收",
);
```

**3.4 uptime tick 与 computed 更名**：将

```ts
const showDockerActionsMenu = ref(false);
const dockerActionsDropdownRef = ref<HTMLElement | null>(null);

const dockerUptimeNow = ref(Date.now());
let dockerUptimeTimer: ReturnType<typeof setInterval> | null = null;

const dockerUptimeSeconds = computed(() => {
  if (props.dockerWorkspaceStatus !== 'running') return 0;
  if (props.dockerWorkspaceStartedAt) {
    try {
      const started = new Date(props.dockerWorkspaceStartedAt).getTime();
      if (!Number.isNaN(started) && started > 0) {
        return Math.max(0, Math.floor((dockerUptimeNow.value - started) / 1000));
      }
    } catch {}
  }
  if (typeof props.dockerWorkspaceUptimeSeconds === 'number') {
    return Math.max(0, props.dockerWorkspaceUptimeSeconds);
  }
  return 0;
});

const dockerUptimeFormatted = computed(() => {
  if (props.dockerWorkspaceStatus !== 'running') return '';
  ...
});
```

整体替换为（`dockerUptimeFormatted` 函数体保留原格式化逻辑，仅改符号与运行前置条件）：

```ts
const showSandboxActionsMenu = ref(false);
const sandboxActionsDropdownRef = ref<HTMLElement | null>(null);

const sandboxUptimeNow = ref(Date.now());
let sandboxUptimeTimer: ReturnType<typeof setInterval> | null = null;

const sandboxUptimeSeconds = computed(() => {
  if (props.sandboxWorkspaceStatus !== 'running') return 0;
  if (props.sandboxWorkspaceStartedAt) {
    try {
      const started = new Date(props.sandboxWorkspaceStartedAt).getTime();
      if (!Number.isNaN(started) && started > 0) {
        return Math.max(0, Math.floor((sandboxUptimeNow.value - started) / 1000));
      }
    } catch {}
  }
  if (typeof props.sandboxWorkspaceUptimeSeconds === 'number') {
    return Math.max(0, props.sandboxWorkspaceUptimeSeconds);
  }
  return 0;
});

const sandboxUptimeFormatted = computed(() => {
  if (props.sandboxWorkspaceStatus !== 'running') return '';
  if (!props.sandboxWorkspaceStartedAt && typeof props.sandboxWorkspaceUptimeSeconds !== 'number') {
    // 无启动时刻也无后端时长信息时不显示时长行
    return '';
  }
  const seconds = sandboxUptimeSeconds.value;
  if (seconds < 60) {
    return `${Math.max(1, seconds)}秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSecs = seconds % 60;
  if (minutes < 60) {
    return remainingSecs > 0 ? `${minutes}分${remainingSecs}秒` : `${minutes}分钟`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMins = minutes % 60;
  return remainingMins > 0 ? `${hours}小时${remainingMins}分` : `${hours}小时`;
});
```

**3.5 template 状态块（从 `<!-- Docker 容器运行状态与控制明细 -->` 所在 `<div v-if="isDockerSandboxPolicy"` 到该面板结束 `</div>`）**：把块内所有 `dockerWorkspaceStatus` → `sandboxWorkspaceStatus`、`dockerWorkspaceContainerId` → `sandboxWorkspaceInstanceId`、`dockerWorkspaceError` → `sandboxWorkspaceError`、`showDockerActionsMenu` → `showSandboxActionsMenu`、`dockerActionsDropdownRef` → `sandboxActionsDropdownRef`；外层 `v-if="isDockerSandboxPolicy"` → `v-if="isSandboxBackendPolicy"`；状态文案三元用 `sandboxStatusText`：

- 状态圆点/文案部分（原 2110-2123 区域）替换为：

```html
                          <div class="flex items-center justify-between gap-2">
                            <div class="flex items-center gap-1.5 min-w-0">
                              <span
                                class="inline-block h-2 w-2 shrink-0 rounded-full"
                                :class="{
                                  'bg-emerald-500 shadow-sm shadow-emerald-500/50': (sandboxWorkspaceStatus || 'idle') === 'running',
                                  'bg-amber-400 animate-pulse': (sandboxWorkspaceStatus || 'idle') === 'starting' || (sandboxWorkspaceStatus || 'idle') === 'stopping',
                                  'bg-rose-500 shadow-sm shadow-rose-500/50': (sandboxWorkspaceStatus || 'idle') === 'error',
                                  'bg-gray-300 dark:bg-gray-600': (sandboxWorkspaceStatus || 'idle') === 'idle'
                                }"
                              ></span>
                              <span class="font-medium text-gray-700 dark:text-gray-200">
                                {{ sandboxStatusText }}
                              </span>
                              <span
                                v-if="sandboxWorkspaceInstanceId"
                                class="truncate text-[9px] text-gray-400 dark:text-gray-500 max-w-[150px]"
                                :title="sandboxInstanceLabel"
                              >
                                {{ sandboxBackend === 'k8s' ? sandboxWorkspaceInstanceId : sandboxWorkspaceInstanceId.slice(0, 12) }}
                              </span>
                            </div>
```

- 启动/刷新/操作按钮区替换为（k8s 的「操作」菜单不含终端）：

```html
                            <div class="shrink-0 flex items-center gap-1">
                              <button
                                v-if="(sandboxWorkspaceStatus || 'idle') === 'idle' || (sandboxWorkspaceStatus || 'idle') === 'error'"
                                type="button"
                                class="rounded bg-indigo-600 px-2 py-0.5 text-[9px] font-medium text-white shadow-sm hover:bg-indigo-500 active:scale-95 transition-all disabled:opacity-50"
                                :disabled="isInteractionLocked"
                                :title="(sandboxWorkspaceStatus || 'idle') === 'error' ? (sandboxWorkspaceError || '重试启动沙箱') : (sandboxBackend === 'k8s' ? '启动当前用户的 Kubernetes 沙箱 Pod' : '启动当前用户的 Docker 沙箱容器')"
                                @click.stop="emit('start-sandbox-workspace')"
                              >
                                {{ sandboxStartButtonLabel }}
                              </button>
                              <button
                                type="button"
                                class="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-gray-400 dark:hover:text-indigo-300 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
                                title="手动检测刷新沙箱状态"
                                :disabled="(sandboxWorkspaceStatus || 'idle') === 'starting' || (sandboxWorkspaceStatus || 'idle') === 'stopping'"
                                @click.stop="emit('refresh-sandbox-workspace', true)"
                              >
                                <ArrowPathIcon class="h-3 w-3" :class="{ 'animate-spin': (sandboxWorkspaceStatus || 'idle') === 'starting' || (sandboxWorkspaceStatus || 'idle') === 'stopping' }" aria-hidden="true" />
                                <span>刷新</span>
                              </button>

                              <!-- 沙箱运行时的操作下拉菜单 -->
                              <div
                                v-if="(sandboxWorkspaceStatus || 'idle') === 'running'"
                                ref="sandboxActionsDropdownRef"
                                class="relative inline-block text-left"
                              >
                                <button
                                  type="button"
                                  class="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-gray-400 dark:hover:text-indigo-300 dark:hover:bg-gray-700 transition-colors" :class="{ 'text-indigo-600 bg-indigo-50 dark:text-indigo-300 dark:bg-gray-700': showSandboxActionsMenu }"
                                  title="沙箱管理操作"
                                  @click.stop="showSandboxActionsMenu = !showSandboxActionsMenu"
                                >
                                  <span>操作</span>
                                  <ChevronDownIcon class="h-2.5 w-2.5 transition-transform duration-200" :class="{ 'rotate-180': showSandboxActionsMenu }" />
                                </button>

                                <div
                                  v-if="showSandboxActionsMenu"
                                  class="absolute right-0 bottom-full mb-1.5 z-50 w-28 rounded-lg bg-white dark:bg-gray-800 py-1 shadow-lg ring-1 ring-black/5 dark:ring-white/10 border border-gray-100 dark:border-gray-700 text-[10px] font-sans"
                                  @click.stop
                                >
                                  <!-- 进入终端仅 Docker 后端提供 -->
                                  <button
                                    v-if="sandboxBackend === 'docker'"
                                    type="button"
                                    class="flex w-full items-center gap-1.5 px-2.5 py-1 text-gray-700 dark:text-gray-200 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 hover:text-indigo-600 dark:hover:text-indigo-300 transition-colors"
                                    @click="showSandboxActionsMenu = false; emit('open-docker-terminal')"
                                  >
                                    <CommandLineIcon class="h-3.5 w-3.5 text-emerald-500" />
                                    <span>进入终端</span>
                                  </button>
                                  <button
                                    type="button"
                                    class="flex w-full items-center gap-1.5 px-2.5 py-1 text-gray-700 dark:text-gray-200 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 hover:text-indigo-600 dark:hover:text-indigo-300 transition-colors"
                                    @click="showSandboxActionsMenu = false; emit('restart-sandbox-workspace')"
                                  >
                                    <ArrowPathIcon class="h-3.5 w-3.5 text-indigo-500" />
                                    <span>{{ sandboxBackend === 'k8s' ? '重启 Pod' : '重启容器' }}</span>
                                  </button>
                                  <div class="my-0.5 border-t border-gray-100 dark:border-gray-700/60"></div>
                                  <button
                                    type="button"
                                    class="flex w-full items-center gap-1.5 px-2.5 py-1 text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors"
                                    @click="showSandboxActionsMenu = false; emit('stop-sandbox-workspace')"
                                  >
                                    <PowerIcon class="h-3.5 w-3.5 text-rose-500" />
                                    <span>停止关机</span>
                                  </button>
                                </div>
                              </div>
                            </div>
```

**3.6 运行时长行**（原 2208-2222 区域）替换为：

```html
                          <!-- 运行时长与自动回收说明（仅在运行中展示） -->
                          <div
                            v-if="(sandboxWorkspaceStatus || 'idle') === 'running' && sandboxUptimeFormatted"
                            class="flex items-center justify-between text-[9px] text-gray-400 dark:text-gray-500 pt-1 border-t border-gray-200/50 dark:border-gray-700/50"
                          >
                            <span class="flex items-center gap-1">
                              <ClockIcon class="h-3 w-3 text-emerald-500/80 shrink-0" aria-hidden="true" />
                              <span>运行时长：<strong class="text-gray-600 dark:text-gray-300 font-medium">{{ sandboxUptimeFormatted }}</strong></span>
                            </span>
                            <span class="text-[8.5px] text-gray-400/80 dark:text-gray-500/80" title="沙箱连续空闲 30 分钟后将自动销毁释放资源">
                              {{ sandboxReclaimHint }}
                            </span>
                          </div>
```

**3.7 外层面板标题**：`<!-- Docker 容器运行状态与控制明细 -->` 注释改为 `<!-- 沙箱运行状态与控制明细 -->`。

**3.8 script 引用同步**：
- 原 `if (isDockerSandboxPolicy.value) { ... emit('refresh-docker-workspace', false); }`（约 1075 行 watch 区）的 watch 回调替换为（docker/k8s 共用同一事件，事件名已统一）：

```ts
watch(showContextUsageDetails, (open) => {
  if (open && isSandboxBackendPolicy.value) {
    void nextTick(() => {
      emit('refresh-sandbox-workspace', false);
    });
  }
});
```

- 全局点击收起（约 1184-1186 行）：`showDockerActionsMenu.value` → `showSandboxActionsMenu.value`，`dockerActionsDropdownRef.value` → `sandboxActionsDropdownRef.value`。
- uptime tick 周期（onMounted，约 1268-1278 行）：`dockerUptimeTimer = setInterval(() => { if (showContextUsageDetails.value && props.dockerWorkspaceStatus === 'running') { dockerUptimeNow.value = Date.now(); } }, 1000);` → `sandboxUptimeTimer` / `props.sandboxWorkspaceStatus` / `sandboxUptimeNow`；onUnmounted 同理改 `sandboxUptimeTimer`。
- 若仍有残余 `dockerWorkspace`（不含 `sandboxWorkspace`）标识符出现在本文件 script 中（props 文档注释里 "Docker 沙箱工作区运行状态" 等注释），同步把注释词改为通用表述。

- [ ] **Step 4: 同步既有契约断言**

`tests/frontend/test_chat_sandbox_workspace_contract.py` 的 `test_chat_input_context_modal_renders_docker_workspace_status_and_actions` 中关于命名/事件的断言更新为**新命名 + docker 语义仍存在**：

```python
def test_chat_input_context_modal_renders_docker_workspace_status_and_actions():
    chat_input_source = CHAT_INPUT.read_text(encoding="utf-8")
    assert "sandboxWorkspaceStatus" in chat_input_source
    assert "sandboxWorkspaceInstanceId" in chat_input_source
    assert "sandboxBackend" in chat_input_source
    assert "isDockerSandboxPolicy" in chat_input_source
    assert "isSandboxBackendPolicy" in chat_input_source
    assert "start-sandbox-workspace" in chat_input_source
    assert "refresh-sandbox-workspace" in chat_input_source
    assert "容器已运行" in chat_input_source
    assert "容器未启动" in chat_input_source
    assert "启动容器" in chat_input_source
    assert "启动沙箱 Pod" in chat_input_source
    assert "重试启动" in chat_input_source
    assert "Pod 已运行" in chat_input_source
    assert "Pod 未启动" in chat_input_source

    # 展开详情面板时静默触发沙箱状态刷新
    assert "if (isSandboxBackendPolicy.value) {" in chat_input_source
    assert "emit('refresh-sandbox-workspace', false);" in chat_input_source

    assert "sandboxWorkspaceStartedAt" in chat_input_source
    assert "sandboxWorkspaceUptimeSeconds" in chat_input_source
```

- [ ] **Step 5: 运行契约测试**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_sandbox_workspace_contract.py -q`
Expected: 全部 PASS。

- [ ] **Step 6: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 0 error。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/embed/ChatInput.vue tests/frontend/test_chat_sandbox_workspace_contract.py
git commit -m "feat(chat): ChatInput 沙箱浮标面板 docker/k8s 通用化与术语切换"
```

---

### Task 6: EmbedChat.vue 泛化状态函数、按 backend 路由端点与 k8s 停止确认

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`
- Test: `tests/frontend/test_chat_sandbox_workspace_contract.py`

- [ ] **Step 1: 写失败契约测试**

`tests/frontend/test_chat_sandbox_workspace_contract.py` 追加：

```python
def test_embed_chat_routes_k8s_workspace_endpoints_and_stop_confirm():
    source = EMBED.read_text(encoding="utf-8")
    # k8s 端点路由
    assert "/api/v1/sandbox/k8s/workspace/status" in source
    assert "/api/v1/sandbox/k8s/workspace/ensure" in source
    assert "/api/v1/sandbox/k8s/workspace/stop" in source
    assert "/api/v1/sandbox/k8s/workspace/restart" in source
    # docker 端点保留
    assert "/api/v1/sandbox/docker/workspace/status" in source
    # k8s 停止前二次确认
    assert "showSandboxStopConfirm" in source
    assert "<ConfirmModal" in source
    assert "销毁沙箱 Pod" in source
    assert "sandboxWorkspaceStatus" in source
    assert "sandboxBackend" in source
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_sandbox_workspace_contract.py -q`
Expected: 断言失败。

- [ ] **Step 3: 实现**

`frontend/src/views/EmbedChat.vue`：

**3.1 props 绑定（约 1095-1105 行）** 替换为（新增 `sandbox-backend` 与事件名同步）：

```html
        :sandbox-workspace-status="sandboxWorkspaceStatus"
        :sandbox-workspace-instance-id="sandboxWorkspaceInstanceId"
        :sandbox-workspace-started-at="sandboxWorkspaceStartedAt"
        :sandbox-workspace-uptime-seconds="sandboxWorkspaceUptimeSeconds"
        :sandbox-workspace-error="sandboxWorkspaceError"
        :sandbox-backend="sandboxBackend"
        :enable-grounding="config.enableGrounding"
        :grounding-block-mode="config.groundingBlockMode"
        @start-sandbox-workspace="ensureSandboxWorkspace"
        @refresh-sandbox-workspace="refreshSandboxWorkspaceStatus"
        @stop-sandbox-workspace="handleStopSandboxWorkspaceRequest"
        @restart-sandbox-workspace="restartSandboxWorkspace"
        @open-docker-terminal="openDockerTerminal"
```

> 定位：在模板中这段是 `:docker-workspace-*` 5 行 + `@start-docker-workspace`/`@refresh-docker-workspace`/`@stop-docker-workspace`/`@restart-docker-workspace`/`@open-docker-terminal` 5 行。逐行按上述映射替换；保留其它 props/事件原顺序。

**3.2 banner 模板（约 1156-1163 行）** 替换为：

```html
                <DockerWorkspaceBanner
                  v-if="showSandboxWorkspaceControl"
                  :workspace-status="sandboxWorkspaceStatus"
                  :workspace-error="sandboxWorkspaceError"
                  :container-id="sandboxWorkspaceInstanceId"
                  :backend="sandboxBackend"
                  @start="ensureSandboxWorkspace"
                  @refresh="refreshSandboxWorkspaceStatus"
                  @close="dismissSandboxWorkspaceBanner"
                />
```

**3.3 DockerTerminalModal（约 1524 行）**：`:container-id="dockerWorkspaceContainerId"` → `:container-id="sandboxWorkspaceInstanceId"`（仅 docker 后端触发，字段承载实例标识）。

**3.4 停止确认 ConfirmModal**（在 DockerTerminalModal 块之后插入）：

```html
    <!-- K8s 沙箱停止二次确认 -->
    <ConfirmModal
      v-if="showSandboxStopConfirm"
      title="停止 Kubernetes 沙箱"
      :message="`确定要停止当前沙箱 Pod 吗？\n将销毁沙箱 Pod；若为动态独立卷且已开启 delete_pvc_on_close，工作区数据将随 PVC 一并删除。停止后可重新启动，Pod 重建需等待镜像拉取与调度。`"
      type="danger"
      @confirm="confirmStopSandboxWorkspace"
      @cancel="showSandboxStopConfirm = false"
    />
```

**3.5 script 状态区（原 `const DOCKER_WORKSPACE_BANNER_DISMISSED_KEY` 起到 watch 结束，约 4055-4290 行）整体替换**：

- 先把常量定义替换为（**两个常量都保留**：旧 key 用于清理遗留标记，新 key 语义上不再持久化任何标记，仅命名迁移）：

```ts
const DOCKER_WORKSPACE_BANNER_DISMISSED_KEY = "nanzi_dismissed_docker_workspace_banner";
const SANDBOX_WORKSPACE_BANNER_DISMISSED_KEY = "nanzi_dismissed_sandbox_workspace_banner";

const readSandboxWorkspaceBannerDismissed = (): boolean => {
  // 不做持久化防打扰，刷新页面或切换会话后始终重新展示；并清理遗留的旧 localStorage 标记
  try {
    localStorage.removeItem(DOCKER_WORKSPACE_BANNER_DISMISSED_KEY);
  } catch {}
  return false;
};
```

> 注：旧常量保留仅用于清理历史遗留 key；若 lint 报未使用变量，可保留 `SANDBOX_WORKSPACE_BANNER_DISMISSED_KEY` 仅作文档常量不读取，但不要删除旧常量清理逻辑。

**替换时逐条更名以下标识符（保留语义）**：

| 旧 | 新 |
| --- | --- |
| `type DockerWorkspaceStatus` | `type SandboxWorkspaceStatus` |
| `dockerWorkspaceStatus` | `sandboxWorkspaceStatus` |
| `dockerWorkspaceStatusLoaded` | `sandboxWorkspaceStatusLoaded` |
| `dockerWorkspaceError` | `sandboxWorkspaceError` |
| `dockerWorkspaceContainerId` | `sandboxWorkspaceInstanceId` |
| `dockerWorkspaceStartedAt` | `sandboxWorkspaceStartedAt` |
| `dockerWorkspaceUptimeSeconds` | `sandboxWorkspaceUptimeSeconds` |
| `dockerWorkspaceBannerDismissed` | `sandboxWorkspaceBannerDismissed` |
| `showDockerWorkspaceControl` | `showSandboxWorkspaceControl` |
| `resetDockerWorkspaceState` | `resetSandboxWorkspaceState` |
| `dismissDockerWorkspaceBanner` | `dismissSandboxWorkspaceBanner` |
| `refreshDockerWorkspaceStatus` | `refreshSandboxWorkspaceStatus` |
| `ensureDockerWorkspace` | `ensureSandboxWorkspace` |
| `stopDockerWorkspace` | `stopSandboxWorkspace` |
| `restartDockerWorkspace` | `restartSandboxWorkspace` |

同时：
- 新增 `const sandboxBackend = computed<"docker" | "k8s">(() => effectiveSandboxPolicy.value === "k8s" ? "k8s" : "docker");`
- 新增 `const showSandboxStopConfirm = ref(false);`
- 新增 `const confirmStopSandboxWorkspace = async () => { showSandboxStopConfirm.value = false; await stopSandboxWorkspace(); };`
- 新增 `const handleStopSandboxWorkspaceRequest = () => { if (sandboxBackend.value === "k8s") { showSandboxStopConfirm.value = true; } else { void stopSandboxWorkspace(); } };`
- `refreshSandboxWorkspaceStatus` 内 API 改为按后端路由：

```ts
const sandboxWorkspaceBaseEndpoint = computed(() => sandboxBackend.value === "k8s"
  ? "/api/v1/sandbox/k8s/workspace"
  : "/api/v1/sandbox/docker/workspace");
```

  并在 `refreshSandboxWorkspaceStatus` 中使用 `${sandboxWorkspaceBaseEndpoint.value}/status`；`ensureSandboxWorkspace` 用 `${...}/ensure`；`stopSandboxWorkspace` 用 `${...}/stop`；`restartSandboxWorkspace` 用 `${...}/restart`。策略守卫从 `effectiveSandboxPolicy.value !== "docker"` 改为 `!isSandboxPolicy`（`docker` 或 `k8s`），即新增：

```ts
const isSandboxWorkspacePolicy = computed(() => {
  const policy = effectiveSandboxPolicy.value;
  return policy === "docker" || policy === "k8s";
});
```

  响应字段归一：docker 分支取 `data.container_id`，k8s 分支取 `data.pod_name`，统一赋给 `sandboxWorkspaceInstanceId.value`：

```ts
const instanceIdFromData = (data: any): string | null => (
  sandboxBackend.value === "k8s"
    ? (data?.pod_name ?? null)
    : (data?.container_id ?? null)
);
```

  状态映射统一（后端 k8s 可能返回 `starting` / `stopped` / `idle`）：

```ts
const mapSandboxStatus = (raw: string): SandboxWorkspaceStatus => {
  if (raw === "running") return "running";
  if (raw === "starting") return "starting";
  if (raw === "stopping") return "stopping";
  if (raw === "error") return "error";
  return "idle"; // stopped / idle -> idle
};
```

  `refreshSandboxWorkspaceStatus` 的成功分支改为：

```ts
    sandboxWorkspaceInstanceId.value = instanceIdFromData(data);
    sandboxWorkspaceStartedAt.value = data?.started_at || null;
    sandboxWorkspaceUptimeSeconds.value = typeof data?.uptime_seconds === "number" ? data.uptime_seconds : null;
    sandboxWorkspaceStatus.value = mapSandboxStatus(String(data?.status || "idle"));
    sandboxWorkspaceError.value = "";
    if (showFeedback) {
      if (sandboxWorkspaceStatus.value === "running") {
        const shortId = (sandboxWorkspaceInstanceId.value || "").slice(0, 12);
        showToast(shortId ? `沙箱运行中 (${shortId})` : "沙箱运行中", "success");
      } else if (sandboxWorkspaceStatus.value === "starting") {
        showToast("沙箱启动中，请稍候...", "info");
      } else {
        showToast("沙箱状态已刷新：尚未启动", "info");
      }
    }
```

  `ensureSandboxWorkspace` 的成功分支（移除「必须 running 否则抛错」的 docker-only 硬校验，容忍 starting）：

```ts
    const mapped = mapSandboxStatus(String(data?.status || "idle"));
    if (mapped === "error") {
      throw new Error("沙箱未返回运行中状态");
    }
    sandboxWorkspaceInstanceId.value = instanceIdFromData(data);
    sandboxWorkspaceStartedAt.value = data?.started_at || null;
    sandboxWorkspaceUptimeSeconds.value = typeof data?.uptime_seconds === "number" ? data.uptime_seconds : 0;
    sandboxWorkspaceStatus.value = mapped;
    showToast(mapped === "starting" ? "沙箱启动中..." : "沙箱已启动", mapped === "starting" ? "info" : "success");
```

  `restartSandboxWorkspace` 成功分支同样用 `mapSandboxStatus` 与 `instanceIdFromData`。

  `openDockerTerminal`（终端仅 docker）保持仅 docker 触发（新增守卫）：

```ts
const openDockerTerminal = () => {
  if (sandboxBackend.value !== "docker") {
    showToast("仅 Docker 沙箱支持终端", "warning");
    return;
  }
  if (sandboxWorkspaceStatus.value !== "running") {
    showToast("沙箱未在运行中，请先启动", "warning");
    return;
  }
  showDockerTerminal.value = true;
};
```

  watch 块（约 4270-4289 行）泛化：

```ts
watch(
  [conversationId, effectiveSandboxPolicy],
  ([conversation, policy], previous) => {
    const previousConversation = String(previous?.[0] || "");
    const previousPolicy = String(previous?.[1] || "");
    if (
      !isSandboxWorkspacePolicy.value
      || !conversation
      || conversation !== previousConversation
      || policy !== previousPolicy
    ) {
      resetSandboxWorkspaceState();
      if (isSandboxWorkspacePolicy.value && conversation) {
        void refreshSandboxWorkspaceStatus();
      }
    }
  },
  { immediate: true },
);
```

  `showSandboxWorkspaceControl` 计算（原 `showDockerWorkspaceControl`）内容保持不变，仅把 docker 专属条件换为 `isSandboxWorkspacePolicy.value` 与 `sandboxBackend`：

```ts
const showSandboxWorkspaceControl = computed(() => {
  if (!isSandboxWorkspacePolicy.value || !conversationId.value) {
    return false;
  }
  if (!sandboxWorkspaceStatusLoaded.value) {
    return false;
  }
  if (sandboxWorkspaceStatus.value === "running") {
    return false;
  }
  if (sandboxWorkspaceStatus.value === "error") {
    return true;
  }
  return isSandboxWorkspacePolicy.value && !sandboxWorkspaceBannerDismissed.value;
});
```

- [ ] **Step 4: 同步既有契约断言并运行**

将 `tests/frontend/test_chat_sandbox_workspace_contract.py` 中 docker 专属断言改为新命名（docker 语义仍须存在），新增步骤 1 的 k8s 断言。整文件跑：

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_sandbox_workspace_contract.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 0 error。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/EmbedChat.vue tests/frontend/test_chat_sandbox_workspace_contract.py
git commit -m "feat(chat): EmbedChat 沙箱状态按 docker/k8s 路由与 k8s 停止二次确认"
```

---

### Task 7: 全量回归、类型检查与 CHECKLIST 更新

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 后端全量相关测试**

Run: `.venv/bin/python -m pytest tests/services/test_sandbox_policy_k8s.py tests/api/v1/test_sandbox_k8s_workspace_ops.py tests/api/v1/test_sandbox_docker_workspace_ops.py tests/ai/runtime/test_agentscope_workspace.py tests/ai/runtime/test_agentscope_workspace_toolkit.py tests/services/ai/test_error_response_service.py tests/ai/runners/test_general_agent_multiturn.py tests/services/ai/test_agent_service_error_enrichment.py -q`
Expected: 全部 PASS。

- [ ] **Step 2: 前端契约与类型**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_sandbox_workspace_contract.py -q`
Run: `cd frontend && npx vue-tsc --noEmit`
Expected: PASS / 0 error。

- [ ] **Step 3: Python 编译检查**

Run: `.venv/bin/python -m compileall -q app/api/v1/endpoints/sandbox.py app/services/ai/runtime/agentscope/k8s_workspace.py app/services/ai/runtime/agentscope/workspace.py`
Expected: 退出码 0。

- [ ] **Step 4: 更新 CHECKLIST**

在 `tests/CHECKLIST.md` 顶部表格新增一行（对照既有条目格式）：

```markdown
| Kubernetes 沙箱输入框浮标 Pod 状态/启停控制（状态展示、运行时长、手动启动/停止/重启与停止二次确认） (K8s Sandbox Input Badge Pod Status & Lifecycle Controls) | `app/services/ai/runtime/agentscope/k8s_workspace.py`, `app/services/ai/runtime/agentscope/workspace.py`, `app/api/v1/endpoints/sandbox.py`, `frontend/src/components/embed/ChatInput.vue`, `frontend/src/views/EmbedChat.vue`, `frontend/src/components/chat/DockerWorkspaceBanner.vue`, `tests/services/test_sandbox_policy_k8s.py`, `tests/api/v1/test_sandbox_k8s_workspace_ops.py`, `tests/frontend/test_chat_sandbox_workspace_contract.py`, `docs/superpowers/specs/2026-09-09-k8s-sandbox-workspace-badge-controls-design.md`, `tests/CHECKLIST.md` | **K8s 沙箱浮标与 Docker 对等的 Pod 生命周期闭环**：① **后端只读状态查询辅助**：`read_k8s_sandbox_pod` 通过 kubernetes-asyncio 只读探测 Pod phase/startTime，SDK 缺失/无凭据/RBAC 受限/404 均降级不抛 500；② **k8s runtime 族**：`k8s_workspace_status`（只读、未缓存返回 idle、实时 Pod phase 优先、降级 is_alive 视图）、`ensure_k8s_workspace`（走 `get_local_workspace` 与聊天同缓存键预热）、`stop_k8s_workspace`（逐出缓存并关闭 Pod）、`restart_k8s_workspace`（重建）与元数据函数，策略不符/无会话/无身份统一抛 `K8sSandboxUnavailableError`；③ **用户端点**：`/api/v1/sandbox/k8s/workspace/{status,ensure,stop,restart}`，仅当前登录用户操作自身会话；④ **前端通用化**：`dockerWorkspace*` 更名 `sandboxWorkspace*` 并新增 `sandboxBackend`（docker|k8s）术语维度；浮标展示 Pod 状态点/Pod 名/每秒滚动运行时长/空闲 30m 自动回收提示，k8s「操作」菜单仅重启 Pod/停止关机（无终端）；⑤ **k8s 停止二次确认**：复用 EmbedChat `<ConfirmModal>`，文案警示动态独立卷随 PVC 删除与重建耗时；⑥ **Docker 回归**：docker 浮标文案/交互与既有端点契约不变。 | ✅ 后端 11 项定向单测（Pod 探测 4 项 + runtime 7 项）+ 5 项端点单测 + 前端契约测试全绿；`vue-tsc --noEmit` 零报错；未代跑真实服务与启动脚本 | 2026-09-09 |
```

- [ ] **Step 5: Commit**

```bash
git add tests/CHECKLIST.md
git commit -m "docs(chat): K8s 沙箱浮标生命周期控制清单记录"
```

---

## 收尾验证清单

- [ ] `.venv/bin/python -m pytest tests/services/test_sandbox_policy_k8s.py tests/api/v1/test_sandbox_k8s_workspace_ops.py -q` 全绿
- [ ] `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_sandbox_workspace_contract.py -q` 全绿
- [ ] `cd frontend && npx vue-tsc --noEmit` 0 报错
- [ ] 通知用户控制台执行 `./dev.sh` 后真机验证（Task 7 之后交付说明）
