"""面板帧 payload 与截图响应的契约。

面板只消费有限字段，此前用 model_dump() 全量下发，于是每 5 秒一帧都会把只有
Agent 才需要的 page_text（6000 字符）与 visible_text（12000 字符）推给前端，
纯占带宽。这里刻意只 import endpoints 模块本身，不经过 app.api.v1.api ——
后者会拉起整条 v1 路由（含 agentscope），与本契约无关。
"""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from starlette.requests import Request

from app.api.v1.endpoints.browser import _viewer_snapshot_payload
from app.api.v1.endpoints.browser import get_browser_screenshot
from app.schemas.browser import BrowserElement, BrowserSnapshot


pytestmark = pytest.mark.no_infrastructure


def _snapshot(screenshot_ref: str | None = "/tmp/frame.webp") -> BrowserSnapshot:
    return BrowserSnapshot(
        session_id="session-1",
        snapshot_id="frame-1",
        url="https://example.com/",
        title="Example",
        screenshot_ref=screenshot_ref,
        elements=[BrowserElement(ref="e1", role="button", name="百度一下")],
        page_state="ready",
        page_status="ready",
        viewport_width=1280,
        viewport_height=800,
        page_text="A" * 6000,
        visible_text="B" * 12000,
    )


def _screenshot_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/chat/browser/sessions/session-1/screenshot",
            "headers": [],
            "query_string": b"snapshot_id=frame-1",
        }
    )


def _patched_endpoint(runtime: SimpleNamespace):
    """按现有 test_browser_sessions.py 的口径 patch 掉 DB 与会话服务。"""
    session_context = Mock()
    session_context.__aenter__ = AsyncMock(return_value=AsyncMock())
    session_context.__aexit__ = AsyncMock(return_value=False)
    session_service = Mock()
    session_service.resolve_viewer_token = AsyncMock(
        return_value=SimpleNamespace(id="session-1")
    )
    stack = ExitStack()
    stack.enter_context(
        patch("app.api.v1.endpoints.browser.AsyncSessionLocal", return_value=session_context)
    )
    stack.enter_context(
        patch("app.api.v1.endpoints.browser.BrowserSessionService", return_value=session_service)
    )
    stack.enter_context(patch("app.api.v1.endpoints.browser.browser_runtime", runtime))
    return stack


async def _call_screenshot_endpoint(runtime: SimpleNamespace):
    with _patched_endpoint(runtime):
        return await get_browser_screenshot(
            "session-1",
            _screenshot_request(),
            token="viewer-token-12345678901234567890",
            snapshot_id="frame-1",
        )


def test_viewer_snapshot_payload_drops_agent_only_text_fields():
    """Agent 专用的大字段不得进入面板 payload，面板消费的字段一个都不能少。"""
    payload = _viewer_snapshot_payload("session-1", _snapshot())

    assert "page_text" not in payload
    assert "visible_text" not in payload

    for field in (
        "session_id",
        "snapshot_id",
        "url",
        "title",
        "page_state",
        "page_status",
        "scroll_x",
        "scroll_y",
        "can_go_back",
        "can_go_forward",
        "viewport_width",
        "viewport_height",
        "document_width",
        "document_height",
        "elements",
        "screenshot_ref",
    ):
        assert field in payload, f"面板依赖的字段被白名单漏掉：{field}"

    # 前端靠 bbox/name 渲染悬停元素标签
    assert payload["elements"][0]["name"] == "百度一下"
    assert payload["screenshot_ref"] == "/api/v1/chat/browser/sessions/session-1/screenshot"


def test_viewer_snapshot_payload_clears_screenshot_ref_without_a_frame():
    payload = _viewer_snapshot_payload("session-1", _snapshot(screenshot_ref=None))

    assert payload["screenshot_ref"] is None


@pytest.mark.asyncio
async def test_screenshot_endpoint_falls_back_to_viewer_frame_cache(tmp_path: Path):
    """面板帧在独立的 viewer 缓存里，Agent 快照表查不到时必须回落到它。

    面板帧刻意不写 Agent 的 snapshot 表（否则会挤掉 AI 正在用的 target_ref），
    因此截图端点必须同时认这两张表，否则画面直接 404。
    """
    frame_path = tmp_path / "frame.webp"
    frame_path.write_bytes(b"webp")
    frame = _snapshot(screenshot_ref=str(frame_path))
    runtime = SimpleNamespace(
        has_session=Mock(return_value=True),
        cached_snapshot=Mock(side_effect=ValueError("浏览器快照已过期，请先重新获取快照")),
        cached_viewer_frame=Mock(return_value=frame),
        snapshot=AsyncMock(),
    )

    response = await _call_screenshot_endpoint(runtime)

    assert response.path == str(frame_path)
    runtime.cached_viewer_frame.assert_called_once_with("session-1", "frame-1")
    runtime.snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_screenshot_response_declares_webp_and_immutable_cache(tmp_path: Path):
    """WebP 帧要以 image/webp 响应，并允许浏览器长缓存同一帧。"""
    frame_path = tmp_path / "frame.webp"
    frame_path.write_bytes(b"webp")
    frame = _snapshot(screenshot_ref=str(frame_path))
    runtime = SimpleNamespace(
        has_session=Mock(return_value=True),
        cached_snapshot=Mock(return_value=frame),
        cached_viewer_frame=Mock(return_value=frame),
        snapshot=AsyncMock(),
    )

    response = await _call_screenshot_endpoint(runtime)

    assert response.media_type == "image/webp"
    # 文件名带 snapshot_id、内容永不变化，因此可以放心长缓存
    assert response.headers["cache-control"] == "private, max-age=60, immutable"


@pytest.mark.asyncio
async def test_screenshot_response_keeps_jpeg_regression(tmp_path: Path):
    """降级到 JPEG 时仍按 image/jpeg 响应（老环境兜底路径不能坏）。"""
    frame_path = tmp_path / "frame.jpeg"
    frame_path.write_bytes(b"jpeg")
    frame = _snapshot(screenshot_ref=str(frame_path))
    runtime = SimpleNamespace(
        has_session=Mock(return_value=True),
        cached_snapshot=Mock(return_value=frame),
        cached_viewer_frame=Mock(return_value=frame),
        snapshot=AsyncMock(),
    )

    response = await _call_screenshot_endpoint(runtime)

    assert response.media_type == "image/jpeg"
