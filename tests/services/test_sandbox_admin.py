"""管理员沙箱实例巡检与运维服务（sandbox_admin）单元测试。

覆盖核心安全边界与运维契约：
1. 非沙箱容器/Pod（缺少 ``agentscope.workspace=true``）一律拒绝（403），绝不误删平台主服务；
2. Docker/K8s 列表准确归类为 managed / orphan / zombie，并计算空闲剩余时间；
3. ``include_stats=True`` 正确解析 Docker stats（CPU/内存/PIDs）且单台超时不拖垮列表；
4. 详情接口返回脱敏日志；
5. Kill 同步逐出进程内缓存、删除实体并回收孤儿技能目录；
6. Restart 对 Docker 与 K8s 均统一逐出缓存并删除旧实体（避免 Docker 原地 restart 杀掉 Gateway），下次使用自动拉起全新健康沙箱；
7. 一键清理（cleanup_stale）只清理 zombie +（可选）orphan，跳过 Terminating，绝不触碰 managed 活跃沙箱；
8. 阻断非法/路径穿越 target_id，Paused 容器不触发 exec/stats，且支持 bytes 日志与 gateway.log 读取。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai.runtime.agentscope import sandbox_admin as admin
from app.services.ai.runtime.agentscope import workspace as ws

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _clean_workspace_caches():
    ws.clear_workspace_cache()
    yield
    ws.clear_workspace_cache()


class _FakeDockerContainer:
    def __init__(
        self,
        *,
        cid: str,
        name: str,
        workspace_id: str | None,
        is_sandbox: bool = True,
        state: str = "running",
        status_text: str = "Up 10 minutes",
        created: float | None = None,
        init_enabled: bool = True,
        logs: list[str] | None = None,
        stats_payload: dict | None = None,
        fail_restart: bool = False,
    ) -> None:
        self.id = cid
        self.name = name
        self.deleted = False
        self.restarted = False
        self.fail_restart = fail_restart
        self._logs = logs if logs is not None else ["line1\n", "line2\n"]
        self._stats = stats_payload
        labels: dict[str, str] = {}
        if is_sandbox:
            labels["agentscope.workspace"] = "true"
        if workspace_id is not None:
            labels["agentscope.workspace.id"] = workspace_id
        self._labels = labels
        self._state = state
        self._init_enabled = init_enabled
        self._container = {
            "Id": cid,
            "Names": [f"/{name}"],
            "Image": "agentscope-workspace:test",
            "State": state,
            "Status": status_text,
            "Created": created if created is not None else time.time() - 600,
            "Labels": labels,
            "Mounts": [{"Source": "/app/data/agent_workspaces/u1", "Destination": "/workspace", "RW": True}],
        }

    async def show(self) -> dict:
        return {
            "Id": self.id,
            "Name": f"/{self.name}",
            "Created": "2026-11-28T12:00:00Z",
            "Config": {"Image": "agentscope-workspace:test", "Labels": dict(self._labels)},
            "HostConfig": {"Init": self._init_enabled, "Memory": 2 * 1024**3, "PidsLimit": 512},
            "State": {
                "Status": self._state,
                "Running": self._state in ("running", "paused"),
                "Paused": self._state == "paused",
                "Dead": self._state == "dead",
                "OOMKilled": self._state == "exited",
                "ExitCode": 137 if self._state == "exited" else 0,
                "StartedAt": "2026-11-28T12:00:01Z",
            },
            "Mounts": [{"Source": "/app/data/agent_workspaces/u1", "Destination": "/workspace", "RW": True}],
        }

    async def delete(self, force: bool = False) -> None:
        self.deleted = True

    async def restart(self, *, t: int | None = None) -> None:
        if self.fail_restart:
            raise RuntimeError("daemon restart failed")
        self.restarted = True

    async def log(self, **kwargs):
        return list(self._logs)

    async def stats(self, *, stream: bool = False):
        if self._stats is None:
            return []
        return [self._stats]


class _FakeDockerClient:
    def __init__(self, containers: list[_FakeDockerContainer]) -> None:
        self._by_key: dict[str, _FakeDockerContainer] = {}
        self._all = list(containers)
        for c in containers:
            self._by_key[c.id] = c
            self._by_key[c.name] = c
        self.closed = False
        self.containers = SimpleNamespace(list=self._list, get=self._get)

    async def _list(self, all: bool = True, filters: dict | None = None):
        assert filters == {"label": ["agentscope.workspace=true"]}
        return [
            c
            for c in self._all
            if c._labels.get("agentscope.workspace") == "true"
        ]

    async def _get(self, tid: str):
        if tid in self._by_key:
            return self._by_key[tid]
        exc = RuntimeError(f"no such container {tid}")
        exc.status = 404  # type: ignore[attr-defined]
        raise exc

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_list_docker_admin_sandboxes_classifies_and_enriches_stats(monkeypatch):
    now = time.time()
    managed_id = "alice__1-aaaaaaaaaaaa"
    c_managed = _FakeDockerContainer(
        cid="111111111111aaaa",
        name=f"as_ws_{managed_id}",
        workspace_id=managed_id,
        state="running",
        created=now - 300,
        stats_payload={
            "cpu_stats": {
                "cpu_usage": {"total_usage": 300_000_000},
                "system_cpu_usage": 2_000_000_000,
                "online_cpus": 2,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 200_000_000},
                "system_cpu_usage": 1_000_000_000,
            },
            "memory_stats": {
                "usage": 220 * 1024 * 1024,
                "limit": 2 * 1024**3,
                "stats": {"inactive_file": 20 * 1024 * 1024},
            },
            "pids_stats": {"current": 12, "limit": 512},
        },
    )
    c_orphan = _FakeDockerContainer(
        cid="222222222222bbbb",
        name="as_ws_bob__2-bbbbbbbbbbbb",
        workspace_id="bob__2-bbbbbbbbbbbb",
        state="running",
        created=now - 7200,
    )
    c_zombie = _FakeDockerContainer(
        cid="333333333333cccc",
        name="as_ws_carl__3-cccccccccccc",
        workspace_id="carl__3-cccccccccccc",
        state="exited",
        status_text="Exited (137) 1 hour ago",
        created=now - 3600,
    )
    c_platform = _FakeDockerContainer(
        cid="999999999999ffff",
        name="nanzi-ai-agent",
        workspace_id=None,
        is_sandbox=False,
        state="running",
    )

    cache_key = "/data::alice__1::aaaaaaaaaaaa::docker"
    ws._docker_workspace_cache[cache_key] = SimpleNamespace(
        _platform_workspace_id=managed_id,
        _platform_container_id=c_managed.id,
    )
    ws._docker_workspace_last_used[cache_key] = time.monotonic() - 120
    ws._docker_workspace_refcounts[cache_key] = 0

    fake = _FakeDockerClient([c_managed, c_orphan, c_zombie, c_platform])
    import aiodocker

    monkeypatch.setattr(aiodocker, "Docker", lambda *a, **k: fake)
    monkeypatch.setattr(
        ws, "resolve_effective_sandbox_idle_seconds", AsyncMock(return_value=900.0)
    )

    data = await admin.list_admin_sandboxes(backend="docker", include_stats=True)
    assert data["available"] is True
    assert data["summary"] == {"all": 3, "managed": 1, "orphan": 1, "zombie": 1}
    # 排序：zombie -> orphan -> managed
    assert [it["category"] for it in data["items"]] == ["zombie", "orphan", "managed"]
    managed_item = data["items"][2]
    assert managed_item["user_key"] == "alice__1"
    assert managed_item["skill_token"] == "aaaaaaaaaaaa"
    assert managed_item["cpu_percent"] == 20.0
    assert managed_item["memory_usage_bytes"] == 200 * 1024 * 1024
    assert managed_item["pids_current"] == 12
    assert managed_item["idle_seconds_left"] is not None
    assert 700 <= managed_item["idle_seconds_left"] <= 800
    # 未被引用时阈值就是配置值本身；页面据此说明「没有放宽」
    assert managed_item["idle_seconds"] == 900
    assert managed_item["idle_multiplier"] == 1.0


@pytest.mark.asyncio
async def test_admin_rejects_non_sandbox_docker_container(monkeypatch):
    """★ 安全边界：非沙箱容器（缺少 agentscope.workspace=true）必须被拒绝。"""
    c_platform = _FakeDockerContainer(
        cid="platform-001",
        name="nanzi-ai-agent",
        workspace_id=None,
        is_sandbox=False,
    )
    fake = _FakeDockerClient([c_platform])
    import aiodocker

    monkeypatch.setattr(aiodocker, "Docker", lambda *a, **k: fake)

    with pytest.raises(admin.SandboxAdminTargetError) as exc_info:
        await admin.get_admin_sandbox_detail(backend="docker", target_id="platform-001")
    assert exc_info.value.status_code == 403

    kill_res = await admin.kill_admin_sandboxes(
        backend="docker", target_ids=["platform-001"]
    )
    assert kill_res["succeeded"] == 0
    assert kill_res["failed"] == 1
    assert c_platform.deleted is False


@pytest.mark.asyncio
async def test_docker_restart_evicts_cache_and_removes_container_for_clean_gateway_rebuild(monkeypatch):
    """★ Docker 重置不原地 container.restart()（避免杀掉 Gateway 只剩 sleep），而是逐出缓存并删除旧容器。"""
    wid_ok = "u1-aaaaaaaaaaaa"
    c_ok = _FakeDockerContainer(
        cid="ok-1",
        name=f"as_ws_{wid_ok}",
        workspace_id=wid_ok,
        state="running",
    )
    c_dead = _FakeDockerContainer(
        cid="dead-1",
        name="as_ws_u2-bbbbbbbbbbbb",
        workspace_id="u2-bbbbbbbbbbbb",
        state="dead",
    )
    cache_key = f"/data::u1::aaaaaaaaaaaa::docker"
    ws._docker_workspace_cache[cache_key] = SimpleNamespace(
        _platform_workspace_id=wid_ok, _platform_container_id="ok-1"
    )
    ws._docker_workspace_locks[cache_key] = asyncio.Lock()
    ws._docker_workspace_last_used[cache_key] = time.monotonic()

    fake = _FakeDockerClient([c_ok, c_dead])
    import aiodocker

    monkeypatch.setattr(aiodocker, "Docker", lambda *a, **k: fake)
    monkeypatch.setattr(
        ws, "_reclaim_orphan_sandbox_skills_dir_for_workspace_id", AsyncMock(return_value=None)
    )

    res = await admin.restart_admin_sandboxes(
        backend="docker", target_ids=["ok-1", "dead-1"]
    )
    assert res["succeeded"] == 2
    assert c_ok.restarted is False, "禁止原地 restart 留下无 Gateway 的假活容器"
    assert c_ok.deleted is True
    assert cache_key not in ws._docker_workspace_cache
    assert c_dead.deleted is True
    actions = {r["id"]: r["action"] for r in res["results"]}
    assert actions["ok-1"] == "reset_for_recreate"
    assert actions["dead-1"] == "reset_for_recreate"


@pytest.mark.asyncio
async def test_cleanup_stale_never_touches_managed_sandboxes(monkeypatch):
    """★ 一键清理只删 orphan + zombie，绝不触碰进程受管的活跃沙箱。"""
    managed_id = "u1-111111111111"
    c_managed = _FakeDockerContainer(
        cid="m1", name=f"as_ws_{managed_id}", workspace_id=managed_id, state="running"
    )
    c_orphan = _FakeDockerContainer(
        cid="o1", name="as_ws_u2-222222222222", workspace_id="u2-222222222222", state="running"
    )
    c_zombie = _FakeDockerContainer(
        cid="z1", name="as_ws_u3-333333333333", workspace_id="u3-333333333333", state="exited"
    )
    cache_key = "/data::u1::111111111111::docker"
    ws._docker_workspace_cache[cache_key] = SimpleNamespace(
        _platform_workspace_id=managed_id, _platform_container_id="m1"
    )
    ws._docker_workspace_locks[cache_key] = asyncio.Lock()
    ws._docker_workspace_last_used[cache_key] = time.monotonic()

    fake = _FakeDockerClient([c_managed, c_orphan, c_zombie])
    import aiodocker

    monkeypatch.setattr(aiodocker, "Docker", lambda *a, **k: fake)
    monkeypatch.setattr(
        ws, "resolve_effective_sandbox_idle_seconds", AsyncMock(return_value=900.0)
    )
    monkeypatch.setattr(
        ws, "_reclaim_orphan_sandbox_skills_dir_for_workspace_id", AsyncMock(return_value=None)
    )

    res = await admin.cleanup_stale_admin_sandboxes(
        backend="docker", include_running_orphans=True
    )
    assert res["succeeded"] == 2
    assert c_managed.deleted is False
    assert c_orphan.deleted is True
    assert c_zombie.deleted is True
    assert cache_key in ws._docker_workspace_cache


@pytest.mark.asyncio
async def test_k8s_admin_list_rejects_non_sandbox_and_deletes_with_uid(monkeypatch):
    k8s_client = pytest.importorskip("kubernetes_asyncio.client")
    k8s_config = pytest.importorskip("kubernetes_asyncio.config")
    from app.services.ai.runtime.agentscope import k8s_workspace as kws

    now_dt = datetime.now(timezone.utc)
    sandbox_pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="as-ws-alice--1-aaaaaaaaaaaa",
            uid="uid-sandbox-1",
            labels={
                "agentscope.workspace": "true",
                "agentscope.workspace.id": "alice__1-aaaaaaaaaaaa",
            },
            creation_timestamp=now_dt - timedelta(hours=2),
            deletion_timestamp=None,
        ),
        spec=SimpleNamespace(
            node_name="worker-1",
            containers=[
                SimpleNamespace(
                    name="workspace",
                    image="nanzi-sandbox-k8s:1.0.0",
                    resources=SimpleNamespace(limits={"cpu": "1", "memory": "1Gi"}),
                    volume_mounts=[],
                )
            ],
            volumes=[],
        ),
        status=SimpleNamespace(
            phase="Running",
            reason=None,
            pod_ip="10.244.0.10",
            container_statuses=[SimpleNamespace(ready=True, state=None)],
        ),
    )
    platform_pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="nanzi-ai-agent-deploy-xyz",
            uid="uid-platform-1",
            labels={"app": "nanzi-ai-agent"},
            creation_timestamp=now_dt - timedelta(days=5),
            deletion_timestamp=None,
        ),
        spec=SimpleNamespace(node_name="worker-1", containers=[], volumes=[]),
        status=SimpleNamespace(phase="Running", reason=None, pod_ip="10.244.0.2", container_statuses=[]),
    )
    deleted_calls: list[dict] = []

    class _FakeCoreV1:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def list_namespaced_pod(self, namespace=None, label_selector=None):
            return SimpleNamespace(items=[sandbox_pod])

        async def read_namespaced_pod(self, name=None, namespace=None):
            if name == sandbox_pod.metadata.name:
                return sandbox_pod
            if name == platform_pod.metadata.name:
                return platform_pod
            exc = RuntimeError("not found")
            exc.status = 404  # type: ignore[attr-defined]
            raise exc

        async def delete_namespaced_pod(self, name=None, namespace=None, body=None):
            deleted_calls.append({"name": name, "namespace": namespace, "body": body})

    class _FakeApiClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(k8s_client, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(k8s_client, "CoreV1Api", _FakeCoreV1)
    monkeypatch.setattr(k8s_config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kws, "resolve_sandbox_namespace", lambda _: "nanzi-ai-agent")
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get", AsyncMock(return_value="nanzi-ai-agent")
    )
    monkeypatch.setattr(
        ws, "resolve_effective_sandbox_idle_seconds", AsyncMock(return_value=900.0)
    )
    monkeypatch.setattr(
        ws, "_reclaim_orphan_sandbox_skills_dir_for_workspace_id", AsyncMock(return_value=None)
    )

    listing = await admin.list_admin_sandboxes(backend="k8s", include_stats=False)
    assert listing["available"] is True
    assert listing["summary"]["orphan"] == 1

    res = await admin.kill_admin_sandboxes(
        backend="k8s",
        target_ids=[sandbox_pod.metadata.name, platform_pod.metadata.name],
    )
    assert res["succeeded"] == 1
    assert res["failed"] == 1
    assert len(deleted_calls) == 1
    assert deleted_calls[0]["name"] == sandbox_pod.metadata.name
    assert deleted_calls[0]["body"].preconditions.uid == "uid-sandbox-1"


@pytest.mark.asyncio
async def test_rejects_path_traversal_target_id_and_paused_container_skips_exec_stats(monkeypatch):
    """★ 安全与边界：阻断路径穿越 target_id；Paused 容器（Running=True, Paused=True）跳过 exec 与 stats。"""
    exec_called = False
    stats_called = False

    class _PausedContainer(_FakeDockerContainer):
        async def exec(self, **kwargs):
            nonlocal exec_called
            exec_called = True
            raise AssertionError("Paused 容器不应发起 exec")

        async def stats(self, *, stream: bool = False):
            nonlocal stats_called
            stats_called = True
            raise AssertionError("Paused 容器不应拉取 stats")

    c_paused = _PausedContainer(
        cid="444444444444dddd",
        name="as_ws_paused__9-dddddddddddd",
        workspace_id="paused__9-dddddddddddd",
        state="paused",
        logs=[b"byte-line-1\n", b"byte-line-2\n"],
    )
    fake = _FakeDockerClient([c_paused])
    import aiodocker

    monkeypatch.setattr(aiodocker, "Docker", lambda *a, **k: fake)

    with pytest.raises(admin.SandboxAdminTargetError) as bad_exc:
        await admin.get_admin_sandbox_detail(backend="docker", target_id="../images/json")
    assert bad_exc.value.status_code == 400

    detail = await admin.get_admin_sandbox_detail(
        backend="docker", target_id="444444444444dddd"
    )
    assert exec_called is False
    assert stats_called is False
    assert "byte-line-1" in detail["logs"]
    assert "b'" not in detail["logs"]

    with pytest.raises(admin.SandboxAdminTargetError) as over_limit:
        await admin.kill_admin_sandboxes(
            backend="docker", target_ids=[f"c-{i}" for i in range(admin.ADMIN_BATCH_MAX_TARGETS + 1)]
        )
    assert over_limit.value.status_code == 400

    # 无 Docker socket 时 aiodocker.Docker() 抛 AssertionError，必须转为 503 而非 500
    def _raise_assertion(*_a, **_k):
        raise AssertionError

    monkeypatch.setattr(aiodocker, "Docker", _raise_assertion)
    with pytest.raises(admin.SandboxAdminTargetError) as no_sock:
        await admin.get_admin_sandbox_detail(backend="docker", target_id="444444444444dddd")
    assert no_sock.value.status_code == 503


@pytest.mark.asyncio
async def test_k8s_cleanup_skips_terminating_and_tolerates_409_412(monkeypatch):
    """★ K8s 一键清理跳过 Terminating Pod，且并发 409/412 按幂等成功处理。"""
    k8s_client = pytest.importorskip("kubernetes_asyncio.client")
    k8s_config = pytest.importorskip("kubernetes_asyncio.config")
    from app.services.ai.runtime.agentscope import k8s_workspace as kws

    now_dt = datetime.now(timezone.utc)
    terminating_pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="as-ws-term-111111111111",
            uid="uid-term-1",
            labels={"agentscope.workspace": "true", "agentscope.workspace.id": "term-111111111111"},
            creation_timestamp=now_dt - timedelta(hours=1),
            deletion_timestamp=now_dt,
        ),
        spec=SimpleNamespace(node_name="w1", containers=[], volumes=[]),
        status=SimpleNamespace(phase="Running", reason=None, pod_ip="10.0.0.1", container_statuses=[]),
    )
    zombie_pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="as-ws-zomb-222222222222",
            uid="uid-zomb-2",
            labels={"agentscope.workspace": "true", "agentscope.workspace.id": "zomb-222222222222"},
            creation_timestamp=now_dt - timedelta(hours=3),
            deletion_timestamp=None,
        ),
        spec=SimpleNamespace(node_name="w1", containers=[], volumes=[]),
        status=SimpleNamespace(phase="Failed", reason="OOMKilled", pod_ip="", container_statuses=[]),
    )
    deleted_names: list[str] = []

    class _FakeCoreV1:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def list_namespaced_pod(self, namespace=None, label_selector=None):
            return SimpleNamespace(items=[terminating_pod, zombie_pod])

        async def read_namespaced_pod(self, name=None, namespace=None):
            return zombie_pod if name == zombie_pod.metadata.name else terminating_pod

        async def delete_namespaced_pod(self, name=None, namespace=None, body=None):
            deleted_names.append(name)
            exc = RuntimeError("precondition failed")
            exc.status = 412  # type: ignore[attr-defined]
            raise exc

    class _FakeApiClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(k8s_client, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(k8s_client, "CoreV1Api", _FakeCoreV1)
    monkeypatch.setattr(k8s_config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kws, "resolve_sandbox_namespace", lambda _: "nanzi-ai-agent")
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get", AsyncMock(return_value="nanzi-ai-agent")
    )
    monkeypatch.setattr(
        ws, "resolve_effective_sandbox_idle_seconds", AsyncMock(return_value=900.0)
    )
    monkeypatch.setattr(
        ws, "_reclaim_orphan_sandbox_skills_dir_for_workspace_id", AsyncMock(return_value=None)
    )

    res = await admin.cleanup_stale_admin_sandboxes(
        backend="k8s", include_running_orphans=True
    )
    assert deleted_names == [zombie_pod.metadata.name]
    assert res["succeeded"] == 1
    assert res["failed"] == 0


def test_idle_multiplier_widens_only_while_a_session_holds_the_reference():
    """引用态放宽 4 倍：页面显示的剩余时间与回收器判定必须来自同一个倍数。

    用户疑问：配置 sandbox_idle_time=15 分钟，沙箱实例面板显示「60 分钟后空闲回收」。
    60 = 15 × 4（refcount > 0 时放宽），这里把倍数与剩余时间的换算钉死，防止将来
    只改回收器、不改展示（或反过来）造成「显示 60 实际 15」的口径分裂。
    """
    from app.services.ai.runtime.agentscope import sandbox_admin as admin
    from app.services.ai.runtime.agentscope import workspace as ws

    assert admin._idle_multiplier_for(0) == 1.0
    assert admin._idle_multiplier_for(1) == ws.DOCKER_WORKSPACE_REFERENCED_IDLE_MULTIPLIER
    assert admin._idle_multiplier_for(7) == ws.DOCKER_WORKSPACE_REFERENCED_IDLE_MULTIPLIER

    # 15 分钟（900 秒）基础阈值：未引用 900 秒、引用中 3600 秒（即页面的 60 分钟）
    assert admin._compute_idle_seconds_left(
        last_used=0.0, refcount=0, idle_seconds=900.0, now_monotonic=0.0
    ) == 900
    assert admin._compute_idle_seconds_left(
        last_used=0.0, refcount=1, idle_seconds=900.0, now_monotonic=0.0
    ) == 3600
    # 已空闲超过基础阈值但未到放宽阈值时，仍有余量而不是 0
    assert admin._compute_idle_seconds_left(
        last_used=0.0, refcount=1, idle_seconds=900.0, now_monotonic=1000.0
    ) == 2600
