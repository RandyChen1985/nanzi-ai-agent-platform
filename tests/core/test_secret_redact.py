"""工具结果的回显兜底。

模型看不到凭据、凭据只在出站那一刻补进请求——这两层已经封住了。但「工具把
收到的参数原样回显」不受我们控制：远端服务、某个调试接口，都可能带着值一起
回到上下文。这里在回灌前做一次精确匹配。

边界同样重要：**扫不到正常内容**。值太短就不登记，宁可漏掉极短的，
也不要把结果搅乱。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.secrets.redact import (
    MAX_VALUES,
    MIN_LENGTH,
    injected_count,
    looks_like_leak,
    redact_text,
    redact_value,
    register_injected,
    reset_injected,
)


pytestmark = pytest.mark.no_infrastructure

TOKEN = "sk-live-abcdefghijklmnop"


@pytest.fixture(autouse=True)
def _clean():
    reset_injected()
    yield
    reset_injected()


def test_registered_value_is_masked():
    register_injected(TOKEN)

    out = redact_text(f"远端回显：{TOKEN}")

    assert TOKEN not in out
    assert "sk-l***mnop" in out


def test_unrelated_text_is_untouched():
    register_injected(TOKEN)

    text = "这是一段完全无关的正常返回内容"
    assert redact_text(text) == text


def test_short_values_are_not_registered():
    """"1" / "true" 这种值在任何文本里都能命中，登记了等于到处打码。"""
    register_injected("1")
    register_injected("true")
    register_injected("x" * (MIN_LENGTH - 1))

    assert injected_count() == 0
    assert redact_text("1 true xxxxxxx") == "1 true xxxxxxx"


def test_value_at_threshold_is_registered():
    value = "y" * MIN_LENGTH
    register_injected(value)
    assert injected_count() == 1


def test_duplicate_registration_is_ignored():
    register_injected(TOKEN)
    register_injected(TOKEN)
    assert injected_count() == 1


def test_value_count_is_capped():
    """一次请求可能连调很多工具；不设上限扫描会线性变慢。"""
    for i in range(MAX_VALUES + 5):
        register_injected(f"secret-value-{i:04d}")
    assert injected_count() == MAX_VALUES


def test_nested_structures_are_redacted():
    register_injected(TOKEN)

    payload = {
        "success": True,
        "content": [{"type": "text", "text": f"echo {TOKEN}"}],
        "meta": {"note": TOKEN},
    }
    out = redact_value(payload)

    assert TOKEN not in str(out)
    assert out["success"] is True
    # key 不动：字段名不是值
    assert "note" in out["meta"]


def test_tuples_keep_their_type():
    register_injected(TOKEN)
    out = redact_value((TOKEN, "ok"))
    assert isinstance(out, tuple)
    assert TOKEN not in out[0]


def test_non_string_values_pass_through():
    register_injected(TOKEN)
    assert redact_value(42) == 42
    assert redact_value(None) is None
    assert redact_value(True) is True


def test_nothing_registered_means_no_work():
    """绝大多数调用没有注入凭据，这条路径必须便宜。"""
    assert redact_text(TOKEN) == TOKEN
    assert looks_like_leak(TOKEN) is False


def test_looks_like_leak_is_a_diagnostic():
    register_injected(TOKEN)
    assert looks_like_leak(f"x {TOKEN} y") is True
    assert looks_like_leak("nothing here") is False


@pytest.mark.no_infrastructure
class TestScriptOutputIsScanned:
    """脚本 stdout 是和其他工具一样的回显面。

    之前故意不扫，理由是「脚本 env 默认关闭」。但默认关闭不等于关着不用——
    用户一旦开了这个场景，脚本里 `echo $API_KEY` 就会把明文写进 stdout，
    再随工具结果回到模型上下文。这条路径和远端服务回显没有区别。
    """

    def test_render_redacts_injected_value(self):
        from app.services.ai.tools.script_tool_runtime import ScriptRunResult, render_result_for_model
        from app.services.secrets.redact import register_injected, reset_injected

        reset_injected()
        token = "sk-script-echo-9f3a2b1c"
        register_injected(token)
        try:
            result = ScriptRunResult(
                ok=True,
                exit_code=0,
                stdout=f'{{"key": "{token}"}}',
                stderr="",
                duration_ms=5,
            )
            import asyncio
            rendered = asyncio.run(
                render_result_for_model(result, workdir=Path("/tmp"))
            )
            assert token not in rendered
            assert "***" in rendered
        finally:
            reset_injected()

    def test_failed_run_stderr_is_also_scanned(self):
        """失败时把 stderr 整段给模型——脚本打印密钥到 stderr 同样会泄。"""
        from app.services.ai.tools.script_tool_runtime import ScriptRunResult, render_result_for_model
        from app.services.secrets.redact import register_injected, reset_injected

        reset_injected()
        token = "sk-stderr-leak-7d1e4f2a"
        register_injected(token)
        try:
            result = ScriptRunResult(
                ok=False,
                exit_code=1,
                stdout="",
                stderr=f"ValueError: bad key {token}",
                duration_ms=5,
            )
            import asyncio
            rendered = asyncio.run(
                render_result_for_model(result, workdir=Path("/tmp"))
            )
            assert token not in rendered
        finally:
            reset_injected()


@pytest.mark.no_infrastructure
class TestOldestValuesAreEvicted:
    """超过上限时淘汰最旧的，而不是拒绝新登记的。

    原来的写法是 `len(current) >= MAX_VALUES` 就 return —— 第 21 个凭据起
    完全得不到脱敏，而那恰恰是当前这次调用正在用的值。旧值对应的调用早就
    结束、结果已经回灌完了，它才该被踢出去。
    """

    def test_newest_value_is_always_protected(self):
        reset_injected()
        for index in range(MAX_VALUES + 5):
            register_injected(f"sk-rolling-{index:04d}-abcdef")
        assert injected_count() == MAX_VALUES
        newest = f"sk-rolling-{MAX_VALUES + 4:04d}-abcdef"
        assert looks_like_leak(f"echo {newest}") is True, "最新登记的值必须受保护"
        assert newest not in redact_text(f"echo {newest}")

    def test_oldest_is_dropped_first(self):
        reset_injected()
        for index in range(MAX_VALUES + 1):
            register_injected(f"sk-evict-{index:04d}-abcdef")
        assert not looks_like_leak("sk-evict-0000-abcdef"), "最旧的应该被淘汰"
        assert looks_like_leak(f"sk-evict-{MAX_VALUES:04d}-abcdef")


class TestTraceStorageNeedsBothLayers:
    """Trace / 会话历史落库必须「redact + scanner」两层。

    `scanner` 的凭据来源是 `registry._collect()`，它只采**配置里的密钥**和
    **MCP 固定 Token**——凭据中心的 `sys_secrets` 不在里面。所以远端把凭据回显在
    响应里时，只过 scanner 等于没脱敏，明文会随 `tool_output` 进 `AgentExecutionTrace`。

    能认出那些值的只有 `redact`：它读的是注入时 `register_injected()` 登记的明文，
    生命周期就是这一次请求。`outbound_audit` 一直是两层，ai/audit 原来只有一层。
    """

    def test_registry_does_not_load_credential_center(self):
        """这条断言是「为什么必须两层」的依据，不是实现细节。"""
        from pathlib import Path

        source = (Path(__file__).parents[2] / "app/services/secrets/registry.py").read_text(
            encoding="utf-8"
        )
        collect = source[source.index("async def _collect"):source.index("async def _from_system_configs")]
        assert "_from_system_configs" in collect
        assert "_from_mcp_servers" in collect
        # 一旦这里开始采 sys_secrets，上面两层就可以退化成一层了
        assert "sys_secrets" not in collect and "UserSecret" not in collect

    def test_trace_writes_go_through_both(self):
        from pathlib import Path

        source = (Path(__file__).parents[2] / "app/services/ai/audit.py").read_text(
            encoding="utf-8"
        )
        assert "from app.services.secrets.redact import redact_text, redact_value" in source
        # 四个落库字段都要叠：入参、出参、错误信息、元数据
        assert source.count("redact_value(log.") == 2
        assert "redact_value(getattr(log, 'meta_info', None)" in source
        assert "redact_text(log.error_message" in source
        # 顺序：先 redact 认注入值，再 scanner 认配置值
        assert "scanner.mask_data(redact_value(" in source

    def test_shared_guard_entry_does_both(self):
        """统一入口自己就得是两层——否则每个调用方都得记得补，迟早漏一个。

        `guard.sanitize_for_storage` 的 docstring 一直写着「三个出口共用同一份逻辑」，
        实际只有 scanner 一层：Redis 运行态快照（30 天 TTL，不进数据库备份、没有
        审计留痕）就是这么漏的。
        """
        from pathlib import Path

        source = (Path(__file__).parents[2] / "app/services/secrets/guard.py").read_text(
            encoding="utf-8"
        )
        assert "scanner.mask_data(redact_value(data, log_hits=False))" in source
        # 降级分支也要擦：取快照失败时更容易漏
        assert "mask_sensitive_data(redact_value(data, log_hits=False))" in source

    def test_state_store_keeps_using_the_shared_entry(self):
        """快照存储不要自己拼脱敏——它走的就该是那个统一入口。"""
        from pathlib import Path

        source = (
            Path(__file__).parents[2]
            / "app/services/ai/runtime/agentscope/state_store.py"
        ).read_text(encoding="utf-8")
        assert 'sanitize_for_storage(serialized_state, field="agent_state")' in source

    def test_history_writes_go_through_both_too(self):
        """`save_history` 与 `save_trace_logs` 必须同口径。

        摘要和推理过程是模型写的散文，没有「敏感键名」给 scanner 判断；凭据中心的值
        又不在 scanner 的采集范围里。只过 scanner 等于没脱敏，而这里看起来「擦过了」。
        """
        from pathlib import Path

        source = (Path(__file__).parents[2] / "app/services/ai/audit.py").read_text(
            encoding="utf-8"
        )
        history = source[source.index("async def save_history"):]
        assert "redact_text(query" in history
        assert "redact_text(summary" in history
        assert "redact_text(reasoning_content" in history
        assert "redact_value(process_timeline" in history
