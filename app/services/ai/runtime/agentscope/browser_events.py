import json
import re
from typing import Any


def _extract_field_fallback(raw: str, field_name: str) -> str | None:
    pattern = rf'"{field_name}"\s*:\s*"((?:[^"\\]|\\.)*)"'
    match = re.search(pattern, raw)
    if match:
        try:
            return json.loads(f'"{match.group(1)}"')
        except Exception:
            return match.group(1)
    return None


def _fallback_from_agent_context() -> dict[str, Any] | None:
    try:
        from app.core.context import get_current_agent_context

        ctx = get_current_agent_context()
        if ctx and getattr(ctx, "browser_session_id", None):
            return {
                "session_id": str(ctx.browser_session_id),
                "url": None,
                "title": None,
                "approval_mode": None,
            }
    except Exception:
        pass
    return None


def _browser_result_payload(output: Any) -> dict[str, Any] | None:
    raw = output.get("text") if isinstance(output, dict) else output
    raw_str = str(raw or "").strip()
    if not raw_str:
        return _fallback_from_agent_context()

    try:
        payload = json.loads(raw_str)
        if isinstance(payload, dict):
            return payload
    except (TypeError, ValueError):
        pass

    # 兜底：工具输出过大（快照 DOM elements 过多）被上下文截断时，通过正则提取关键头部字段
    session_id = _extract_field_fallback(raw_str, "session_id")
    if session_id:
        return {
            "session_id": session_id,
            "url": _extract_field_fallback(raw_str, "url"),
            "title": _extract_field_fallback(raw_str, "title"),
            "approval_mode": _extract_field_fallback(raw_str, "approval_mode"),
        }

    return _fallback_from_agent_context()


def build_browser_session_event(tool_name: str, output: Any) -> dict[str, Any] | None:
    """把 browser_open 工具结果转换为不携带 viewer token 的面板事件。"""
    if str(tool_name or "") != "browser_open":
        return None
    payload = _browser_result_payload(output)
    if payload is None:
        return None
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return None
    event = {
        "type": "browser_session",
        "session_id": session_id,
        "url": payload.get("url"),
        "title": payload.get("title"),
    }
    approval_mode = payload.get("approval_mode")
    if approval_mode in {"guarded", "autopilot"}:
        event["approval_mode"] = approval_mode
    return event


def build_browser_refresh_event(tool_name: str, output: Any) -> dict[str, Any] | None:
    """通知已连接的浏览器面板刷新 AI 操作后的页面，不透传工具结果。"""
    if str(tool_name or "") not in {
        "browser_click",
        "browser_fill",
        "browser_scroll",
        "browser_press",
        "browser_wait_for",
        "browser_select_option",
        "browser_hover",
        "browser_drag",
        "browser_slider_drag",
        "browser_back",
        "browser_forward",
        "browser_reload",
        "browser_switch_tab",
        "browser_close_tab",
        "browser_upload",
        "browser_download",
        "browser_execute_js",
        "browser_handle_dialog",
        "browser_set_cookies",
    }:
        return None
    payload = _browser_result_payload(output)
    if payload is None:
        return None
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return None
    return {"type": "browser_refresh", "session_id": session_id}
