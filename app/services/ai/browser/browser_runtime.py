from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import AsyncSessionLocal
from app.models.browser import BrowserSession
from app.schemas.browser import BrowserSnapshot, BrowserTab, BrowserToolResult
from app.services.ai.browser.browser_profile_service import BrowserProfileService
from app.services.ai.browser.browser_session_service import BrowserSessionService
from app.services.ai.browser.browser_worker import BrowserPageInfo, BrowserWorker
from app.services.ai.browser.captcha_solver import (
    CAPTCHA_REASON_HUMAN_TAKEOVER,
    CAPTCHA_REASON_NO_VISION_MODEL,
    BrowserCaptchaSolver,
)

# 人工接管超时：当验证码 / 人工接管触发而无人持续操作时，AI 等待超过该阈值即抛错
# 终止并上报，避免全自动运行在无人值守下永久死锁。可通过环境变量覆盖。
HUMAN_CONTROL_TIMEOUT_SECONDS = float(
    os.environ.get("BROWSER_HUMAN_CONTROL_TIMEOUT_SECONDS", "45")
)

# 验证码自动解算：一轮内的最大尝试次数与指数退避（按需求写死常量，不做配置化）
CAPTCHA_MAX_ATTEMPTS = 3
CAPTCHA_RETRY_BACKOFF_BASE_SECONDS = 1.5
CAPTCHA_RETRY_BACKOFF_MAX_SECONDS = 4.0
CAPTCHA_REASON_ATTEMPTS_EXHAUSTED = "attempts_exhausted"


class BrowserHumanControlRequired(RuntimeError):
    """页面需要人工接管（如验证码）但限时内无人持续操作，AI 应终止并上报。"""


class BrowserControlConflict(RuntimeError):
    """当前浏览器会话已被另一个人工连接控制。"""


@dataclass
class _HumanControl:
    reason: str
    captcha: bool = False
    owner_id: str | None = None
    released: asyncio.Event = field(default_factory=asyncio.Event)
    held_at: float = 0.0
    last_activity: float = 0.0


class BrowserRuntime:
    """当前应用进程内的浏览器 Worker 注册表。生产部署需保证会话粘滞到同一 Worker。"""

    def __init__(
        self,
        worker: BrowserWorker | None = None,
        captcha_solver: BrowserCaptchaSolver | None = None,
    ) -> None:
        self.worker = worker or BrowserWorker()
        self.captcha_solver = captcha_solver or BrowserCaptchaSolver(self.worker)
        self._captcha_attempts: dict[str, int] = {}
        self._captcha_tasks: dict[str, asyncio.Task] = {}
        self._captcha_round_events: dict[str, asyncio.Event] = {}
        self._snapshots: dict[str, BrowserSnapshot] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._human_controls: dict[str, _HumanControl] = {}
        self._ai_action_status: dict[str, dict[str, Any]] = {}
        self._event_subscribers: dict[str, set[asyncio.Queue]] = {}

    async def check_environment(self) -> dict[str, Any]:
        """查询底层浏览器 Worker 的运行环境状态。"""
        return await self.worker.check_environment()

    def subscribe_events(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._event_subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe_events(self, session_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._event_subscribers.get(session_id)
        if subscribers:
            subscribers.discard(queue)
            if not subscribers:
                self._event_subscribers.pop(session_id, None)

    async def broadcast_event(self, session_id: str, event_data: dict[str, Any]) -> None:
        subscribers = list(self._event_subscribers.get(session_id, []))
        for q in subscribers:
            try:
                q.put_nowait(event_data)
            except Exception:
                pass

    def get_ai_action(self, session_id: str) -> dict[str, Any] | None:
        return self._ai_action_status.get(session_id)

    async def set_ai_action(
        self,
        session_id: str,
        action: str,
        detail: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """广播 AI 动作；extra 承载结构化过程数据（如验证码目标坐标、需拖动距离与执行轨迹）。"""
        data: dict[str, Any] = {"action": action, "detail": detail}
        if extra:
            data["extra"] = extra
        self._ai_action_status[session_id] = data
        await self.broadcast_event(session_id, {"type": "ai_action", **data})

    async def clear_ai_action(self, session_id: str) -> None:
        self._ai_action_status.pop(session_id, None)
        await self.broadcast_event(session_id, {"type": "ai_action", "action": "", "detail": ""})

    async def clean_idle_sessions(self, max_idle_seconds: float = 1800) -> list[str]:
        """按空闲时间自动释放过期的 Chromium 实例与内存快照。"""
        cleaned = await self.worker.clean_idle_sessions(max_idle_seconds=max_idle_seconds)
        for session_id in cleaned:
            self._snapshots.pop(session_id, None)
            self._session_locks.pop(session_id, None)
            state = self._human_controls.pop(session_id, None)
            if state is not None:
                state.released.set()
            task = self._captcha_tasks.pop(session_id, None)
            if task is not None and not task.done():
                task.cancel()
            round_event = self._captcha_round_events.pop(session_id, None)
            if round_event is not None:
                round_event.set()
        return cleaned

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    def _set_human_control_locked(
        self,
        session_id: str,
        *,
        reason: str,
        captcha: bool = False,
        owner_id: str | None = None,
    ) -> _HumanControl:
        now = time.monotonic()
        state = self._human_controls.get(session_id)
        if (
            state is not None
            and owner_id is not None
            and state.owner_id not in {None, owner_id}
        ):
            raise BrowserControlConflict("当前浏览器会话已被另一个人工连接控制")
        if state is None:
            state = _HumanControl(
                reason=reason,
                captcha=captcha,
                owner_id=owner_id,
                held_at=now,
                last_activity=now,
            )
            self._human_controls[session_id] = state
        else:
            state.reason = reason
            state.captcha = state.captcha or captcha
            # 每次人工互动（手动输入、重新获取接管）都会刷新接管计时，
            # 使正在持续处理的人工不会被误判为超时放弃。
            state.last_activity = now
            if owner_id is not None:
                state.owner_id = owner_id
        return state

    async def _wait_for_ai_control(self, session_id: str, timeout_ms: int | None = None) -> None:
        """等待 AI 获得页面控制权。

        若页面正被人工接管（验证码 / 手动输入），且超过 ``timeout_ms`` 内无人持续操作，
        则抛出 :class:`BrowserHumanControlRequired`，让 AI 停止而非永久挂起。
        """
        timeout_s = (timeout_ms or 0) / 1000 if timeout_ms else HUMAN_CONTROL_TIMEOUT_SECONDS
        while True:
            solving = self._captcha_round_events.get(session_id)
            if solving is not None and not solving.is_set():
                # 自动解算正在操作页面，AI 的其他动作必须让行，避免双方并发操作同一浏览器
                try:
                    await asyncio.wait_for(
                        solving.wait(),
                        timeout=None if timeout_s <= 0 else timeout_s,
                    )
                except asyncio.TimeoutError:
                    raise BrowserHumanControlRequired(
                        "AI 正在自动识别验证码，其他浏览器操作在 {} 秒内未取得控制权。".format(
                            int(timeout_s)
                        )
                    )
                continue
            async with self._session_lock(session_id):
                state = self._human_controls.get(session_id)
                if state is None:
                    return
                released = state.released
                last_activity = state.last_activity
                reason = state.reason
            if timeout_s > 0:
                elapsed_s = time.monotonic() - last_activity
                if elapsed_s >= timeout_s:
                    raise BrowserHumanControlRequired(
                        "页面需要人工接管（原因：{}，人工在 {} 秒内未持续操作）。"
                        "AI 未能获得控制权，请人工处理或结束任务。".format(reason, int(elapsed_s))
                    )
            try:
                await asyncio.wait_for(
                    released.wait(),
                    timeout=None if timeout_s <= 0 else max(0.2, timeout_s - 0.0),
                )
            except asyncio.TimeoutError:
                # 超时后回到循环头重新评估 last_activity，避免在释放与竞争间的漏检。
                continue

    def control_state(self, session_id: str) -> dict[str, Any]:
        state = self._human_controls.get(session_id)
        if state is None:
            return {"owner": "ai", "reason": None, "captcha": False}
        return {"owner": "human", "reason": state.reason, "captcha": state.captcha}

    async def acquire_human_control(
        self,
        session_id: str,
        *,
        reason: str,
        captcha: bool = False,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._session_lock(session_id):
            self._set_human_control_locked(
                session_id,
                reason=reason,
                captcha=captcha,
                owner_id=owner_id,
            )
            return self.control_state(session_id)

    async def release_human_control(
        self,
        session_id: str,
        *,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._session_lock(session_id):
            state = self._human_controls.get(session_id)
            if state is None:
                return self.control_state(session_id)
            if owner_id is not None and state.owner_id not in {None, owner_id}:
                return self.control_state(session_id)
            self._human_controls.pop(session_id, None)
            if state is not None:
                state.released.set()
            # 用户显式交还 AI 即表示希望 AI 继续处理，重置验证码重试额度
            self._captcha_attempts.pop(session_id, None)
            return self.control_state(session_id)

    async def open_session(self, db: AsyncSession, session: BrowserSession) -> BrowserPageInfo:
        await self._wait_for_ai_control(session.id)
        async with self._session_lock(session.id):
            profile = await BrowserProfileService(db).get_owned(
                user_id=int(session.user_id), profile_id=session.profile_id
            )
            profile_path = await BrowserProfileService(db).profile_path(profile)
            target_url = session.current_url or "https://www.baidu.com/"
            await db.commit()

            if self.worker.has_session(session.id):
                current_page_info = getattr(self.worker, "current_page_info", None)
                if callable(current_page_info):
                    current_info = await current_page_info(session.id)
                else:
                    current_info = None
                if current_info is not None and _same_browser_url(current_info.url, target_url):
                    info = current_info
                else:
                    info = await self.worker.navigate(session.id, target_url)
            else:
                info = await self.worker.open(
                    session_id=session.id,
                    profile_path=profile_path,
                    url=target_url,
                )
            session.current_url = info.url
            session.page_title = info.title
            session.last_seen_at = datetime.now()
            session.updated_at = datetime.now()
            db.add(session)
            await db.commit()
            return info

    async def open_for_user(
        self,
        *,
        user_id: int,
        conversation_id: str | None,
        url: str,
        profile_id: str | None,
    ) -> BrowserSession:
        async with AsyncSessionLocal() as db:
            service = BrowserSessionService(db)
            session = await service.open_or_resume(
                user_id=user_id,
                conversation_id=conversation_id,
                url=url,
                profile_id=profile_id,
            )
            await self.open_session(db, session)
            await db.commit()
            return session

    def _remember_snapshot_locked(self, session_id: str, snapshot: BrowserSnapshot) -> BrowserSnapshot:
        current_map = self._snapshots.get(session_id)
        if not isinstance(current_map, dict):
            current_map = {}
            self._snapshots[session_id] = current_map
        current_map[snapshot.snapshot_id] = snapshot
        if len(current_map) > 5:
            oldest_key = next(iter(current_map))
            current_map.pop(oldest_key, None)
        if snapshot.page_state != "captcha":
            self._captcha_attempts.pop(session_id, None)
            state = self._human_controls.get(session_id)
            if state is not None:
                state.captcha = False
        return snapshot

    def _captcha_retry_backoff(self, attempt_index: int) -> float:
        """第 attempt_index 次失败后的退避秒数（attempt_index 从 1 开始）。"""
        backoff = CAPTCHA_RETRY_BACKOFF_BASE_SECONDS * (2 ** max(0, attempt_index - 1))
        return min(backoff, CAPTCHA_RETRY_BACKOFF_MAX_SECONDS)

    def _human_takeover_active(self, session_id: str) -> bool:
        """用户是否已通过面板显式接管（此时 AI 不应继续自动解算）。"""
        state = self._human_controls.get(session_id)
        return state is not None and state.owner_id is not None

    async def _enter_captcha_human_control(
        self, session_id: str, *, reason_code: str, message: str
    ) -> None:
        async with self._session_lock(session_id):
            self._set_human_control_locked(session_id, reason="captcha", captcha=True)
        # 必须先广播控制权转移：前端只有在 owner=human 时才会显示"人工接管"横幅
        await self.broadcast_event(
            session_id,
            {"type": "control_state", **self.control_state(session_id)},
        )
        await self.broadcast_event(
            session_id,
            {
                "type": "captcha",
                "detected": True,
                "reason": message,
                "reason_code": reason_code,
                "requires_human": True,
            },
        )
        # 最后广播动作状态：所有降级路径（未配置模型 / 额度用尽 / 重试全败）都经过这里，
        # 前端据此把解算 HUD 转红并弹出"AI 无法完成，请人工处理"卡片。
        await self.set_ai_action(
            session_id,
            "captcha_human_required",
            message or "请人工完成验证",
            extra={
                "requires_human": True,
                "reason_code": reason_code,
                "attempt": self._captcha_attempts.get(session_id, 0),
                "max_attempts": CAPTCHA_MAX_ATTEMPTS,
            },
        )

    async def _broadcast_captcha_progress(
        self, session_id: str, attempt: int, payload: dict[str, Any]
    ) -> None:
        """把解算器"看到了什么、打算怎么动、实际划到哪"随动作一起广播。

        前端据 extra 中的目标坐标/滑块起点/需拖动距离/执行轨迹，在静态截图上绘制
        AI 的解算计划与轨迹回放；没有这些数据，用户只能看到一行"正在识别"。
        """
        await self.set_ai_action(
            session_id,
            "solving_captcha",
            self._captcha_progress_detail(payload, attempt),
            extra={
                **payload,
                "attempt": attempt,
                "max_attempts": CAPTCHA_MAX_ATTEMPTS,
            },
        )

    @staticmethod
    def _captcha_progress_detail(payload: dict[str, Any], attempt: int) -> str:
        """把阶段化进度翻译成用户可读的一行文案。"""
        phase = str(payload.get("phase") or "")
        if phase == "recognized":
            distance = payload.get("distance_px")
            if payload.get("kind") == "slider" and distance:
                return (
                    f"已识别缺口（第 {attempt}/{CAPTCHA_MAX_ATTEMPTS} 次），"
                    f"需拖动 {int(distance)}px，正在执行…"
                )
            return f"已识别验证目标（第 {attempt}/{CAPTCHA_MAX_ATTEMPTS} 次），正在执行…"
        if phase == "dragged":
            return f"动作已执行（第 {attempt}/{CAPTCHA_MAX_ATTEMPTS} 次），正在校验结果…"
        return f"AI 正在尝试自动识别（第 {attempt}/{CAPTCHA_MAX_ATTEMPTS} 次）…"

    async def try_auto_solve_captcha(
        self, session_id: str, snapshot: BrowserSnapshot
    ) -> BrowserSnapshot:
        """在一轮内连续重试自动解算验证码；全部失败后平滑降级为人工接管。

        与旧实现的关键差异：
        - 一轮内最多尝试 ``CAPTCHA_MAX_ATTEMPTS`` 次（而非每次快照只试一次）；
        - 每次失败后退避，并在重试前重新截图，避免拿旧拼图重复识别；
        - 未配置多模态模型时不计入重试额度，配置补齐后仍可自动尝试；
        - 用户中途接管时立即中止，不抢夺控制权。
        """
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
            if self._human_takeover_active(session_id):
                # 用户已经接管：立刻收手、清掉"AI 正在识别"的提示，也不再弹 AI 失败警示
                self._captcha_attempts.pop(session_id, None)
                await self.clear_ai_action(session_id)
                return snapshot

            await self.set_ai_action(
                session_id,
                "solving_captcha",
                f"检测到验证码，AI 正在尝试自动识别（第 {index}/{CAPTCHA_MAX_ATTEMPTS} 次）…",
                extra={
                    "phase": "detecting",
                    "attempt": index,
                    "max_attempts": CAPTCHA_MAX_ATTEMPTS,
                },
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

            outcome = await self.captcha_solver.solve_captcha_detailed(
                session_id,
                snapshot,
                on_progress=lambda payload, current=index: self._broadcast_captcha_progress(
                    session_id, current, payload
                ),
                should_abort=lambda: self._human_takeover_active(session_id),
            )
            if outcome.solved:
                solved = True
                break

            if outcome.reason_code == CAPTCHA_REASON_HUMAN_TAKEOVER:
                # 用户接管导致的中止：静默退出，不消耗额度、不再提示 AI 失败
                self._captcha_attempts.pop(session_id, None)
                await self.clear_ai_action(session_id)
                return snapshot

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
                # 类型明确不可自动处理：标记额度用尽，避免后续快照反复消耗模型调用
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
        return snapshot

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
        """同一 session 内复用同一轮解算，避免 Viewer 与 AI 重复触发并重复消耗模型调用。"""
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
        round_event = asyncio.Event()
        self._captcha_round_events[session_id] = round_event
        try:
            if snapshot is None:
                async with self._session_lock(session_id):
                    snapshot = self._remember_snapshot_locked(
                        session_id, await self.worker.snapshot(session_id)
                    )
            return await self.try_auto_solve_captcha(session_id, snapshot)
        finally:
            round_event.set()
            if self._captcha_round_events.get(session_id) is round_event:
                self._captcha_round_events.pop(session_id, None)
            if self._captcha_tasks.get(session_id) is asyncio.current_task():
                self._captcha_tasks.pop(session_id, None)

    async def scroll(self, session_id: str, *, direction: str, amount: int) -> BrowserSnapshot:
        """执行低风险滚动并返回滚动后的新快照，供 Agent 继续使用最新 target_ref。"""
        await self.set_ai_action(session_id, "scrolling", f"正在{'向下' if direction == 'down' else '向上'}滚动页面")
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    snapshot = await self.worker.scroll(
                        session_id,
                        direction=direction,
                        amount=amount,
                    )
                    return self._remember_snapshot_locked(session_id, snapshot)
        finally:
            await self.clear_ai_action(session_id)

    def cached_snapshot(self, session_id: str, snapshot_id: str) -> BrowserSnapshot:
        snapshots = self._snapshots.get(session_id)
        if isinstance(snapshots, BrowserSnapshot):
            if snapshots.snapshot_id == snapshot_id:
                return snapshots
            raise ValueError("浏览器快照已过期，请先重新获取快照")
        if not isinstance(snapshots, dict) or snapshot_id not in snapshots:
            raise ValueError("浏览器快照已过期，请先重新获取快照")
        return snapshots[snapshot_id]

    def has_session(self, session_id: str) -> bool:
        return self.worker.has_session(session_id)

    async def navigate(
        self,
        session_id: str,
        url: str,
        *,
        owner_id: str | None = None,
    ) -> BrowserPageInfo:
        await self.acquire_human_control(session_id, reason="navigate", owner_id=owner_id)
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            return await self.worker.navigate(session_id, url)

    async def go_back(
        self,
        session_id: str,
        *,
        owner_id: str | None = None,
    ) -> BrowserToolResult:
        await self.acquire_human_control(session_id, reason="navigate", owner_id=owner_id)
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            return await self.worker.go_back(session_id)

    async def go_forward(
        self,
        session_id: str,
        *,
        owner_id: str | None = None,
    ) -> BrowserToolResult:
        await self.acquire_human_control(session_id, reason="navigate", owner_id=owner_id)
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            return await self.worker.go_forward(session_id)

    async def reload(
        self,
        session_id: str,
        *,
        owner_id: str | None = None,
    ) -> BrowserToolResult:
        await self.acquire_human_control(session_id, reason="navigate", owner_id=owner_id)
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            return await self.worker.reload(session_id)

    async def list_tabs(self, session_id: str) -> list[BrowserTab]:
        async with self._session_lock(session_id):
            return await self.worker.list_tabs(session_id)

    async def switch_tab(self, session_id: str, tab_id: str, *, owner_id: str | None = None) -> BrowserPageInfo:
        if owner_id is not None:
            await self.acquire_human_control(session_id, reason="switch_tab", owner_id=owner_id)
        else:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    break
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            return await self.worker.switch_tab(session_id, tab_id)

    async def close_tab(self, session_id: str, tab_id: str, *, owner_id: str | None = None) -> BrowserPageInfo:
        if owner_id is not None:
            await self.acquire_human_control(session_id, reason="close_tab", owner_id=owner_id)
        else:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    break
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            return await self.worker.close_tab(session_id, tab_id)

    async def close_other_tabs(self, session_id: str, tab_id: str, *, owner_id: str | None = None) -> BrowserPageInfo:
        if owner_id is not None:
            await self.acquire_human_control(session_id, reason="close_other_tabs", owner_id=owner_id)
        else:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    break
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            return await self.worker.close_other_tabs(session_id, tab_id)

    async def close_tabs_to_right(self, session_id: str, tab_id: str, *, owner_id: str | None = None) -> BrowserPageInfo:
        if owner_id is not None:
            await self.acquire_human_control(session_id, reason="close_tabs_to_right", owner_id=owner_id)
        else:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    break
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            return await self.worker.close_tabs_to_right(session_id, tab_id)

    async def close_all_tabs(self, session_id: str, *, owner_id: str | None = None) -> BrowserPageInfo:
        if owner_id is not None:
            await self.acquire_human_control(session_id, reason="close_all_tabs", owner_id=owner_id)
        else:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    break
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            return await self.worker.close_all_tabs(session_id)

    async def new_tab(self, session_id: str, url: str = "https://www.baidu.com", *, owner_id: str | None = None) -> BrowserPageInfo:
        if owner_id is not None:
            await self.acquire_human_control(session_id, reason="new_tab", owner_id=owner_id)
        else:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    break
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            return await self.worker.new_tab(session_id, url)

    async def manual_input(
        self,
        session_id: str,
        *,
        event: str,
        payload: dict[str, Any],
        owner_id: str | None = None,
    ) -> BrowserPageInfo:
        reason = {
            "mouse_click": "click",
            "mouse_down": "drag",
            "mouse_move": "drag",
            "mouse_up": "drag",
            "key": "input",
            "text": "input",
            "scroll": "scroll",
        }.get(event, "input")
        async with self._session_lock(session_id):
            self._set_human_control_locked(session_id, reason=reason, owner_id=owner_id)
            self._snapshots.pop(session_id, None)
            return await self.worker.manual_input(session_id, event=event, payload=payload)

    async def click(
        self,
        session_id: str,
        *,
        target_ref: str,
        snapshot_id: str,
        approval_mode: str,
        confirmed: bool,
    ) -> BrowserToolResult:
        await self.set_ai_action(session_id, "clicking", f"正在点击元素 {target_ref}")
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    snapshot = self.cached_snapshot(session_id, snapshot_id)
                    result = await self.worker.click(
                        session_id,
                        target_ref=target_ref,
                        snapshot=snapshot,
                        approval_mode=approval_mode,
                        confirmed=confirmed,
                    )
                    self._snapshots.pop(session_id, None)
                    return result
        finally:
            await self.clear_ai_action(session_id)

    async def fill(
        self,
        session_id: str,
        *,
        target_ref: str,
        snapshot_id: str,
        value: str,
        sensitive: bool | None,
    ) -> BrowserToolResult:
        detail = "正在输入内容…" if sensitive else f"正在输入「{value[:20]}」"
        await self.set_ai_action(session_id, "filling", detail)
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    snapshot = self.cached_snapshot(session_id, snapshot_id)
                    result = await self.worker.fill(
                        session_id,
                        target_ref=target_ref,
                        value=value,
                        snapshot=snapshot,
                        sensitive=sensitive,
                    )
                    self._snapshots.pop(session_id, None)
                    return result
        finally:
            await self.clear_ai_action(session_id)

    async def press(
        self,
        session_id: str,
        *,
        target_ref: str | None,
        snapshot_id: str | None,
        key: str,
    ) -> BrowserToolResult:
        await self.set_ai_action(session_id, "pressing", f"正在发送按键 {key}")
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    snapshot = self.cached_snapshot(session_id, snapshot_id) if target_ref and snapshot_id else None
                    result = await self.worker.press(
                        session_id,
                        target_ref=target_ref,
                        key=key,
                        snapshot=snapshot,
                    )
                    self._snapshots.pop(session_id, None)
                    return result
        finally:
            await self.clear_ai_action(session_id)

    async def select_option(
        self,
        session_id: str,
        *,
        target_ref: str,
        snapshot_id: str,
        value: str | None,
        label: str | None,
    ) -> BrowserToolResult:
        await self.set_ai_action(session_id, "selecting", f"正在选择选项 {label or value}")
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    result = await self.worker.select_option(
                        session_id,
                        target_ref=target_ref,
                        value=value,
                        label=label,
                        snapshot=self.cached_snapshot(session_id, snapshot_id),
                    )
                    self._snapshots.pop(session_id, None)
                    return result
        finally:
            await self.clear_ai_action(session_id)

    async def hover(self, session_id: str, *, target_ref: str, snapshot_id: str) -> BrowserToolResult:
        await self.set_ai_action(session_id, "hovering", f"正在悬停元素 {target_ref}")
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    result = await self.worker.hover(
                        session_id,
                        target_ref=target_ref,
                        snapshot=self.cached_snapshot(session_id, snapshot_id),
                    )
                    self._snapshots.pop(session_id, None)
                    return result
        finally:
            await self.clear_ai_action(session_id)

    async def drag(
        self,
        session_id: str,
        *,
        source_ref: str,
        target_ref: str,
        snapshot_id: str,
    ) -> BrowserToolResult:
        await self.set_ai_action(session_id, "dragging", f"正在拖拽元素 {source_ref} -> {target_ref}")
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    result = await self.worker.drag(
                        session_id,
                        source_ref=source_ref,
                        target_ref=target_ref,
                        snapshot=self.cached_snapshot(session_id, snapshot_id),
                    )
                    self._snapshots.pop(session_id, None)
                    return result
        finally:
            await self.clear_ai_action(session_id)

    async def slider_drag(
        self,
        session_id: str,
        *,
        source_ref: str,
        snapshot_id: str,
        distance_px: int | None = None,
        gap_target_ref: str | None = None,
    ) -> BrowserToolResult:
        await self.set_ai_action(session_id, "dragging", f"正在拖拽滑块验证码 {source_ref}")
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    result = await self.worker.slider_drag(
                        session_id,
                        source_ref=source_ref,
                        snapshot=self.cached_snapshot(session_id, snapshot_id),
                        distance_px=distance_px,
                        gap_target_ref=gap_target_ref,
                    )
                    self._snapshots.pop(session_id, None)
                    return result
        finally:
            await self.clear_ai_action(session_id)

    async def wait_for(
        self,
        session_id: str,
        *,
        condition: str = "ready",
        value: str = "",
        target_ref: str | None = None,
        snapshot_id: str | None = None,
        timeout_ms: int = 5000,
    ) -> BrowserSnapshot:
        await self.set_ai_action(session_id, "waiting", "正在等待页面加载完成…")
        try:
            while True:
                await self._wait_for_ai_control(session_id, timeout_ms=timeout_ms)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    snapshot = (
                        self.cached_snapshot(session_id, snapshot_id)
                        if target_ref and snapshot_id
                        else None
                    )
                    result_snapshot = await self.worker.wait_for(
                        session_id,
                        condition=condition,
                        value=value,
                        target_ref=target_ref,
                        snapshot=snapshot,
                        timeout_ms=timeout_ms,
                    )
                    return self._remember_snapshot_locked(session_id, result_snapshot)
        finally:
            await self.clear_ai_action(session_id)

    async def read_visible(self, session_id: str) -> dict[str, Any]:
        async with self._session_lock(session_id):
            return await self.worker.read_visible(session_id)

    async def navigate_history(self, session_id: str, *, action: str) -> BrowserToolResult:
        if action not in {"back", "forward", "reload"}:
            raise ValueError("历史导航动作必须是 back、forward 或 reload")
        while True:
            await self._wait_for_ai_control(session_id)
            async with self._session_lock(session_id):
                if session_id in self._human_controls:
                    continue
                if action == "reload":
                    result = await self.worker.reload(session_id)
                else:
                    result = await getattr(self.worker, f"go_{action}")(session_id)
                self._snapshots.pop(session_id, None)
                return result

    async def upload(
        self,
        session_id: str,
        *,
        target_ref: str,
        snapshot_id: str,
        file_path: str,
    ) -> BrowserToolResult:
        while True:
            await self._wait_for_ai_control(session_id)
            async with self._session_lock(session_id):
                if session_id in self._human_controls:
                    continue
                result = await self.worker.upload(
                    session_id,
                    target_ref=target_ref,
                    file_path=file_path,
                    snapshot=self.cached_snapshot(session_id, snapshot_id),
                )
                self._snapshots.pop(session_id, None)
                return result

    async def download(
        self,
        session_id: str,
        *,
        target_ref: str,
        snapshot_id: str,
    ) -> BrowserToolResult:
        while True:
            await self._wait_for_ai_control(session_id)
            async with self._session_lock(session_id):
                if session_id in self._human_controls:
                    continue
                result = await self.worker.download(
                    session_id,
                    target_ref=target_ref,
                    snapshot=self.cached_snapshot(session_id, snapshot_id),
                )
                self._snapshots.pop(session_id, None)
                return result

    async def export_pdf(
        self,
        session_id: str,
        *,
        filename: str | None = None,
        print_background: bool = True,
    ) -> BrowserToolResult:
        await self.set_ai_action(session_id, "exporting_pdf", "AI 正在导出网页 PDF 文件")
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    return await self.worker.export_pdf(
                        session_id,
                        filename=filename,
                        print_background=print_background,
                    )
        finally:
            await self.clear_ai_action(session_id)

    async def extract_table(
        self,
        session_id: str,
        *,
        selector: str | None = None,
        max_rows: int = 50,
    ) -> BrowserToolResult:
        await self.set_ai_action(session_id, "extracting_table", "AI 正在提取结构化表格数据")
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    return await self.worker.extract_table(
                        session_id,
                        selector=selector,
                        max_rows=max_rows,
                    )
        finally:
            await self.clear_ai_action(session_id)

    async def handle_dialog(
        self,
        session_id: str,
        *,
        action: str = "accept",
        prompt_text: str | None = None,
    ) -> BrowserToolResult:
        while True:
            await self._wait_for_ai_control(session_id)
            async with self._session_lock(session_id):
                if session_id in self._human_controls:
                    continue
                return await self.worker.handle_dialog(
                    session_id,
                    action=action,
                    prompt_text=prompt_text,
                )

    async def execute_js(
        self,
        session_id: str,
        *,
        script: str,
    ) -> BrowserToolResult:
        await self.set_ai_action(session_id, "executing_js", "AI 正在执行页面脚本")
        try:
            while True:
                await self._wait_for_ai_control(session_id)
                async with self._session_lock(session_id):
                    if session_id in self._human_controls:
                        continue
                    result = await self.worker.execute_js(session_id, script=script)
                    self._snapshots.pop(session_id, None)
                    return result
        finally:
            await self.clear_ai_action(session_id)

    async def check_auth(self, session_id: str) -> BrowserToolResult:
        while True:
            await self._wait_for_ai_control(session_id)
            async with self._session_lock(session_id):
                if session_id in self._human_controls:
                    continue
                return await self.worker.check_auth(session_id)

    async def get_network_logs(
        self,
        session_id: str,
        *,
        filter_url: str | None = None,
        limit: int = 20,
    ) -> BrowserToolResult:
        while True:
            await self._wait_for_ai_control(session_id)
            async with self._session_lock(session_id):
                if session_id in self._human_controls:
                    continue
                return await self.worker.get_network_logs(
                    session_id,
                    filter_url=filter_url,
                    limit=limit,
                )

    async def get_cookies(
        self,
        session_id: str,
        *,
        urls: list[str] | None = None,
    ) -> BrowserToolResult:
        while True:
            await self._wait_for_ai_control(session_id)
            async with self._session_lock(session_id):
                if session_id in self._human_controls:
                    continue
                return await self.worker.get_cookies(session_id, urls=urls)

    async def set_cookies(
        self,
        session_id: str,
        *,
        cookies: list[dict[str, Any]],
    ) -> BrowserToolResult:
        while True:
            await self._wait_for_ai_control(session_id)
            async with self._session_lock(session_id):
                if session_id in self._human_controls:
                    continue
                result = await self.worker.set_cookies(session_id, cookies=cookies)
                self._snapshots.pop(session_id, None)
                return result

    async def close(self, session_id: str) -> None:
        task = self._captcha_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
        round_event = self._captcha_round_events.pop(session_id, None)
        if round_event is not None:
            round_event.set()
        async with self._session_lock(session_id):
            self._snapshots.pop(session_id, None)
            state = self._human_controls.pop(session_id, None)
            if state is not None:
                state.released.set()
            await self.worker.close(session_id)

    async def shutdown(self) -> None:
        self._snapshots.clear()
        for task in list(self._captcha_tasks.values()):
            task.cancel()
        self._captcha_tasks.clear()
        for round_event in self._captcha_round_events.values():
            round_event.set()
        self._captcha_round_events.clear()
        for state in self._human_controls.values():
            state.released.set()
        self._human_controls.clear()
        self._session_locks.clear()
        await self.worker.shutdown()


def _same_browser_url(left: str | None, right: str | None) -> bool:
    """避免根地址仅因尾部斜杠差异而重复导航。"""
    left_value = str(left or "").rstrip("/")
    right_value = str(right or "").rstrip("/")
    return bool(left_value) and left_value == right_value


browser_runtime = BrowserRuntime()
