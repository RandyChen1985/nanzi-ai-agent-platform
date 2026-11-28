"""沙箱销毁机制的两条补强：执行期心跳 + 孤儿容器/Pod 对账。

背景（本次审计发现）：
1. 空闲回收只看 ``last_used``，而它此前只在「拉起沙箱/缓存命中/Bash 调用开始前」
   刷新：一条执行超过 ``sandbox_idle_time`` 的命令会被 reaper 关掉正在用的沙箱。
2. 回收只遍历进程内字典，进程重启或技能集合令牌变化后留下的容器/Pod 再也无人认领
   （``as_ws_<user>`` 这类历史命名连同名替换都蹭不到），会永久驻留。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.ai.runtime.agentscope import workspace as ws

pytestmark = pytest.mark.no_infrastructure


def _container(
    *,
    workspace_id: str,
    created: float,
    name: str,
    state: str | None = None,
) -> SimpleNamespace:
    raw: dict = {
        "Labels": {
            "agentscope.workspace": "true",
            "agentscope.workspace.id": workspace_id,
        },
        "Created": created,
        "Names": [f"/{name}"],
    }
    if state is not None:
        raw["State"] = state
    item = SimpleNamespace(
        _container=raw,
        id=name,
        deleted=False,
    )

    async def _delete(force: bool = False) -> None:
        item.deleted = True

    item.delete = _delete
    return item


class _FakeDocker:
    def __init__(self, items: list[SimpleNamespace]) -> None:
        self.closed = False
        self.last_filters = None

        async def _close() -> None:
            self.closed = True

        self.containers = SimpleNamespace(
            list=self._list, close=_close
        )
        self._items = items

    async def _list(self, all: bool = True, filters: dict | None = None):
        import json
        from aiodocker.utils import httpize

        self.last_filters = filters
        # 复刻真实 Docker Engine API 对 /containers/json?filters=... 的严格校验：
        # aiodocker.containers.DockerContainers.list 不调用 clean_filters，
        # 若 filters 里的值是裸字符串而非列表，Docker Daemon 会 100% 返回 400。
        if filters is not None:
            encoded = httpize({"all": all, "filters": filters})
            decoded = json.loads(encoded["filters"])
            for key, val in decoded.items():
                if not isinstance(val, list):
                    exc = RuntimeError(
                        f"json: cannot unmarshal {type(val).__name__} into Go value of type []string (key={key})"
                    )
                    exc.status = 400  # type: ignore[attr-defined]
                    raise exc
        return list(self._items)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_caches():
    ws.clear_workspace_cache()
    yield
    ws.clear_workspace_cache()


@pytest.mark.asyncio
async def test_reconcile_reclaims_only_untracked_and_old_containers(monkeypatch):
    """活跃条目对应的容器不删；不在注册表里且创建够久的容器删掉。"""
    now = time.time()
    live_id = "user__1-aaaaaaaaaaaa"
    live = _container(workspace_id=live_id, created=now - 7200, name=f"as_ws_{live_id}")
    legacy_orphan = _container(
        workspace_id="user__1", created=now - 7200, name="as_ws_user__1"
    )
    fresh_orphan = _container(
        workspace_id="user__2-bbbbbbbbbbbb", created=now - 30, name="as_ws_user__2-bbbbbbbbbbbb"
    )

    ws._docker_workspace_cache[f"root::{live_id}::aaaaaaaaaaaa::docker"] = SimpleNamespace(
        _platform_workspace_id=live_id
    )

    import aiodocker

    fake = _FakeDocker([live, legacy_orphan, fresh_orphan])
    monkeypatch.setattr(aiodocker, "Docker", lambda *a, **k: fake)

    removed = await ws.reconcile_orphan_docker_workspaces(now=now)

    assert removed == 1
    assert live.deleted is False, "注册表内的活跃沙箱不能被对账回收"
    assert legacy_orphan.deleted is True, "脱离注册表的历史命名容器必须被回收"
    assert fresh_orphan.deleted is False, "刚创建的沙箱必须留出宽限，不能被误删"
    assert fake.closed is True


@pytest.mark.asyncio
async def test_reconcile_skips_when_creation_time_is_unknown(monkeypatch):
    """拿不到创建时间时保守跳过：宁可留一台孤儿也不误删刚拉起的沙箱。"""
    orphan = _container(workspace_id="user__9", created=0.0, name="as_ws_user__9")

    import aiodocker

    monkeypatch.setattr(aiodocker, "Docker", lambda *a, **k: _FakeDocker([orphan]))

    assert await ws.reconcile_orphan_docker_workspaces(now=time.time()) == 0
    assert orphan.deleted is False


@pytest.mark.asyncio
async def test_orphan_reconcile_flag_reads_config(monkeypatch):
    async def _fake_get(key, default=None):
        assert key == ws.SANDBOX_ORPHAN_RECONCILE_KEY
        return "false"

    monkeypatch.setattr("app.services.config_service.ConfigService.get", _fake_get)
    assert await ws._orphan_reconcile_enabled() is False

    async def _dummy_get(key, default=None):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("app.services.config_service.ConfigService.get", _dummy_get)
    assert await ws._orphan_reconcile_enabled() is True, "读不到配置时按开启处理"


@pytest.mark.asyncio
async def test_sandbox_activity_heartbeat_refreshes_last_used(monkeypatch):
    """心跳必须在命令执行期间持续刷新活跃时间（否则长命令会被 reaper 关掉）。"""
    cache_key = "root::u1::aaaaaaaaaaaa::docker"
    ws._docker_workspace_cache[cache_key] = SimpleNamespace(
        _platform_workspace_id="u1-aaaaaaaaaaaa"
    )
    ws._docker_workspace_last_used[cache_key] = 0.0

    async def _fake_idle(*, default_seconds: float) -> float:
        return 0.02

    # 解析函数曾名 _effective_*、后提升为公共名 resolve_effective_*；心跳内部按当前
    # 文件里的那个名字调用，两个名字都接管，断言才不会因重命名而失效。
    monkeypatch.setattr(
        ws, "resolve_effective_sandbox_idle_seconds", _fake_idle, raising=False
    )
    monkeypatch.setattr(ws, "_effective_sandbox_idle_seconds", _fake_idle, raising=False)
    monkeypatch.setattr(ws, "SANDBOX_ACTIVITY_HEARTBEAT_MIN_SECONDS", 0.01)

    async with ws._sandbox_activity_heartbeat(cache_key=cache_key):
        # 人为清零：进入上下文时的那一次 touch 不算，只有心跳能把它刷新回来
        ws._docker_workspace_last_used[cache_key] = 0.0
        await asyncio.sleep(0.06)

    assert ws._docker_workspace_last_used[cache_key] > 0.0


@pytest.mark.asyncio
async def test_sandbox_activity_heartbeat_is_noop_without_key():
    """没有沙箱键（纯宿主策略）时上下文必须安全直通。"""
    async with ws._sandbox_activity_heartbeat():
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_lazy_bash_tool_heartbeats_during_long_command(monkeypatch):
    """真实 Bash 入口：命令执行期间必须持续刷新活跃时间。"""
    cache_key = "root::u1::aaaaaaaaaaaa::docker"
    ws._docker_workspace_cache[cache_key] = SimpleNamespace(
        _platform_workspace_id="u1-aaaaaaaaaaaa"
    )

    class _FakeRealTool:
        async def __call__(self, **kwargs):
            # 模拟一条长命令：执行期间没有任何显式 touch
            ws._docker_workspace_last_used[cache_key] = 0.0
            await asyncio.sleep(0.06)
            return "ok"

    async def _fake_idle(*, default_seconds: float) -> float:
        return 0.02

    # 解析函数曾名 _effective_*、后提升为公共名 resolve_effective_*；心跳内部按当前
    # 文件里的那个名字调用，两个名字都接管，断言才不会因重命名而失效。
    monkeypatch.setattr(
        ws, "resolve_effective_sandbox_idle_seconds", _fake_idle, raising=False
    )
    monkeypatch.setattr(ws, "_effective_sandbox_idle_seconds", _fake_idle, raising=False)
    monkeypatch.setattr(ws, "SANDBOX_ACTIVITY_HEARTBEAT_MIN_SECONDS", 0.01)

    proxy = ws.LazySandboxWorkspaceProxy(
        policy="docker", root="root", user_key="u1", skill_paths=None
    )
    proxy._sandbox_cache_key = cache_key
    tool = ws.LazySandboxBashNativeTool(proxy)

    async def _resolve() -> object:
        return _FakeRealTool()

    monkeypatch.setattr(tool, "_resolve_real_tool", _resolve)

    assert await tool(command="sleep 3600") == "ok"
    assert ws._docker_workspace_last_used[cache_key] > 0.0


@pytest.mark.asyncio
async def test_reconcile_k8s_reclaims_untracked_pod(monkeypatch):
    """K8s 侧同构：不在注册表里且创建够久的 Pod 被删除。"""
    k8s_client = pytest.importorskip("kubernetes_asyncio.client")
    k8s_config = pytest.importorskip("kubernetes_asyncio.config")
    from app.services.ai.runtime.agentscope import k8s_workspace as kws

    old = datetime.now(timezone.utc) - timedelta(hours=3)
    orphan_pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="as-ws-user--1",
            labels={"agentscope.workspace.id": "user__1"},
            creation_timestamp=old,
        )
    )
    live_pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="as-ws-user--1-aaaaaaaaaaaa",
            labels={"agentscope.workspace.id": "user__1-aaaaaaaaaaaa"},
            creation_timestamp=old,
        )
    )

    deleted: list[str] = []

    class _FakeCoreV1:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def list_namespaced_pod(self, namespace=None, label_selector=None):
            return SimpleNamespace(items=[orphan_pod, live_pod])

        async def delete_namespaced_pod(self, name=None, namespace=None):
            deleted.append(name)

    class _FakeApiClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(k8s_client, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(k8s_client, "CoreV1Api", _FakeCoreV1)
    monkeypatch.setattr(k8s_config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kws, "resolve_sandbox_namespace", lambda configured: "agent-sandboxes")

    async def _fake_get(key, default=None):
        return default

    monkeypatch.setattr("app.services.config_service.ConfigService.get", _fake_get)

    ws._k8s_workspace_cache["root::user__1::aaaaaaaaaaaa::k8s"] = SimpleNamespace(
        _platform_workspace_id="user__1-aaaaaaaaaaaa"
    )

    assert await ws.reconcile_orphan_k8s_workspaces() == 1
    assert deleted == ["as-ws-user--1"]


@pytest.mark.asyncio
async def test_docker_label_filters_serialize_as_list_for_docker_api():
    """★ 回归：containers.list 的 filters 必须序列化为 map[string][]string，否则 Docker 返回 400。"""
    import json
    from aiodocker.utils import httpize

    now = time.time()
    c1 = _container(
        workspace_id="alice__1-aaaaaaaaaaaa",
        created=now - 100,
        name="as_ws_alice__1-aaaaaaaaaaaa",
    )
    fake = _FakeDocker([c1])

    names = await ws._docker_sandbox_container_names(fake, user_key="alice__1")
    assert "as_ws_alice__1-aaaaaaaaaaaa" in names
    assert fake.last_filters == {"label": ["agentscope.workspace=true"]}
    encoded = httpize({"all": True, "filters": fake.last_filters})
    assert json.loads(encoded["filters"]) == {"label": ["agentscope.workspace=true"]}


@pytest.mark.asyncio
async def test_reconcile_docker_reclaims_zombies_even_when_cached_or_orphan_switch_off(monkeypatch):
    """★ 明确终态/卡死容器（exited/dead/created 超时）即使在缓存里或关闭运行中孤儿对账也要清理。"""
    now = time.time()
    cached_exited_id = "user__1-111111111111"
    dead_orphan_id = "user__2-222222222222"
    stuck_created_id = "user__3-333333333333"
    fresh_created_id = "user__4-444444444444"
    running_orphan_id = "user__5-555555555555"

    cached_exited = _container(
        workspace_id=cached_exited_id,
        created=now - 120,
        name=f"as_ws_{cached_exited_id}",
        state="exited",
    )
    dead_orphan = _container(
        workspace_id=dead_orphan_id,
        created=now - 120,
        name=f"as_ws_{dead_orphan_id}",
        state="dead",
    )
    stuck_created = _container(
        workspace_id=stuck_created_id,
        created=now - 400,
        name=f"as_ws_{stuck_created_id}",
        state="created",
    )
    fresh_created = _container(
        workspace_id=fresh_created_id,
        created=now - 30,
        name=f"as_ws_{fresh_created_id}",
        state="created",
    )
    running_orphan = _container(
        workspace_id=running_orphan_id,
        created=now - 7200,
        name=f"as_ws_{running_orphan_id}",
        state="running",
    )

    closed_cached = False

    class _FakeCachedWs:
        _platform_workspace_id = cached_exited_id

        async def close(self) -> None:
            nonlocal closed_cached
            closed_cached = True

    cache_key = f"/tmp/root::user__1::111111111111::docker"
    ws._docker_workspace_cache[cache_key] = _FakeCachedWs()
    ws._docker_workspace_locks[cache_key] = asyncio.Lock()
    ws._docker_workspace_last_used[cache_key] = time.monotonic()

    import aiodocker

    fake = _FakeDocker(
        [cached_exited, dead_orphan, stuck_created, fresh_created, running_orphan]
    )
    monkeypatch.setattr(aiodocker, "Docker", lambda *a, **k: fake)

    # 模拟多副本关闭运行中孤儿对账：只清僵死态，绝不动运行中的 running_orphan
    removed = await ws.reconcile_orphan_docker_workspaces(
        now=now, reclaim_running_orphans=False
    )

    assert removed == 3
    assert cached_exited.deleted is True
    assert closed_cached is True
    assert cache_key not in ws._docker_workspace_cache
    assert dead_orphan.deleted is True
    assert stuck_created.deleted is True
    assert fresh_created.deleted is False, "刚创建还在启动宽限期的容器不能误删"
    assert running_orphan.deleted is False, "关闭运行中孤儿对账时不能误删运行中容器"


@pytest.mark.asyncio
async def test_reconcile_k8s_reclaims_zombie_pods_even_when_cached_or_orphan_switch_off(monkeypatch):
    """★ K8s Failed/Evicted/Succeeded 及超时 Pending Pod 即使在缓存内或关孤儿开关也必须清理。"""
    k8s_client = pytest.importorskip("kubernetes_asyncio.client")
    k8s_config = pytest.importorskip("kubernetes_asyncio.config")
    from app.services.ai.runtime.agentscope import k8s_workspace as kws

    now_dt = datetime.now(timezone.utc)
    cached_failed_id = "user__1-111111111111"

    def _pod(name: str, wid: str, age_seconds: float, phase: str, reason: str = ""):
        return SimpleNamespace(
            metadata=SimpleNamespace(
                name=name,
                labels={"agentscope.workspace.id": wid},
                creation_timestamp=now_dt - timedelta(seconds=age_seconds),
                deletion_timestamp=None,
            ),
            status=SimpleNamespace(phase=phase, reason=reason),
        )

    pods = [
        _pod("as-ws-cached-evicted", cached_failed_id, 120, "Failed", "Evicted"),
        _pod("as-ws-succeeded", "user__2-222222222222", 120, "Succeeded"),
        _pod("as-ws-pending-stuck", "user__3-333333333333", 700, "Pending"),
        _pod("as-ws-pending-fresh", "user__4-444444444444", 30, "Pending"),
        _pod("as-ws-running-orphan", "user__5-555555555555", 7200, "Running"),
    ]
    deleted: list[str] = []

    class _FakeCoreV1:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def list_namespaced_pod(self, namespace=None, label_selector=None):
            return SimpleNamespace(items=pods)

        async def delete_namespaced_pod(self, name=None, namespace=None):
            deleted.append(name)

    class _FakeApiClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(k8s_client, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(k8s_client, "CoreV1Api", _FakeCoreV1)
    monkeypatch.setattr(k8s_config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kws, "resolve_sandbox_namespace", lambda configured: "nanzi-ai-agent")

    async def _fake_get(key, default=None):
        return default

    monkeypatch.setattr("app.services.config_service.ConfigService.get", _fake_get)

    closed_cached = False

    class _FakeCachedK8sWs:
        _platform_workspace_id = cached_failed_id

        async def close(self) -> None:
            nonlocal closed_cached
            closed_cached = True

    k_key = f"/tmp/root::user__1::111111111111::k8s"
    ws._k8s_workspace_cache[k_key] = _FakeCachedK8sWs()
    ws._k8s_workspace_locks[k_key] = asyncio.Lock()
    ws._k8s_workspace_last_used[k_key] = time.monotonic()

    removed = await ws.reconcile_orphan_k8s_workspaces(
        now=now_dt.timestamp(), reclaim_running_orphans=False
    )

    assert removed == 3
    assert deleted == ["as-ws-cached-evicted", "as-ws-succeeded", "as-ws-pending-stuck"]
    assert closed_cached is True
    assert k_key not in ws._k8s_workspace_cache


@pytest.mark.asyncio
async def test_stop_reapers_close_concurrently_and_timeout_does_not_block(monkeypatch):
    """★ shutdown 并发关闭所有沙箱，单台卡死超时不得拖死其它沙箱的清理。"""
    monkeypatch.setattr(ws, "SANDBOX_SHUTDOWN_CLOSE_TIMEOUT_SECONDS", 0.05)

    closed: list[str] = []

    class _SlowWs:
        async def close(self) -> None:
            await asyncio.sleep(5.0)

    class _FastWs:
        def __init__(self, tag: str) -> None:
            self.tag = tag

        async def close(self) -> None:
            await asyncio.sleep(0.01)
            closed.append(self.tag)

    for key, obj in (
        ("r::u1::aaaaaaaaaaaa::docker", _SlowWs()),
        ("r::u2::bbbbbbbbbbbb::docker", _FastWs("d2")),
        ("r::u3::cccccccccccc::docker", _FastWs("d3")),
    ):
        ws._docker_workspace_cache[key] = obj
        ws._docker_workspace_locks[key] = asyncio.Lock()

    start = time.monotonic()
    await ws.stop_docker_workspace_reaper()
    elapsed = time.monotonic() - start

    assert set(closed) == {"d2", "d3"}
    assert elapsed < 0.5, "并发关闭 + 单台超时保护应在毫秒级完成，不能串行卡死"
    assert not ws._docker_workspace_cache


@pytest.mark.asyncio
async def test_reconcile_zombie_does_not_evict_concurrently_rebuilt_docker_or_k8s(monkeypatch):
    """★ Review 防竞态回归：僵死扫描期间缓存若已被并发重建，绝不误逐出新容器/误删新 Pod。"""
    now = time.time()
    wid = "user__9-999999999999"
    old_exited = _container(
        workspace_id=wid, created=now - 120, name=f"as_ws_{wid}", state="exited"
    )
    old_exited.id = "old-container-id"

    rebuilt_closed = False

    class _RebuiltWs:
        _platform_workspace_id = wid
        _platform_container_id = "new-container-id"

        async def close(self) -> None:
            nonlocal rebuilt_closed
            rebuilt_closed = True

    cache_key = f"/tmp/root::user__9::999999999999::docker"
    ws._docker_workspace_cache[cache_key] = _RebuiltWs()
    ws._docker_workspace_locks[cache_key] = asyncio.Lock()
    ws._docker_workspace_last_used[cache_key] = 999.0

    import aiodocker

    monkeypatch.setattr(aiodocker, "Docker", lambda *a, **k: _FakeDocker([old_exited]))

    removed = await ws.reconcile_orphan_docker_workspaces(
        now=now, reclaim_running_orphans=False
    )

    assert removed == 1
    assert old_exited.deleted is True, "旧 ID 的僵死容器仍应被清理"
    assert rebuilt_closed is False, "缓存里已换成新容器 ID，绝不能误逐出关闭新容器"
    assert cache_key in ws._docker_workspace_cache
