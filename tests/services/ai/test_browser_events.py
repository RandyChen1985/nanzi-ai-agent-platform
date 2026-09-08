import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agentscope.permission import PermissionBehavior

from app.core import context as core_context
from app.schemas.browser import BrowserElement, BrowserSnapshot, BrowserToolResult
from app.services.ai.browser.browser_runtime import browser_runtime
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.ai.runtime.agentscope.tools import _browser_permission_decision
from app.services.ai.runtime.agentscope.tools import _redact_runtime_tool_arguments
from app.services.ai.runtime.agentscope.browser_events import build_browser_refresh_event
from app.services.ai.runtime.agentscope.browser_events import build_browser_session_event
from app.services.ai.tools.browser_tools import (
    browser_back,
    browser_click,
    browser_close_tab,
    browser_download,
    browser_drag,
    browser_fill,
    browser_forward,
    browser_hover,
    browser_open,
    browser_press,
    browser_read_visible,
    browser_reload,
    browser_scroll,
    browser_select_option,
    browser_snapshot,
    browser_switch_tab,
    browser_tabs,
    browser_upload,
    browser_wait_for,
)
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.tools import browser_tools as browser_tools_module


pytestmark = pytest.mark.no_infrastructure


def test_browser_open_result_emits_session_event_without_viewer_token():
    event = build_browser_session_event(
        "browser_open",
        '{"session_id":"bs-1","url":"https://www.baidu.com/","title":"百度"}',
    )
    assert event == {
        "type": "browser_session",
        "session_id": "bs-1",
        "url": "https://www.baidu.com/",
        "title": "百度",
    }


def test_non_browser_tool_does_not_emit_browser_event():
    assert build_browser_session_event("browser_snapshot", "{}") is None


def test_browser_action_result_emits_refresh_event_without_sensitive_output():
    event = build_browser_refresh_event(
        "browser_click",
        '{"session_id":"bs-1","url":"https://www.baidu.com/s?wd=test"}',
    )

    assert event == {
        "type": "browser_refresh",
        "session_id": "bs-1",
    }
    assert build_browser_refresh_event("browser_snapshot", "{}") is None


def test_browser_scroll_result_emits_refresh_event():
    event = build_browser_refresh_event(
        "browser_scroll",
        '{"session_id":"bs-1","snapshot_id":"snap-2","scroll_y":640}',
    )

    assert event == {
        "type": "browser_refresh",
        "session_id": "bs-1",
    }


@pytest.mark.parametrize(
    "tool_name",
    [
        "browser_press",
        "browser_wait_for",
        "browser_select_option",
        "browser_hover",
        "browser_drag",
        "browser_back",
        "browser_forward",
        "browser_reload",
        "browser_switch_tab",
        "browser_close_tab",
        "browser_upload",
        "browser_download",
    ],
)
def test_all_state_changing_browser_tools_refresh_the_viewer(tool_name):
    event = build_browser_refresh_event(tool_name, '{"session_id":"bs-1"}')

    assert event == {"type": "browser_refresh", "session_id": "bs-1"}


def test_browser_tools_are_explicit_first_party_agent_tools():
    names = {getattr(tool, "name", "") for tool in ToolRegistry.get_system_implicit_tools()}
    assert not {
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_fill",
        "browser_scroll",
        "browser_press",
        "browser_wait_for",
        "browser_select_option",
        "browser_read_visible",
        "browser_hover",
        "browser_drag",
        "browser_back",
        "browser_forward",
        "browser_reload",
        "browser_tabs",
        "browser_switch_tab",
        "browser_close_tab",
        "browser_upload",
        "browser_download",
    }.intersection(names)


def test_browser_tools_do_not_expose_confirmation_or_sensitive_override():
    assert "confirmed" not in browser_click.args_schema.model_fields
    assert "sensitive" not in browser_fill.args_schema.model_fields


def test_browser_upload_audit_redacts_local_file_path():
    payload = _redact_runtime_tool_arguments(
        "browser_upload",
        {"file_path": "/private/user/workspace/secret.pdf", "target_ref": "e1"},
    )

    assert payload == {"file_path": "<redacted>", "target_ref": "e1"}


def test_browser_set_cookies_audit_redacts_cookie_values():
    payload = _redact_runtime_tool_arguments(
        "browser_set_cookies",
        {
            "cookies": [
                {"name": "session", "value": "secret-token", "domain": "example.com"},
            ],
        },
    )

    assert payload["cookies"] == [
        {"name": "session", "value": "<redacted>", "domain": "example.com"},
    ]


def test_browser_dialog_audit_redacts_prompt_text():
    payload = _redact_runtime_tool_arguments(
        "browser_handle_dialog",
        {"action": "accept", "prompt_text": "secret-answer"},
    )

    assert payload == {"action": "accept", "prompt_text": "<redacted>"}


@pytest.mark.asyncio
async def test_browser_tab_tools_return_page_info(monkeypatch):
    class FakeContext:
        user_id = 1
        browser_session_id = "bs-1"

    session = SimpleNamespace(id="bs-1")
    info = SimpleNamespace(url="https://example.com/next", title="Next")
    monkeypatch.setattr(browser_tools_module, "get_current_agent_context", lambda: FakeContext())
    monkeypatch.setattr(browser_tools_module, "_owned_session", AsyncMock(return_value=session))
    monkeypatch.setattr(
        browser_tools_module,
        "_browser_result_json",
        AsyncMock(side_effect=lambda _context, result: json.dumps(result.model_dump(mode="json"))),
    )
    monkeypatch.setattr(browser_runtime, "switch_tab", AsyncMock(return_value=info))
    monkeypatch.setattr(browser_runtime, "close_tab", AsyncMock(return_value=info))

    switched = json.loads(await browser_switch_tab.ainvoke({"tab_id": "tab-2"}))
    closed = json.loads(await browser_close_tab.ainvoke({"tab_id": "tab-1"}))

    assert switched["action"] == "switch_tab"
    assert closed["action"] == "close_tab"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["browser_execute_js", "browser_set_cookies"])
async def test_guarded_browser_sensitive_tools_require_session_confirmation(monkeypatch, tool_name):
    class FakeContext:
        browser_session_id = "bs-1"
        user_id = 1

    class DbContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    service = SimpleNamespace(
        get_owned_session=AsyncMock(return_value=SimpleNamespace(approval_mode="guarded"))
    )
    monkeypatch.setattr(core_context, "get_current_agent_context", lambda: FakeContext())
    monkeypatch.setattr("app.core.orm.AsyncSessionLocal", lambda: DbContext())
    monkeypatch.setattr(
        "app.services.ai.browser.browser_session_service.BrowserSessionService",
        lambda _db: service,
    )

    decision = await _browser_permission_decision(tool_name, {})

    assert decision.behavior == PermissionBehavior.ASK
    assert decision.decision_reason == "guarded_browser_sensitive_tool"


@pytest.mark.asyncio
async def test_guarded_enter_from_textbox_requires_confirmation(monkeypatch):
    class FakeContext:
        browser_session_id = "bs-1"
        user_id = 1

    class DbContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    snapshot = BrowserSnapshot(
        session_id="bs-1",
        snapshot_id="snap-1",
        url="https://example.com/",
        title="Example",
        elements=[BrowserElement(ref="e1", role="textbox", name="关键词")],
    )
    service = SimpleNamespace(
        get_owned_session=AsyncMock(return_value=SimpleNamespace(approval_mode="guarded"))
    )
    monkeypatch.setattr(core_context, "get_current_agent_context", lambda: FakeContext())
    monkeypatch.setattr(browser_runtime, "cached_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr("app.core.orm.AsyncSessionLocal", lambda: DbContext())
    monkeypatch.setattr(
        "app.services.ai.browser.browser_session_service.BrowserSessionService",
        lambda _db: service,
    )

    decision = await _browser_permission_decision(
        "browser_press",
        {"snapshot_id": "snap-1", "target_ref": "e1", "key": "Enter"},
    )

    assert decision.behavior == PermissionBehavior.ASK
    assert decision.decision_reason == "guarded_browser_commit"


@pytest.mark.asyncio
async def test_browser_scroll_tool_returns_the_fresh_snapshot(monkeypatch):
    class FakeContext:
        user_id = 1

    snapshot = BrowserSnapshot(
        session_id="bs-1",
        snapshot_id="snap-scroll",
        url="https://example.com/",
        title="Example",
        scroll_y=640,
    )
    scroll = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(
        "app.services.ai.tools.browser_tools.get_current_agent_context",
        lambda: FakeContext(),
    )
    monkeypatch.setattr(
        "app.services.ai.tools.browser_tools._owned_session",
        AsyncMock(return_value=SimpleNamespace(id="bs-1")),
    )
    monkeypatch.setattr(browser_runtime, "scroll", scroll)

    payload = json.loads(await browser_scroll.ainvoke({"direction": "down", "amount": 640}))

    assert payload["snapshot_id"] == "snap-scroll"
    assert payload["scroll_y"] == 640
    scroll.assert_awaited_once_with("bs-1", direction="down", amount=640)


@pytest.mark.asyncio
async def test_browser_press_tool_persists_latest_page_info(monkeypatch):
    class FakeContext:
        user_id = 1

    result = SimpleNamespace(
        model_dump=lambda mode="json": {
            "session_id": "bs-1",
            "action": "press",
            "url": "https://example.com/next",
            "title": "Next",
        },
        url="https://example.com/next",
        title="Next",
    )
    monkeypatch.setattr(browser_tools_module, "get_current_agent_context", lambda: FakeContext())
    monkeypatch.setattr(
        browser_tools_module,
        "_owned_session",
        AsyncMock(return_value=SimpleNamespace(id="bs-1")),
    )
    monkeypatch.setattr(browser_tools_module.browser_runtime, "press", AsyncMock(return_value=result))
    persist = AsyncMock()
    monkeypatch.setattr(browser_tools_module, "_persist_browser_result", persist, raising=False)

    await browser_press.ainvoke({"key": "Enter"})

    persist.assert_awaited_once()
    assert persist.await_args.args[0].user_id == 1
    assert persist.await_args.args[1] is result


@pytest.mark.asyncio
async def test_browser_click_tool_persists_latest_page_info(monkeypatch):
    class FakeContext:
        user_id = 1
        browser_session_id = "bs-1"

    class DbContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            return None

    result = BrowserToolResult(
        session_id="bs-1",
        action="click",
        url="https://example.com/next",
        title="Next",
    )
    session = SimpleNamespace(approval_mode="autopilot")
    service = SimpleNamespace(get_owned_session=AsyncMock(return_value=session))
    persist = AsyncMock()
    monkeypatch.setattr(browser_tools_module, "get_current_agent_context", lambda: FakeContext())
    monkeypatch.setattr(browser_tools_module, "AsyncSessionLocal", DbContext)
    monkeypatch.setattr(
        "app.services.ai.browser.browser_session_service.BrowserSessionService",
        lambda _db: service,
    )
    monkeypatch.setattr(browser_tools_module.browser_runtime, "click", AsyncMock(return_value=result))
    monkeypatch.setattr(browser_tools_module, "_persist_browser_result", persist)

    await browser_click.ainvoke({"target_ref": "e1", "snapshot_id": "snap-1"})

    persist.assert_awaited_once()
    assert persist.await_args.args[0].user_id == 1
    assert persist.await_args.args[1] is result


@pytest.mark.asyncio
async def test_persist_browser_result_commits_session_state(monkeypatch):
    class FakeContext:
        user_id = 1
        browser_session_id = "bs-1"

    class DbContext:
        def __init__(self):
            self.commit_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            self.commit_count += 1

    db = DbContext()
    service = SimpleNamespace(update_state=AsyncMock())
    monkeypatch.setattr(browser_tools_module, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        "app.services.ai.browser.browser_session_service.BrowserSessionService",
        lambda _db: service,
    )

    await browser_tools_module._persist_browser_result(
        FakeContext(),
        SimpleNamespace(url="https://example.com/next", title="Next"),
    )

    service.update_state.assert_awaited_once_with(
        user_id=1,
        session_id="bs-1",
        url="https://example.com/next",
        title="Next",
    )
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_runtime_tool_audit_redacts_browser_fill_value():
    events = []
    spec = RuntimeToolSpec(
        name="browser_fill",
        description="test",
        parameters_schema={"type": "object"},
        source_type="system",
        callable=lambda **kwargs: {"ok": True},
        audit_callback=events.append,
    )

    await spec.invoke({"target_ref": "e1", "value": "secret", "sensitive": False})

    assert len(events) == 2
    assert all(event.arguments["value"] == "<redacted>" for event in events)


@pytest.mark.asyncio
async def test_browser_fill_allows_sensitive_snapshot_targets(monkeypatch):
    class FakeContext:
        browser_session_id = "bs-1"
        user_id = 1

    snapshot = BrowserSnapshot(
        session_id="bs-1",
        snapshot_id="snap-1",
        url="https://example.com/",
        title="Example",
        elements=[
            BrowserElement(
                ref="e1",
                role="textbox",
                name="验证码",
                sensitive=True,
            )
        ],
    )
    monkeypatch.setattr(core_context, "get_current_agent_context", lambda: FakeContext())
    monkeypatch.setattr(browser_runtime, "cached_snapshot", lambda *_args: snapshot)

    decision = await _browser_permission_decision(
        "browser_fill",
        {"snapshot_id": "snap-1", "target_ref": "e1"},
    )

    assert decision.behavior == PermissionBehavior.ALLOW
    assert decision.decision_reason == "browser_fill_target"


@pytest.mark.asyncio
async def test_stale_browser_click_is_denied_instead_of_asking_for_confirmation(monkeypatch):
    class FakeContext:
        browser_session_id = "bs-1"
        user_id = 1

    monkeypatch.setattr(core_context, "get_current_agent_context", lambda: FakeContext())

    def stale_snapshot(*_args):
        raise ValueError("浏览器快照已过期")

    monkeypatch.setattr(browser_runtime, "cached_snapshot", stale_snapshot)

    decision = await _browser_permission_decision(
        "browser_click",
        {"snapshot_id": "stale-snapshot", "target_ref": "e1"},
    )

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.bypass_immune is True
    assert decision.decision_reason == "browser_target_not_in_snapshot"


def test_browser_open_result_emits_persisted_approval_mode():
    event = build_browser_session_event(
        "browser_open",
        '{"session_id":"bs-1","url":"https://www.baidu.com/","title":"百度",'
        '"approval_mode":"autopilot"}',
    )

    assert event["approval_mode"] == "autopilot"


@pytest.mark.asyncio
async def test_browser_open_result_contains_session_approval_mode(monkeypatch):
    class FakeContext:
        user_id = 1
        conversation_id = "conv-1"

    session = SimpleNamespace(id="bs-1", approval_mode="guarded")
    snapshot = BrowserSnapshot(
        session_id="bs-1",
        snapshot_id="snap-1",
        url="https://www.baidu.com/",
        title="百度",
    )
    monkeypatch.setattr(
        "app.services.ai.tools.browser_tools.get_current_agent_context",
        lambda: FakeContext(),
    )
    monkeypatch.setattr(browser_runtime, "open_for_user", AsyncMock(return_value=session))
    monkeypatch.setattr(browser_runtime, "snapshot", AsyncMock(return_value=snapshot))

    payload = json.loads(await browser_open.ainvoke({}))

    assert payload["approval_mode"] == "guarded"


def test_browser_open_result_recovers_from_truncated_output():
    # 模拟工具输出因过长被 truncate_for_context 截断导致 JSON 损坏的场景
    truncated_output = (
        '{"session_id":"bs-truncated-123","snapshot_id":"snap-abc","url":"https://www.baidu.com/",'
        '"title":"百度一下","approval_mode":"guarded","elements":[{"id":"el-1","tag":"div"'
        '\n… [输出已截断]'
    )
    event = build_browser_session_event("browser_open", truncated_output)
    assert event is not None
    assert event["session_id"] == "bs-truncated-123"
    assert event["url"] == "https://www.baidu.com/"
    assert event["title"] == "百度一下"
    assert event["approval_mode"] == "guarded"


def test_browser_open_result_falls_back_to_agent_context(monkeypatch):
    class FakeContext:
        browser_session_id = "bs-context-456"

    monkeypatch.setattr(
        "app.core.context.get_current_agent_context",
        lambda: FakeContext(),
    )
    event = build_browser_session_event("browser_open", "")
    assert event is not None
    assert event["session_id"] == "bs-context-456"

