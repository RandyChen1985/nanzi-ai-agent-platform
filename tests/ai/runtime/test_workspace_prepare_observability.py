"""工作区准备（get_local_workspace）的分段测量、单飞与线程化 IO。

背景：`工作区准备` 是会话运行时构建里最长、最不稳定的一段（截图里出现过 61s），但
此前只有一个总耗时，慢在沙箱获取、技能播种还是会话初始化全靠猜。这里的契约是：

1. **分段测量**：子阶段（config/skills/sandbox/preseed/session）与关键事实（策略、
   技能数、沙箱是新建还是复用）都要能被调用方拿到，并且慢的时候日志里能看出来；
2. **单飞**：preflight 预热用 10s/60s 超时包着同一个调用，超时只应结束「这一方不再
   等」，绝不能把已经铺好的技能快照一起作废、让构建段从头再干一遍；
3. **不阻塞事件循环**：技能发现与技能播种是同步磁盘 IO，必须在工作线程里跑。
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock

import pytest

from app.services.ai.runtime.agentscope import workspace as ws
from app.services.ai.runtime.agentscope.workspace import (
    SANDBOX_POLICY_DOCKER,
    SANDBOX_POLICY_LOCAL,
    WorkspacePrepareTrace,
    get_local_workspace,
)

pytestmark = pytest.mark.no_infrastructure


def _empty_stats() -> dict[str, int]:
    return {"total": 0, "copied": 0, "refreshed": 0, "pruned": 0, "removed": 0}


@pytest.fixture(autouse=True)
def _clean_state():
    ws.clear_workspace_cache()
    yield
    ws.clear_workspace_cache()


def _patch_common(monkeypatch, tmp_path, *, policy: str = SANDBOX_POLICY_LOCAL) -> None:
    """把配置、技能库、权限全部就地替换，测试不碰 DB/Redis 与真实技能目录。"""
    from app.services.config_service import ConfigService

    monkeypatch.setattr(
        ConfigService, "get", AsyncMock(return_value=policy)
    )
    monkeypatch.setattr(
        ws, "resolve_workspace_root", AsyncMock(return_value=str(tmp_path))
    )
    monkeypatch.setattr(ws, "discover_platform_skill_paths", lambda **kwargs: [])
    monkeypatch.setattr(
        "app.services.ai.skills.policy.get_forbidden_skill_ids",
        AsyncMock(return_value=set()),
    )


async def _prepare(**kwargs):
    return await get_local_workspace(
        user_id="101",
        user_name="tester",
        user_info={"user_id": 101, "user_name": "tester"},
        conversation_id="conv-trace",
        **kwargs,
    )


# ── 1. 分段测量 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prepare_trace_reports_stages_notes_and_summary(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ws,
        "_preseed_session_skills",
        lambda *a, **k: {
            "total": 4,
            "copied": 3,
            "refreshed": 0,
            "pruned": 0,
            "removed": 0,
        },
    )

    trace = WorkspacePrepareTrace()
    pair = await _prepare(prepare_trace=trace)

    assert pair is not None
    # 子阶段覆盖整条链路，顺序即真实执行顺序
    assert [name for name, _ms in trace.stages] == [
        "config",
        "skills",
        "sandbox",
        "preseed",
        "session",
    ]
    assert all(ms >= 0 for _name, ms in trace.stages)
    assert trace.notes["policy"] == SANDBOX_POLICY_LOCAL
    assert trace.notes["skills"] == 0
    # local 策略没有沙箱
    assert trace.notes["sandbox"] == "none"
    # 只有真正搬了技能才写这一格（0 值不占位，摘要才读得顺）
    assert trace.notes["copied"] == 3
    assert "refreshed" not in trace.notes
    assert trace.total_ms >= 0

    summary = trace.summary()
    assert "策略=local" in summary
    assert "技能=0" in summary
    assert "新播种=3" in summary


@pytest.mark.asyncio
async def test_prepare_trace_records_lazy_sandbox_without_starting_it(tmp_path, monkeypatch):
    """docker 策略且沙箱未存活：只拿到惰性代理，摘要必须写「待按需拉起」。"""
    _patch_common(monkeypatch, tmp_path, policy=SANDBOX_POLICY_DOCKER)
    monkeypatch.setattr(ws, "_preseed_session_skills", lambda *a, **k: _empty_stats())
    acquire = AsyncMock()
    monkeypatch.setattr(ws, "_acquire_docker_workspace", acquire)

    trace = WorkspacePrepareTrace()
    pair = await _prepare(lazy_sandbox=True, prepare_trace=trace)

    assert pair is not None
    acquire.assert_not_awaited()
    assert trace.notes["sandbox"] == "lazy"
    assert "待按需拉起" in trace.summary()


@pytest.mark.asyncio
async def test_cache_hit_produces_no_sub_stage_measurement(tmp_path, monkeypatch):
    """缓存命中是几十毫秒的路径，不该凭空产出子阶段（否则时间线会误导人）。"""
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(ws, "_preseed_session_skills", lambda *a, **k: _empty_stats())

    await _prepare()
    second = WorkspacePrepareTrace()
    await _prepare(prepare_trace=second)

    assert second.stages == []
    assert second.total_ms == 0.0
    assert second.summary() == ""


@pytest.mark.asyncio
async def test_slow_prepare_is_logged_with_breakdown(tmp_path, monkeypatch, caplog):
    """超过阈值必须留一条带分解的 WARNING——否则线上只能靠猜。"""
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(ws, "_WORKSPACE_PREPARE_SLOW_WARN_MS", 0.0)
    monkeypatch.setattr(ws, "_preseed_session_skills", lambda *a, **k: _empty_stats())

    with caplog.at_level("WARNING", logger="app.services.ai.runtime.agentscope.workspace"):
        await _prepare()

    messages = [record.getMessage() for record in caplog.records if "工作区准备" in record.getMessage()]
    assert messages, "慢构建没有打出可追的日志"
    assert "策略=local" in messages[-1]


# ── 2. 单飞：预热超时不作废已做的工作 ──────────────────────────


@pytest.mark.asyncio
async def test_prewarm_timeout_does_not_discard_the_inflight_build(tmp_path, monkeypatch):
    """预热 10s 超时只结束「预热这一方」，构建段接续同一次工作、不重播技能。"""
    _patch_common(monkeypatch, tmp_path)
    preseed_calls: list[int] = []

    def _slow_preseed(*_args, **_kwargs):
        time.sleep(0.4)
        preseed_calls.append(1)
        return _empty_stats()

    monkeypatch.setattr(ws, "_preseed_session_skills", _slow_preseed)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_prepare(prepare_trace=WorkspacePrepareTrace()), timeout=0.1)
    # 超时只是这一方退出：构建任务仍在跑，没被取消
    assert ws._workspace_inflight, "构建任务被预热超时取消掉了"

    trace = WorkspacePrepareTrace()
    pair = await _prepare(prepare_trace=trace)

    assert pair is not None
    assert len(preseed_calls) == 1, "构建段把预热已经干过的活重干了一遍"
    assert trace.joined_inflight is True
    assert "接续预热中的构建" in trace.summary()


@pytest.mark.asyncio
async def test_concurrent_prepare_builds_once(tmp_path, monkeypatch):
    """同一 cache_key 的并发调用只构建一次，两个调用方拿到同一对工作区。"""
    _patch_common(monkeypatch, tmp_path)
    preseed_calls: list[int] = []

    def _preseed(*_args, **_kwargs):
        preseed_calls.append(1)
        return _empty_stats()

    monkeypatch.setattr(ws, "_preseed_session_skills", _preseed)

    first, second = await asyncio.gather(_prepare(), _prepare())

    assert first is not None and second is not None
    assert first is second
    assert len(preseed_calls) == 1


@pytest.mark.asyncio
async def test_failed_build_is_shared_and_retried_next_time(tmp_path, monkeypatch):
    """失败由所有等待者共享，且失败不会被单飞表粘住（下一次重新构建）。"""
    _patch_common(monkeypatch, tmp_path, policy=SANDBOX_POLICY_DOCKER)
    build_calls: list[int] = []

    async def _boom(**_kwargs):
        build_calls.append(1)
        await asyncio.sleep(0)
        raise RuntimeError("docker daemon unreachable")

    monkeypatch.setattr(ws, "_acquire_docker_workspace", _boom)

    results = await asyncio.gather(
        _prepare(lazy_sandbox=False), _prepare(lazy_sandbox=False), return_exceptions=True
    )

    assert all(isinstance(item, ws.DockerSandboxUnavailableError) for item in results), results
    assert len(build_calls) == 1, "两个等待者各自建了一遍"
    # 失败后单飞表不残留，下一次会重新尝试
    assert not ws._workspace_inflight
    with pytest.raises(ws.DockerSandboxUnavailableError):
        await _prepare(lazy_sandbox=False)
    assert len(build_calls) == 2


@pytest.mark.asyncio
async def test_cancelled_waiter_keeps_build_alive_for_later_callers(tmp_path, monkeypatch):
    """调用方被取消（客户端断开）时，构建不能被连带取消。"""
    _patch_common(monkeypatch, tmp_path)

    def _slow_preseed(*_args, **_kwargs):
        time.sleep(0.25)
        return _empty_stats()

    monkeypatch.setattr(ws, "_preseed_session_skills", _slow_preseed)

    waiter = asyncio.ensure_future(_prepare())
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    pair = await _prepare()
    assert pair is not None


# ── 3. 同步磁盘 IO 必须离开事件循环 ────────────────────────────


@pytest.mark.asyncio
async def test_preseed_and_discovery_run_off_the_event_loop_thread(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def _discover(**_kwargs):
        seen["discovery"] = threading.get_ident()
        return []

    def _preseed(*_args, **_kwargs):
        seen["preseed"] = threading.get_ident()
        return _empty_stats()

    monkeypatch.setattr(ws, "discover_platform_skill_paths", _discover)
    monkeypatch.setattr(ws, "_preseed_session_skills", _preseed)

    await _prepare()

    assert seen["discovery"] != loop_thread, "技能发现仍在事件循环线程里跑"
    assert seen["preseed"] != loop_thread, "技能播种仍在事件循环线程里跑"


@pytest.mark.asyncio
async def test_host_only_degradation_also_runs_off_the_event_loop_thread(tmp_path, monkeypatch):
    """沙箱降级路径同样在首 token 之前，不能拿同步 IO 卡住事件循环。"""
    _patch_common(monkeypatch, tmp_path)
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def _preseed(*_args, **_kwargs):
        seen["preseed"] = threading.get_ident()
        return _empty_stats()

    monkeypatch.setattr(ws, "_preseed_session_skills", _preseed)

    local_ws = await ws.build_host_only_workspace(
        user_id="101",
        user_name="tester",
        user_info={"user_id": 101, "user_name": "tester"},
        conversation_id="conv-degrade",
    )

    assert local_ws is not None
    assert seen["preseed"] != loop_thread
