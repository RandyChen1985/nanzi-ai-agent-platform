"""沙箱存活判断：容器被外部删除要能自愈，并发判定过期只能逐出一次。

回归两个真实故障：

1. **404 不自愈**：``is_alive`` 是 AgentScope 的普通布尔属性，只在 ``initialize()`` /
   ``close()`` 时改写。容器被 ``docker rm``、daemon prune、OOM 或别的实例
   ``create_or_replace`` 掉之后它仍是 True，平台于是把 ``No such container`` 一直抛给
   工具层——同一个容器 ID 连续报错，直到用户重启容器为止。
2. **并发互删**：同一台沙箱上两个请求同时判定「技能快照过期」，后到者若无条件逐出，
   会把先到者刚重建好的新容器当过期删掉，先到者的后续 Bash 全部 404。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import types
from typing import Any

import pytest

from app.services.ai.runtime.agentscope import workspace as ws

pytestmark = pytest.mark.no_infrastructure


class _FakeContainer:
    """只实现被探测用到的 ``show()``。"""

    def __init__(self, *, exists: bool = True, status: int = 404) -> None:
        self.exists = exists
        self.status = status
        self.show_calls = 0

    async def show(self) -> dict[str, Any]:
        self.show_calls += 1
        if not self.exists:
            exc = RuntimeError("No such container: deadbeef")
            exc.status = self.status  # type: ignore[attr-defined]
            raise exc
        return {"State": {"Running": True}}


class _FakeWorkspace:
    def __init__(self, *, exists: bool = True) -> None:
        self.is_alive = True
        self._container = _FakeContainer(exists=exists)
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        self.is_alive = False


@pytest.fixture(autouse=True)
def _clean_cache():
    ws.clear_workspace_cache()
    yield
    ws.clear_workspace_cache()


def _cache_key() -> str:
    return ws._docker_workspace_cache_key("/tmp/ws-root", "alice", ["/skills/a"])


async def test_probe_detects_missing_container() -> None:
    assert await ws._docker_workspace_container_alive(_FakeWorkspace(exists=False)) is False


async def test_probe_treats_other_errors_as_alive() -> None:
    """探测本身失败（500/超时/权限）不能触发重建：重建会换掉一台可能好着的容器。"""

    class _Boom:
        async def show(self) -> None:
            exc = RuntimeError("dockerd hiccup")
            exc.status = 500  # type: ignore[attr-defined]
            raise exc

    workspace = _FakeWorkspace()
    workspace._container = _Boom()  # type: ignore[assignment]
    assert await ws._docker_workspace_container_alive(workspace) is True


async def test_probe_falls_back_to_is_alive_without_container() -> None:
    workspace = _FakeWorkspace()
    workspace._container = None  # type: ignore[assignment]
    assert await ws._docker_workspace_container_alive(workspace) is True
    workspace.is_alive = False
    assert await ws._docker_workspace_container_alive(workspace) is False


async def test_acquire_rebuilds_when_container_disappeared(monkeypatch) -> None:
    """容器被外部删除：命中缓存也必须重建，并摘掉引用它的会话级条目。"""
    cache_key = _cache_key()
    dead = _FakeWorkspace(exists=False)
    ws._docker_workspace_cache[cache_key] = dead
    ws._docker_workspace_refcounts[cache_key] = 1
    ws._docker_workspace_verified_at[cache_key] = 0.0  # 探测 TTL 已过
    ws._workspace_cache["/tmp/ws-root/alice/c1::all::docker"] = (dead, None)
    ws._workspace_sandbox_refs["/tmp/ws-root/alice/c1::all::docker"] = cache_key

    fresh = _FakeWorkspace()
    built: list[Any] = []

    async def fake_policy(_skill_paths, **kwargs):
        built.append(kwargs)
        return fresh

    monkeypatch.setattr(ws, "_policy_docker_workspace", fake_policy)

    workspace, returned_key = await ws._acquire_docker_workspace(
        root="/tmp/ws-root", user_key="alice", skill_paths=["/skills/a"]
    )

    assert returned_key == cache_key
    assert workspace is fresh
    assert dead.closed is True
    assert ws._docker_workspace_cache[cache_key] is fresh
    # 会话级条目必须一起摘掉：留着它下一轮会把死沙箱又交出去
    assert "/tmp/ws-root/alice/c1::all::docker" not in ws._workspace_cache
    assert "/tmp/ws-root/alice/c1::all::docker" not in ws._workspace_sandbox_refs
    assert len(built) == 1


async def test_acquire_reuses_cache_within_probe_ttl(monkeypatch) -> None:
    """TTL 内不重复探测：热路径不能每次命中都发一次 Docker RPC。"""
    cache_key = _cache_key()
    alive = _FakeWorkspace()
    ws._docker_workspace_cache[cache_key] = alive
    ws._docker_workspace_verified_at[cache_key] = ws.time.monotonic()

    async def fake_policy(_skill_paths, **kwargs):  # pragma: no cover - 不该被调用
        raise AssertionError("TTL 内不应重建容器")

    monkeypatch.setattr(ws, "_policy_docker_workspace", fake_policy)

    workspace, _ = await ws._acquire_docker_workspace(
        root="/tmp/ws-root", user_key="alice", skill_paths=["/skills/a"]
    )

    assert workspace is alive
    assert alive._container.show_calls == 0
    assert ws._docker_workspace_refcounts[cache_key] == 1


async def test_acquire_probes_once_per_ttl(monkeypatch) -> None:
    cache_key = _cache_key()
    alive = _FakeWorkspace()
    ws._docker_workspace_cache[cache_key] = alive
    ws._docker_workspace_verified_at[cache_key] = ws.time.monotonic() - (
        ws.DOCKER_WORKSPACE_ALIVE_PROBE_TTL_SECONDS + 1
    )

    workspace, _ = await ws._acquire_docker_workspace(
        root="/tmp/ws-root", user_key="alice", skill_paths=["/skills/a"]
    )

    assert workspace is alive
    assert alive._container.show_calls == 1
    # 探测成功要刷新时间戳，否则下一次命中又会再问一遍
    assert cache_key in ws._docker_workspace_verified_at


async def test_evict_is_skipped_when_entry_was_refreshed(monkeypatch) -> None:
    """``expected_last_used`` 不匹配说明条目已被别人重建：不能再关它。"""
    cache_key = _cache_key()
    replaced = _FakeWorkspace()
    ws._docker_workspace_cache[cache_key] = replaced
    ws._docker_workspace_locks[cache_key] = asyncio.Lock()
    ws._docker_workspace_last_used[cache_key] = 200.0

    evicted = await ws._evict_docker_workspace_cache_entry(
        cache_key,
        reason="skill snapshot stale",
        expected_last_used=100.0,  # 我判定过期时看到的是 100
    )

    assert evicted == 0
    assert replaced.closed is False
    assert ws._docker_workspace_cache[cache_key] is replaced


async def test_evict_closes_entry_when_expected_matches() -> None:
    cache_key = _cache_key()
    doomed = _FakeWorkspace()
    ws._docker_workspace_cache[cache_key] = doomed
    ws._docker_workspace_locks[cache_key] = asyncio.Lock()
    ws._docker_workspace_last_used[cache_key] = 100.0

    evicted = await ws._evict_docker_workspace_cache_entry(
        cache_key,
        reason="skill snapshot stale",
        expected_last_used=100.0,
    )

    assert evicted == 1
    assert doomed.closed is True
    assert cache_key not in ws._docker_workspace_cache
    assert cache_key not in ws._docker_workspace_verified_at


async def test_probe_timeout_is_treated_as_alive(monkeypatch) -> None:
    """探测卡住不能锁死该沙箱的所有请求：超时按「还活着」处理。"""

    class _Slow:
        async def show(self) -> None:
            await asyncio.sleep(5.0)

    monkeypatch.setattr(ws, "DOCKER_WORKSPACE_ALIVE_PROBE_TIMEOUT_SECONDS", 0.01)
    workspace = _FakeWorkspace()
    workspace._container = _Slow()  # type: ignore[assignment]

    assert await ws._docker_workspace_container_alive(workspace) is True


def test_workspace_id_parts_splits_prefix_and_token() -> None:
    assert ws._workspace_id_parts("ppx002__14-be26ba6a8fde") == (
        "ppx002__14",
        "be26ba6a8fde",
    )
    assert ws._workspace_id_parts("alice-noskills") == ("alice", "noskills")
    # 历史命名不带令牌：那是共用的 sandbox/skills 单份布局，不归对账回收
    assert ws._workspace_id_parts("alice") is None


async def test_orphan_reconcile_reclaims_skills_dir(monkeypatch, tmp_path) -> None:
    """对账删掉孤儿容器后，必须顺手回收它留在磁盘上的技能集合目录。

    对账走 ``container.delete(force=True)``，不经过逐出函数，而目录回收只挂在逐出
    路径上——不补这一下，进程每重启一次 ``sandbox/skills-<令牌>`` 就多留一份。
    """
    layout = "0123456789ab"
    user_dir = tmp_path / "alice"
    skills_dir = user_dir / "sandbox" / f"skills-{layout}"
    (skills_dir / ".seed" / "demo").mkdir(parents=True)
    (skills_dir / ".seed" / "demo" / "SKILL.md").write_text("x", encoding="utf-8")
    old = time.time() - 100_000
    os.utime(skills_dir, (old, old))
    os.utime(user_dir / "sandbox", (old, old))

    class _FakeContainer:
        def __init__(self) -> None:
            self.id = "abcdef"
            self.deleted = False
            self._container = {
                "Created": time.time() - 100_000,
                "Labels": {
                    "agentscope.workspace": "true",
                    "agentscope.workspace.id": f"alice-{layout}",
                },
                "Names": [f"/as_ws_alice-{layout}"],
            }

        async def delete(self, force: bool = False) -> None:
            self.deleted = True

    container = _FakeContainer()

    class _FakeContainers:
        async def list(self, *, all: bool = True, filters: Any = None):
            return [container]

    class _FakeClient:
        def __init__(self) -> None:
            self.containers = _FakeContainers()

        async def close(self) -> None:
            return None

    monkeypatch.setitem(
        sys.modules, "aiodocker", types.SimpleNamespace(Docker=_FakeClient)
    )

    async def fake_root() -> str:
        return str(tmp_path)

    monkeypatch.setattr(ws, "resolve_workspace_root", fake_root)

    removed = await ws.reconcile_orphan_docker_workspaces(min_age_seconds=0)

    assert removed == 1
    assert container.deleted is True
    assert not skills_dir.exists()
    assert ws._workspace_id_parts("alice") is None


async def test_reaper_reclaims_immediately_when_no_session_holds() -> None:
    """没有会话持有的沙箱仍按原阈值回收（放宽只针对 refcount > 0）。"""
    cache_key = _cache_key()
    workspace = _FakeWorkspace()
    ws._docker_workspace_cache[cache_key] = workspace
    ws._docker_workspace_locks[cache_key] = asyncio.Lock()
    ws._docker_workspace_refcounts[cache_key] = 0
    ws._docker_workspace_last_used[cache_key] = 0.0
    ws._docker_workspace_verified_at[cache_key] = 0.0

    reaped = await ws.reap_idle_docker_workspaces(idle_seconds=30, now=31)

    assert reaped == 1
    assert workspace.closed is True


async def test_reaper_widens_threshold_for_held_workspace() -> None:
    """会话仍持有时阈值放宽，1 倍阈值内不回收、4 倍之后仍回收。"""
    cache_key = _cache_key()
    workspace = _FakeWorkspace()
    ws._docker_workspace_cache[cache_key] = workspace
    ws._docker_workspace_locks[cache_key] = asyncio.Lock()
    ws._docker_workspace_refcounts[cache_key] = 1
    ws._docker_workspace_last_used[cache_key] = 0.0
    ws._docker_workspace_verified_at[cache_key] = 0.0

    assert (
        await ws.reap_idle_docker_workspaces(idle_seconds=30, now=31) == 0
    )
    assert workspace.closed is False
    assert cache_key in ws._docker_workspace_cache

    widened = 30 * ws.DOCKER_WORKSPACE_REFERENCED_IDLE_MULTIPLIER + 1
    assert (
        await ws.reap_idle_docker_workspaces(idle_seconds=30, now=widened) == 1
    )
    assert workspace.closed is True
