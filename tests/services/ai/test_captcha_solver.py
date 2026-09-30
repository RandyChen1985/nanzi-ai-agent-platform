import asyncio
import json
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

    async def screenshot(self, **kwargs):
        return b"fake_jpeg_bytes"

    def locator(self, sel):
        loc = AsyncMock()
        loc.bounding_box = AsyncMock(return_value={"x": 100, "y": 200, "width": 40, "height": 40})
        loc.first = loc
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
    async def fake_detailed(session_id, snapshot):
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
async def test_runtime_retries_captcha_up_to_max_attempts(monkeypatch):
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

    async def fake_detailed(session_id, snapshot):
        calls.append(1)
        if len(calls) < 2:
            return _retryable_failure()
        worker._page_state = "ready"
        return CaptchaSolveOutcome(solved=True, retryable=False, message="验证码自动解算成功")

    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(side_effect=fake_detailed)

    snapshot = await runtime.snapshot("session-1")

    assert len(calls) == 2
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

    async def fake_detailed(session_id, snapshot):
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

    async def fake_detailed(session_id, snapshot):
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
    assert "3 次" in captcha_events[-1]["reason"]


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
    assert worker.page.mouse.click.await_count == 1, "触发按钮只应点击一次"
    assert solver._query_vision_model.await_count == 2, "点击后必须用展开后的新截图重新识别"


@pytest.mark.asyncio
async def test_solver_stops_after_single_trigger_click():
    """模型持续返回 trigger 时不得反复点击，应作为识别失败进入重试/降级流程。"""
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
    assert outcome.retryable is True
    assert outcome.reason_code == CAPTCHA_REASON_RECOGNITION_FAILED
    assert worker.page.mouse.click.await_count == 1


def test_detect_captcha_script_separates_trigger_button_from_challenge():
    """检测脚本必须区分“触发按钮”与“已展开的挑战”，否则 AI 会在挑战出现前就被冻结。"""
    source = pathlib.Path("app/services/ai/browser/browser_worker.py").read_text(encoding="utf-8")

    assert "拖动滑块" in source
    assert "geetest_(?:panel|popup" in source
    assert "触发" in source
