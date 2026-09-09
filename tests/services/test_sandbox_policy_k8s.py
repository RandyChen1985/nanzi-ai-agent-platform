import asyncio

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_policy_k8s_workspace_build_and_initialize(monkeypatch):
    from app.services.ai.runtime.agentscope.workspace import (
        _policy_k8s_workspace,
        SANDBOX_POLICY_K8S,
    )

    # 避免任何 config 键触达真实 Redis（redis asyncio 连接跨 event loop 复用时
    # 会在组合测试顺序下抛 "Future attached to a different loop"）。
    async def fake_get(key, default=None):
        return default

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    mock_ws = MagicMock()
    mock_ws.initialize = AsyncMock()

    config_overrides = {
        "sandbox_k8s_namespace": "custom-ns",
        "sandbox_k8s_image": "python:3.11-custom",
        "sandbox_k8s_existing_pvc": "shared-data-pvc",
        "sandbox_k8s_cpu_request": "200m",
        "sandbox_k8s_memory_request": "256Mi",
        "sandbox_k8s_cpu_limit": "2000m",
        "sandbox_k8s_memory_limit": "4Gi",
        "sandbox_k8s_delete_pvc_on_close": "true",
    }

    with patch(
        "app.services.ai.runtime.agentscope.k8s_workspace.build_k8s_workspace_with_nanzi_adapter",
        return_value=mock_ws,
    ) as mock_builder:
        ws = await _policy_k8s_workspace(
            skill_paths=["/fake/skill"],
            config_overrides=config_overrides,
            sandbox_user_key="test_user__1",
        )

        assert ws is mock_ws
        assert ws._platform_sandbox_policy == SANDBOX_POLICY_K8S
        assert ws._platform_execution_backend == SANDBOX_POLICY_K8S
        mock_ws.initialize.assert_awaited_once()

        # 校验构造参数装配
        call_kwargs = mock_builder.call_args.kwargs
        assert call_kwargs["namespace"] == "custom-ns"
        assert call_kwargs["image"] == "python:3.11-custom"
        assert call_kwargs["existing_pvc"] == "shared-data-pvc"
        assert call_kwargs["sandbox_user_key"] == "test_user__1"
        assert call_kwargs["delete_pvc_on_close"] is True
        assert call_kwargs["resources"] == {
            "requests": {"cpu": "200m", "memory": "256Mi"},
            "limits": {"cpu": "2000m", "memory": "4Gi"},
        }
        assert call_kwargs["default_mcps"][0].model_dump(mode="json")["mcp_config"]["command"] == (
            "/root/.agentscope/.venv/bin/python"
        )


@pytest.mark.asyncio
async def test_policy_k8s_workspace_prohibits_unauthenticated_shared_pvc():
    from app.services.ai.runtime.agentscope.workspace import _policy_k8s_workspace

    config_overrides = {
        "sandbox_k8s_namespace": "agent-sandboxes",
        "sandbox_k8s_image": "python:3.11-slim",
        "sandbox_k8s_existing_pvc": "shared-data-pvc",
        "sandbox_k8s_storage_class": "",
        "sandbox_k8s_storage_size": "1Gi",
        "sandbox_k8s_cpu_request": "100m",
        "sandbox_k8s_memory_request": "128Mi",
        "sandbox_k8s_cpu_limit": "1000m",
        "sandbox_k8s_memory_limit": "1Gi",
        "sandbox_k8s_delete_pvc_on_close": "true",
    }

    with pytest.raises(ValueError, match="sandbox_user_key 不能为空"):
        await _policy_k8s_workspace(
            skill_paths=[],
            config_overrides=config_overrides,
            sandbox_user_key=None,
        )


@pytest.mark.asyncio
async def test_build_sandbox_workspace_for_test_dispatches_k8s():
    from app.services.ai.runtime.agentscope.workspace import (
        build_sandbox_workspace_for_test,
    )

    mock_ws = MagicMock()
    mock_ws.initialize = AsyncMock()

    with patch(
        "app.services.ai.runtime.agentscope.workspace._policy_k8s_workspace",
        new=AsyncMock(return_value=mock_ws),
    ) as mock_policy:
        res = await build_sandbox_workspace_for_test("k8s", {"sandbox_k8s_namespace": "test-ns"})
        assert res is mock_ws
        mock_policy.assert_awaited_once_with([], {"sandbox_k8s_namespace": "test-ns"})


@pytest.mark.asyncio
async def test_nanzi_k8s_adapter_handles_existing_pvc_subpath():
    from app.services.ai.runtime.agentscope.k8s_workspace import (
        build_k8s_workspace_with_nanzi_adapter,
    )

    class DummyBaseK8sWorkspace:
        def __init__(self, **kwargs):
            self._pod_name = "test-pod"
            self._namespace = "agent-sandboxes"
            self._image = "python:3.11-slim"
            self._image_pull_policy = "IfNotPresent"
            self.gateway_port = 5600
            self.env = {}
            self._resources = kwargs.get("resources")
            self._node_selector = None
            self._tolerations = None
            self._service_account = None
            self._image_pull_secrets = None
            self._delete_pvc_on_close = True
            self._v1 = MagicMock()
            self._api_client = MagicMock()
            self.workspace_id = "test-ws-id"

        async def _ensure_pvc(self):
            raise AssertionError("Should not be called when using existing_pvc")

        async def _create_pod(self):
            pass

        async def _teardown_backend(self):
            pass

    ws = build_k8s_workspace_with_nanzi_adapter(
        DummyBaseK8sWorkspace,
        existing_pvc="platform-app-data",
        sandbox_user_key="user__101",
        public_docs_mounted=True,
    )

    # 1. ensure_pvc 遇已有 PVC 自动跳过，不创建新 PVC
    await ws._ensure_pvc()

    # 2. teardown 时不误删平台共享 PVC
    await ws._teardown_backend()
    assert ws._delete_pvc_on_close is True  # 状态复原


@pytest.mark.asyncio
async def test_nanzi_k8s_adapter_create_pod_spec_structure(tmp_path):
    from app.services.ai.runtime.agentscope.k8s_workspace import (
        build_k8s_workspace_with_nanzi_adapter,
    )

    created_pod_holder = []

    class DummyBaseWithPod:
        def __init__(self, **kwargs):
            self._pod_name = "test-pod"
            self._namespace = "agent-sandboxes"
            self._image = "python:3.11-slim"
            self._image_pull_policy = "IfNotPresent"
            self.gateway_port = 5600
            self.env = {"FOO": "BAR"}
            self._resources = kwargs.get("resources")
            self._node_selector = None
            self._tolerations = None
            self._service_account = None
            self._image_pull_secrets = None
            self._delete_pvc_on_close = True
            self.workspace_id = "test-ws-id"
            self._v1 = MagicMock()

            async def mock_create_namespaced_pod(ns, pod):
                created_pod_holder.append((ns, pod))

            self._v1.create_namespaced_pod = mock_create_namespaced_pod

    ws = build_k8s_workspace_with_nanzi_adapter(
        DummyBaseWithPod,
        existing_pvc="shared-pvc",
        sandbox_user_key="u_test_123",
        public_docs_mounted=True,
        local_data_root=str(tmp_path),
        resources={"limits": {"cpu": "1000m", "memory": "1Gi"}},
    )

    await ws._create_pod()

    assert len(created_pod_holder) == 1
    ns, pod = created_pod_holder[0]
    assert ns == "agent-sandboxes"

    spec = pod.spec
    assert len(spec.containers) == 1
    container = spec.containers[0]

    # 校验 subPath 挂载
    mounts = container.volume_mounts
    assert len(mounts) == 2
    user_mount = next(m for m in mounts if m.mount_path == "/workspace")
    assert user_mount.sub_path == "agent_workspaces/u_test_123/sandbox"

    docs_mount = next(m for m in mounts if m.sub_path == "docs")
    assert docs_mount.mount_path == "/workspace/public/docs"
    assert docs_mount.read_only is True

    # 校验 resources 自动补齐 requests
    assert container.resources is not None
    assert container.resources.requests["cpu"] == "100m"
    assert container.resources.requests["memory"] == "128Mi"
    assert container.resources.limits["cpu"] == "1000m"
    assert container.resources.limits["memory"] == "1Gi"

    # 校验 uv venv 幂等开关注入（避免 Pod 重复 initialize 时 bootstrap 因 venv 已存在而失败）
    env_map = {e.name: e.value for e in (container.env or [])}
    assert env_map.get("UV_VENV_CLEAR") == "1"


@pytest.mark.asyncio
async def test_nanzi_k8s_adapter_ensure_namespace_graceful_403():
    from app.services.ai.runtime.agentscope.k8s_workspace import (
        build_k8s_workspace_with_nanzi_adapter,
    )

    class DummyWith403:
        def __init__(self, **kwargs):
            self._namespace = "agent-sandboxes"

        async def _ensure_namespace(self):
            exc = Exception("User cannot create resource 'namespaces' in API group")
            exc.status = 403
            raise exc

    ws = build_k8s_workspace_with_nanzi_adapter(
        DummyWith403,
        existing_pvc="shared-pvc",
        sandbox_user_key="u_1",
    )

    # 遇到 403 不应抛异常抛出，应优雅跳过（假设管理员已预建）
    await ws._ensure_namespace()


@pytest.mark.asyncio
async def test_k8s_workspace_lifecycle_refcounts():
    from app.services.ai.runtime.agentscope.workspace import (
        _acquire_k8s_workspace,
        _release_k8s_workspace,
        _k8s_workspace_cache,
        _k8s_workspace_refcounts,
    )

    mock_ws = MagicMock()
    mock_ws.is_alive = True
    mock_ws.close = AsyncMock()

    with patch(
        "app.services.ai.runtime.agentscope.workspace._policy_k8s_workspace",
        new=AsyncMock(return_value=mock_ws),
    ):
        # 第一次请求：初始化并获得引用
        ws1, key1 = await _acquire_k8s_workspace(
            root="/tmp/data",
            user_key="user_alpha",
            skill_paths=[],
        )
        assert ws1 is mock_ws
        assert _k8s_workspace_refcounts[key1] == 1

        # 第二次并发请求：复用已有实例，递增引用计数
        ws2, key2 = await _acquire_k8s_workspace(
            root="/tmp/data",
            user_key="user_alpha",
            skill_paths=[],
        )
        assert ws2 is ws1
        assert key2 == key1
        assert _k8s_workspace_refcounts[key1] == 2

        # 释放第一个会话：引用计数减为 1，Pod 依然存活未被删除
        await _release_k8s_workspace(key1, reason="session 1 closed")
        assert _k8s_workspace_refcounts[key1] == 1
        mock_ws.close.assert_not_awaited()

        # 释放第二个会话：引用计数归零，真正触发销毁关闭
        await _release_k8s_workspace(key1, reason="session 2 closed")
        assert key1 not in _k8s_workspace_cache
        assert key1 not in _k8s_workspace_refcounts
        mock_ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_k8s_rbac_status_no_sdk():
    from app.services.ai.runtime.agentscope.k8s_workspace import check_k8s_rbac_status

    with patch.dict("sys.modules", {"kubernetes_asyncio": None}):
        result = await check_k8s_rbac_status(namespace="test-sandboxes")
        assert result["ok"] is False
        assert "未安装 kubernetes-asyncio" in result["message"]


@pytest.mark.asyncio
async def test_check_k8s_rbac_status_success():
    from app.services.ai.runtime.agentscope.k8s_workspace import check_k8s_rbac_status

    mock_auth_api = MagicMock()
    mock_review_allowed = MagicMock()
    mock_review_allowed.status.allowed = True
    mock_auth_api.create_self_subject_access_review = AsyncMock(return_value=mock_review_allowed)

    mock_client_module = MagicMock()
    mock_client_module.AuthorizationV1Api.return_value = mock_auth_api

    class FakeApiClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    mock_client_module.ApiClient = FakeApiClient

    mock_config_module = MagicMock()
    mock_config_module.load_incluster_config.return_value = None

    mock_k8s = MagicMock()
    mock_k8s.client = mock_client_module
    mock_k8s.config = mock_config_module

    with patch.dict("sys.modules", {
        "kubernetes_asyncio": mock_k8s,
        "kubernetes_asyncio.client": mock_client_module,
        "kubernetes_asyncio.config": mock_config_module,
    }):
        result = await check_k8s_rbac_status(namespace="test-sandboxes")
        assert result["ok"] is True
        assert result["namespace"] == "test-sandboxes"
        assert result["can_create_pods"] is True
        assert result["can_create_pvcs"] is True
        assert "具备命名空间 [test-sandboxes] 的 Pod 与 PVC 操作权限" in result["message"]


@pytest.mark.asyncio
async def test_check_k8s_rbac_status_forbidden_pods():
    from app.services.ai.runtime.agentscope.k8s_workspace import check_k8s_rbac_status

    mock_auth_api = MagicMock()
    mock_review_forbidden = MagicMock()
    mock_review_forbidden.status.allowed = False
    mock_auth_api.create_self_subject_access_review = AsyncMock(return_value=mock_review_forbidden)

    mock_client_module = MagicMock()
    mock_client_module.AuthorizationV1Api.return_value = mock_auth_api

    class FakeApiClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    mock_client_module.ApiClient = FakeApiClient

    mock_config_module = MagicMock()
    mock_config_module.load_incluster_config.return_value = None

    mock_k8s = MagicMock()
    mock_k8s.client = mock_client_module
    mock_k8s.config = mock_config_module

    with patch.dict("sys.modules", {
        "kubernetes_asyncio": mock_k8s,
        "kubernetes_asyncio.client": mock_client_module,
        "kubernetes_asyncio.config": mock_config_module,
    }):
        result = await check_k8s_rbac_status(namespace="test-sandboxes")
        assert result["ok"] is False
        assert result["can_create_pods"] is False
        assert "缺少 Pods 创建权限" in result["message"]
        assert "kubectl apply -f k8s_deploy/sandbox-rbac.example.yaml" in result["remedy"]


@pytest.mark.asyncio
async def test_release_sandbox_workspace_routes_by_policy_suffix():
    """''_release_sandbox_workspace'' must route by the policy suffix, not by
    a substring scan, so a user_key containing ``k8s`` cannot be misrouted."""
    from app.services.ai.runtime.agentscope.workspace import _release_sandbox_workspace

    # A cache_key whose *user* identity contains "k8s" but whose policy is docker:
    # must be released via the docker channel (no false-positive k8s routing).
    docker_key = "/data::myk8s_user_7::docker"
    with patch(
        "app.services.ai.runtime.agentscope.workspace._release_k8s_workspace",
        new=AsyncMock(),
    ) as mock_rel_k8s, patch(
        "app.services.ai.runtime.agentscope.workspace._release_docker_workspace",
        new=AsyncMock(),
    ) as mock_rel_docker:
        await _release_sandbox_workspace(docker_key, reason="test")
        mock_rel_docker.assert_awaited_once_with(docker_key, reason="test")
        mock_rel_k8s.assert_not_awaited()

    # A genuine k8s cache_key must be released via the k8s channel.
    k8s_key = "/data::user_1::k8s"
    with patch(
        "app.services.ai.runtime.agentscope.workspace._release_k8s_workspace",
        new=AsyncMock(),
    ) as mock_rel_k8s, patch(
        "app.services.ai.runtime.agentscope.workspace._release_docker_workspace",
        new=AsyncMock(),
    ) as mock_rel_docker:
        await _release_sandbox_workspace(k8s_key, reason="test")
        mock_rel_k8s.assert_awaited_once_with(k8s_key, reason="test")
        mock_rel_docker.assert_not_awaited()


@pytest.mark.asyncio
async def test_k8s_reaper_start_stop_and_idle_reap():
    from app.services.ai.runtime.agentscope import workspace as ws_module

    # Reset any lingering reaper task from a previous run.
    if ws_module._k8s_workspace_reaper_task is not None:
        ws_module._k8s_workspace_reaper_task.cancel()
        try:
            await ws_module._k8s_workspace_reaper_task
        except Exception:
            pass
        ws_module._k8s_workspace_reaper_task = None

    ws_module._k8s_workspace_cache.clear()
    ws_module._k8s_workspace_refcounts.clear()
    ws_module._k8s_workspace_last_used.clear()
    ws_module._k8s_workspace_locks.clear()

    try:
        mock_ws = MagicMock()
        mock_ws.is_alive = True
        mock_ws.close = AsyncMock()

        with patch(
            "app.services.ai.runtime.agentscope.workspace._policy_k8s_workspace",
            new=AsyncMock(return_value=mock_ws),
        ):
            k8s_key = "/data::reap_user::k8s"
            await ws_module._acquire_k8s_workspace(
                root="/data",
                user_key="reap_user",
                skill_paths=[],
            )
            assert ws_module._k8s_workspace_refcounts[k8s_key] == 1

            # Arc the last_used timestamp back so the workspace is idle.
            ws_module._k8s_workspace_last_used[k8s_key] = (
                ws_module.time.monotonic()
                - ws_module.K8S_WORKSPACE_IDLE_SECONDS
                - 5
            )
            reaped = await ws_module.reap_idle_k8s_workspaces(
                idle_seconds=ws_module.K8S_WORKSPACE_IDLE_SECONDS
            )
            assert reaped == 1
            mock_ws.close.assert_awaited_once()
            assert k8s_key not in ws_module._k8s_workspace_cache

        # start/stop wiring and idempotency
        task1 = ws_module.start_k8s_workspace_reaper()
        assert task1 is not None and not task1.done()
        task2 = ws_module.start_k8s_workspace_reaper()
        assert task2 is task1  # idempotent
        await ws_module.stop_k8s_workspace_reaper()
        assert ws_module._k8s_workspace_reaper_task is None

        # start() raises on non-positive interval
        with pytest.raises(ValueError):
            ws_module.start_k8s_workspace_reaper(interval_seconds=0)
    finally:
        if ws_module._k8s_workspace_reaper_task is not None:
            ws_module._k8s_workspace_reaper_task.cancel()
            try:
                await ws_module._k8s_workspace_reaper_task
            except Exception:
                pass
            ws_module._k8s_workspace_reaper_task = None
        ws_module._k8s_workspace_cache.clear()
        ws_module._k8s_workspace_refcounts.clear()
        ws_module._k8s_workspace_last_used.clear()
        ws_module._k8s_workspace_locks.clear()


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


@pytest.mark.asyncio
async def test_k8s_workspace_status_best_effort_running_when_cache_missed(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_module
    from app.services.ai.runtime.agentscope.workspace import k8s_workspace_status

    async def fake_get(key, default=None):
        return "agent-sandboxes" if key == "sandbox_k8s_namespace" else "k8s"

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    async def fake_root():
        return "/data"

    monkeypatch.setattr(ws_module, "resolve_workspace_root", fake_root)

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

    result = await k8s_workspace_status(
        user_id=1,
        user_name="alice",
        conversation_id="conv-1",
    )
    assert result["status"] == "running"
    assert result["running"] is True
    assert result["pod_name"] == "as-ws-alice--1"
    assert result["started_at"] == "2026-09-09T10:00:00+00:00"


@pytest.mark.asyncio
async def test_build_host_only_workspace_initializes_local(tmp_path, monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_module
    from app.services.ai.runtime.agentscope.workspace import build_host_only_workspace

    fake_local = MagicMock()
    fake_local.initialize = AsyncMock()

    async def fake_root():
        return str(tmp_path)

    monkeypatch.setattr(ws_module, "resolve_workspace_root", fake_root)
    monkeypatch.setattr(ws_module, "resolve_session_workdir", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(ws_module, "discover_platform_skill_paths", lambda **kwargs: [])
    monkeypatch.setattr(ws_module, "_preseed_session_skills", lambda *a, **k: None)
    monkeypatch.setattr("agentscope.workspace.LocalWorkspace", lambda **kwargs: fake_local)

    ws = await build_host_only_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="conv-1",
    )
    assert ws is fake_local
    fake_local.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_exec_k8s_sandbox_command_returns_output_and_workdir():
    import re

    from app.services.ai.runtime.agentscope.k8s_workspace import exec_k8s_sandbox_command

    core = MagicMock()

    def fake_exec(name, namespace, container, command, **kw):
        cmd = command[-1] if isinstance(command, list) and command else ""
        m = re.search(r"(__NANZI_PWD_[0-9a-f]+__)", cmd or "")
        marker = m.group(1) if m else "x"
        return f"hello world\n{marker}:/workspace\n"

    core.connect_get_namespaced_pod_exec = AsyncMock(side_effect=fake_exec)

    mock_client = MagicMock()
    mock_client.CoreV1Api.return_value = core
    mock_config = MagicMock()
    mock_config.load_incluster_config.return_value = None
    mock_ws = MagicMock()
    mock_ws.WsApiClient.return_value = MagicMock(close=AsyncMock())

    k8s = MagicMock()
    k8s.client = mock_client
    k8s.config = mock_config
    stream = MagicMock()
    stream.ws_client = mock_ws

    with patch.dict("sys.modules", {
        "kubernetes_asyncio": k8s,
        "kubernetes_asyncio.client": mock_client,
        "kubernetes_asyncio.config": mock_config,
        "kubernetes_asyncio.stream": stream,
        "kubernetes_asyncio.stream.ws_client": mock_ws,
    }):
        result = await exec_k8s_sandbox_command(
            namespace="agent-sandboxes",
            pod_name="as-ws-admin--1",
            command="ls",
        )
    assert result["output"] == "hello world"
    assert result["workdir"] == "/workspace"
    assert result["exit_code"] is None
    assert isinstance(result["duration_ms"], int)


@pytest.mark.asyncio
async def test_exec_k8s_workspace_command_derives_pod_and_returns(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_module
    from app.services.ai.runtime.agentscope.workspace import exec_k8s_workspace_command

    await _patch_k8s_policy(monkeypatch)

    async def fake_root():
        return "/data"

    monkeypatch.setattr(ws_module, "resolve_workspace_root", fake_root)
    ws_module._k8s_workspace_cache.clear()

    async def fake_exec_cmd(namespace, pod_name, command, workdir=None):
        return {
            "output": "hi",
            "stdout": "hi",
            "stderr": "",
            "exit_code": None,
            "duration_ms": 5,
            "workdir": "/workspace",
        }

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.k8s_workspace.exec_k8s_sandbox_command",
        fake_exec_cmd,
    )

    result = await exec_k8s_workspace_command(
        user_id=1,
        user_name="alice",
        conversation_id="conv-1",
        command="ls",
    )
    assert result["pod_name"] == "as-ws-alice--1"
    assert result["execution_backend"] == "k8s"
    assert result["output"] == "hi"


@pytest.mark.asyncio
async def test_read_k8s_sandbox_pod_terminating_sets_deleting():
    from datetime import datetime, timezone

    from app.services.ai.runtime.agentscope.k8s_workspace import read_k8s_sandbox_pod

    pod_status = MagicMock()
    pod_status.phase = "Running"
    pod_status.start_time = None
    pod_status.container_statuses = []
    pod = MagicMock()
    pod.metadata.creation_timestamp = None
    pod.metadata.deletion_timestamp = datetime.now(timezone.utc)
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
    assert result["found"] is True
    assert result["deleting"] is True


@pytest.mark.asyncio
async def test_k8s_workspace_status_terminating_reports_stopping(monkeypatch):
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
        return {
            "available": True,
            "found": True,
            "phase": "Running",
            "start_time": "2026-09-09T10:00:00+00:00",
            "ready": False,
            "deleting": True,
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
    assert result["status"] == "stopping"
    assert result["running"] is False


@pytest.mark.asyncio
async def test_k8s_workspace_status_best_effort_terminating_reports_stopping(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_module
    from app.services.ai.runtime.agentscope.workspace import k8s_workspace_status

    async def fake_get(key, default=None):
        return "agent-sandboxes" if key == "sandbox_k8s_namespace" else "k8s"

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    async def fake_root():
        return "/data"

    monkeypatch.setattr(ws_module, "resolve_workspace_root", fake_root)

    async def fake_probe(namespace, pod_name):
        return {
            "available": True,
            "found": True,
            "phase": "Running",
            "start_time": "2026-09-09T10:00:00+00:00",
            "ready": False,
            "deleting": True,
        }

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.k8s_workspace.read_k8s_sandbox_pod",
        fake_probe,
    )
    ws_module._k8s_workspace_cache.clear()

    result = await k8s_workspace_status(
        user_id=1,
        user_name="alice",
        conversation_id="conv-1",
    )
    assert result["status"] == "stopping"
    assert result["running"] is False
    assert result["pod_name"] == "as-ws-alice--1"
