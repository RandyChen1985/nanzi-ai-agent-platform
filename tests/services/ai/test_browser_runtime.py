import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

from app.schemas.browser import BrowserSnapshot, BrowserToolResult
from app.services.ai.browser.browser_runtime import BrowserControlConflict
from app.services.ai.browser.browser_runtime import BrowserRuntime
from app.services.ai.browser.browser_worker import BrowserPageInfo
from app.services.ai.browser.captcha_solver import (
    CAPTCHA_REASON_NO_VISION_MODEL,
    CaptchaSolveOutcome,
)


pytestmark = pytest.mark.no_infrastructure


class SerialProbeWorker:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.navigate_started = asyncio.Event()
        self.release_navigation = asyncio.Event()
        self.snapshot_started = asyncio.Event()

    def _enter(self):
        self.active += 1
        self.max_active = max(self.max_active, self.active)

    def _exit(self):
        self.active -= 1

    def has_session(self, _session_id):
        return True

    async def shutdown(self):
        return None

    async def navigate(self, _session_id, _url):
        self._enter()
        self.navigate_started.set()
        await self.release_navigation.wait()
        self._exit()
        return BrowserPageInfo(url="https://example.com/", title="Example")

    async def snapshot(self, session_id):
        self._enter()
        self.snapshot_started.set()
        self._exit()
        return BrowserSnapshot(
            session_id=session_id,
            snapshot_id="snapshot-1",
            url="https://example.com/",
            title="Example",
        )


class ControlProbeWorker:
    def __init__(self):
        self.manual_input = AsyncMock(
            return_value=BrowserPageInfo(url="https://example.com/", title="Example")
        )
        self.click = AsyncMock(
            return_value=BrowserToolResult(
                session_id="session-1",
                action="click",
                url="https://example.com/",
                title="Example",
            )
        )
        self.fill = AsyncMock(
            return_value=BrowserToolResult(
                session_id="session-1",
                action="fill",
                url="https://example.com/",
                title="Example",
            )
        )
        self.slider_drag = AsyncMock(
            return_value=BrowserToolResult(
                session_id="session-1",
                action="slider_drag",
                url="https://example.com/",
                title="Example",
                data={"distance_px": 190, "steps": 5, "measured_gap_px": None},
            )
        )

    def has_session(self, _session_id):
        return True

    async def shutdown(self):
        return None


@pytest.mark.asyncio
async def test_browser_runtime_serializes_navigation_and_snapshot_per_session():
    worker = SerialProbeWorker()
    runtime = BrowserRuntime(worker=worker)

    navigate_task = asyncio.create_task(runtime.navigate("session-1", "https://example.com/"))
    await worker.navigate_started.wait()
    snapshot_task = asyncio.create_task(runtime.snapshot("session-1"))
    await asyncio.sleep(0)

    assert worker.snapshot_started.is_set() is False

    worker.release_navigation.set()
    await asyncio.gather(navigate_task, snapshot_task)

    assert worker.max_active == 1


@pytest.mark.asyncio
async def test_browser_runtime_reuses_existing_page_when_target_url_is_unchanged(monkeypatch):
    worker = ControlProbeWorker()
    worker.current_page_info = AsyncMock(
        return_value=BrowserPageInfo(url="https://example.com/", title="Example")
    )
    worker.open = AsyncMock()
    worker.navigate = AsyncMock()
    runtime = BrowserRuntime(worker=worker)
    profile_service = Mock()
    profile_service.get_owned = AsyncMock(return_value=SimpleNamespace(id="profile-1"))
    profile_service.profile_path = AsyncMock(return_value="/tmp/browser-profile")
    monkeypatch.setattr(
        "app.services.ai.browser.browser_runtime.BrowserProfileService",
        lambda _db: profile_service,
    )
    session = SimpleNamespace(
        id="session-1",
        user_id=1,
        profile_id="profile-1",
        current_url="https://example.com/",
        page_title=None,
        last_seen_at=None,
        updated_at=None,
    )
    db = Mock()
    db.commit = AsyncMock()

    info = await runtime.open_session(db, session)

    assert info.url == "https://example.com/"
    worker.current_page_info.assert_awaited_once_with("session-1")
    worker.navigate.assert_not_awaited()
    worker.open.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_runtime_blocks_ai_until_human_releases_control():
    worker = ControlProbeWorker()
    runtime = BrowserRuntime(worker=worker)
    runtime._snapshots["session-1"] = BrowserSnapshot(
        session_id="session-1",
        snapshot_id="snapshot-1",
        url="https://example.com/",
        title="Example",
    )

    await runtime.manual_input("session-1", event="mouse_click", payload={"x": 10, "y": 10})
    assert runtime.control_state("session-1")["owner"] == "human"
    runtime._snapshots["session-1"] = BrowserSnapshot(
        session_id="session-1",
        snapshot_id="snapshot-1",
        url="https://example.com/",
        title="Example",
    )

    click_task = asyncio.create_task(
        runtime.click(
            "session-1",
            target_ref="e1",
            snapshot_id="snapshot-1",
            approval_mode="autopilot",
            confirmed=True,
        )
    )
    await asyncio.sleep(0)
    worker.click.assert_not_awaited()

    await runtime.release_human_control("session-1")
    await click_task
    worker.click.assert_awaited_once()
    assert runtime.control_state("session-1")["owner"] == "ai"


@pytest.mark.asyncio
async def test_browser_runtime_forwards_slider_drag_payload():
    worker = ControlProbeWorker()
    runtime = BrowserRuntime(worker=worker)
    runtime._snapshots["session-1"] = BrowserSnapshot(
        session_id="session-1",
        snapshot_id="snapshot-1",
        url="https://example.com/",
        title="Example",
    )

    result = await runtime.slider_drag(
        "session-1",
        source_ref="handle",
        snapshot_id="snapshot-1",
        distance_px=190,
        gap_target_ref="gap",
    )

    worker.slider_drag.assert_awaited_once()
    call_kwargs = worker.slider_drag.await_args.kwargs
    assert worker.slider_drag.await_args.args[0] == "session-1"
    assert call_kwargs["source_ref"] == "handle"
    assert call_kwargs["snapshot"].session_id == "session-1"
    assert call_kwargs["snapshot"].snapshot_id == "snapshot-1"
    assert call_kwargs["distance_px"] == 190
    assert call_kwargs["gap_target_ref"] == "gap"
    assert result.action == "slider_drag"
    assert result.data["distance_px"] == 190


@pytest.mark.asyncio
async def test_browser_runtime_invalidates_snapshot_after_cookie_injection():
    worker = ControlProbeWorker()
    worker.set_cookies = AsyncMock(
        return_value=BrowserToolResult(
            session_id="session-1",
            action="set_cookies",
            url="https://example.com/",
            title="Example",
        )
    )
    runtime = BrowserRuntime(worker=worker)
    runtime._snapshots["session-1"] = {
        "snapshot-1": BrowserSnapshot(
            session_id="session-1",
            snapshot_id="snapshot-1",
            url="https://example.com/",
            title="Example",
        )
    }

    await runtime.set_cookies("session-1", cookies=[{"name": "sid", "value": "secret"}])

    assert "session-1" not in runtime._snapshots


@pytest.mark.asyncio
async def test_browser_runtime_rejects_input_from_stale_viewer_owner():
    worker = ControlProbeWorker()
    runtime = BrowserRuntime(worker=worker)

    await runtime.manual_input(
        "session-1",
        event="mouse_click",
        payload={"x": 10, "y": 10},
        owner_id="viewer-old",
    )
    with pytest.raises(BrowserControlConflict):
        await runtime.manual_input(
            "session-1",
            event="mouse_click",
            payload={"x": 20, "y": 20},
            owner_id="viewer-new",
        )

    await runtime.release_human_control("session-1", owner_id="viewer-old")

    assert runtime.control_state("session-1")["owner"] == "ai"

    await runtime.manual_input(
        "session-1",
        event="mouse_click",
        payload={"x": 20, "y": 20},
        owner_id="viewer-new",
    )
    assert runtime.control_state("session-1")["owner"] == "human"

    await runtime.release_human_control("session-1", owner_id="viewer-new")

    assert runtime.control_state("session-1")["owner"] == "ai"


@pytest.mark.asyncio
async def test_browser_runtime_releases_unowned_captcha_control_for_viewer():
    worker = ControlProbeWorker()
    worker.snapshot = AsyncMock(
        return_value=BrowserSnapshot(
            session_id="session-1",
            snapshot_id="captcha-1",
            url="https://example.com/verify",
            title="Verify",
            page_state="captcha",
        )
    )
    runtime = BrowserRuntime(worker=worker)
    # 本用例只验证控制权归属，显式隔离自动解算，避免依赖真实多模态配置
    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(
        return_value=CaptchaSolveOutcome(
            solved=False,
            retryable=False,
            reason_code=CAPTCHA_REASON_NO_VISION_MODEL,
            message="未配置多模态模型",
        )
    )

    await runtime.snapshot("session-1")
    await runtime.release_human_control("session-1", owner_id="viewer-1")

    assert runtime.control_state("session-1")["owner"] == "ai"


@pytest.mark.asyncio
async def test_browser_runtime_keeps_recent_ai_snapshot_when_viewer_refreshes():
    worker = ControlProbeWorker()
    first = BrowserSnapshot(
        session_id="session-1",
        snapshot_id="snapshot-ai",
        url="https://example.com/",
        title="Example",
    )
    second = BrowserSnapshot(
        session_id="session-1",
        snapshot_id="snapshot-viewer",
        url="https://example.com/",
        title="Example",
    )
    worker.snapshot = AsyncMock(side_effect=[first, second])
    runtime = BrowserRuntime(worker=worker)

    await runtime.snapshot("session-1")
    await runtime.snapshot("session-1")

    assert runtime.cached_snapshot("session-1", "snapshot-ai") is first
    assert runtime.cached_snapshot("session-1", "snapshot-viewer") is second


@pytest.mark.asyncio
async def test_browser_runtime_scrolls_and_remembers_the_fresh_snapshot():
    worker = ControlProbeWorker()
    scrolled = BrowserSnapshot(
        session_id="session-1",
        snapshot_id="snapshot-scroll",
        url="https://example.com/",
        title="Example",
        scroll_y=640,
        document_height=2400,
    )
    worker.scroll = AsyncMock(return_value=scrolled)
    runtime = BrowserRuntime(worker=worker)

    result = await runtime.scroll("session-1", direction="down", amount=640)

    assert result is scrolled
    worker.scroll.assert_awaited_once_with("session-1", direction="down", amount=640)
    assert runtime.cached_snapshot("session-1", "snapshot-scroll") is scrolled


@pytest.mark.asyncio
async def test_browser_runtime_control_state_isolated_and_shutdown_clears_it():
    worker = ControlProbeWorker()
    runtime = BrowserRuntime(worker=worker)

    await runtime.manual_input("session-1", event="key", payload={"key": "Enter"})

    assert runtime.control_state("session-1")["owner"] == "human"
    assert runtime.control_state("session-2")["owner"] == "ai"

    await runtime.shutdown()

    assert runtime.control_state("session-1")["owner"] == "ai"


@pytest.mark.asyncio
async def test_browser_runtime_clears_captcha_flag_after_human_verification_completes():
    worker = ControlProbeWorker()
    worker.snapshot = AsyncMock(
        side_effect=[
            BrowserSnapshot(
                session_id="session-1",
                snapshot_id="captcha-1",
                url="https://example.com/",
                title="Verify",
                page_state="captcha",
            ),
            BrowserSnapshot(
                session_id="session-1",
                snapshot_id="ready-1",
                url="https://example.com/",
                title="Example",
                page_state="ready",
            ),
        ]
    )
    runtime = BrowserRuntime(worker=worker)
    # 本用例只验证人工完成验证后 captcha 标记被清除，显式隔离自动解算
    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(
        return_value=CaptchaSolveOutcome(
            solved=False,
            retryable=False,
            reason_code=CAPTCHA_REASON_NO_VISION_MODEL,
            message="未配置多模态模型",
        )
    )

    await runtime.snapshot("session-1")
    assert runtime.control_state("session-1")["captcha"] is True

    await runtime.snapshot("session-1")

    assert runtime.control_state("session-1") == {
        "owner": "human",
        "reason": "captcha",
        "captcha": False,
    }


@pytest.mark.asyncio
async def test_browser_runtime_broadcasts_captcha_geometry_progress():
    """解算过程中的几何信息必须广播出去，前端才能在静态截图上画出目标与轨迹。"""
    worker = ControlProbeWorker()
    worker.snapshot = AsyncMock(
        return_value=BrowserSnapshot(
            session_id="session-1",
            snapshot_id="captcha-1",
            url="https://example.com/verify",
            title="Verify",
            page_state="captcha",
        )
    )
    runtime = BrowserRuntime(worker=worker)

    async def fake_solve(session_id, snapshot, *, model_name=None, on_progress=None):
        assert on_progress is not None, "运行时必须向解算器传入进度回调"
        await on_progress({
            "phase": "recognized",
            "kind": "slider",
            "target_x": 320.0,
            "target_y": 220.0,
            "slider_x": 120.0,
            "slider_y": 220.0,
            "distance_px": 200,
        })
        return CaptchaSolveOutcome(solved=True, retryable=False, message="ok")

    runtime.captcha_solver.solve_captcha_detailed = fake_solve

    events: list[dict] = []

    async def fake_broadcast(session_id, payload):
        events.append(payload)

    runtime.broadcast_event = fake_broadcast

    await runtime.snapshot("session-1")

    progress_events = [
        e for e in events
        if e.get("type") == "ai_action"
        and e.get("action") == "solving_captcha"
        and e.get("extra")
    ]
    assert progress_events, "未广播带几何信息的解算进度"
    extra = progress_events[-1]["extra"]
    assert extra["target_x"] == 320.0
    assert extra["target_y"] == 220.0
    assert extra["distance_px"] == 200
    assert extra["kind"] == "slider"
    # 次数信息随进度一起下发，前端据此显示「第 N/3 次」
    assert extra["attempt"] == 1
    assert extra["max_attempts"] == 3


@pytest.mark.asyncio
async def test_browser_runtime_marks_captcha_human_required_with_reason():
    """降级为人工时必须广播原因，前端才能显示「AI 无法完成，请人工处理」卡片。"""
    worker = ControlProbeWorker()
    worker.snapshot = AsyncMock(
        return_value=BrowserSnapshot(
            session_id="session-1",
            snapshot_id="captcha-1",
            url="https://example.com/verify",
            title="Verify",
            page_state="captcha",
        )
    )
    runtime = BrowserRuntime(worker=worker)
    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(
        return_value=CaptchaSolveOutcome(
            solved=False,
            retryable=False,
            reason_code=CAPTCHA_REASON_NO_VISION_MODEL,
            message="未配置多模态模型",
        )
    )

    events: list[dict] = []

    async def fake_broadcast(session_id, payload):
        events.append(payload)

    runtime.broadcast_event = fake_broadcast

    await runtime.snapshot("session-1")

    human_events = [
        e for e in events
        if e.get("type") == "ai_action" and e.get("action") == "captcha_human_required"
    ]
    assert human_events, "未广播人工接手动作"
    extra = human_events[-1].get("extra") or {}
    assert extra.get("reason_code") == CAPTCHA_REASON_NO_VISION_MODEL
    assert extra.get("requires_human") is True
