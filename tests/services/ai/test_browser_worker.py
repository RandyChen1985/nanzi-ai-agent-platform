import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

from app.schemas.browser import BrowserSnapshot
from app.services.ai.browser.browser_worker import BrowserTargetStale, BrowserWorker
from app.services.ai.browser.browser_worker import _BrowserHandle


pytestmark = pytest.mark.no_infrastructure


class FakeLocator:
    def __init__(self, page, elements=None):
        self.page = page
        self.elements = elements or []
        self.evaluate_all_script = None
        self.click = AsyncMock()
        self.fill = AsyncMock()
        self.press = AsyncMock()
        self.select_option = AsyncMock()
        self.hover = AsyncMock()
        self.drag_to = AsyncMock()
        self.set_input_files = AsyncMock()
        self.evaluate = AsyncMock(return_value={})
        self.nth = Mock(return_value=self)
        self.bounding_box = AsyncMock(
            return_value={"x": 0.0, "y": 100.0, "width": 40.0, "height": 40.0}
        )

    async def evaluate_all(self, script):
        self.evaluate_all_script = script
        return self.elements


class FakeFrame:
    def __init__(self, focused_input=False, page=None, elements=None):
        self.evaluate = AsyncMock(return_value=focused_input)
        self.role_calls = []
        self.locator_value = FakeLocator(page, elements or [])
        self.elements = elements or []

    def locator(self, selector):
        return self.locator_value

    def get_by_role(self, role, name=None, exact=True):
        self.role_calls.append((role, name, exact))
        return self.locator_value


class FakeDownload:
    suggested_filename = "report.csv"

    async def path(self):
        return "/tmp/report.csv"


class FakeDownloadContext:
    def __init__(self):
        self.value = FakeDownload()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

@pytest.mark.asyncio
async def test_clean_idle_sessions_closes_expired_sessions():
    worker = BrowserWorker(url_validator=lambda url: url, request_validator=lambda url: url)
    mock_close = AsyncMock()
    worker.close = mock_close

    handle1 = Mock()
    handle1.last_active_at = 100.0
    handle2 = Mock()
    handle2.last_active_at = 900.0

    worker._handles = {"s1": handle1, "s2": handle2}

    loop = asyncio.get_running_loop()
    with pytest.MonkeyPatch().context() as m:
        m.setattr(loop, "time", lambda: 1000.0)
        cleaned = await worker.clean_idle_sessions(max_idle_seconds=500)

    assert cleaned == ["s1"]
    mock_close.assert_called_once_with("s1")


class FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.role_calls = []
        self.locator_calls = []
        self.mouse = type(
            "FakeMouse",
            (),
            {
                "click": AsyncMock(),
                "move": AsyncMock(),
                "down": AsyncMock(),
                "up": AsyncMock(),
                "wheel": AsyncMock(),
            },
        )()
        self.keyboard = type(
            "FakeKeyboard",
            (),
            {
                "press": AsyncMock(),
                "type": AsyncMock(),
                "insert_text": AsyncMock(),
            },
        )()
        self.frames = []
        self.evaluate_scripts = []
        self.wait_for_load_state = AsyncMock()
        self.wait_for_timeout = AsyncMock()
        self.go_back = AsyncMock(return_value=object())
        self.go_forward = AsyncMock(return_value=object())
        self.reload = AsyncMock(return_value=object())
        self.close = AsyncMock()
        self.download_context = FakeDownloadContext()
        self.dialog_listeners = []
        self.locator_value = FakeLocator(
            self,
            [
                {
                    "role": "searchbox",
                    "name": "搜索",
                    "value": "",
                    "disabled": False,
                    "sensitive": False,
                },
                {
                    "role": "button",
                    "name": "百度一下",
                    "value": "",
                    "disabled": False,
                    "sensitive": False,
                },
                {
                    "role": "textbox",
                    "name": "密码",
                    "value": "should-not-leak",
                    "disabled": False,
                    "sensitive": True,
                },
            ],
        )

    async def goto(self, url, **_kwargs):
        self.url = url

    async def title(self):
        return "百度一下"

    def on(self, event, listener):
        if event == "dialog":
            self.dialog_listeners.append(listener)

    def remove_listener(self, event, listener):
        if event == "dialog" and listener in self.dialog_listeners:
            self.dialog_listeners.remove(listener)

    async def evaluate(self, script):
        self.evaluate_scripts.append(script)
        return False

    def locator(self, selector):
        self.locator_calls.append(selector)
        return self.locator_value

    def expect_download(self, **_kwargs):
        return self.download_context

    def get_by_role(self, role, name=None, exact=True):
        self.role_calls.append((role, name, exact))
        return self.locator_value

    async def screenshot(self, path, full_page=False):
        Path(path).write_bytes(b"png")


class FakeContext:
    def __init__(self):
        self.page = FakePage()
        self.pages = [self.page]
        self.close = AsyncMock()
        self.add_init_script = AsyncMock()

    async def new_page(self):
        return self.page


class FakeChromium:
    def __init__(self):
        self.context = FakeContext()
        self.launch_persistent_context = AsyncMock(return_value=self.context)


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()


@pytest.mark.asyncio
async def test_worker_open_snapshot_and_semantic_click(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=str(tmp_path),
    )

    opened = await worker.open(
        session_id="bs-1",
        profile_path=str(tmp_path / "profile-1"),
        url="https://www.baidu.com/",
    )
    snapshot = await worker.snapshot("bs-1")
    result = await worker.click("bs-1", target_ref="e2", snapshot=snapshot)

    assert opened.url == "https://www.baidu.com/"
    assert snapshot.elements[0].ref == "e1"
    assert snapshot.elements[2].value is None
    assert snapshot.elements[2].sensitive is True
    assert result.action == "click"
    assert fake_playwright.chromium.launch_persistent_context.await_count == 1
    assert fake_context_page(fake_playwright).role_calls == [("button", "百度一下", True)]


@pytest.mark.asyncio
async def test_execute_js_rejects_oversized_scripts():
    worker = BrowserWorker(url_validator=lambda url: url)

    worker._handles["js-1"] = _BrowserHandle(context=Mock(), page=FakePage())

    with pytest.raises(ValueError, match="脚本长度超过限制"):
        await worker.execute_js("js-1", script="x" * 50001)


@pytest.mark.asyncio
async def test_execute_js_allows_page_automation_primitives():
    page = FakePage()
    page.evaluate = AsyncMock(return_value={"ok": True})
    worker = BrowserWorker(url_validator=lambda url: url)
    worker._handles["js-automation"] = _BrowserHandle(context=Mock(), page=page)

    result = await worker.execute_js(
        "js-automation",
        script="document.querySelector('form').submit(); fetch('/api/orders')",
    )

    page.evaluate.assert_awaited_once_with(
        "document.querySelector('form').submit(); fetch('/api/orders')"
    )
    assert result.data["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_execute_js_truncates_oversized_results():
    page = FakePage()
    page.evaluate = AsyncMock(return_value="x" * 20001)
    worker = BrowserWorker(url_validator=lambda url: url)
    worker._handles["js-3"] = _BrowserHandle(context=Mock(), page=page)

    result = await worker.execute_js("js-3", script="document.body.innerText")

    assert result.data["result"]["truncated"] is True
    assert result.data["result"]["size_chars"] > 20000
    assert len(result.data["result"]["preview"]) == 20000


@pytest.mark.asyncio
async def test_get_cookies_redacts_cookie_values():
    page = FakePage()
    context = Mock()
    context.cookies = AsyncMock(
        return_value=[{"name": "sid", "value": "secret", "domain": "example.com"}]
    )
    worker = BrowserWorker(url_validator=lambda url: url)
    worker._handles["cookie-1"] = _BrowserHandle(context=context, page=page)

    result = await worker.get_cookies("cookie-1")

    assert result.data["cookies"] == [
        {"name": "sid", "value": "<redacted>", "domain": "example.com"}
    ]


@pytest.mark.asyncio
async def test_check_auth_does_not_treat_an_arbitrary_cookie_as_authenticated():
    page = FakePage()
    page.evaluate = AsyncMock(
        return_value={
            "has_local_storage_tokens": False,
            "has_session_storage_tokens": False,
            "has_logout_indicator": False,
            "has_login_indicator": False,
            "page_url": "https://example.com/",
        }
    )
    context = Mock()
    context.cookies = AsyncMock(
        return_value=[{"name": "analytics_id", "value": "opaque-id"}]
    )
    worker = BrowserWorker(url_validator=lambda url: url)
    worker._handles["auth-1"] = _BrowserHandle(context=context, page=page)

    result = await worker.check_auth("auth-1")

    assert result.data["is_authenticated"] is None
    assert result.data["auth_confidence"] == "unknown"


@pytest.mark.asyncio
async def test_handle_dialog_replaces_previous_listener():
    page = FakePage()
    worker = BrowserWorker(url_validator=lambda url: url)
    worker._handles["dialog-1"] = _BrowserHandle(context=Mock(), page=page)

    await worker.handle_dialog("dialog-1", action="accept")
    await worker.handle_dialog("dialog-1", action="dismiss")

    assert len(page.dialog_listeners) == 1


def _page_with_subframe(subframe_elements):
    """在 FakePage 上挂一个子帧主力帧占位，构造 frames=[主帧占位, 子帧] 的页面对象。"""
    page = FakePage()
    sub_frame = FakeFrame(page=page, elements=subframe_elements)
    # frames[0] 为主帧占位（快照逻辑跳过 index 0），frames[1] 为真实 iframe 子帧。
    page.frames = [FakePage(), sub_frame]
    return page, sub_frame


@pytest.mark.asyncio
async def test_snapshot_captures_elements_from_subframe(tmp_path):
    fake_playwright = FakePlaywright()
    page, _sub_frame = _page_with_subframe(
        [
            {
                "role": "button",
                "name": "iframe按钮",
                "value": "",
                "disabled": False,
                "sensitive": False,
            }
        ]
    )
    fake_playwright.chromium.context.page = page
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=str(tmp_path),
    )
    opened = await worker.open(
        session_id="bs-iframe",
        profile_path=str(tmp_path / "profile-iframe"),
        url="https://example.com/",
    )
    snapshot = await worker.snapshot("bs-iframe")

    # 主帧元素行走 handle.page.locator 路径，不带 _frame_index。
    assert snapshot.elements[0].ref == "e1"
    assert snapshot.elements[0].name == "搜索"
    target_map = worker._snapshots["bs-iframe"][snapshot.snapshot_id]
    assert "_frame_index" not in target_map["e1"]

    # 子帧元素被聚合且带 _frame_index=1（帧内 ref 用 frame_index*120 的偏移量）。
    sub_elem = next(e for e in snapshot.elements if e.name == "iframe按钮")
    assert sub_elem.ref == "e121"
    assert target_map[sub_elem.ref]["_frame_index"] == 1


@pytest.mark.asyncio
async def test_click_resolves_locator_against_subframe(tmp_path):
    fake_playwright = FakePlaywright()
    page, sub_frame = _page_with_subframe(
        [
            {
                "role": "button",
                "name": "iframe按钮",
                "value": "",
                "disabled": False,
                "sensitive": False,
            }
        ]
    )
    fake_playwright.chromium.context.page = page
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=str(tmp_path),
    )
    await worker.open(
        session_id="bs-iframe-click",
        profile_path=str(tmp_path / "profile-iframe-click"),
        url="https://example.com/",
    )
    snapshot = await worker.snapshot("bs-iframe-click")

    # 用 frame_index==1 的子帧按钮做语义点击。
    result = await worker.click(
        "bs-iframe-click",
        target_ref="e121",
        snapshot=snapshot,
        approval_mode="guarded",
    )

    assert result.action == "click"
    # 点击必须落到子帧 container，而非主帧 page。
    assert sub_frame.role_calls == [("button", "iframe按钮", True)]
    assert page.role_calls == []


@pytest.mark.asyncio
async def test_snapshot_javascript_preserves_regex_escape_sequences(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-snapshot-script",
        profile_path=str(tmp_path / "profile-snapshot-script"),
        url="https://example.com/",
    )

    await worker.snapshot("bs-snapshot-script")

    page = fake_context_page(fake_playwright)
    element_script = page.locator_value.evaluate_all_script
    assert element_script is not None
    assert r"\s*\n\s*" in element_script
    assert r"'\n'" in element_script

    context_scripts = [script for script in page.evaluate_scripts if "page_text:" in str(script)]
    assert context_scripts
    assert r"\s*\n\s*" in context_scripts[-1]
    assert r"'\n'" in context_scripts[-1]


@pytest.mark.asyncio
async def test_worker_can_click_a_recent_snapshot_after_another_snapshot_is_created(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-snapshot-history",
        profile_path=str(tmp_path / "profile-snapshot-history"),
        url="https://www.baidu.com/",
    )

    first = await worker.snapshot("bs-snapshot-history")
    await worker.snapshot("bs-snapshot-history")
    result = await worker.click(
        "bs-snapshot-history",
        target_ref="e2",
        snapshot=first,
        approval_mode="autopilot",
        confirmed=True,
    )

    assert result.action == "click"


@pytest.mark.asyncio
async def test_worker_scroll_returns_fresh_snapshot_metadata(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-scroll",
        profile_path=str(tmp_path / "profile-scroll"),
        url="https://example.com/",
    )
    page = fake_context_page(fake_playwright)
    page.evaluate = AsyncMock(
        side_effect=[
            False,
            {
                "scroll_x": 0,
                "scroll_y": 640,
                "viewport_width": 1280,
                "viewport_height": 800,
                "document_width": 1280,
                "document_height": 2400,
                "page_text": "起飞时间早-晚\n22:20 上海-长沙",
            },
        ]
    )

    snapshot = await worker.scroll("bs-scroll", direction="down", amount=640)

    page.mouse.wheel.assert_awaited_once_with(0, 640)
    assert snapshot.scroll_y == 640
    assert snapshot.viewport_height == 800
    assert snapshot.document_height == 2400
    assert "起飞时间早-晚" in snapshot.page_text


@pytest.mark.asyncio
async def test_worker_resolves_inferred_custom_target_by_snapshot_dom_index(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-custom-target",
        profile_path=str(tmp_path / "profile-custom-target"),
        url="https://example.com/",
    )
    snapshot = BrowserSnapshot(
        session_id="bs-custom-target",
        snapshot_id="snapshot-custom-target",
        url="https://example.com/",
        title="Example",
    )
    worker._snapshots["bs-custom-target"] = {
        snapshot.snapshot_id: {
            "e1": {
                "role": "button",
                "name": "起飞时间早-晚",
                "value": "",
                "disabled": False,
                "sensitive": False,
                "_role_source": "inferred",
                "_node_index": 17,
            }
        }
    }

    result = await worker.click(
        "bs-custom-target",
        target_ref="e1",
        snapshot=snapshot,
        approval_mode="autopilot",
        confirmed=True,
    )

    assert result.action == "click"
    assert fake_context_page(fake_playwright).role_calls == []
    assert fake_context_page(fake_playwright).locator_calls[-1] == "body *"
    fake_context_page(fake_playwright).locator_value.nth.assert_called_once_with(17)


@pytest.mark.asyncio
async def test_worker_rejects_inferred_target_when_dom_index_now_points_elsewhere(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-stale-custom-target",
        profile_path=str(tmp_path / "profile-stale-custom-target"),
        url="https://example.com/",
    )
    snapshot = BrowserSnapshot(
        session_id="bs-stale-custom-target",
        snapshot_id="snapshot-stale-custom-target",
        url="https://example.com/",
        title="Example",
    )
    worker._snapshots["bs-stale-custom-target"] = {
        snapshot.snapshot_id: {
            "e1": {
                "role": "button",
                "name": "起飞时间早-晚",
                "value": "",
                "disabled": False,
                "sensitive": False,
                "_role_source": "inferred",
                "_node_index": 17,
            }
        }
    }
    fake_context_page(fake_playwright).locator_value.evaluate.return_value = {"name": "其他排序"}

    with pytest.raises(BrowserTargetStale, match="页面已变化"):
        await worker.click(
            "bs-stale-custom-target",
            target_ref="e1",
            snapshot=snapshot,
            approval_mode="autopilot",
            confirmed=True,
        )


class _FakePlaywrightStaleError(RuntimeError):
    """类名含 stale 的执行期异常，模拟 Playwright 的 stale-element 错误（非 BrowserTargetStale）。"""

    name = "Error"


def _native_button_snapshot_map(snapshot_id):
    """构造一个指向 e2(button“百度一下”) 的原生目标 target_map。"""
    return {
        snapshot_id: {
            "e2": {
                "role": "button",
                "name": "百度一下",
                "value": "",
                "disabled": False,
                "sensitive": False,
            }
        }
    }


@pytest.mark.asyncio
async def test_worker_click_recovers_after_execution_stale_and_succeeds(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-recover-stale",
        profile_path=str(tmp_path / "profile-recover-stale"),
        url="https://example.com/",
    )
    snapshot = BrowserSnapshot(
        session_id="bs-recover-stale",
        snapshot_id="snapshot-recover-stale",
        url="https://example.com/",
        title="Example",
    )
    worker._snapshots["bs-recover-stale"] = _native_button_snapshot_map(snapshot.snapshot_id)

    locator = fake_context_page(fake_playwright).locator_value
    # 首轮点击碰到 Playwright stale-element 错误，第二轮（重试）成功。
    locator.click.side_effect = [_FakePlaywrightStaleError("element is not attached"), None]

    result = await worker.click(
        "bs-recover-stale",
        target_ref="e2",
        snapshot=snapshot,
        approval_mode="autopilot",
        confirmed=True,
    )

    assert result.action == "click"
    # 首轮失败 + 刷新快照后重试一次 = 调用 2 次点击。
    assert locator.click.await_count == 2


@pytest.mark.asyncio
async def test_worker_click_does_not_retry_unrecoverable_target_stale(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-unrecoverable-stale",
        profile_path=str(tmp_path / "profile-unrecoverable-stale"),
        url="https://example.com/",
    )
    snapshot = BrowserSnapshot(
        session_id="bs-unrecoverable-stale",
        snapshot_id="snapshot-unrecoverable-stale",
        url="https://example.com/",
        title="Example",
    )
    worker._snapshots["bs-unrecoverable-stale"] = _native_button_snapshot_map(
        snapshot.snapshot_id
    )

    locator = fake_context_page(fake_playwright).locator_value
    # 默认 recoverable=False 的 BrowserTargetStale（如 _validate_inferred_target 失配）必须立即上抛。
    locator.click.side_effect = [BrowserTargetStale("目标已变化"), None]

    with pytest.raises(BrowserTargetStale, match="目标已变化"):
        await worker.click(
            "bs-unrecoverable-stale",
            target_ref="e2",
            snapshot=snapshot,
            approval_mode="autopilot",
            confirmed=True,
        )
    # 不可恢复的目标解析失败不应触发自动重试：点击只尝试一次。
    assert locator.click.await_count == 1


@pytest.mark.asyncio
async def test_worker_click_recovers_after_wait_timeout(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-recover-timeout",
        profile_path=str(tmp_path / "profile-recover-timeout"),
        url="https://example.com/",
    )
    snapshot = BrowserSnapshot(
        session_id="bs-recover-timeout",
        snapshot_id="snapshot-recover-timeout",
        url="https://example.com/",
        title="Example",
    )
    worker._snapshots["bs-recover-timeout"] = _native_button_snapshot_map(snapshot.snapshot_id)

    locator = fake_context_page(fake_playwright).locator_value
    # 首轮 asyncio.TimeoutError（超时类可恢复），第二轮成功。
    locator.click.side_effect = [TimeoutError("timed out"), None]

    result = await worker.click(
        "bs-recover-timeout",
        target_ref="e2",
        snapshot=snapshot,
        approval_mode="autopilot",
        confirmed=True,
    )

    assert result.action == "click"
    assert locator.click.await_count == 2


@pytest.mark.asyncio
async def test_worker_click_raises_normalized_error_after_retries_exhausted(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-exhausted",
        profile_path=str(tmp_path / "profile-exhausted"),
        url="https://example.com/",
    )
    snapshot = BrowserSnapshot(
        session_id="bs-exhausted",
        snapshot_id="snapshot-exhausted",
        url="https://example.com/",
        title="Example",
    )
    worker._snapshots["bs-exhausted"] = _native_button_snapshot_map(snapshot.snapshot_id)

    locator = fake_context_page(fake_playwright).locator_value
    # 每次重试都命中 Playwright stale-element 错误，直到 ACTION_RETRY_COUNT 耗尽。
    locator.click.side_effect = [
        _FakePlaywrightStaleError("stale 1"),
        _FakePlaywrightStaleError("stale 2"),
        _FakePlaywrightStaleError("stale 3"),
    ]

    with pytest.raises(BrowserTargetStale, match="连续执行失败"):
        await worker.click(
            "bs-exhausted",
            target_ref="e2",
            snapshot=snapshot,
            approval_mode="autopilot",
            confirmed=True,
        )
    # 初始尝试 + ACTION_RETRY_COUNT 次重试。
    assert locator.click.await_count == 3


@pytest.mark.asyncio
async def test_worker_fill_redacts_sensitive_value_from_result(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-2",
        profile_path=str(tmp_path / "profile-2"),
        url="https://example.com/",
    )
    snapshot = await worker.snapshot("bs-2")
    result = await worker.fill(
        "bs-2",
        target_ref="e1",
        value="secret",
        snapshot=snapshot,
        sensitive=True,
    )

    assert result.data["value"] == "<redacted>"
    fake_context_page(fake_playwright).locator_value.fill.assert_awaited_once_with("secret")


@pytest.mark.asyncio
async def test_worker_adopts_new_tab_after_manual_mouse_click(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-popup",
        profile_path=str(tmp_path / "profile-popup"),
        url="https://www.baidu.com/",
    )

    popup = FakePage()
    popup.url = "https://image.baidu.com/i?tn=baiduimage"

    async def open_popup(*_args):
        fake_playwright.chromium.context.pages.append(popup)

    fake_context_page(fake_playwright).mouse.click.side_effect = open_popup

    info = await worker.manual_input(
        "bs-popup",
        event="mouse_click",
        payload={"x": 300, "y": 36},
    )

    assert info.url == popup.url
    assert worker._handles["bs-popup"].page is popup


@pytest.mark.asyncio
async def test_worker_adopts_delayed_new_tab_after_manual_mouse_click(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-delayed-popup",
        profile_path=str(tmp_path / "profile-delayed-popup"),
        url="https://www.baidu.com/",
    )

    popup = FakePage()
    popup.url = "https://image.baidu.com/i?tn=baiduimage"

    async def append_popup():
        await asyncio.sleep(0.06)
        fake_playwright.chromium.context.pages.append(popup)

    async def open_popup(*_args):
        asyncio.create_task(append_popup())

    fake_context_page(fake_playwright).mouse.click.side_effect = open_popup

    info = await worker.manual_input(
        "bs-delayed-popup",
        event="mouse_click",
        payload={"x": 300, "y": 36},
    )

    assert info.url == popup.url
    assert worker._handles["bs-delayed-popup"].page is popup


@pytest.mark.asyncio
async def test_worker_uses_short_wait_after_manual_click(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-click-wait",
        profile_path=str(tmp_path / "profile-click-wait"),
        url="https://example.com/",
    )

    await worker.manual_input(
        "bs-click-wait",
        event="mouse_click",
        payload={"x": 300, "y": 36},
    )

    fake_context_page(fake_playwright).wait_for_load_state.assert_awaited_once_with(
        "domcontentloaded", timeout=1500
    )


@pytest.mark.asyncio
async def test_worker_reports_when_human_click_focuses_text_input(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-focus-input",
        profile_path=str(tmp_path / "profile-focus-input"),
        url="https://example.com/",
    )
    page = fake_context_page(fake_playwright)
    page.evaluate = AsyncMock(return_value=True)

    info = await worker.manual_input(
        "bs-focus-input",
        event="mouse_click",
        payload={"x": 300, "y": 200},
    )

    assert info.focused_input is True
    page.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_exposes_current_page_info_without_navigating(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-current-page",
        profile_path=str(tmp_path / "profile-current-page"),
        url="https://example.com/",
    )

    info = await worker.current_page_info("bs-current-page")

    assert info.url == "https://example.com/"
    assert info.title == "百度一下"


@pytest.mark.asyncio
async def test_worker_reports_text_focus_inside_accessible_iframe(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-focus-iframe",
        profile_path=str(tmp_path / "profile-focus-iframe"),
        url="https://example.com/",
    )
    page = fake_context_page(fake_playwright)
    page.evaluate = AsyncMock(return_value=False)
    frame = FakeFrame(focused_input=True)
    page.frames = [frame]

    info = await worker.manual_input(
        "bs-focus-iframe",
        event="mouse_click",
        payload={"x": 300, "y": 200},
    )

    assert info.focused_input is True
    frame.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_does_not_report_non_input_click_as_text_focus(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-focus-button",
        profile_path=str(tmp_path / "profile-focus-button"),
        url="https://example.com/",
    )

    info = await worker.manual_input(
        "bs-focus-button",
        event="mouse_click",
        payload={"x": 300, "y": 36},
    )

    assert info.focused_input is False


@pytest.mark.asyncio
async def test_worker_forwards_human_drag_pointer_events(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-drag",
        profile_path=str(tmp_path / "profile-drag"),
        url="https://example.com/",
    )
    page = fake_context_page(fake_playwright)

    await worker.manual_input("bs-drag", event="mouse_down", payload={"x": 100, "y": 200})
    await worker.manual_input("bs-drag", event="mouse_move", payload={"x": 180, "y": 200})
    await worker.manual_input("bs-drag", event="mouse_up", payload={"x": 180, "y": 200})

    page.mouse.move.assert_any_await(100, 200)
    page.mouse.move.assert_any_await(180, 200)
    page.mouse.down.assert_awaited_once_with()
    page.mouse.up.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_worker_inserts_human_text_without_key_by_key_ime_events(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-text",
        profile_path=str(tmp_path / "profile-text"),
        url="https://example.com/",
    )
    page = fake_context_page(fake_playwright)

    await worker.manual_input(
        "bs-text",
        event="text",
        payload={"text": "中文搜索"},
    )

    page.keyboard.insert_text.assert_awaited_once_with("中文搜索")
    page.keyboard.type.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_detects_explicit_captcha_page_without_matching_generic_verify_copy(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-captcha",
        profile_path=str(tmp_path / "profile-captcha"),
        url="https://example.com/",
    )
    page = fake_context_page(fake_playwright)
    page.evaluate = AsyncMock(return_value={"matched": True, "reason": "滑块验证"})

    snapshot = await worker.snapshot("bs-captcha")

    assert snapshot.page_state == "captcha"


@pytest.mark.asyncio
async def test_worker_retries_snapshot_after_navigation_context_is_destroyed(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-snapshot-retry",
        profile_path=str(tmp_path / "profile-snapshot-retry"),
        url="https://example.com/",
    )
    locator = fake_context_page(fake_playwright).locator_value
    locator.evaluate_all = AsyncMock(
        side_effect=[
            RuntimeError("Execution context was destroyed, most likely because of a navigation"),
            [],
        ]
    )

    snapshot = await worker.snapshot("bs-snapshot-retry")

    assert snapshot.url == "https://example.com/"
    assert locator.evaluate_all.await_count == 2


@pytest.mark.asyncio
async def test_worker_returns_current_page_after_navigation_timeout(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-navigation-timeout",
        profile_path=str(tmp_path / "profile-navigation-timeout"),
        url="https://www.baidu.com/",
    )
    page = fake_context_page(fake_playwright)

    async def goto_with_timeout(url, **_kwargs):
        page.url = url
        raise TimeoutError("Page.goto: Timeout 25000ms exceeded")

    page.goto = goto_with_timeout

    info = await worker.navigate("bs-navigation-timeout", "https://www.baidu.com/s?wd=有孚")

    assert info.url == "https://www.baidu.com/s?wd=有孚"


@pytest.mark.asyncio
async def test_worker_cannot_override_snapshot_sensitive_flag(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-3",
        profile_path=str(tmp_path / "profile-3"),
        url="https://example.com/",
    )
    snapshot = await worker.snapshot("bs-3")
    result = await worker.fill(
        "bs-3",
        target_ref="e3",
        value="secret",
        snapshot=snapshot,
        sensitive=False,
    )

    assert result.data["value"] == "<redacted>"


@pytest.mark.asyncio
async def test_worker_open_applies_stealth_anti_bot_options_and_init_script(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-stealth",
        profile_path=str(tmp_path / "profile-stealth"),
        url="https://example.com/",
    )

    launch_mock = fake_playwright.chromium.launch_persistent_context
    assert launch_mock.await_count == 1
    _, kwargs = launch_mock.call_args
    assert "--disable-blink-features=AutomationControlled" in kwargs["args"]
    assert "Mozilla/5.0" in kwargs["user_agent"]
    assert "HeadlessChrome" not in kwargs["user_agent"]
    assert kwargs["locale"] == "zh-CN"
    assert kwargs["timezone_id"] == "Asia/Shanghai"

    context = fake_playwright.chromium.context
    assert context.add_init_script.await_count == 1
    injected_script = context.add_init_script.call_args[0][0]
    assert "webdriver" in injected_script
    assert "window.chrome" in injected_script


def fake_context_page(fake_playwright):
    return fake_playwright.chromium.context.page


@pytest.mark.asyncio
async def test_worker_supports_keyboard_select_hover_drag_and_history_actions(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-actions",
        profile_path=str(tmp_path / "profile-actions"),
        url="https://example.com/",
    )
    snapshot = BrowserSnapshot(
        session_id="bs-actions",
        snapshot_id="snapshot-actions",
        url="https://example.com/",
        title="Example",
    )
    worker._snapshots["bs-actions"] = {
        snapshot.snapshot_id: {
            "e1": {"role": "combobox", "name": "日期", "value": "", "_role_source": "native"},
            "e2": {"role": "button", "name": "目标", "value": "", "_role_source": "native"},
        }
    }

    def restore_targets():
        worker._snapshots["bs-actions"] = {
            snapshot.snapshot_id: {
                "e1": {"role": "combobox", "name": "日期", "value": "", "_role_source": "native"},
                "e2": {"role": "button", "name": "目标", "value": "", "_role_source": "native"},
            }
        }

    await worker.press("bs-actions", target_ref="e2", key="Enter", snapshot=snapshot)
    restore_targets()
    await worker.select_option("bs-actions", target_ref="e1", value="2026-08-19", snapshot=snapshot)
    restore_targets()
    await worker.hover("bs-actions", target_ref="e2", snapshot=snapshot)
    restore_targets()
    await worker.drag("bs-actions", source_ref="e1", target_ref="e2", snapshot=snapshot)
    await worker.go_back("bs-actions")
    await worker.go_forward("bs-actions")
    await worker.reload("bs-actions")

    page = fake_context_page(fake_playwright)
    page.locator_value.press.assert_awaited_once_with("Enter")
    page.locator_value.select_option.assert_awaited_once_with(value="2026-08-19")
    page.locator_value.hover.assert_awaited_once_with()
    page.locator_value.drag_to.assert_awaited_once_with(page.locator_value)
    page.go_back.assert_awaited_once()
    page.go_forward.assert_awaited_once()
    page.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_slider_drag_manual_and_gap_measurement(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-slider",
        profile_path=str(tmp_path / "profile-slider"),
        url="https://example.com/",
    )
    snapshot = BrowserSnapshot(
        session_id="bs-slider",
        snapshot_id="snapshot-slider",
        url="https://example.com/",
        title="Example",
    )
    worker._snapshots["bs-slider"] = {
        snapshot.snapshot_id: {
            "handle": {"role": "button", "name": "滑块", "value": "", "_role_source": "native"},
            "gap": {"role": "img", "name": "缺口", "value": "", "_role_source": "native"},
        }
    }
    page = fake_context_page(fake_playwright)

    # 手动距离模式：source 中心 x = 10 + 20 = 30，拖 120px
    page.locator_value.bounding_box = AsyncMock(
        return_value={"x": 10.0, "y": 100.0, "width": 40.0, "height": 40.0}
    )
    result = await worker.slider_drag(
        "bs-slider", source_ref="handle", snapshot=snapshot, distance_px=120
    )
    assert result.action == "slider_drag"
    assert result.data["distance_px"] == 120
    assert result.data["steps"] > 0
    assert result.data["measured_gap_px"] is None
    page.mouse.down.assert_awaited_once()
    page.mouse.up.assert_awaited_once()

    # 缺口测量模式：滑块中心 x=30，缺口中心 x=220 => 190px
    page.mouse.down.reset_mock()
    page.mouse.up.reset_mock()
    # 上一轮 slider_drag 已 pop snapshot map，需重新注入
    worker._snapshots["bs-slider"] = {
        snapshot.snapshot_id: {
            "handle": {"role": "button", "name": "滑块", "value": "", "_role_source": "native"},
            "gap": {"role": "img", "name": "缺口", "value": "", "_role_source": "native"},
        }
    }
    page.locator_value.bounding_box = AsyncMock(
        side_effect=[
            {"x": 10.0, "y": 100.0, "width": 40.0, "height": 40.0},
            {"x": 200.0, "y": 100.0, "width": 40.0, "height": 40.0},
        ]
    )
    result = await worker.slider_drag(
        "bs-slider", source_ref="handle", snapshot=snapshot, gap_target_ref="gap"
    )
    assert result.data["measured_gap_px"] == 190
    assert result.data["distance_px"] == 190
    page.mouse.down.assert_awaited_once()
    page.mouse.up.assert_awaited_once()

    # 两者皆缺应抛 ValueError
    with pytest.raises(ValueError):
        await worker.slider_drag("bs-slider", source_ref="handle", snapshot=snapshot)


@pytest.mark.asyncio
async def test_worker_validates_final_url_after_history_navigation(tmp_path):
    fake_playwright = FakePlaywright()
    validated_urls = []

    def validate(url):
        validated_urls.append(url)
        if url == "https://blocked.example/":
            raise RuntimeError("blocked history destination")
        return url

    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=validate,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-history-url",
        profile_path=str(tmp_path / "profile-history-url"),
        url="https://example.com/",
    )
    page = fake_context_page(fake_playwright)

    async def go_back_to_blocked(**_kwargs):
        page.url = "https://blocked.example/"

    page.go_back.side_effect = go_back_to_blocked

    with pytest.raises(RuntimeError, match="blocked history destination"):
        await worker.go_back("bs-history-url")

    assert "https://blocked.example/" in validated_urls


@pytest.mark.asyncio
async def test_worker_waits_for_url_reads_visible_text_and_manages_tabs(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-wait-tabs",
        profile_path=str(tmp_path / "profile-wait-tabs"),
        url="https://example.com/results",
    )
    page = fake_context_page(fake_playwright)
    page.evaluate = AsyncMock(
        return_value={
            "scroll_x": 0,
            "scroll_y": 800,
            "viewport_width": 1280,
            "viewport_height": 800,
            "document_width": 1280,
            "document_height": 2400,
            "page_text": "完整页面",
            "visible_text": "上海-长沙 22:20 ¥800",
        }
    )

    waited = await worker.wait_for(
        "bs-wait-tabs",
        condition="url",
        value="/results",
        timeout_ms=1000,
    )
    visible = await worker.read_visible("bs-wait-tabs")
    tabs = await worker.list_tabs("bs-wait-tabs")

    popup = FakePage()
    popup.url = "https://example.com/detail"
    fake_playwright.chromium.context.pages.append(popup)
    tabs_with_popup = await worker.list_tabs("bs-wait-tabs")
    await worker.switch_tab("bs-wait-tabs", tabs_with_popup[1].tab_id)
    await worker.close_tab("bs-wait-tabs", tabs_with_popup[0].tab_id)

    assert waited.url.endswith("/results")
    assert visible["text"] == "上海-长沙 22:20 ¥800"
    assert len(tabs) == 1
    assert [tab.url for tab in tabs_with_popup] == ["https://example.com/results", "https://example.com/detail"]
    assert worker._handles["bs-wait-tabs"].page is popup
    page.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_worker_uploads_a_file_through_a_snapshot_target(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-upload",
        profile_path=str(tmp_path / "profile-upload"),
        url="https://example.com/upload",
    )
    source = tmp_path / "ticket.pdf"
    source.write_bytes(b"pdf")
    snapshot = BrowserSnapshot(
        session_id="bs-upload",
        snapshot_id="snapshot-upload",
        url="https://example.com/upload",
        title="Upload",
    )
    worker._snapshots["bs-upload"] = {
        snapshot.snapshot_id: {
            "e1": {"role": "button", "name": "选择文件", "value": "", "_role_source": "native"},
        }
    }

    result = await worker.upload(
        "bs-upload",
        target_ref="e1",
        file_path=str(source),
        snapshot=snapshot,
    )

    assert result.action == "upload"
    fake_context_page(fake_playwright).locator_value.set_input_files.assert_awaited_once_with(str(source))


@pytest.mark.asyncio
async def test_worker_select_option_falls_back_to_aria_option_for_custom_combobox(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-aria-select",
        profile_path=str(tmp_path / "profile-aria-select"),
        url="https://example.com/filters",
    )
    snapshot = BrowserSnapshot(
        session_id="bs-aria-select",
        snapshot_id="snapshot-aria-select",
        url="https://example.com/filters",
        title="Filters",
    )
    worker._snapshots["bs-aria-select"] = {
        snapshot.snapshot_id: {
            "e1": {"role": "combobox", "name": "筛选", "value": "", "_role_source": "explicit"},
        }
    }
    page = fake_context_page(fake_playwright)
    page.locator_value.select_option.side_effect = RuntimeError("not a native select")

    result = await worker.select_option(
        "bs-aria-select",
        target_ref="e1",
        value="economy",
        snapshot=snapshot,
    )

    assert result.action == "select_option"
    assert page.locator_value.click.await_count == 2
    assert page.role_calls[-1] == ("option", "economy", True)


@pytest.mark.asyncio
async def test_worker_captures_download_path_without_exposing_it_in_filename(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-download",
        profile_path=str(tmp_path / "profile-download"),
        url="https://example.com/download",
    )
    snapshot = BrowserSnapshot(
        session_id="bs-download",
        snapshot_id="snapshot-download",
        url="https://example.com/download",
        title="Download",
    )
    worker._snapshots["bs-download"] = {
        snapshot.snapshot_id: {
            "e1": {"role": "link", "name": "下载", "value": "", "_role_source": "explicit"},
        }
    }

    result = await worker.download(
        "bs-download",
        target_ref="e1",
        snapshot=snapshot,
    )

    assert result.action == "download"
    assert result.data == {"download_path": "/tmp/report.csv", "filename": "report.csv"}


@pytest.mark.asyncio
async def test_worker_rejects_a_snapshot_from_another_active_tab(tmp_path):
    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-tab-snapshot",
        profile_path=str(tmp_path / "profile-tab-snapshot"),
        url="https://example.com/one",
    )
    first_snapshot = await worker.snapshot("bs-tab-snapshot")
    popup = FakePage()
    popup.url = "https://example.com/two"
    fake_playwright.chromium.context.pages.append(popup)
    tabs = await worker.list_tabs("bs-tab-snapshot")
    await worker.switch_tab("bs-tab-snapshot", tabs[1].tab_id)

    with pytest.raises(BrowserTargetStale, match="页面已变化"):
        await worker.click(
            "bs-tab-snapshot",
            target_ref="e2",
            snapshot=first_snapshot,
            approval_mode="autopilot",
            confirmed=True,
        )


@pytest.mark.asyncio
async def test_snapshot_js_recognizes_contenteditable_and_aria_textboxes(tmp_path):
    fake_playwright = FakePlaywright()
    fake_page = FakePage()
    fake_page.locator_value.elements = [
        {
            "role": "textbox",
            "tag": "div",
            "_role_source": "native",
            "_node_index": 0,
            "_in_shadow": False,
            "sensitive": False,
            "name": "富文本编辑器",
            "value": "已有内容",
            "disabled": False,
            "bbox": {"x": 100, "y": 100, "width": 400, "height": 200},
        },
        {
            "role": "textbox",
            "tag": "div",
            "_role_source": "explicit",
            "_node_index": 1,
            "_in_shadow": False,
            "sensitive": False,
            "name": "自定义文本框",
            "value": "",
            "disabled": False,
            "bbox": {"x": 100, "y": 320, "width": 300, "height": 40},
        },
        {
            "role": "searchbox",
            "tag": "input",
            "_role_source": "native",
            "_node_index": 2,
            "_in_shadow": False,
            "sensitive": False,
            "name": "站内搜索",
            "value": "",
            "disabled": False,
            "bbox": {"x": 500, "y": 20, "width": 200, "height": 32},
        },
    ]
    fake_playwright.chromium.context.page = fake_page

    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=str(tmp_path),
    )
    await worker.open(
        session_id="bs-textbox-test",
        profile_path=str(tmp_path / "profile-textbox"),
        url="https://example.com/",
    )
    snapshot = await worker.snapshot("bs-textbox-test")

    assert len(snapshot.elements) == 3
    rich_editor = snapshot.elements[0]
    assert rich_editor.role == "textbox"
    assert rich_editor.tag == "div"
    assert rich_editor.name == "富文本编辑器"

    aria_box = snapshot.elements[1]
    assert aria_box.role == "textbox"
    assert aria_box.name == "自定义文本框"

    search_box = snapshot.elements[2]
    assert search_box.role == "searchbox"
    assert search_box.name == "站内搜索"


@pytest.mark.asyncio
async def test_worker_raises_browser_environment_error_when_playwright_missing(tmp_path):
    from app.services.ai.browser.browser_worker import BrowserEnvironmentError, BrowserWorker

    def failing_factory():
        raise ModuleNotFoundError("No module named 'playwright'")

    worker = BrowserWorker(
        playwright_factory=failing_factory,
        url_validator=lambda url: url,
        screenshot_dir=str(tmp_path),
    )

    with pytest.raises(BrowserEnvironmentError) as exc_info:
        await worker.open(
            session_id="bs-missing-pw",
            profile_path=str(tmp_path / "profile-missing-pw"),
            url="https://example.com/",
        )

    assert "pip install playwright" in str(exc_info.value)
    assert "playwright install chromium" in str(exc_info.value)


@pytest.mark.asyncio
async def test_worker_raises_browser_environment_error_when_chromium_not_installed(tmp_path):
    from app.services.ai.browser.browser_worker import BrowserEnvironmentError, BrowserWorker

    class FakeChromiumMissing:
        async def launch_persistent_context(self, *args, **kwargs):
            raise Exception("Executable doesn't exist at /root/.cache/ms-playwright/chromium-1112/chrome-linux/chrome. Please run \"playwright install\"")

    class FakePlaywrightObj:
        chromium = FakeChromiumMissing()

    worker = BrowserWorker(
        playwright_factory=lambda: FakePlaywrightObj(),
        url_validator=lambda url: url,
        screenshot_dir=str(tmp_path),
    )

    with pytest.raises(BrowserEnvironmentError) as exc_info:
        await worker.open(
            session_id="bs-missing-chromium",
            profile_path=str(tmp_path / "profile-missing-chromium"),
            url="https://example.com/",
        )

    assert "playwright install chromium" in str(exc_info.value)


@pytest.mark.asyncio
async def test_worker_check_environment():
    from app.services.ai.browser.browser_worker import BrowserWorker

    class FakeChromiumReady:
        def executable_path(self):
            return "/dummy/path/to/chrome"

    class FakePlaywrightReady:
        chromium = FakeChromiumReady()

    worker = BrowserWorker(
        playwright_factory=lambda: FakePlaywrightReady(),
    )
    # mock os.path.exists
    import unittest.mock as mock
    with mock.patch("os.path.exists", return_value=True):
        env = await worker.check_environment()
        assert env["status"] == "ready"
        assert env["playwright_installed"] is True
        assert env["chromium_installed"] is True
        assert env["chromium_executable"] == "/dummy/path/to/chrome"


@pytest.mark.asyncio
async def test_worker_wait_for_accepts_target_ref_and_snapshot(tmp_path):
    from app.services.ai.browser.browser_worker import BrowserWaitTimeout

    fake_playwright = FakePlaywright()
    worker = BrowserWorker(
        playwright_factory=lambda: fake_playwright,
        url_validator=lambda url: url,
        screenshot_dir=None,
    )
    await worker.open(
        session_id="bs-wait-target-ref",
        profile_path=str(tmp_path / "profile-wait-target-ref"),
        url="https://example.com/flights",
    )
    page = fake_context_page(fake_playwright)
    page.evaluate = AsyncMock(
        return_value={
            "scroll_x": 0,
            "scroll_y": 0,
            "viewport_width": 1280,
            "viewport_height": 800,
            "document_width": 1280,
            "document_height": 800,
            "page_text": "航班查询结果",
            "visible_text": "航班列表已加载",
        }
    )

    # 1. 验证传入 target_ref 和 snapshot 时不会发生 unexpected keyword argument 异常
    snapshot = await worker.snapshot("bs-wait-target-ref")
    waited = await worker.wait_for(
        "bs-wait-target-ref",
        condition="text",
        value="航班",
        target_ref="e1",
        snapshot=snapshot,
        timeout_ms=1000,
    )
    assert waited.url == "https://example.com/flights"

    # 2. 验证支持 condition="ready" 自动映射为网络空闲等待
    waited_ready = await worker.wait_for(
        "bs-wait-target-ref",
        condition="ready",
        target_ref=None,
        snapshot=None,
        timeout_ms=1000,
    )
    assert waited_ready.url == "https://example.com/flights"

    # 3. 验证超时异常携带清晰秒数与条件说明
    with pytest.raises(BrowserWaitTimeout) as exc_info:
        await worker.wait_for(
            "bs-wait-target-ref",
            condition="url",
            value="/non_existent_path",
            timeout_ms=100,
        )
    assert "秒内页面等待未满足" in str(exc_info.value)


