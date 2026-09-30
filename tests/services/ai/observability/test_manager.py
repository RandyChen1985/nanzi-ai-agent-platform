"""``manager`` 单元测试：SDK 生命周期、导出过滤、内容脱敏、热重建、降级。

真实 ``langfuse`` 已安装，这里只把 ``langfuse.Langfuse`` 换成假实现，
从而在不起网络请求、不注册全局 TracerProvider 的前提下验证：
1. 关闭态绝不构造 client（冷启动零开销的依据）；
2. 两个钩子的判定逻辑；
3. 构造失败只降级、不影响业务；
4. 连接参数变化时旧 client 被 shutdown 且不会重复上报。
"""

import threading
import time
import types

import httpx
import pytest
from langfuse.types import MaskOtelSpansResult, OtelSpanIdentifier

from app.services.ai.observability import manager, settings as obs_settings

pytestmark = pytest.mark.no_infrastructure


def _row(**overrides):
    row = {
        "enabled": True,
        "host": "http://lf:3000",
        "public_key": "pk-lf",
        "secret_key": "sk-lf",
        "sample_rate": 1.0,
        "capture_content": True,
        "environment": None,
        "release": None,
        "timeout_seconds": 5,
        "trace_url_template": None,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _reset():
    manager.reset_for_tests()
    obs_settings.reset_for_tests()
    yield
    manager.reset_for_tests()
    obs_settings.reset_for_tests()


def _install_fake_client(monkeypatch, captured, fail: bool = False):
    """把 langfuse.Langfuse 替换成记录参数的假实现。"""

    class FakeClient:
        def __init__(self, **kwargs):
            if fail:
                raise RuntimeError("sdk exploded")
            captured.setdefault("init_kwargs", []).append(kwargs)
            self.kwargs = kwargs

        def flush(self):
            captured["flush"] = captured.get("flush", 0) + 1

        def shutdown(self):
            captured["shutdown"] = captured.get("shutdown", 0) + 1

    monkeypatch.setattr("langfuse.Langfuse", FakeClient)
    return FakeClient


def _wait_until(predicate, timeout=2.0):
    """等后台线程跑完。

    重建 client 时旧 client 的关闭已挪到后台线程（避免阻塞事件循环），
    因此断言「旧 client 被关闭」不能假设同步完成。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


async def _refresh(monkeypatch, **overrides):
    async def fake_load():
        return _row(**overrides)

    monkeypatch.setattr(obs_settings.config_store, "load_config", fake_load)
    await obs_settings.refresh_snapshot()


def _fake_span(scope="agentscope", attributes=None, trace_id=1):
    context = types.SimpleNamespace(trace_id=trace_id)
    return types.SimpleNamespace(
        instrumentation_scope=types.SimpleNamespace(name=scope),
        attributes=attributes or {},
        get_span_context=lambda: context,
    )


def _mask_params(spans):
    from langfuse.types import MaskOtelSpansParams

    return MaskOtelSpansParams(spans=spans)


# --------------------------------------------------------------------------- 初始化


async def test_init_skipped_when_disabled(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, enabled=False)

    await manager.init_observability()

    assert "init_kwargs" not in captured
    assert manager.current_client() is None
    assert manager.current_fingerprint() is None


async def test_init_skipped_when_not_configured(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, host=None)

    await manager.init_observability()

    assert "init_kwargs" not in captured
    assert manager.current_client() is None


async def test_init_creates_client_with_connection_and_hooks(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, environment="prod", release="2026.09")

    await manager.init_observability()

    kwargs = captured["init_kwargs"][0]
    assert kwargs["host"] == "http://lf:3000"
    assert kwargs["public_key"] == "pk-lf"
    assert kwargs["secret_key"] == "sk-lf"
    assert kwargs["timeout"] == 5
    assert kwargs["environment"] == "prod"
    assert kwargs["release"] == "2026.09"
    assert callable(kwargs["should_export_span"])
    assert callable(kwargs["mask_otel_spans"])
    assert manager.current_client() is not None
    assert manager.current_fingerprint()


async def test_init_failure_degrades_without_raising(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured, fail=True)
    await _refresh(monkeypatch)

    await manager.init_observability()  # 不抛异常

    assert manager.current_client() is None
    assert "sdk exploded" in (manager.last_error() or "")


async def test_init_degrades_when_sdk_missing(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "langfuse", None)
    await _refresh(monkeypatch)

    await manager.init_observability()  # 不抛异常

    assert manager.current_client() is None
    assert manager.last_error()


# --------------------------------------------------------------------------- 导出过滤


async def test_should_export_span_allows_known_scopes(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch)
    await manager.init_observability()
    predicate = captured["init_kwargs"][0]["should_export_span"]

    assert predicate(_fake_span(scope="agentscope")) is True
    assert predicate(_fake_span(scope="nanzi.llm")) is True


async def test_should_export_span_allows_genai_attributes_from_unknown_scope(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch)
    await manager.init_observability()
    predicate = captured["init_kwargs"][0]["should_export_span"]

    span = _fake_span(scope="openai", attributes={"gen_ai.request.model": "x"})
    assert predicate(span) is True


async def test_should_export_span_rejects_unrelated_scope(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch)
    await manager.init_observability()
    predicate = captured["init_kwargs"][0]["should_export_span"]

    assert predicate(_fake_span(scope="sqlalchemy.engine")) is False


async def test_should_export_span_drops_everything_when_disabled(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch)
    await manager.init_observability()
    predicate = captured["init_kwargs"][0]["should_export_span"]

    await _refresh(monkeypatch, enabled=False)
    assert predicate(_fake_span(scope="agentscope")) is False


async def test_should_export_span_honours_sampling(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, sample_rate=0.0)
    await manager.init_observability()
    predicate = captured["init_kwargs"][0]["should_export_span"]

    # trace_id 为 0、采样率 0 → 一律丢弃；采样率 1 → 一律保留。
    assert predicate(_fake_span(trace_id=0)) is False
    await _refresh(monkeypatch, sample_rate=1.0)
    assert predicate(_fake_span(trace_id=0)) is True


async def test_should_export_span_is_trace_consistent(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, sample_rate=0.5)
    await manager.init_observability()
    predicate = captured["init_kwargs"][0]["should_export_span"]

    decisions = {predicate(_fake_span(trace_id=987654321)) for _ in range(20)}
    assert len(decisions) == 1


async def test_should_export_span_swallows_predicate_errors(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch)
    await manager.init_observability()
    predicate = captured["init_kwargs"][0]["should_export_span"]

    class Broken:
        @property
        def instrumentation_scope(self):
            raise RuntimeError("boom")

    assert predicate(Broken()) is False


# --------------------------------------------------------------------------- 内容脱敏


async def test_mask_returns_none_when_capture_enabled(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, capture_content=True)
    await manager.init_observability()
    mask = captured["init_kwargs"][0]["mask_otel_spans"]

    identifier = OtelSpanIdentifier(trace_id="a" * 32, span_id="b" * 16)
    span = types.SimpleNamespace(attributes={"gen_ai.input.messages": "[]"})

    assert mask(params=_mask_params({identifier: span})) is None


async def test_mask_deletes_only_content_attributes(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, capture_content=False)
    await manager.init_observability()
    mask = captured["init_kwargs"][0]["mask_otel_spans"]

    identifier = OtelSpanIdentifier(trace_id="a" * 32, span_id="b" * 16)
    span = types.SimpleNamespace(
        attributes={
            "gen_ai.input.messages": "[]",
            "gen_ai.output.messages": "[]",
            "gen_ai.tool.call.arguments": "{}",
            "gen_ai.tool.call.result": "{}",
            "langfuse.observation.input": "x",
            "langfuse.observation.output": "y",
            # 结构信息必须保留，否则脱敏后排障失去意义。
            "gen_ai.tool.name": "search",
            "gen_ai.tool.definitions": "[]",
            "gen_ai.usage.input_tokens": 3,
        }
    )

    result = mask(params=_mask_params({identifier: span}))

    assert isinstance(result, MaskOtelSpansResult)
    patch = result.span_patches[identifier]
    assert set(patch.delete_attributes) == {
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "langfuse.observation.input",
        "langfuse.observation.output",
    }
    assert "gen_ai.tool.name" not in patch.delete_attributes


async def test_mask_skips_spans_without_content(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, capture_content=False)
    await manager.init_observability()
    mask = captured["init_kwargs"][0]["mask_otel_spans"]

    identifier = OtelSpanIdentifier(trace_id="a" * 32, span_id="c" * 16)
    span = types.SimpleNamespace(attributes={"gen_ai.usage.input_tokens": 1})

    result = mask(params=_mask_params({identifier: span}))
    assert result is None or not result.span_patches


async def test_mask_preserves_structure_when_capture_toggles_back_on(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, capture_content=False)
    await manager.init_observability()
    mask = captured["init_kwargs"][0]["mask_otel_spans"]

    identifier = OtelSpanIdentifier(trace_id="a" * 32, span_id="d" * 16)
    span = types.SimpleNamespace(attributes={"gen_ai.input.messages": "[]"})
    assert mask(params=_mask_params({identifier: span})) is not None

    # 热改回开启：无需重建 client，钩子读实时快照即可恢复上报原文。
    await _refresh(monkeypatch, capture_content=True)
    assert mask(params=_mask_params({identifier: span})) is None


async def test_mask_failure_is_isolated(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, capture_content=False)
    await manager.init_observability()
    mask = captured["init_kwargs"][0]["mask_otel_spans"]

    class Broken:
        @property
        def attributes(self):
            raise RuntimeError("boom")

    identifier = OtelSpanIdentifier(trace_id="a" * 32, span_id="e" * 16)
    result = mask(params=_mask_params({identifier: Broken()}))
    assert result is None or not result.span_patches


# --------------------------------------------------------------------------- 生命周期


async def test_shutdown_flushes_and_clears_client(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch)
    await manager.init_observability()

    await manager.shutdown_observability()

    assert captured["flush"] == 1
    assert captured["shutdown"] == 1
    assert manager.current_client() is None


async def test_shutdown_is_safe_when_disabled(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch, enabled=False)
    await manager.init_observability()

    await manager.shutdown_observability()  # 不抛异常

    assert "shutdown" not in captured


async def test_connection_change_rebuilds_client(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch)
    await manager.init_observability()
    first_fingerprint = manager.current_fingerprint()

    # 采样率变化不重建。
    await _refresh(monkeypatch, sample_rate=0.2)
    assert len(captured["init_kwargs"]) == 1

    # 主机变化必须重建，且旧 client 被 shutdown（避免重复上报）。
    await _refresh(monkeypatch, host="http://other:3000")
    assert len(captured["init_kwargs"]) == 2
    assert _wait_until(lambda: captured.get("shutdown") == 1), "旧 client 未被关闭"
    assert manager.current_fingerprint() != first_fingerprint


async def test_rebuild_failure_keeps_old_client_usable(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch)
    await manager.init_observability()
    old_client = manager.current_client()

    class BoomAfterFirst:
        """重建时才装上的假实现：构造必失败。"""

        def __init__(self, **kwargs):
            raise RuntimeError("rebuild failed")

        def flush(self):
            pass

        def shutdown(self):
            pass

    monkeypatch.setattr("langfuse.Langfuse", BoomAfterFirst)
    await _refresh(monkeypatch, host="http://other:3000")

    assert manager.current_client() is old_client
    assert "rebuild failed" in (manager.last_error() or "")


# --------------------------------------------------------------------------- 连通性探测


async def test_connection_reports_missing_configuration(monkeypatch):
    await _refresh(monkeypatch, host=None)

    ok, message = await manager.test_connection()

    assert ok is False
    assert "host" in message


async def test_connection_reports_disabled_state(monkeypatch):
    await _refresh(monkeypatch, enabled=False)

    ok, message = await manager.test_connection()

    assert ok is False
    assert "未启用" in message


def _patch_httpx(monkeypatch, captured, *, status_code=200, payload=None, exc=None):
    class FakeResponse:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            if payload is not None:
                return payload
            return {"data": [{"id": "project-1"}]}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, url):
            captured["url"] = url
            if exc is not None:
                raise exc
            return FakeResponse()

    monkeypatch.setattr(manager.httpx, "AsyncClient", FakeAsyncClient)


async def test_connection_success_hits_projects_endpoint(monkeypatch):
    captured = {}
    _patch_httpx(monkeypatch, captured)
    await _refresh(monkeypatch, timeout_seconds=7)

    ok, message = await manager.test_connection()

    assert ok is True
    assert captured["url"] == "http://lf:3000/api/public/projects"
    assert isinstance(captured["client_kwargs"]["auth"], httpx.BasicAuth)
    assert captured["client_kwargs"]["timeout"] == 7
    assert "project-1" not in message  # 不回显项目内容，只报告连通性


async def test_connection_reports_invalid_credentials(monkeypatch):
    captured = {}
    _patch_httpx(monkeypatch, captured, status_code=401)
    await _refresh(monkeypatch)

    ok, message = await manager.test_connection()

    assert ok is False
    assert "401" in message


async def test_connection_reports_unreachable_host(monkeypatch):
    captured = {}
    _patch_httpx(monkeypatch, captured, exc=httpx.ConnectError("dns failed"))
    await _refresh(monkeypatch)

    ok, message = await manager.test_connection()

    assert ok is False
    assert "无法连接" in message


async def test_connection_reports_timeout(monkeypatch):
    captured = {}
    _patch_httpx(monkeypatch, captured, exc=httpx.ReadTimeout("slow"))
    await _refresh(monkeypatch)

    ok, message = await manager.test_connection()

    assert ok is False
    assert "超时" in message


async def test_connection_reports_empty_project_list(monkeypatch):
    captured = {}
    _patch_httpx(monkeypatch, captured, status_code=200, payload={"data": []})
    await _refresh(monkeypatch)

    ok, message = await manager.test_connection()

    assert ok is False
    assert "项目" in message


async def test_connection_streams_through_sdk_exception_types(monkeypatch):
    captured = {}
    _patch_httpx(monkeypatch, captured, exc=RuntimeError("unexpected"))
    await _refresh(monkeypatch)

    ok, message = await manager.test_connection()

    assert ok is False
    assert "unexpected" in message


# ------------------------------------------------- 连通性探测：草稿值（未保存的表单输入）

def _basic_credentials(auth):
    """解出 httpx.BasicAuth 实际发送的凭据。

    该类只保留编码后的 ``_auth_header``（无 username/password 属性），
    因此这里解码断言——反而更接近"真正发到线上的 HTTP 头"。
    """
    import base64

    scheme, token = auth._auth_header.split(" ", 1)
    assert scheme == "Basic"
    username, password = base64.b64decode(token).decode().split(":", 1)
    return username, password


async def test_probe_uses_draft_values_without_saving(monkeypatch):
    """未保存的表单值应当可直接用于探测（否则用户必须"先保存再测"）。"""
    captured = {}
    _patch_httpx(monkeypatch, captured)
    await _refresh(monkeypatch, host="http://old:3000", public_key="pk-old",
                   secret_key="sk-old")

    ok, _ = await manager.test_connection(
        {"host": "http://draft:3000", "public_key": "pk-new", "secret_key": "sk-new"}
    )

    assert ok is True
    assert captured["url"].startswith("http://draft:3000")
    assert _basic_credentials(captured["client_kwargs"]["auth"]) == ("pk-new", "sk-new")


async def test_probe_draft_values_do_not_touch_saved_snapshot(monkeypatch):
    """探测必须无副作用：不能用草稿值污染进程内快照。"""
    captured = {}
    _patch_httpx(monkeypatch, captured)
    await _refresh(monkeypatch, host="http://old:3000", public_key="pk-old",
                   secret_key="sk-old")

    await manager.test_connection({"host": "http://draft:3000"})

    assert obs_settings.get_snapshot().host == "http://old:3000"


async def test_probe_falls_back_to_saved_secret_when_draft_omits_it(monkeypatch):
    """密钥框留空（表示不修改）时，必须复用已保存的密钥。"""
    captured = {}
    _patch_httpx(monkeypatch, captured)
    await _refresh(monkeypatch, host="http://old:3000", public_key="pk-old",
                   secret_key="sk-saved")

    ok, _ = await manager.test_connection({"host": "http://draft:3000", "secret_key": ""})

    assert ok is True
    assert _basic_credentials(captured["client_kwargs"]["auth"]) == ("pk-old", "sk-saved")


async def test_probe_reports_missing_host_from_draft(monkeypatch):
    """草稿里没填 host 且库里也没有时，报缺字段而不是"未启用"。"""
    await _refresh(monkeypatch, enabled=True, host=None, public_key=None, secret_key=None)

    ok, message = await manager.test_connection({"enabled": True})

    assert ok is False
    assert "host" in message


async def test_probe_allows_enabling_via_draft_switch(monkeypatch):
    """界面上刚打开开关（尚未保存）就点测试，不应回"未启用"。"""
    captured = {}
    _patch_httpx(monkeypatch, captured)
    await _refresh(monkeypatch, enabled=False, host="http://lf:3000", public_key="pk",
                   secret_key="sk")

    ok, _ = await manager.test_connection({"enabled": True})

    assert ok is True


async def test_probe_respects_disabled_draft_switch(monkeypatch):
    """草稿里开关是关的，探测就该直接拒绝。"""
    await _refresh(monkeypatch, enabled=True, host="http://lf:3000", public_key="pk",
                   secret_key="sk")

    ok, message = await manager.test_connection({"enabled": False})

    assert ok is False
    assert "未启用" in message


async def test_probe_draft_timeout_overrides_saved(monkeypatch):
    captured = {}
    _patch_httpx(monkeypatch, captured, exc=manager.httpx.TimeoutException("slow"))
    await _refresh(monkeypatch, timeout_seconds=5)

    ok, message = await manager.test_connection({"timeout_seconds": 17})

    assert ok is False
    assert captured["client_kwargs"]["timeout"] == 17
    assert "17" in message


async def test_probe_without_overrides_keeps_old_behaviour(monkeypatch):
    captured = {}
    _patch_httpx(monkeypatch, captured)
    await _refresh(monkeypatch, host="http://lf:3000", public_key="pk", secret_key="sk")

    ok, _ = await manager.test_connection()

    assert ok is True
    assert captured["url"].startswith("http://lf:3000")


# ------------------------------------------------- 重建 client 不得阻塞事件循环


async def test_rebuild_does_not_block_event_loop_when_teardown_is_slow(monkeypatch):
    """回归：旧 client 的关闭会同步 flush（``force_flush`` 要等导出线程把队列发完），
    Langfuse 不可达时最长要等一个 timeout。这**绝不能在事件循环线程里做**——
    否则管理员一改配置，正在流式输出的对话就会被一起卡住。

    约束：重建必须立刻返回（不阻塞事件循环），但旧 client 仍须确实被关闭。
    """
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch)
    await manager.init_observability()
    assert manager.current_client() is not None

    started = threading.Event()

    def slow_teardown(client):
        started.set()
        time.sleep(1.0)

    monkeypatch.setattr(manager, "_teardown_client", slow_teardown)

    t0 = time.perf_counter()
    await _refresh(monkeypatch, host="http://other:3000")  # 指纹变化 -> 重建
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.5, (
        f"重建 client 阻塞了事件循环 {elapsed:.2f}s；"
        "旧 client 的 flush 必须挪出事件循环线程"
    )
    assert started.wait(2.0), "旧 client 仍必须被关闭（只是不能在事件循环里同步做）"


async def test_rebuild_closes_every_replaced_client(monkeypatch):
    """后台关闭不能变成「不关闭」：被换下的 client 仍要 flush + shutdown。"""
    captured = {}
    _install_fake_client(monkeypatch, captured)
    await _refresh(monkeypatch)
    await manager.init_observability()
    first = manager.current_client()

    await _refresh(monkeypatch, host="http://other:3000")
    second = manager.current_client()

    assert second is not first
    assert _wait_until(lambda: captured.get("shutdown") == 1), "旧 client 未被关闭"
    assert captured.get("flush") == 1, "关闭前应尽力 flush，避免丢最后一批 span"
