from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin, require_api_key
from app.core.orm import AsyncSessionLocal, get_db_session
from app.schemas.browser import (
    BrowserPolicyUpdateRequest,
    BrowserProfileResponse,
    BrowserSessionOpenRequest,
    BrowserSessionResponse,
)
from app.services.ai.browser import BrowserEnvironmentError
from app.services.ai.browser.browser_policy import BrowserUrlBlocked
from app.services.ai.browser.browser_runtime import (
    BrowserControlConflict,
    BrowserHumanControlRequired,
)
from app.services.ai.browser.browser_runtime import browser_runtime
from app.services.ai.browser.browser_profile_service import (
    BrowserProfileAccessDenied,
    BrowserProfileService,
)
from app.services.ai.browser.browser_session_service import (
    BrowserAccessDenied,
    BrowserSessionService,
)


router = APIRouter()
viewer_router = APIRouter()
logger = logging.getLogger(__name__)
_browser_install_lock = asyncio.Lock()


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _browser_install_commands() -> list[list[str]]:
    """生成适配 uv 虚拟环境的固定安装命令，不依赖 shell 或用户输入。"""
    uv = shutil.which("uv")
    if uv:
        dependency_command = [uv, "pip", "install", "--python", sys.executable, "playwright"]
    else:
        dependency_command = [sys.executable, "-m", "pip", "install", "playwright"]
    return [dependency_command, [sys.executable, "-m", "playwright", "install", "chromium"]]


def _user_id(user_info: dict[str, Any]) -> int:
    raw_user_id = user_info.get("user_id") or user_info.get("id")
    if raw_user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少用户身份")
    try:
        return int(raw_user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户身份无效") from exc


def _viewer_cookie_name(session_id: str) -> str:
    return f"browser_viewer_{session_id}"


@router.get("/environment")
async def get_browser_environment(
    user_info: dict[str, Any] = Depends(require_api_key),
):
    """查询当前服务端的 Playwright 与 Chromium 运行环境状态、版本及路径。"""
    return await browser_runtime.check_environment()


@router.post("/environment/install/stream")
async def install_browser_environment(
    user_info: dict[str, Any] = Depends(require_admin),
):
    """管理员在当前服务端运行环境安装 Playwright 与 Chromium，并实时返回日志。"""

    async def stream_install() -> Any:
        if _browser_install_lock.locked():
            yield _sse({"type": "error", "message": "已有浏览器环境安装任务正在运行"})
            return

        async with _browser_install_lock:
            yield _sse({"type": "status", "status": "running", "message": "开始安装服务端浏览器环境"})
            commands = _browser_install_commands()
            for index, command in enumerate(commands, start=1):
                yield _sse({"type": "status", "status": "running", "step": index, "message": "执行：" + " ".join(command[1:])})
                try:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                    )
                except Exception as exc:
                    yield _sse({"type": "error", "message": f"无法启动安装进程：{exc}"})
                    return

                assert process.stdout is not None
                async for raw_line in process.stdout:
                    line = raw_line.decode("utf-8", errors="replace").rstrip()
                    if line:
                        yield _sse({"type": "log", "line": line})
                return_code = await process.wait()
                if return_code != 0:
                    yield _sse({"type": "error", "message": f"安装步骤失败，退出码：{return_code}"})
                    return
            yield _sse({"type": "done", "status": "success", "message": "服务端浏览器环境安装完成"})

    return StreamingResponse(
        stream_install(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/profiles", response_model=list[BrowserProfileResponse])
async def list_browser_profiles(
    user_info: dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    service = BrowserProfileService(db)
    return await service.list_owned(user_id=_user_id(user_info))


@router.delete("/profiles/clear")
async def clear_browser_profiles(
    user_info: dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = _user_id(user_info)
    service = BrowserProfileService(db)
    profiles = await service.list_owned(user_id=user_id)
    session_service = BrowserSessionService(db)
    active_sessions = await session_service.list_owned_active(user_id=user_id)
    for session in active_sessions:
        await session_service.close(user_id=user_id, session_id=session.id, destroy_profile=True)
        await browser_runtime.close(session.id)
    for profile in profiles:
        try:
            await service.delete_owned(user_id=user_id, profile_id=profile.id)
        except Exception:
            pass
    return {"success": True, "message": "浏览器历史、登录态及本地缓存已彻底清除"}


@router.post("/sessions/open", response_model=BrowserSessionResponse)
async def open_browser_session(
    payload: BrowserSessionOpenRequest,
    user_info: dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        service = BrowserSessionService(db)
        session = await service.open_or_resume(
            user_id=_user_id(user_info),
            conversation_id=payload.conversation_id,
            url=payload.url,
            profile_id=payload.profile_id,
        )
        await browser_runtime.open_session(db, session)
        return session
    except BrowserUrlBlocked as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except BrowserProfileAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except BrowserHumanControlRequired as exc:
        # 忙碌冲突不是环境故障：必须回 409，否则前端会误报"运行环境未就绪"。
        # 该异常当前只有一个来源——AI 正在自动解算验证码、其他操作让行超时（人工接管
        # 超时会自动释放控制权，不会走到这里），所以提示要引导"稍等"，而不是去点「交还 AI」。
        logger.info("Browser session is busy while solving captcha: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            # 异常消息本身已含完整引导（"…请稍等几秒后重试"），此处不再追加同类文案，
            # 否则用户会连续读到两遍"请稍等几秒"。
            detail=str(exc),
        ) from exc
    except BrowserEnvironmentError as exc:
        logger.warning("Browser environment missing: %s", exc)
        # 带上可判定的标记：前端据此展示"安装 Playwright/Chromium"引导，
        # 而不是把任何 503 都当成环境问题（启动失败、人工接管冲突另有语义）
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Browser-Error": "environment_not_ready"},
        ) from exc
    except Exception as exc:
        logger.exception("Failed to open browser session")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="浏览器启动失败，请稍后重试") from exc


@router.get("/sessions/active", response_model=list[BrowserSessionResponse])
async def list_active_browser_sessions(
    user_info: dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    service = BrowserSessionService(db)
    return await service.list_owned_active(user_id=_user_id(user_info))


@router.get("/sessions/{session_id}", response_model=BrowserSessionResponse)
async def get_browser_session(
    session_id: str,
    user_info: dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await BrowserSessionService(db).get_owned_session(
            user_id=_user_id(user_info), session_id=session_id
        )
    except BrowserAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/sessions/{session_id}/policy", response_model=BrowserSessionResponse)
async def update_browser_policy(
    session_id: str,
    payload: BrowserPolicyUpdateRequest,
    user_info: dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await BrowserSessionService(db).set_approval_mode(
            user_id=_user_id(user_info), session_id=session_id, mode=payload.approval_mode
        )
    except BrowserAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/detach", response_model=BrowserSessionResponse)
async def detach_browser_session(
    session_id: str,
    user_info: dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await BrowserSessionService(db).detach(
            user_id=_user_id(user_info), session_id=session_id
        )
    except BrowserAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/continue", response_model=BrowserSessionResponse)
async def continue_browser_session(
    session_id: str,
    user_info: dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        session = await BrowserSessionService(db).get_owned_session(
            user_id=_user_id(user_info), session_id=session_id
        )
        session.status = "active"
        await db.flush()
        return session
    except BrowserAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/sessions/{session_id}")
async def close_browser_session(
    session_id: str,
    destroy_profile: bool = Query(False),
    user_info: dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = _user_id(user_info)
    try:
        session = await BrowserSessionService(db).get_owned_session(
            user_id=user_id, session_id=session_id
        )
        await BrowserSessionService(db).close(
            user_id=user_id,
            session_id=session_id,
            destroy_profile=destroy_profile,
        )
        await browser_runtime.close(session.id)
        return {"success": True, "session_id": session_id}
    except BrowserAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/viewer-token")
async def issue_browser_viewer_token(
    session_id: str,
    request: Request,
    response: Response,
    user_info: dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        token, expires_at = await BrowserSessionService(db).issue_viewer_token(
            user_id=_user_id(user_info), session_id=session_id
        )
        response.set_cookie(
            key=_viewer_cookie_name(session_id),
            value=token,
            max_age=30 * 60,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path=f"/api/v1/chat/browser/sessions/{session_id}",
        )
        return {"session_id": session_id, "token": token, "expires_at": expires_at}
    except BrowserAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/snapshot")
async def get_browser_snapshot(
    session_id: str,
    user_info: dict[str, Any] = Depends(require_api_key),
):
    user_id = _user_id(user_info)
    async with AsyncSessionLocal() as db:
        try:
            session = await BrowserSessionService(db).get_owned_session(
                user_id=user_id, session_id=session_id
            )
            if not browser_runtime.has_session(session.id):
                await browser_runtime.open_session(db, session)
            snapshot = await browser_runtime.snapshot(session.id)
            await db.commit()
            payload = snapshot.model_dump(mode="json")
            payload["screenshot_ref"] = None
            return payload
        except BrowserAccessDenied as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@viewer_router.get("/sessions/{session_id}/screenshot")
async def get_browser_screenshot(
    session_id: str,
    request: Request,
    token: str | None = Query(None, min_length=20),
    snapshot_id: str | None = Query(None, min_length=8, max_length=128),
):
    viewer_token = token or request.cookies.get(_viewer_cookie_name(session_id))
    if not viewer_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少浏览器查看令牌")
    async with AsyncSessionLocal() as db:
        try:
            session = await BrowserSessionService(db).resolve_viewer_token(viewer_token)
            if session.id != session_id:
                raise BrowserAccessDenied("浏览器查看令牌与会话不匹配")
            if snapshot_id:
                if not browser_runtime.has_session(session.id):
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器截图已过期，请刷新页面")
                try:
                    snapshot = browser_runtime.cached_snapshot(session.id, snapshot_id)
                except ValueError as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器截图已过期，请刷新页面") from exc
            else:
                if not browser_runtime.has_session(session.id):
                    await browser_runtime.open_session(db, session)
                snapshot = await _viewer_snapshot(session.id)
                await db.commit()
        except BrowserAccessDenied as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    screenshot_ref = snapshot.screenshot_ref
    if not screenshot_ref or not Path(screenshot_ref).is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器截图不存在")
    is_jpeg = screenshot_ref.endswith(".jpeg") or screenshot_ref.endswith(".jpg")
    media_type = "image/jpeg" if is_jpeg else "image/png"
    filename = f"{session_id}.jpeg" if is_jpeg else f"{session_id}.png"
    return FileResponse(screenshot_ref, media_type=media_type, filename=filename)


def _viewer_snapshot_payload(session_id: str, snapshot) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    if snapshot.screenshot_ref:
        payload["screenshot_ref"] = f"/api/v1/chat/browser/sessions/{session_id}/screenshot"
    return payload


# Viewer 路径的验证码自动解算后台任务（按 session 去重）
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
    """Viewer 取快照：先返回画面，验证码解算交给后台任务，避免阻塞人工操作。"""
    snapshot = await browser_runtime.snapshot(session_id, auto_solve=False)
    if snapshot.page_state == "captcha":
        _schedule_captcha_auto_solve(session_id)
    return snapshot


def _viewer_token_from_websocket(websocket: WebSocket) -> tuple[str | None, str | None]:
    protocols = [item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    for protocol in protocols:
        if protocol.startswith("browser-viewer."):
            return protocol[len("browser-viewer."):], protocol
    return websocket.query_params.get("token"), None


def _viewer_origin_allowed(websocket: WebSocket) -> bool:
    """限制浏览器端 WebSocket 只能来自当前站点或显式配置的前端站点。"""
    origin = str(websocket.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        # 非浏览器客户端通常没有 Origin，仍由 viewer token 负责认证。
        return True
    configured_origins = {
        item.strip().rstrip("/")
        for item in os.environ.get("BROWSER_VIEWER_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    if "*" in configured_origins or origin in configured_origins:
        return True
    parsed = urlsplit(origin)
    host = str(websocket.headers.get("host") or "")
    return parsed.scheme in {"http", "https"} and bool(host) and parsed.netloc == host


_VIEWER_MAX_MESSAGE_BYTES = 64 * 1024
_VIEWER_RATE_WINDOW_SECONDS = 10.0
_VIEWER_MAX_MESSAGES_PER_WINDOW = 120
_VIEWER_MAX_MOUSE_MOVES_PER_WINDOW = 900


def _accept_viewer_message(
    message: Any,
    timestamps: deque[float],
    *,
    mouse_move_timestamps: deque[float] | None = None,
    now: float | None = None,
) -> tuple[bool, str | None]:
    """校验单条查看器输入，限制消息体大小和单连接事件速率。"""
    if not isinstance(message, dict):
        return False, "浏览器输入消息格式无效"
    try:
        message_size = len(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, OverflowError):
        return False, "浏览器输入消息格式无效"
    if message_size > _VIEWER_MAX_MESSAGE_BYTES:
        return False, "浏览器输入消息过大"

    current_time = time.monotonic() if now is None else now
    cutoff = current_time - _VIEWER_RATE_WINDOW_SECONDS
    event_type = str(message.get("type") or "")
    rate_timestamps = (
        mouse_move_timestamps
        if event_type == "mouse_move" and mouse_move_timestamps is not None
        else timestamps
    )
    max_messages = (
        _VIEWER_MAX_MOUSE_MOVES_PER_WINDOW
        if event_type == "mouse_move" and mouse_move_timestamps is not None
        else _VIEWER_MAX_MESSAGES_PER_WINDOW
    )
    while rate_timestamps and rate_timestamps[0] <= cutoff:
        rate_timestamps.popleft()
    if len(rate_timestamps) >= max_messages:
        return False, "浏览器输入过于频繁"
    rate_timestamps.append(current_time)
    return True, None


async def _send_viewer_control_state(
    websocket: WebSocket,
    session_id: str,
    snapshot=None,
) -> None:
    state = browser_runtime.control_state(session_id)
    await websocket.send_json({"type": "control_state", **state})
    if snapshot is not None:
        detected = snapshot.page_state == "captcha"
        await websocket.send_json(
            {
                "type": "captcha",
                "detected": detected,
                "reason": "页面要求人工完成安全验证" if detected else None,
            }
        )

async def _send_viewer_tabs(websocket: WebSocket, session_id: str) -> None:
    try:
        tabs = await browser_runtime.list_tabs(session_id)
        await websocket.send_json(
            {
                "type": "tabs",
                "tabs": [
                    {
                        "tab_id": tab.tab_id,
                        "url": tab.url,
                        "title": tab.title,
                        "active": tab.active,
                    }
                    for tab in tabs
                ],
            }
        )
    except Exception:
        pass


async def _forward_runtime_events(
    websocket: WebSocket,
    event_queue: asyncio.Queue,
) -> None:
    while True:
        try:
            event_data = await event_queue.get()
            await websocket.send_json(event_data)
        except asyncio.CancelledError:
            break
        except Exception:
            break


@viewer_router.websocket("/sessions/{session_id}/viewer")
async def browser_viewer(websocket: WebSocket, session_id: str):
    if not _viewer_origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    token, selected_protocol = _viewer_token_from_websocket(websocket)
    if not token:
        await websocket.close(code=4403)
        return
    async with AsyncSessionLocal() as db:
        should_release_control = False
        forward_task: asyncio.Task | None = None
        event_queue: asyncio.Queue | None = None
        message_timestamps: deque[float] = deque()
        mouse_move_timestamps: deque[float] = deque()
        try:
            session = await BrowserSessionService(db).resolve_viewer_token(token)
            if session.id != session_id:
                raise BrowserAccessDenied("浏览器查看令牌与会话不匹配")
            viewer_connection_id = uuid.uuid4().hex
            await websocket.accept(subprotocol=selected_protocol)
            should_release_control = True
            event_queue = browser_runtime.subscribe_events(session.id)
            forward_task = asyncio.create_task(_forward_runtime_events(websocket, event_queue))
            if not browser_runtime.has_session(session.id):
                await browser_runtime.open_session(db, session)
            snapshot = await _viewer_snapshot(session.id)
            await db.commit()
            await websocket.send_json({"type": "snapshot", "snapshot": _viewer_snapshot_payload(session_id, snapshot)})
            await _send_viewer_control_state(websocket, session.id, snapshot)
            await _send_viewer_tabs(websocket, session.id)
            current_action = browser_runtime.get_ai_action(session.id)
            if current_action:
                await websocket.send_json({"type": "ai_action", **current_action})

            while True:
                message = await websocket.receive_json()
                accepted, input_error = _accept_viewer_message(
                    message,
                    message_timestamps,
                    mouse_move_timestamps=mouse_move_timestamps,
                )
                if not accepted:
                    await websocket.send_json({"type": "error", "message": input_error})
                    if input_error in {"浏览器输入消息过大", "浏览器输入过于频繁"}:
                        await websocket.close(code=1009 if input_error.endswith("过大") else 4429)
                        break
                    continue
                event = str(message.get("type", ""))
                try:
                    if event == "snapshot":
                        snapshot = await _viewer_snapshot(session.id)
                    elif event in {"mouse_click", "mouse_down", "mouse_move", "mouse_up", "key", "text", "scroll"}:
                        info = await browser_runtime.manual_input(
                            session.id,
                            event=event,
                            payload=message,
                            owner_id=viewer_connection_id,
                        )
                        session.current_url = info.url
                        session.page_title = info.title
                        session.last_seen_at = datetime.now()
                        await db.commit()
                        if event != "mouse_move":
                            await _send_viewer_control_state(websocket, session.id)
                        if event in {"mouse_down", "mouse_move"}:
                            continue
                        if event == "mouse_click":
                            await websocket.send_json({"type": "focus", "focused_input": info.focused_input})
                        snapshot = await _viewer_snapshot(session.id)
                    elif event == "release_control":
                        await browser_runtime.release_human_control(
                            session.id,
                            owner_id=viewer_connection_id,
                        )
                        snapshot = await _viewer_snapshot(session.id)
                    elif event == "navigate":
                        info = await browser_runtime.navigate(
                            session.id,
                            str(message.get("url", "")),
                            owner_id=viewer_connection_id,
                        )
                        session.current_url = info.url
                        session.page_title = info.title
                        session.last_seen_at = datetime.now()
                        await db.commit()
                        snapshot = await _viewer_snapshot(session.id)
                    elif event in {"go_back", "go_forward", "reload"}:
                        if event == "go_back":
                            result = await browser_runtime.go_back(session.id, owner_id=viewer_connection_id)
                        elif event == "go_forward":
                            result = await browser_runtime.go_forward(session.id, owner_id=viewer_connection_id)
                        else:
                            result = await browser_runtime.reload(session.id, owner_id=viewer_connection_id)
                        session.current_url = result.url
                        session.page_title = result.title
                        session.last_seen_at = datetime.now()
                        await db.commit()
                        snapshot = await _viewer_snapshot(session.id)
                    elif event == "switch_tab":
                        tab_id = str(message.get("tab_id", ""))
                        info = await browser_runtime.switch_tab(session.id, tab_id, owner_id=viewer_connection_id)
                        session.current_url = info.url
                        session.page_title = info.title
                        session.last_seen_at = datetime.now()
                        await db.commit()
                        snapshot = await _viewer_snapshot(session.id)
                    elif event == "close_tab":
                        tab_id = str(message.get("tab_id", ""))
                        info = await browser_runtime.close_tab(session.id, tab_id, owner_id=viewer_connection_id)
                        session.current_url = info.url
                        session.page_title = info.title
                        session.last_seen_at = datetime.now()
                        await db.commit()
                        snapshot = await _viewer_snapshot(session.id)
                    elif event == "close_other_tabs":
                        tab_id = str(message.get("tab_id", ""))
                        info = await browser_runtime.close_other_tabs(session.id, tab_id, owner_id=viewer_connection_id)
                        session.current_url = info.url
                        session.page_title = info.title
                        session.last_seen_at = datetime.now()
                        await db.commit()
                        snapshot = await _viewer_snapshot(session.id)
                    elif event == "close_tabs_to_right":
                        tab_id = str(message.get("tab_id", ""))
                        info = await browser_runtime.close_tabs_to_right(session.id, tab_id, owner_id=viewer_connection_id)
                        session.current_url = info.url
                        session.page_title = info.title
                        session.last_seen_at = datetime.now()
                        await db.commit()
                        snapshot = await _viewer_snapshot(session.id)
                    elif event == "close_all_tabs":
                        info = await browser_runtime.close_all_tabs(session.id, owner_id=viewer_connection_id)
                        session.current_url = info.url
                        session.page_title = info.title
                        session.last_seen_at = datetime.now()
                        await db.commit()
                        snapshot = await _viewer_snapshot(session.id)
                    elif event == "new_tab":
                        target_url = str(message.get("url", "https://www.baidu.com"))
                        info = await browser_runtime.new_tab(session.id, target_url, owner_id=viewer_connection_id)
                        session.current_url = info.url
                        session.page_title = info.title
                        session.last_seen_at = datetime.now()
                        await db.commit()
                        snapshot = await _viewer_snapshot(session.id)
                    elif event == "semantic_click":
                        result = await browser_runtime.click(
                            session.id,
                            target_ref=str(message.get("target_ref", "")),
                            snapshot_id=str(message.get("snapshot_id", "")),
                            approval_mode=session.approval_mode,
                            confirmed=True,
                        )
                        session.current_url = result.url
                        session.page_title = result.title
                        session.last_seen_at = datetime.now()
                        session.updated_at = datetime.now()
                        await db.commit()
                        snapshot = await _viewer_snapshot(session.id)
                    elif event == "semantic_fill":
                        result = await browser_runtime.fill(
                            session.id,
                            target_ref=str(message.get("target_ref", "")),
                            snapshot_id=str(message.get("snapshot_id", "")),
                            value=str(message.get("value", "")),
                            sensitive=bool(message.get("sensitive", False)),
                        )
                        session.current_url = result.url
                        session.page_title = result.title
                        session.last_seen_at = datetime.now()
                        session.updated_at = datetime.now()
                        await db.commit()
                        snapshot = await _viewer_snapshot(session.id)
                    else:
                        await websocket.send_json({"type": "error", "message": "不支持的浏览器输入事件"})
                        continue
                    await _send_viewer_control_state(websocket, session.id, snapshot)
                    await websocket.send_json({"type": "snapshot", "snapshot": _viewer_snapshot_payload(session_id, snapshot)})
                    await _send_viewer_tabs(websocket, session.id)
                except BrowserControlConflict:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "浏览器正在由另一个连接控制，请先等待其他浏览器面板释放控制权",
                        }
                    )
                except Exception:
                    logger.exception("Browser viewer operation failed")
                    await websocket.send_json({"type": "error", "message": "浏览器操作失败，请刷新后重试"})
        except BrowserAccessDenied:
            await websocket.close(code=4403)
        except WebSocketDisconnect:
            return
        except Exception as exc:
            logger.exception("Browser viewer operation failed")
            await websocket.send_json({"type": "error", "message": "浏览器操作失败，请刷新后重试"})
        finally:
            if forward_task is not None:
                forward_task.cancel()
            if event_queue is not None:
                browser_runtime.unsubscribe_events(session_id, event_queue)
            if should_release_control:
                await browser_runtime.release_human_control(
                    session_id,
                    owner_id=viewer_connection_id,
                )
