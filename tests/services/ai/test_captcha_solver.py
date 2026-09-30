import asyncio
import base64
import json
import logging
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.browser import BrowserSnapshot
from app.services.ai.browser import browser_runtime as browser_runtime_module
from app.services.ai.browser.browser_runtime import (
    CAPTCHA_MAX_ATTEMPTS,
    BrowserHumanControlRequired,
    BrowserRuntime,
)
from app.services.ai.browser.captcha_solver import (
    CAPTCHA_REASON_HUMAN_TAKEOVER,
    CAPTCHA_REASON_NO_VISION_MODEL,
    CAPTCHA_REASON_RECOGNITION_FAILED,
    CAPTCHA_REASON_UNSUPPORTED_TYPE,
    BrowserCaptchaSolver,
    CaptchaSolveOutcome,
)

pytestmark = pytest.mark.no_infrastructure


class DummyPage:
    def __init__(self):
        self.mouse = SimpleNamespace(
            move=AsyncMock(),
            down=AsyncMock(),
            up=AsyncMock(),
            click=AsyncMock(),
        )
        self.viewport_size = {"width": 1280, "height": 800}
        self.locator_clicks: list[str] = []
        self.screenshot_kwargs: list[dict] = []

    async def screenshot(self, **kwargs):
        self.screenshot_kwargs.append(kwargs)
        return b"fake_jpeg_bytes"

    def locator(self, sel):
        loc = AsyncMock()
        loc.count = AsyncMock(return_value=1)
        loc.bounding_box = AsyncMock(return_value={"x": 100, "y": 200, "width": 40, "height": 40})
        loc.first = loc

        async def _record_click(*_args, **_kwargs):
            self.locator_clicks.append(sel)

        loc.click = AsyncMock(side_effect=_record_click)
        return loc


class DummyWorker:
    def __init__(self, page_state="captcha"):
        self.page = DummyPage()
        self.handle = SimpleNamespace(page=self.page)
        self._page_state = page_state
        self.snapshot_call_count = 0

    def _handle(self, session_id):
        return self.handle

    def _slider_trajectory(self, sx, sy, travel_px):
        return [(sx + travel_px * 0.5, sy, 0.01), (sx + travel_px, sy, 0.01)]

    async def _human_smooth_mouse_move(self, page, x, y, steps=10):
        await page.mouse.move(x, y)

    async def snapshot(self, session_id):
        self.snapshot_call_count += 1
        return BrowserSnapshot(
            session_id=session_id,
            snapshot_id=f"snap-{self.snapshot_call_count}",
            url="https://example.com/login",
            title="Login",
            page_state=self._page_state,
        )


@pytest.mark.asyncio
async def test_captcha_solver_slider_success():
    worker = DummyWorker(page_state="ready")  # 动作执行后变为 ready
    solver = BrowserCaptchaSolver(worker)

    mock_llm_response = SimpleNamespace(
        content=json.dumps({
            "type": "slider",
            "slider_x": 120,
            "slider_y": 220,
            "target_x": 320,
            "target_y": 220,
            "distance_px": 200,
        })
    )

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_llm_response

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        success = await solver.solve_captcha("s1", snapshot)
        assert success is True
        assert worker.page.mouse.move.called
        assert worker.page.mouse.down.called
        assert worker.page.mouse.up.called


@pytest.mark.asyncio
async def test_captcha_solver_click_sequence_success():
    worker = DummyWorker(page_state="ready")
    solver = BrowserCaptchaSolver(worker)

    mock_llm_response = SimpleNamespace(
        content=json.dumps({
            "type": "click_sequence",
            "points": [{"x": 150, "y": 250}, {"x": 280, "y": 310}],
        })
    )

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_llm_response

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        success = await solver.solve_captcha("s1", snapshot)
        assert success is True
        assert worker.page.mouse.click.call_count == 2


@pytest.mark.asyncio
async def test_captcha_solver_unsupported_returns_false():
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)

    mock_llm_response = SimpleNamespace(
        content=json.dumps({
            "type": "unsupported",
            "reason": "需要短信验证码",
        })
    )

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_llm_response

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        success = await solver.solve_captcha("s1", snapshot)
        assert success is False


@pytest.mark.asyncio
async def test_runtime_auto_solves_captcha_and_falls_back_to_human_control_on_failure():
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)

    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(
        return_value=CaptchaSolveOutcome(
            solved=False,
            retryable=False,
            reason_code=CAPTCHA_REASON_UNSUPPORTED_TYPE,
            message="需要短信验证码",
        )
    )

    snapshot = await runtime.snapshot("session-1")
    assert snapshot.page_state == "captcha"
    # 确认触发了人工接管
    control = runtime.control_state("session-1")
    assert control["owner"] == "human"
    assert control["reason"] == "captcha"
    assert control["captcha"] is True


@pytest.mark.asyncio
async def test_runtime_auto_solves_captcha_successfully():
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)

    # 首次触发自解算成功，worker 下一次 snapshot 变为 ready
    async def fake_detailed(session_id, snapshot, *, model_name=None, on_progress=None, should_abort=None):
        worker._page_state = "ready"
        return CaptchaSolveOutcome(solved=True, retryable=False, message="验证码自动解算成功")

    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(side_effect=fake_detailed)

    snapshot = await runtime.snapshot("session-1")
    assert snapshot.page_state == "ready"
    control = runtime.control_state("session-1")
    assert control["owner"] == "ai"
    assert control["captcha"] is False


@pytest.mark.asyncio
async def test_solver_reports_no_vision_model_as_non_retryable():
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value=None)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        outcome = await solver.solve_captcha_detailed("s1", snapshot)

    assert outcome.solved is False
    assert outcome.retryable is False
    assert outcome.reason_code == CAPTCHA_REASON_NO_VISION_MODEL


@pytest.mark.asyncio
async def test_solver_reports_missing_screenshot_as_non_retryable():
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch.object(solver, "_get_screenshot_base64", AsyncMock(return_value=None)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        outcome = await solver.solve_captcha_detailed("s1", snapshot)

    assert outcome.solved is False
    assert outcome.retryable is False
    assert outcome.reason_code == "no_screenshot"


@pytest.mark.asyncio
async def test_solver_reports_unsupported_type_as_non_retryable():
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(
        content=json.dumps({"type": "unsupported", "reason": "需要短信验证码"})
    )

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        outcome = await solver.solve_captcha_detailed("s1", snapshot)

    assert outcome.solved is False
    assert outcome.retryable is False
    assert outcome.reason_code == CAPTCHA_REASON_UNSUPPORTED_TYPE


@pytest.mark.asyncio
async def test_solver_reports_still_captcha_as_retryable():
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(
        content=json.dumps({
            "type": "slider",
            "slider_x": 120,
            "slider_y": 220,
            "target_x": 320,
            "target_y": 220,
            "distance_px": 200,
        })
    )

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)), \
         patch("app.services.ai.browser.captcha_solver.asyncio.sleep", AsyncMock()):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        outcome = await solver.solve_captcha_detailed("s1", snapshot)

    assert outcome.solved is False
    assert outcome.retryable is True
    assert outcome.reason_code == "still_captcha"


@pytest.mark.asyncio
async def test_solver_solve_captcha_keeps_boolean_contract():
    worker = DummyWorker(page_state="ready")
    solver = BrowserCaptchaSolver(worker)
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(
        content=json.dumps({
            "type": "slider",
            "slider_x": 120, "slider_y": 220,
            "target_x": 320, "target_y": 220,
            "distance_px": 200,
        })
    )

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)), \
         patch("app.services.ai.browser.captcha_solver.asyncio.sleep", AsyncMock()):
        snapshot = BrowserSnapshot(
            session_id="s1", snapshot_id="snap-0",
            url="https://example.com/login", title="Login", page_state="captcha",
        )
        solved = await solver.solve_captcha("s1", snapshot)

    assert solved is True


def _retryable_failure():
    return CaptchaSolveOutcome(
        solved=False,
        retryable=True,
        reason_code=CAPTCHA_REASON_RECOGNITION_FAILED,
        message="识别失败",
    )


@pytest.mark.asyncio
async def test_runtime_attempts_captcha_once_before_handing_over(monkeypatch):
    """收手策略：同一会话只自动尝试一次，随后必定交人工。"""
    monkeypatch.setattr(browser_runtime_module, "CAPTCHA_RETRY_BACKOFF_BASE_SECONDS", 0.0)
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    solver = AsyncMock(return_value=_retryable_failure())
    runtime.captcha_solver.solve_captcha_detailed = solver

    snapshot = await runtime.snapshot("session-1")

    assert solver.await_count == CAPTCHA_MAX_ATTEMPTS
    assert snapshot.page_state == "captcha"
    control = runtime.control_state("session-1")
    assert control["owner"] == "human"
    assert control["captcha"] is True


@pytest.mark.asyncio
async def test_runtime_stops_retrying_when_captcha_is_solved(monkeypatch):
    monkeypatch.setattr(browser_runtime_module, "CAPTCHA_RETRY_BACKOFF_BASE_SECONDS", 0.0)
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    calls: list[int] = []

    async def fake_detailed(session_id, snapshot, *, model_name=None, on_progress=None, should_abort=None):
        calls.append(1)
        worker._page_state = "ready"
        return CaptchaSolveOutcome(solved=True, retryable=False, message="验证码自动解算成功")

    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(side_effect=fake_detailed)

    snapshot = await runtime.snapshot("session-1")

    assert len(calls) == 1, "单次成功即结束，不得再调第二次"
    assert snapshot.page_state == "ready"
    control = runtime.control_state("session-1")
    assert control["owner"] == "ai"
    assert control["captcha"] is False


@pytest.mark.asyncio
async def test_runtime_does_not_consume_attempts_without_vision_model():
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    solver = AsyncMock(
        return_value=CaptchaSolveOutcome(
            solved=False,
            retryable=False,
            reason_code=CAPTCHA_REASON_NO_VISION_MODEL,
            message="未配置多模态模型",
        )
    )
    runtime.captcha_solver.solve_captcha_detailed = solver

    await runtime.snapshot("session-1")

    assert "session-1" not in runtime._captcha_attempts
    assert runtime.control_state("session-1")["owner"] == "human"

    await runtime.snapshot("session-1")

    assert solver.await_count == 2


@pytest.mark.asyncio
async def test_runtime_stops_after_unsupported_captcha_type(monkeypatch):
    monkeypatch.setattr(browser_runtime_module, "CAPTCHA_RETRY_BACKOFF_BASE_SECONDS", 0.0)
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    solver = AsyncMock(
        return_value=CaptchaSolveOutcome(
            solved=False,
            retryable=False,
            reason_code=CAPTCHA_REASON_UNSUPPORTED_TYPE,
            message="短信验证码",
        )
    )
    runtime.captcha_solver.solve_captcha_detailed = solver

    await runtime.snapshot("session-1")
    assert solver.await_count == 1

    await runtime.snapshot("session-1")
    assert solver.await_count == 1


@pytest.mark.asyncio
async def test_runtime_aborts_retry_when_human_takes_over(monkeypatch):
    monkeypatch.setattr(browser_runtime_module, "CAPTCHA_RETRY_BACKOFF_BASE_SECONDS", 0.0)
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    calls: list[int] = []

    async def fake_detailed(session_id, snapshot, *, model_name=None, on_progress=None, should_abort=None):
        calls.append(1)
        await runtime.acquire_human_control(session_id, reason="click", owner_id="viewer-1")
        return _retryable_failure()

    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(side_effect=fake_detailed)

    await runtime.snapshot("session-1")

    assert len(calls) == 1
    assert runtime.control_state("session-1")["owner"] == "human"


@pytest.mark.asyncio
async def test_runtime_snapshot_can_skip_auto_solve():
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    solver = AsyncMock(return_value=_retryable_failure())
    runtime.captcha_solver.solve_captcha_detailed = solver

    snapshot = await runtime.snapshot("session-1", auto_solve=False)

    assert snapshot.page_state == "captcha"
    assert solver.await_count == 0
    assert "session-1" not in runtime._captcha_attempts


@pytest.mark.asyncio
async def test_runtime_reuses_single_captcha_round(monkeypatch):
    monkeypatch.setattr(browser_runtime_module, "CAPTCHA_RETRY_BACKOFF_BASE_SECONDS", 0.05)
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    solver = AsyncMock(return_value=_retryable_failure())
    runtime.captcha_solver.solve_captcha_detailed = solver

    first, second = await asyncio.gather(
        runtime.auto_solve_captcha("session-1"),
        runtime.auto_solve_captcha("session-1"),
    )

    assert solver.await_count == CAPTCHA_MAX_ATTEMPTS
    assert first.session_id == "session-1"
    assert second.session_id == "session-1"


@pytest.mark.asyncio
async def test_runtime_blocks_ai_action_while_captcha_round_runs(monkeypatch):
    """解算进行期间，AI 的其他浏览器动作必须让行，避免并发操作同一页面。"""
    monkeypatch.setattr(browser_runtime_module, "CAPTCHA_RETRY_BACKOFF_BASE_SECONDS", 0.0)
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_detailed(session_id, snapshot, *, model_name=None, on_progress=None, should_abort=None):
        started.set()
        await release.wait()
        return _retryable_failure()

    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(side_effect=fake_detailed)

    round_task = asyncio.create_task(runtime.auto_solve_captcha("session-1"))
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(BrowserHumanControlRequired):
        await runtime.wait_for("session-1", condition="ready", timeout_ms=100)

    release.set()
    await asyncio.wait_for(round_task, timeout=2)

    assert runtime.control_state("session-1")["owner"] == "human"


@pytest.mark.asyncio
async def test_runtime_broadcasts_human_control_when_captcha_needs_human(monkeypatch):
    """AI 放弃自动解算时必须广播控制权转移与人工介入原因，否则界面无从提示用户。"""
    monkeypatch.setattr(browser_runtime_module, "CAPTCHA_RETRY_BACKOFF_BASE_SECONDS", 0.0)
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(return_value=_retryable_failure())
    queue = runtime.subscribe_events("session-1")

    await runtime.auto_solve_captcha("session-1")

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    control_events = [event for event in events if event.get("type") == "control_state"]
    captcha_events = [event for event in events if event.get("type") == "captcha"]

    assert control_events, "降级为人工接管时必须广播 control_state"
    assert control_events[-1]["owner"] == "human"
    assert control_events[-1]["captcha"] is True
    assert control_events.index(control_events[-1]) < events.index(captcha_events[-1])
    assert captcha_events[-1]["requires_human"] is True
    assert "请手动拖动一次完成验证" in captcha_events[-1]["reason"]


@pytest.mark.asyncio
async def test_runtime_resets_captcha_attempts_after_human_release(monkeypatch):
    """用户点击“交还 AI”后应重置额度，让 AI 能重新尝试一轮。"""
    monkeypatch.setattr(browser_runtime_module, "CAPTCHA_RETRY_BACKOFF_BASE_SECONDS", 0.0)
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    solver = AsyncMock(return_value=_retryable_failure())
    runtime.captcha_solver.solve_captcha_detailed = solver

    await runtime.snapshot("session-1")
    assert solver.await_count == CAPTCHA_MAX_ATTEMPTS

    await runtime.release_human_control("session-1", owner_id="viewer-1")
    await runtime.snapshot("session-1")

    assert solver.await_count == CAPTCHA_MAX_ATTEMPTS * 2


@pytest.mark.asyncio
async def test_solver_clicks_trigger_button_then_recognizes_challenge():
    """“点击按钮进行验证”这类触发按钮：应先点开、重新截图识别，再执行真实验证动作。"""
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        side_effect=[
            {"type": "trigger", "x": 300, "y": 400, "reason": "点击按钮进行验证"},
            {"type": "slider", "slider_x": 100, "slider_y": 200, "distance_px": 120},
        ]
    )
    # 1) 初始识别到触发按钮 → 2) 点击后挑战展开（仍处于 captcha）→ 3) 拖动成功后恢复
    states = ["captcha", "captcha", "ready"]

    async def fake_snapshot(session_id):
        worker.snapshot_call_count += 1
        index = min(worker.snapshot_call_count - 1, len(states) - 1)
        return BrowserSnapshot(
            session_id=session_id,
            snapshot_id=f"snap-{worker.snapshot_call_count}",
            url="https://example.com/login",
            title="Login",
            page_state=states[index],
        )

    worker.snapshot = fake_snapshot

    outcome = await solver.solve_captcha_detailed(
        "session-1", await fake_snapshot("session-1"), model_name="vision-model"
    )

    assert outcome.solved is True
    assert worker.page.locator_clicks == [".geetest_btn"], "触发按钮只应通过 DOM 点击一次"
    assert solver._query_vision_model.await_count == 2, "点击后必须用展开后的新截图重新识别"


@pytest.mark.asyncio
async def test_solver_stops_after_single_trigger_click():
    """模型持续返回 trigger 时不得反复点击，且判该站点不可自动处理、不再空耗剩余额度。"""
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={"type": "trigger", "x": 300, "y": 400, "reason": "点击按钮进行验证"}
    )

    outcome = await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    assert outcome.solved is False
    assert outcome.retryable is False
    assert outcome.reason_code == CAPTCHA_REASON_UNSUPPORTED_TYPE
    assert len(worker.page.locator_clicks) == 1


def test_detect_captcha_script_separates_trigger_button_from_challenge():
    """检测脚本必须区分“触发按钮”与“已展开的挑战”，否则 AI 会在挑战出现前就被冻结。"""
    source = pathlib.Path("app/services/ai/browser/browser_worker.py").read_text(encoding="utf-8")

    assert "拖动滑块" in source
    assert "geetest_(?:panel|popup" in source
    assert "触发" in source


@pytest.mark.asyncio
async def test_solver_reports_captcha_geometry_to_progress_callback():
    """识别成功后必须上报缺口/滑块/距离与轨迹，前端才能在画面上画出目标与距离。"""
    worker = DummyWorker(page_state="ready")
    solver = BrowserCaptchaSolver(worker)

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(
        content=json.dumps({
            "type": "slider",
            "slider_x": 120,
            "slider_y": 220,
            "target_x": 320,
            "target_y": 220,
            "distance_px": 200,
        })
    )

    events: list[dict] = []

    async def on_progress(payload):
        events.append(payload)

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        outcome = await solver.solve_captcha_detailed("s1", snapshot, on_progress=on_progress)

    recognized = [e for e in events if e.get("phase") == "recognized"]
    assert recognized, "未上报识别结果"
    payload = recognized[0]
    assert payload["kind"] == "slider"
    assert payload["target_x"] == 320
    assert payload["target_y"] == 220
    assert payload["slider_x"] == 120
    assert payload["distance_px"] == 200

    dragged = [e for e in events if e.get("phase") == "dragged"]
    assert dragged, "未上报拖动执行结果"
    trajectory = dragged[0]["trajectory"]
    assert isinstance(trajectory, list) and len(trajectory) >= 2
    assert {"x", "y"} <= set(trajectory[0])

    # outcome 同样携带几何信息，便于上层在失败时诊断
    assert outcome.distance_px == 200
    assert outcome.target_x == 320
    assert outcome.slider_x == 120
    assert outcome.trajectory is not None and len(outcome.trajectory) >= 2


@pytest.mark.asyncio
async def test_solver_progress_callback_is_optional():
    """未传回调时必须保持原有行为，不能因为缺少回调而失败。"""
    worker = DummyWorker(page_state="ready")
    solver = BrowserCaptchaSolver(worker)

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(
        content=json.dumps({
            "type": "slider",
            "slider_x": 120,
            "slider_y": 220,
            "target_x": 320,
            "target_y": 220,
            "distance_px": 200,
        })
    )

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        outcome = await solver.solve_captcha_detailed("s1", snapshot)

    assert outcome.solved is True
    assert outcome.distance_px == 200


@pytest.mark.asyncio
async def test_solver_aborts_before_dragging_when_human_took_over():
    """识别完成后若用户已接管，必须放弃执行拖拽动作，不能和用户抢滑块。"""
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 120,
            "slider_y": 220,
            "target_x": 320,
            "target_y": 220,
            "distance_px": 200,
        }
    )

    outcome = await solver.solve_captcha_detailed(
        "session-1",
        await worker.snapshot("session-1"),
        model_name="vision-model",
        should_abort=lambda: True,
    )

    assert outcome.solved is False
    assert outcome.reason_code == CAPTCHA_REASON_HUMAN_TAKEOVER
    assert worker.page.mouse.move.await_count == 0, "用户已接管时不得再移动鼠标"
    assert worker.page.mouse.up.await_count == 0, "用户已接管时不得松手拖拽"


@pytest.mark.asyncio
async def test_solver_reports_each_stage_of_a_failed_attempt():
    """解算不能是黑盒：截图、调用模型、识别失败三个阶段都必须上报。"""
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(content="抱歉，我看不清这张图")
    events: list[dict] = []

    async def on_progress(payload):
        events.append(payload)

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        outcome = await solver.solve_captcha_detailed("s1", snapshot, on_progress=on_progress)

    assert outcome.reason_code == CAPTCHA_REASON_RECOGNITION_FAILED
    phases = [event["phase"] for event in events]
    assert phases[0] == "screenshot"
    assert "analyzing" in phases
    assert "unrecognized" in phases
    analyzing = next(event for event in events if event["phase"] == "analyzing")
    assert analyzing["model"] == "mock-vision-model"


@pytest.mark.asyncio
async def test_solver_reports_unsupported_stage_with_reason():
    """模型判定不可自动处理时，必须把原因带进过程，用户才知道该不该人工上。"""
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(
        content=json.dumps({"type": "unsupported", "reason": "需要短信验证码"})
    )
    events: list[dict] = []

    async def on_progress(payload):
        events.append(payload)

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        outcome = await solver.solve_captcha_detailed("s1", snapshot, on_progress=on_progress)

    assert outcome.reason_code == CAPTCHA_REASON_UNSUPPORTED_TYPE
    unsupported = next(event for event in events if event["phase"] == "unsupported")
    assert unsupported["reason"] == "需要短信验证码"


@pytest.mark.asyncio
async def test_solver_reports_action_and_verification_stages():
    """执行与校验阶段也要可见，否则用户只看到"识别中"却不知道动作是否已发出。"""
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(
        content=json.dumps({
            "type": "slider",
            "slider_x": 120,
            "slider_y": 220,
            "target_x": 320,
            "target_y": 220,
            "distance_px": 200,
        })
    )
    events: list[dict] = []

    async def on_progress(payload):
        events.append(payload)

    with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        outcome = await solver.solve_captcha_detailed("s1", snapshot, on_progress=on_progress)

    phases = [event["phase"] for event in events]
    assert "recognized" in phases
    assert "acting" in phases
    assert "dragged" in phases
    assert "verifying" in phases
    assert outcome.reason_code == "still_captcha"


@pytest.mark.asyncio
async def test_solver_logs_model_raw_return_and_geometry(caplog):
    """定位"识别不到"必须能回看模型原始返回与识别出的几何数据。"""
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(
        content=json.dumps({
            "type": "slider",
            "slider_x": 120,
            "slider_y": 220,
            "target_x": 320,
            "target_y": 220,
            "distance_px": 200,
        })
    )

    with caplog.at_level(logging.INFO, logger="app.services.ai.browser.captcha_solver"), \
         patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
        snapshot = BrowserSnapshot(
            session_id="s1",
            snapshot_id="snap-0",
            url="https://example.com/login",
            title="Login",
            page_state="captcha",
        )
        await solver.solve_captcha_detailed("s1", snapshot)

    text = caplog.text
    assert "slider" in text, "必须记录模型返回的验证码类型"
    assert "200" in text, "必须记录识别出的拖动距离"
    assert "mock-vision-model" in text, "必须记录实际使用的视觉模型"


@pytest.mark.asyncio
async def test_solver_clicks_verify_entry_only_once_across_attempts():
    """入口按钮只该点一次：否则 3 次额度会全耗在反复点"点击按钮进行验证"上。"""
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(
        content=json.dumps({"type": "trigger", "x": 300, "y": 400, "reason": "点击按钮进行验证"})
    )

    async def attempt() -> CaptchaSolveOutcome:
        with patch.object(solver, "_resolve_vision_model", AsyncMock(return_value="mock-vision-model")), \
             patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)):
            snapshot = BrowserSnapshot(
                session_id="s1",
                snapshot_id="snap-0",
                url="https://example.com/login",
                title="Login",
                page_state="captcha",
            )
            return await solver.solve_captcha_detailed("s1", snapshot)

    first = await attempt()

    assert len(worker.page.locator_clicks) == 1
    assert first.solved is False

    second = await attempt()

    assert len(worker.page.locator_clicks) == 1, "第二次尝试不得再点同一个入口按钮"
    assert second.reason_code == CAPTCHA_REASON_UNSUPPORTED_TYPE
    assert second.retryable is False


@pytest.mark.asyncio
async def test_solver_prefers_live_screenshot_over_stale_snapshot(tmp_path):
    """缓存快照可能是"验证码尚未渲染"时的画面：解算必须优先实时截图。"""
    stale = tmp_path / "stale.jpg"
    stale.write_bytes(b"stale_snapshot_bytes")
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    snapshot = BrowserSnapshot(
        session_id="s1",
        snapshot_id="snap-0",
        url="https://example.com/login",
        title="Login",
        page_state="captcha",
        screenshot_ref=str(stale),
    )

    encoded = await solver._get_screenshot_base64("s1", snapshot)

    assert encoded is not None
    assert base64.b64decode(encoded) == b"fake_jpeg_bytes", "必须用实时截图而非过期的快照缓存"


@pytest.mark.asyncio
async def test_solver_waits_for_page_render_before_live_screenshot():
    """截图前必须等渲染稳定，否则只会截到空白占位容器。"""
    worker = DummyWorker(page_state="captcha")
    worker.page.wait_for_load_state = AsyncMock()
    solver = BrowserCaptchaSolver(worker)
    snapshot = BrowserSnapshot(
        session_id="s1",
        snapshot_id="snap-0",
        url="https://example.com/login",
        title="Login",
        page_state="captcha",
    )

    await solver._get_screenshot_base64("s1", snapshot)

    worker.page.wait_for_load_state.assert_awaited_once()
    args, _kwargs = worker.page.wait_for_load_state.await_args
    assert args[0] == "networkidle"


def test_trigger_expand_wait_covers_geetest_animation():
    """极验展开动画超过 1.5 秒：等待必须放宽，否则展开后的第二次截图仍是空白。"""
    from app.services.ai.browser import captcha_solver as solver_module

    assert solver_module.CAPTCHA_TRIGGER_EXPAND_WAIT_SECONDS >= 2.5


@pytest.mark.asyncio
async def test_solver_uses_dom_slider_position_for_pointer_but_model_distance():
    """极验按钮仅约 40px 宽：鼠标落点必须用 DOM 实测位置，但拖动距离用模型自报值。"""
    worker = DummyWorker(page_state="ready")
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 900,
            "slider_y": 700,
            "target_x": 1000,
            "target_y": 700,
            "distance_px": 100,
        }
    )

    outcome = await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    # DummyPage 的 DOM 按钮中心是 (120, 220)：模型目测的 (900, 700) 必须被取代
    assert outcome.slider_x == 120.0
    assert outcome.slider_y == 220.0
    assert worker.page.mouse.move.await_args_list[0].args[:2] == (120.0, 220.0)
    # 距离不随按钮位置变化：模型自报的 100 才是“拼图块 → 缺口”的真实位移
    assert outcome.distance_px == 100


@pytest.mark.asyncio
async def test_solver_falls_back_to_model_coordinates_when_dom_lookup_fails():
    """DOM 定位不到滑块按钮时必须回退模型坐标，不能因此彻底失去定位能力。"""
    worker = DummyWorker(page_state="ready")

    def failing_locator(_selector):
        loc = MagicMock()
        loc.first = loc
        loc.count = AsyncMock(return_value=1)
        loc.bounding_box = AsyncMock(return_value=None)
        return loc

    worker.page.locator = failing_locator
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 900,
            "slider_y": 700,
            "target_x": 1000,
            "target_y": 700,
            "distance_px": 100,
        }
    )

    outcome = await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    assert outcome.slider_x == 900.0
    assert outcome.slider_y == 700.0


@pytest.mark.asyncio
async def test_solver_clicks_trigger_button_via_dom_locator():
    """极验入口按钮目测偏差可达数十像素：必须用 DOM 点击，不能按模型坐标盲点。"""
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={"type": "trigger", "x": 553, "y": 353, "reason": "点击按钮进行验证"}
    )

    await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    assert worker.page.locator_clicks, "必须通过 DOM 选择器点击验证入口按钮"
    assert worker.page.mouse.click.await_count == 0, "DOM 可用时不该按模型坐标盲点"


@pytest.mark.asyncio
async def test_solver_falls_back_to_model_click_when_trigger_dom_fails():
    """DOM 点不到入口按钮时仍要按模型坐标尝试，不能直接放弃。"""
    worker = DummyWorker(page_state="captcha")

    def failing_locator(_selector):
        loc = MagicMock()
        loc.first = loc
        loc.click = AsyncMock(side_effect=RuntimeError("元素不存在"))
        loc.count = AsyncMock(return_value=1)
        loc.bounding_box = AsyncMock(return_value=None)
        return loc

    worker.page.locator = failing_locator
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={"type": "trigger", "x": 553, "y": 353, "reason": "点击按钮进行验证"}
    )

    await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    assert worker.page.mouse.click.await_count >= 1
    assert worker.page.mouse.click.await_args_list[0].args[:2] == (553.0, 353.0)


@pytest.mark.asyncio
async def test_solver_crops_captcha_region_and_shifts_model_coordinates():
    """整屏 1280×800 里验证码只占几个百分点：必须裁剪后再识别，并把坐标换算回页面。"""
    worker = DummyWorker(page_state="ready")

    def container_locator(_selector):
        loc = AsyncMock()
        loc.bounding_box = AsyncMock(
            return_value={"x": 400.0, "y": 200.0, "width": 300.0, "height": 150.0}
        )
        loc.first = loc
        return loc

    worker.page.locator = container_locator
    solver = BrowserCaptchaSolver(worker)
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 50,
            "slider_y": 60,
            "target_x": 150,
            "target_y": 60,
            "distance_px": 100,
        }
    )

    outcome = await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    crop_call = worker.page.screenshot_kwargs[0]
    assert "clip" in crop_call, "必须裁剪到验证码区域后再识别"
    assert crop_call["type"] == "png", "裁剪后图很小，应使用无损编码"
    # 模型基于裁剪图给出的坐标必须换算回页面坐标：150 + (400 - 28) = 522
    assert outcome.target_x == 522.0


@pytest.mark.asyncio
async def test_solver_falls_back_to_full_page_when_captcha_container_missing():
    """定位不到验证码容器时必须回退整屏截图，且不得偏移模型坐标。"""
    worker = DummyWorker(page_state="ready")

    def missing_locator(_selector):
        loc = AsyncMock()
        loc.count = AsyncMock(return_value=1)
        loc.bounding_box = AsyncMock(return_value=None)
        loc.first = loc
        return loc

    worker.page.locator = missing_locator
    solver = BrowserCaptchaSolver(worker)
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 50,
            "slider_y": 60,
            "target_x": 150,
            "target_y": 60,
            "distance_px": 100,
        }
    )

    outcome = await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    full_call = worker.page.screenshot_kwargs[0]
    assert "clip" not in full_call
    assert outcome.target_x == 150.0


@pytest.mark.asyncio
async def test_solver_dom_probes_skip_missing_selectors_without_waiting():
    """bounding_box 对不存在的元素默认等 30 秒：必须先 count() 预检，否则解算整体卡死。"""
    worker = DummyWorker(page_state="captcha")
    probe = {"count": 0, "bbox_calls": 0}

    def locator_probe(_selector):
        loc = MagicMock()
        loc.first = loc

        async def _count():
            probe["count"] += 1
            return 0

        async def _bbox(*_args, **_kwargs):
            probe["bbox_calls"] += 1
            return None

        loc.count = AsyncMock(side_effect=_count)
        loc.bounding_box = AsyncMock(side_effect=_bbox)
        loc.click = AsyncMock(side_effect=RuntimeError("元素不存在"))
        return loc

    worker.page.locator = locator_probe
    solver = BrowserCaptchaSolver(worker)
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 100,
            "slider_y": 200,
            "target_x": 300,
            "target_y": 200,
            "distance_px": 200,
        }
    )

    await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    assert probe["count"] > 0, "必须先 count() 预检元素是否存在"
    assert probe["bbox_calls"] == 0, "count() 为 0 时不得再调用 bounding_box（会等满默认超时）"


@pytest.mark.asyncio
async def test_solver_logs_drag_baseline_comparison_and_actual_shift(caplog):
    """必须同时打出“按钮基准/画布基准”两个候选距离，并实测拖动后按钮的真实位移。"""
    caplog.set_level(logging.INFO)
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 100,
            "slider_y": 200,
            "target_x": 300,
            "target_y": 200,
            "distance_px": 200,
        }
    )

    await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    messages = [record.getMessage() for record in caplog.records]
    assert any("拖动参考" in msg for msg in messages), messages
    assert any("拖动结果实测" in msg for msg in messages), messages


@pytest.mark.asyncio
async def test_solver_reports_button_kept_at_target_after_drag(caplog):
    """按钮停在终点 = 距离被接受，此时仍失败就另有原因（行为特征/风控），需能区分。"""
    caplog.set_level(logging.INFO)
    worker = DummyWorker(page_state="captcha")
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 100,
            "slider_y": 200,
            "target_x": 300,
            "target_y": 200,
            "distance_px": 200,
        }
    )
    solver._locate_slider_by_dom = AsyncMock(side_effect=[(100.0, 200.0), (300.0, 200.0)])
    solver._locate_captcha_piece = AsyncMock(return_value=(100.0, 200.0))

    await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    messages = [record.getMessage() for record in caplog.records]
    assert any("停在终点" in msg for msg in messages), messages


@pytest.mark.asyncio
async def test_solver_uses_piece_position_for_distance_but_button_for_drag():
    """鼠标必须按在按钮中心：模型给的是拼图块位置，绝不能拿它当鼠标落点。"""
    worker = DummyWorker(page_state="captcha")

    def locator_by_selector(selector):
        loc = MagicMock()
        loc.first = loc
        loc.count = AsyncMock(return_value=1)
        loc.bounding_box = AsyncMock(
            return_value=(
                {"x": 628.0, "y": 200.0, "width": 20.0, "height": 20.0}  # 拼图块：中心 x=638
                if "slice" in selector
                else {"x": 600.0, "y": 380.0, "width": 40.0, "height": 40.0}
            )
        )
        return loc

    worker.page.locator = locator_by_selector
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 638,
            "slider_y": 210,
            "target_x": 678,
            "target_y": 210,
            "distance_px": 40,
        }
    )

    outcome = await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    assert outcome.distance_px == 40, "距离必须按拼图块位置算：678 - 638 = 40"
    first_move = worker.page.mouse.move.await_args_list[0].args
    assert first_move[:2] == (620.0, 400.0), "鼠标必须按在按钮中心，而不是拼图块上"


@pytest.mark.asyncio
async def test_solver_keeps_model_distance_when_piece_lookup_fails():
    """拼图块 DOM 缺失时沿用模型自报距离：slider_x 与 target_x 同基准，误差相消。"""
    worker = DummyWorker(page_state="captcha")

    def locator_by_selector(selector):
        loc = MagicMock()
        loc.first = loc
        loc.count = AsyncMock(
            return_value=0 if ("slice" in selector or "puzzle" in selector) else 1
        )
        loc.bounding_box = AsyncMock(
            return_value={"x": 600.0, "y": 380.0, "width": 40.0, "height": 40.0}
        )
        return loc

    worker.page.locator = locator_by_selector
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 638,
            "slider_y": 210,
            "target_x": 678,
            "target_y": 210,
            "distance_px": 37,
        }
    )

    outcome = await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    assert outcome.distance_px == 37, "拼图块定位失败时必须沿用模型自报距离"


@pytest.mark.asyncio
async def test_solver_uses_model_distance_when_dom_slice_covers_whole_canvas():
    """铁证回归：.geetest_canvas_slice 的 bounding box 覆盖整幅画布（其 x = 画布 x + 半宽），
    拿它当拼图块会把 165px 算成 77px。距离必须一律采用模型自报值。"""
    worker = DummyWorker(page_state="captcha")

    def locator_by_selector(selector):
        loc = MagicMock()
        loc.first = loc
        loc.count = AsyncMock(return_value=1)
        loc.bounding_box = AsyncMock(
            return_value=(
                {"x": 590.0, "y": 190.0, "width": 260.0, "height": 160.0}  # 整幅画布
                if ("slice" in selector or "puzzle" in selector)
                else {"x": 600.0, "y": 380.0, "width": 40.0, "height": 40.0}
            )
        )
        return loc

    worker.page.locator = locator_by_selector
    solver = BrowserCaptchaSolver(worker)
    solver._get_screenshot_base64 = AsyncMock(return_value="fake_b64")
    solver._query_vision_model = AsyncMock(
        return_value={
            "type": "slider",
            "slider_x": 633,
            "slider_y": 277,
            "target_x": 798,
            "target_y": 277,
            "distance_px": 165,
        }
    )

    outcome = await solver.solve_captcha_detailed(
        "session-1", await worker.snapshot("session-1"), model_name="vision-model"
    )

    assert outcome.distance_px == 165, "必须采用模型自报距离，而不是画布中心推出的 77px"
