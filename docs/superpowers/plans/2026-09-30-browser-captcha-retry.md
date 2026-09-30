# 浏览器验证码自动解算重试策略 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本项目 Git 边界**：全局指令禁止 Agent 自行 `git commit`，因此本计划不含提交步骤，改动完成并验证后由用户自行提交。

**Goal:** 让浏览器验证码自动解算在一轮内连续重试 3 次（带指数退避），未配置多模态模型时不空转、不计数，并把失败原因暴露到面板，同时保证自动解算绝不阻塞人工接管。

**Architecture:** `BrowserCaptchaSolver` 由返回 `bool` 升级为返回结构化 `CaptchaSolveOutcome`（保留旧入口做包装）；`BrowserRuntime.try_auto_solve_captcha()` 由"每次快照试一次"改为"一轮内循环 3 次 + 退避 + 每轮刷新截图"；Viewer（面板 WebSocket）路径改为 `auto_solve=False` 并调度去重的后台任务，通过既有事件通道广播快照，前端零改动。

**Tech Stack:** Python 3.11、asyncio、FastAPI WebSocket、pytest + pytest-asyncio、Playwright（经 `BrowserWorker` 封装）。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/services/ai/browser/captcha_solver.py` | 单次解算尝试，输出结构化结果 | 修改 |
| `app/services/ai/browser/browser_runtime.py` | 一轮重试编排、额度、人工接管降级、解算去重 | 修改 |
| `app/api/v1/endpoints/browser.py` | Viewer 路径非阻塞取快照 + 后台任务广播 | 修改 |
| `tests/services/ai/test_captcha_solver.py` | 解算与重试策略测试 | 修改 |
| `tests/CHECKLIST.md` | 项目测试清单 | 修改 |

已确认无需改动：`frontend/src/components/embed/BrowserPanel.vue`（`payload.type === 'snapshot'` 分支已存在）、`tests/frontend/*` 契约测试。

---

## Task 1: Solver 输出结构化结果

**Files:**
- Modify: `app/services/ai/browser/captcha_solver.py`
- Test: `tests/services/ai/test_captcha_solver.py`

- [ ] **Step 1: 写失败测试**

在 `tests/services/ai/test_captcha_solver.py` 末尾追加（并在文件顶部导入补上 `CaptchaSolveOutcome`）：

```python
from app.services.ai.browser.captcha_solver import BrowserCaptchaSolver, CaptchaSolveOutcome


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
    assert outcome.reason_code == "no_vision_model"


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
    assert outcome.reason_code == "unsupported_type"


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/services/ai/test_captcha_solver.py -q`
Expected: FAIL —— `ImportError: cannot import name 'CaptchaSolveOutcome'`

- [ ] **Step 3: 实现 outcome 与详细入口**

在 `app/services/ai/browser/captcha_solver.py` 的 import 区补 `from dataclasses import dataclass`，在 `CAPTCHA_SOLVER_PROMPT` 之后加入：

```python
CAPTCHA_REASON_NO_VISION_MODEL = "no_vision_model"
CAPTCHA_REASON_UNSUPPORTED_TYPE = "unsupported_type"
CAPTCHA_REASON_RECOGNITION_FAILED = "recognition_failed"
CAPTCHA_REASON_ACTION_FAILED = "action_failed"
CAPTCHA_REASON_STILL_CAPTCHA = "still_captcha"
CAPTCHA_REASON_ERROR = "error"

CAPTCHA_REASON_MESSAGES = {
    CAPTCHA_REASON_NO_VISION_MODEL: "未配置支持多模态的默认模型，无法自动识别验证码",
    CAPTCHA_REASON_UNSUPPORTED_TYPE: "验证码类型不支持自动识别",
    CAPTCHA_REASON_RECOGNITION_FAILED: "未能从页面截图中识别出验证码目标",
    CAPTCHA_REASON_ACTION_FAILED: "验证码目标已识别，但操作执行失败",
    CAPTCHA_REASON_STILL_CAPTCHA: "已执行拖动/点击，页面仍处于验证状态",
    CAPTCHA_REASON_ERROR: "自动解算过程发生异常",
}


@dataclass(frozen=True)
class CaptchaSolveOutcome:
    """一次验证码自动解算尝试的结果，供上层决定是否重试。"""

    solved: bool
    retryable: bool
    reason_code: str = ""
    message: str = ""
```

把 `solve_captcha` 改为包装 + 新增详细实现（**注意先解析模型再取截图**，避免"未配置模型"时做无用的截图调用）：

```python
    async def solve_captcha(
        self,
        session_id: str,
        snapshot: BrowserSnapshot,
        *,
        model_name: Optional[str] = None,
    ) -> bool:
        """兼容入口：仅返回是否解算成功。"""
        outcome = await self.solve_captcha_detailed(session_id, snapshot, model_name=model_name)
        return outcome.solved

    async def solve_captcha_detailed(
        self,
        session_id: str,
        snapshot: BrowserSnapshot,
        *,
        model_name: Optional[str] = None,
    ) -> CaptchaSolveOutcome:
        """执行一次自动解算尝试，返回可判定的结构化结果。"""
        try:
            vision_model = model_name or await self._resolve_vision_model()
            if not vision_model:
                logger.info("[CaptchaSolver] 未配置支持多模态（Vision）的大模型，放弃自动解算")
                return CaptchaSolveOutcome(
                    solved=False,
                    retryable=False,
                    reason_code=CAPTCHA_REASON_NO_VISION_MODEL,
                    message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_NO_VISION_MODEL],
                )

            image_b64 = await self._get_screenshot_base64(session_id, snapshot)
            if not image_b64:
                logger.info("[CaptchaSolver] 无法获取会话 %s 的截图，放弃自动解算", session_id)
                return CaptchaSolveOutcome(
                    solved=False,
                    retryable=True,
                    reason_code=CAPTCHA_REASON_RECOGNITION_FAILED,
                    message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_RECOGNITION_FAILED],
                )

            parsed_data = await self._query_vision_model(image_b64, vision_model)
            if not parsed_data:
                logger.info("[CaptchaSolver] Vision 模型未能返回有效解算数据")
                return CaptchaSolveOutcome(
                    solved=False,
                    retryable=True,
                    reason_code=CAPTCHA_REASON_RECOGNITION_FAILED,
                    message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_RECOGNITION_FAILED],
                )

            if str(parsed_data.get("type") or "") == "unsupported":
                detail = str(parsed_data.get("reason") or "").strip()
                message = CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_UNSUPPORTED_TYPE]
                if detail:
                    message = f"{message}（{detail}）"
                logger.info("[CaptchaSolver] 模型判定该验证码类型不可处理: %s", detail)
                return CaptchaSolveOutcome(
                    solved=False,
                    retryable=False,
                    reason_code=CAPTCHA_REASON_UNSUPPORTED_TYPE,
                    message=message,
                )

            action_success = await self._execute_captcha_action(session_id, snapshot, parsed_data)
            if not action_success:
                logger.info("[CaptchaSolver] 执行验证码模拟动作失败")
                return CaptchaSolveOutcome(
                    solved=False,
                    retryable=True,
                    reason_code=CAPTCHA_REASON_ACTION_FAILED,
                    message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_ACTION_FAILED],
                )

            await asyncio.sleep(1.5)

            new_snapshot = await self.worker.snapshot(session_id)
            if new_snapshot.page_state != "captcha":
                logger.info("[CaptchaSolver] 验证码自动解算成功！页面已恢复正常状态")
                return CaptchaSolveOutcome(solved=True, retryable=False, message="验证码自动解算成功")

            logger.info("[CaptchaSolver] 执行动作后页面仍处于验证码状态")
            return CaptchaSolveOutcome(
                solved=False,
                retryable=True,
                reason_code=CAPTCHA_REASON_STILL_CAPTCHA,
                message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_STILL_CAPTCHA],
            )

        except Exception as exc:
            logger.warning("[CaptchaSolver] 自动解算验证码发生异常: %s", exc, exc_info=True)
            return CaptchaSolveOutcome(
                solved=False,
                retryable=True,
                reason_code=CAPTCHA_REASON_ERROR,
                message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_ERROR],
            )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/services/ai/test_captcha_solver.py -q`
Expected: PASS（含既有 5 个用例）

---

## Task 2: Runtime 单轮重试循环

**Files:**
- Modify: `app/services/ai/browser/browser_runtime.py:20-24`（常量）、`browser_runtime.py:288-335`（解算逻辑）
- Test: `tests/services/ai/test_captcha_solver.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/services/ai/test_captcha_solver.py`：

```python
from app.services.ai.browser import browser_runtime as browser_runtime_module
from app.services.ai.browser.browser_runtime import CAPTCHA_MAX_ATTEMPTS
from app.services.ai.browser.captcha_solver import (
    CAPTCHA_REASON_NO_VISION_MODEL,
    CAPTCHA_REASON_RECOGNITION_FAILED,
    CAPTCHA_REASON_UNSUPPORTED_TYPE,
)


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
```

同时把两个存量用例改为 mock 新入口：

```python
@pytest.mark.asyncio
async def test_runtime_auto_solves_captcha_and_falls_back_to_human_control_on_failure():
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)
    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(
        return_value=CaptchaSolveOutcome(
            solved=False,
            retryable=False,
            reason_code=CAPTCHA_REASON_UNSUPPORTED_TYPE,
            message="短信验证码",
        )
    )

    snapshot = await runtime.snapshot("session-1")

    assert snapshot.page_state == "captcha"
    control = runtime.control_state("session-1")
    assert control["owner"] == "human"
    assert control["reason"] == "captcha"
    assert control["captcha"] is True


@pytest.mark.asyncio
async def test_runtime_auto_solves_captcha_successfully():
    worker = DummyWorker(page_state="captcha")
    runtime = BrowserRuntime(worker=worker)

    async def fake_detailed(session_id, snapshot):
        worker._page_state = "ready"
        return CaptchaSolveOutcome(solved=True, retryable=False, message="验证码自动解算成功")

    runtime.captcha_solver.solve_captcha_detailed = AsyncMock(side_effect=fake_detailed)

    snapshot = await runtime.snapshot("session-1")

    assert snapshot.page_state == "ready"
    control = runtime.control_state("session-1")
    assert control["owner"] == "ai"
    assert control["captcha"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/services/ai/test_captcha_solver.py -q`
Expected: FAIL —— `ImportError: cannot import name 'CAPTCHA_MAX_ATTEMPTS'`

- [ ] **Step 3: 实现常量与重试循环**

在 `app/services/ai/browser/browser_runtime.py` 的 `HUMAN_CONTROL_TIMEOUT_SECONDS` 之后加入：

```python
# 验证码自动解算：一轮内的最大尝试次数与指数退避（按需求写死，不做配置化）
CAPTCHA_MAX_ATTEMPTS = 3
CAPTCHA_RETRY_BACKOFF_BASE_SECONDS = 1.5
CAPTCHA_RETRY_BACKOFF_MAX_SECONDS = 4.0
CAPTCHA_REASON_ATTEMPTS_EXHAUSTED = "attempts_exhausted"
```

把 import 行改为：

```python
from app.services.ai.browser.captcha_solver import (
    CAPTCHA_REASON_NO_VISION_MODEL,
    BrowserCaptchaSolver,
)
```

用以下实现替换 `try_auto_solve_captcha` 全体：

```python
    def _captcha_retry_backoff(self, attempt_index: int) -> float:
        """第 attempt_index 次失败后的退避秒数（attempt_index 从 1 开始）。"""
        backoff = CAPTCHA_RETRY_BACKOFF_BASE_SECONDS * (2 ** max(0, attempt_index - 1))
        return min(backoff, CAPTCHA_RETRY_BACKOFF_MAX_SECONDS)

    def _human_takeover_active(self, session_id: str) -> bool:
        state = self._human_controls.get(session_id)
        return state is not None and state.owner_id is not None

    async def _enter_captcha_human_control(
        self, session_id: str, *, reason_code: str, message: str
    ) -> None:
        async with self._session_lock(session_id):
            self._set_human_control_locked(session_id, reason="captcha", captcha=True)
        await self.broadcast_event(
            session_id,
            {"type": "captcha", "detected": True, "reason": message, "reason_code": reason_code},
        )

    async def try_auto_solve_captcha(
        self, session_id: str, snapshot: BrowserSnapshot
    ) -> BrowserSnapshot:
        """一轮内连续重试自动解算验证码；全部失败后降级人工接管。"""
        if snapshot.page_state != "captcha":
            return snapshot

        # 用户已显式接管时，不干扰用户操作
        if self._human_takeover_active(session_id):
            return snapshot

        attempts_done = self._captcha_attempts.get(session_id, 0)
        if attempts_done >= CAPTCHA_MAX_ATTEMPTS:
            await self._enter_captcha_human_control(
                session_id,
                reason_code=CAPTCHA_REASON_ATTEMPTS_EXHAUSTED,
                message="自动识别重试次数已用尽，请人工完成验证",
            )
            return snapshot

        last_reason_code = ""
        last_message = ""
        solved = False

        for index in range(attempts_done + 1, CAPTCHA_MAX_ATTEMPTS + 1):
            await self.set_ai_action(
                session_id,
                "solving_captcha",
                f"检测到验证码，AI 正在尝试自动识别（第 {index}/{CAPTCHA_MAX_ATTEMPTS} 次）…",
            )

            if index > 1:
                # 失败后页面通常已换新图，重试前必须重新截图
                async with self._session_lock(session_id):
                    snapshot = self._remember_snapshot_locked(
                        session_id, await self.worker.snapshot(session_id)
                    )
                if snapshot.page_state != "captcha":
                    self._captcha_attempts.pop(session_id, None)
                    await self.clear_ai_action(session_id)
                    return snapshot

            outcome = await self.captcha_solver.solve_captcha_detailed(session_id, snapshot)
            if outcome.solved:
                solved = True
                break

            last_reason_code = outcome.reason_code
            last_message = outcome.message

            if outcome.reason_code == CAPTCHA_REASON_NO_VISION_MODEL:
                # 配置缺失：不消耗重试额度，直接降级，便于配置补齐后再次自动尝试
                await self.clear_ai_action(session_id)
                await self._enter_captcha_human_control(
                    session_id,
                    reason_code=last_reason_code,
                    message=f"{last_message}，请人工完成验证",
                )
                return snapshot

            self._captcha_attempts[session_id] = index

            if not outcome.retryable:
                # 类型明确不可自动处理：直接标记额度用尽，避免后续快照反复烧模型
                self._captcha_attempts[session_id] = CAPTCHA_MAX_ATTEMPTS
                break

            if index < CAPTCHA_MAX_ATTEMPTS:
                await asyncio.sleep(self._captcha_retry_backoff(index))
                if self._human_takeover_active(session_id):
                    await self.clear_ai_action(session_id)
                    return snapshot

        if solved:
            self._captcha_attempts.pop(session_id, None)
            await self.clear_ai_action(session_id)
            async with self._session_lock(session_id):
                return self._remember_snapshot_locked(
                    session_id, await self.worker.snapshot(session_id)
                )

        attempt_count = self._captcha_attempts.get(session_id, 0)
        await self._enter_captcha_human_control(
            session_id,
            reason_code=last_reason_code or "unknown",
            message=(
                f"AI 已自动尝试 {attempt_count} 次未通过"
                f"（{last_message or '自动识别未通过'}），请人工完成验证"
            ),
        )
        # 保留可见提示，让面板能显示"试过几次、为什么交人"
        await self.set_ai_action(
            session_id,
            "captcha_human_required",
            last_message or "请人工完成验证",
        )
        return snapshot
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/services/ai/test_captcha_solver.py -q`
Expected: PASS

- [ ] **Step 5: 回归 runtime 既有测试**

Run: `pytest tests/services/ai/test_browser_runtime.py tests/services/ai/test_browser_events.py -q`
Expected: PASS（未被 mock 的解算路径会走 `no_vision_model` 分支并立即降级，不会产生 3 次重试与退避等待）

---

## Task 3: 快照入口与解算去重

**Files:**
- Modify: `app/services/ai/browser/browser_runtime.py`（`__init__`、`snapshot`、`close`、`shutdown`、`clean_idle_sessions`）
- Test: `tests/services/ai/test_captcha_solver.py`

- [ ] **Step 1: 写失败测试**

```python
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
    assert first.session_id == second.session_id == "session-1"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/services/ai/test_captcha_solver.py -q -k "skip_auto_solve or single_captcha_round"`
Expected: FAIL —— `TypeError: snapshot() got an unexpected keyword argument 'auto_solve'`

- [ ] **Step 3: 实现**

`__init__` 中新增字段：

```python
        self._captcha_tasks: dict[str, asyncio.Task] = {}
```

替换 `snapshot` 方法：

```python
    async def snapshot(self, session_id: str, *, auto_solve: bool = True) -> BrowserSnapshot:
        async with self._session_lock(session_id):
            raw_snapshot = self._remember_snapshot_locked(
                session_id,
                await self.worker.snapshot(session_id),
            )
        if not auto_solve:
            return raw_snapshot
        return await self.auto_solve_captcha(session_id, raw_snapshot)

    async def auto_solve_captcha(
        self, session_id: str, snapshot: BrowserSnapshot | None = None
    ) -> BrowserSnapshot:
        """同一 session 内复用同一轮解算，避免 Viewer 与 AI 重复触发。"""
        if snapshot is not None and snapshot.page_state != "captcha":
            return snapshot
        task = self._captcha_tasks.get(session_id)
        if task is None or task.done():
            task = asyncio.create_task(self._auto_solve_captcha_round(session_id, snapshot))
            self._captcha_tasks[session_id] = task
        return await task

    async def _auto_solve_captcha_round(
        self, session_id: str, snapshot: BrowserSnapshot | None
    ) -> BrowserSnapshot:
        try:
            if snapshot is None:
                async with self._session_lock(session_id):
                    snapshot = self._remember_snapshot_locked(
                        session_id, await self.worker.snapshot(session_id)
                    )
            return await self.try_auto_solve_captcha(session_id, snapshot)
        finally:
            if self._captcha_tasks.get(session_id) is asyncio.current_task():
                self._captcha_tasks.pop(session_id, None)
```

`close()` 与 `clean_idle_sessions()` 内、`shutdown()` 内补上任务清理：

```python
        task = self._captcha_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
```

`shutdown()` 内：

```python
        for task in list(self._captcha_tasks.values()):
            task.cancel()
        self._captcha_tasks.clear()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/services/ai/test_captcha_solver.py tests/services/ai/test_browser_runtime.py -q`
Expected: PASS

---

## Task 4: Viewer 路径非阻塞

**Files:**
- Modify: `app/api/v1/endpoints/browser.py`（新增 helper；替换 Viewer 路径的 15 处 `browser_runtime.snapshot(...)`：行 353、521、546、564、570、581、593、601、609、617、625、632、640、654、668；**保留**行 319 的 API 端点同步调用）
- Test: 手工契约核对（该文件为 WebSocket 端点，仓库内无对应单测基座）

- [ ] **Step 1: 新增 helper 与后台任务**

在 `_viewer_snapshot_payload` 之后加入：

```python
_captcha_auto_solve_tasks: dict[str, asyncio.Task] = {}


def _schedule_captcha_auto_solve(session_id: str) -> None:
    """Viewer 路径：后台完成一轮验证码自动解算，不阻塞人工操作。"""
    task = _captcha_auto_solve_tasks.get(session_id)
    if task is not None and not task.done():
        return
    _captcha_auto_solve_tasks[session_id] = asyncio.create_task(
        _captcha_auto_solve_worker(session_id)
    )


async def _captcha_auto_solve_worker(session_id: str) -> None:
    try:
        snapshot = await browser_runtime.auto_solve_captcha(session_id)
        await browser_runtime.broadcast_event(
            session_id,
            {"type": "snapshot", "snapshot": _viewer_snapshot_payload(session_id, snapshot)},
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("浏览器验证码自动解算后台任务失败", exc_info=True)
    finally:
        if _captcha_auto_solve_tasks.get(session_id) is asyncio.current_task():
            _captcha_auto_solve_tasks.pop(session_id, None)


async def _viewer_snapshot(session_id: str) -> Any:
    """Viewer 取快照：先返回画面，验证码解算交给后台任务。"""
    snapshot = await browser_runtime.snapshot(session_id, auto_solve=False)
    if snapshot.page_state == "captcha":
        _schedule_captcha_auto_solve(session_id)
    return snapshot
```

- [ ] **Step 2: 替换 Viewer 路径调用点**

把上表 15 处的 `await browser_runtime.snapshot(session.id)` 替换为 `await _viewer_snapshot(session.id)`；**行 319（`GET /sessions/{session_id}/snapshot`）保持 `browser_runtime.snapshot(session.id)` 不变**（该端点供平台 API 调用，仍需同步解算语义）。

- [ ] **Step 3: 语法与导入校验**

Run: `python -c "import ast, pathlib; ast.parse(pathlib.Path('app/api/v1/endpoints/browser.py').read_text())" && grep -c "_viewer_snapshot(session.id)" app/api/v1/endpoints/browser.py`
Expected: 无输出且计数为 `15`

- [ ] **Step 4: 契约核对（人工）**

确认三点：
1. `browser_viewer` 的 `snapshot`、`manual_input` 后续刷新等分支不再同步跑解算；
2. 后台任务完成后广播 `{"type":"snapshot","snapshot":...}`，前端 `payload.type === 'snapshot'` 分支会刷新画面；
3. 解算失败时 `try_auto_solve_captcha` 已广播 `{"type":"captcha","detected":true,...}`，面板进入人工接管提示。

---

## Task 5: 全量回归与清单同步

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 运行相关测试**

Run: `pytest tests/services/ai/test_captcha_solver.py tests/services/ai/test_browser_runtime.py tests/services/ai/test_browser_events.py tests/services/ai/test_browser_worker.py tests/services/ai/test_browser_integration.py tests/frontend/test_browser_panel_contract.py -q`
Expected: 全部 PASS

- [ ] **Step 2: 运行前端契约测试（确认零前端改动未破坏契约）**

Run: `pytest --confcutdir=tests/frontend tests/frontend -q`
Expected: PASS

- [ ] **Step 3: 更新 `tests/CHECKLIST.md`**

按表格既有格式追加一行，中文描述：验证码自动解算重试策略（一轮 3 次 + 指数退避 + 未配多模态模型不计数 + Viewer 非阻塞后台解算），涉及文件 `app/services/ai/browser/captcha_solver.py`、`app/services/ai/browser/browser_runtime.py`、`app/api/v1/endpoints/browser.py`、`tests/services/ai/test_captcha_solver.py`，验证结论与日期（2026-09-30）。

- [ ] **Step 4: 交付说明**

向用户汇报：改动文件、验证命令与结果、`./dev.sh` 由用户自行执行（Agent 不代跑）、spec 与 plan 文档路径。

---

## 自检记录

**Spec 覆盖**：常量写死（Task 2 Step 3）、单轮 3 次重试（Task 2）、退避（Task 2 `_captcha_retry_backoff`）、未配模型不计数（Task 2 + 测试 3）、`unsupported_type` 立即停止（Task 2 + 测试 4）、失败原因可见（Task 2 末尾 `set_ai_action`）、Viewer 非阻塞（Task 4）、一轮去重（Task 3）——均有对应任务。

**占位符扫描**：无 TBD/TODO；所有代码步骤给出完整代码。

**类型一致性**：`CaptchaSolveOutcome(solved, retryable, reason_code, message)`、`solve_captcha_detailed(session_id, snapshot, *, model_name=None)`、`CAPTCHA_MAX_ATTEMPTS`、`CAPTCHA_REASON_*`、`snapshot(session_id, *, auto_solve=True)`、`auto_solve_captcha(session_id, snapshot=None)`、`_viewer_snapshot(session_id)` 在各任务间命名一致。
