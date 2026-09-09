import pytest
from unittest.mock import AsyncMock, patch, MagicMock

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_policy_k8s_workspace_build_and_initialize():
    from app.services.ai.runtime.agentscope.workspace import (
        _policy_k8s_workspace,
        SANDBOX_POLICY_K8S,
    )

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
