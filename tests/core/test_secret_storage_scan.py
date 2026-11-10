"""凭据落库扫描（P0 止血）的单元测试。

覆盖两层：
1. ``SecretScanner`` 的匹配语义——长值优先、短值忽略、键名规则不被绕过；
2. 三个落库出口（trace / history / Redis 运行态）确实走了这一层。

这里不连库、不连 Redis：出口用假会话捕获待写入对象，运行态用假 redis 捕获 payload。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.services.secrets import SecretScanner, secret_fingerprint
from app.services.secrets.scanner import MIN_SECRET_LEN

DB_PASSWORD = "P@ssw0rd!2024"
JIRA_TOKEN = "Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghijklmnop"


class TestSecretFingerprint:
    def test_same_value_same_fingerprint(self):
        assert secret_fingerprint(DB_PASSWORD) == secret_fingerprint(DB_PASSWORD)

    def test_different_value_different_fingerprint(self):
        assert secret_fingerprint(DB_PASSWORD) != secret_fingerprint(DB_PASSWORD + "x")

    def test_surrounding_whitespace_ignored(self):
        assert secret_fingerprint(f"  {DB_PASSWORD}  ") == secret_fingerprint(DB_PASSWORD)

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_value_has_no_fingerprint(self, blank):
        assert secret_fingerprint(blank) == ""

    def test_fingerprint_does_not_leak_plaintext(self):
        fingerprint = secret_fingerprint(DB_PASSWORD)
        assert len(fingerprint) == 16
        assert DB_PASSWORD not in fingerprint
        assert fingerprint.isalnum()


class TestSecretScannerMatching:
    def test_masks_known_value_inside_free_text(self):
        scanner = SecretScanner([("config.mysql_ops_password", DB_PASSWORD)])

        masked = scanner.mask_text(
            f"connect failed: Access denied for user 'ops' using '{DB_PASSWORD}'"
        )

        assert DB_PASSWORD not in masked
        assert "${secret:config.mysql_ops_password}" in masked

    def test_longer_secret_wins_to_avoid_leftover_fragments(self):
        # 短值先替换会留下 "${secret:short}XYZ"——既不是原文也不是完整占位符。
        scanner = SecretScanner([("short", "abcdefgh"), ("long", "abcdefghXYZ")])

        assert scanner.mask_text("abcdefghXYZ") == "${secret:long}"

    def test_short_value_is_ignored(self):
        short = "a" * (MIN_SECRET_LEN - 1)
        scanner = SecretScanner([("tiny", short)])

        assert scanner.is_empty
        assert scanner.mask_text(f"flag={short}") == f"flag={short}"

    def test_duplicate_values_collapse_to_first_label(self):
        scanner = SecretScanner([("first", DB_PASSWORD), ("second", DB_PASSWORD)])

        assert scanner.mask_text(DB_PASSWORD) == "${secret:first}"

    def test_label_is_sanitized(self):
        scanner = SecretScanner([("weird label:带中文/斜杠", DB_PASSWORD)])

        masked = scanner.mask_text(DB_PASSWORD)

        # 标签会进 JSON / 日志，只允许安全字符；中文与斜杠一律折叠成下划线。
        assert masked.startswith("${secret:weird_label")
        assert masked.endswith("}")
        assert "/" not in masked and "带" not in masked

    def test_non_string_secret_values_are_skipped(self):
        scanner = SecretScanner([("label", None)])  # type: ignore[arg-type]

        assert scanner.is_empty

    def test_mask_text_without_secrets_is_identity(self):
        scanner = SecretScanner([])

        assert scanner.mask_text("nothing to see") == "nothing to see"


class TestSecretScannerStructures:
    def test_recurses_into_dict_and_list(self):
        scanner = SecretScanner([("config.mysql_ops_password", DB_PASSWORD)])
        payload = {
            "rows": [{"error": f"bad password {DB_PASSWORD}"}],
            "meta": {"note": DB_PASSWORD},
            "count": 3,
            "ok": True,
            "missing": None,
        }

        masked = scanner.mask_data(payload)

        assert DB_PASSWORD not in str(masked)
        assert masked["rows"][0]["error"] == "bad password ${secret:config.mysql_ops_password}"
        assert masked["meta"]["note"] == "${secret:config.mysql_ops_password}"
        # 非字符串原样保留，避免把计数/布尔写成字符串
        assert masked["count"] == 3
        assert masked["ok"] is True
        assert masked["missing"] is None

    def test_sensitive_key_still_wins_over_fingerprint(self):
        # 键名规则更严格，值必须彻底变成 ******，而不是留下可反查的引用标签。
        scanner = SecretScanner([("config.llm_api_key", DB_PASSWORD)])

        masked = scanner.mask_data({"password": DB_PASSWORD, "note": DB_PASSWORD})

        assert masked["password"] == "******"
        assert masked["note"] == "${secret:config.llm_api_key}"

    def test_none_payload_stays_none(self):
        assert SecretScanner([("x", DB_PASSWORD)]).mask_data(None) is None

    def test_empty_scanner_still_applies_key_rules(self):
        # 快照为空（降级）时不能连既有的键名脱敏也丢掉。
        masked = SecretScanner([]).mask_data({"api_key": "whatever-value"})

        assert masked["api_key"] == "******"


@pytest.mark.asyncio
async def test_trace_logs_are_sanitized_before_persist(monkeypatch):
    """tool_input / tool_output / error_message 落库前必须已擦除。"""
    from app.schemas.agent import AgentExecutionStep
    from app.services.ai import audit as audit_module

    monkeypatch.setattr(
        audit_module,
        "build_storage_scanner",
        _fake_scanner_factory(),
    )

    captured: dict[str, Any] = {}

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def add_all(self, objects):
            captured["rows"] = objects

        async def commit(self):
            return None

    monkeypatch.setattr(audit_module, "AsyncSessionLocal", lambda: _FakeSession())

    step = AgentExecutionStep(
        step_number=1,
        event_type="tool_result",
        tool_name="mysql_query",
        tool_input={"sql": "select 1", "password": DB_PASSWORD},
        tool_output={"error": f"denied for {DB_PASSWORD}"},
        error_message=f"connection failed: {DB_PASSWORD}",
        meta_info={"debug": DB_PASSWORD},
        timestamp=datetime(2026, 11, 9, 10, 0, 0),
    )

    await audit_module.AuditManager.save_trace_logs("trace-1", [step])

    row = captured["rows"][0]
    assert DB_PASSWORD not in str(row.tool_input)
    assert DB_PASSWORD not in str(row.tool_output)
    assert DB_PASSWORD not in str(row.error_message)
    assert DB_PASSWORD not in str(row.meta_info)
    assert row.tool_input["password"] == "******"


@pytest.mark.asyncio
async def test_history_fields_are_sanitized(monkeypatch):
    """query / summary / reasoning / timeline 四字段落库前必须已擦除。"""
    from app.services.ai import audit as audit_module

    monkeypatch.setattr(
        audit_module,
        "build_storage_scanner",
        _fake_scanner_factory(),
    )

    captured: dict[str, Any] = {}

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def add(self, obj):
            captured["history"] = obj

        async def commit(self):
            return None

    monkeypatch.setattr(audit_module, "AsyncSessionLocal", lambda: _FakeSession())

    ok = await audit_module.AuditManager.save_history(
        trace_id="trace-2",
        agent_id="agent-1",
        query=f"帮我把 {DB_PASSWORD} 填进配置",
        summary=f"已使用 {DB_PASSWORD} 连接成功",
        user_info={"user_id": 7, "user_name": "tester"},
        reasoning_content=f"我需要 {DB_PASSWORD} 才能连上",
        process_timeline=[{"event": "tool", "output": f"auth={DB_PASSWORD}"}],
    )

    assert ok is True
    history = captured["history"]
    assert DB_PASSWORD not in (history.query or "")
    assert DB_PASSWORD not in (history.summary or "")
    assert DB_PASSWORD not in (history.reasoning_content or "")
    assert DB_PASSWORD not in str(history.process_timeline)


@pytest.mark.asyncio
async def test_runtime_state_is_sanitized_before_redis_write(monkeypatch):
    """Redis 运行态存 30 天，写入前必须擦除。"""
    from app.services.ai.runtime.agentscope import state_store as state_module

    captured: dict[str, Any] = {}

    class _FakeRedis:
        async def set(self, key, value, ex=None):
            captured["key"] = key
            captured["value"] = value
            captured["ex"] = ex
            return True

    async def _fake_get_redis():
        return _FakeRedis()

    monkeypatch.setattr(
        "app.core.redis.get_redis",
        _fake_get_redis,
    )
    monkeypatch.setattr(
        state_module,
        "sanitize_for_storage",
        _fake_sanitize(),
    )

    state = {"context": [{"role": "tool", "content": f"token={DB_PASSWORD}"}]}

    from app.services.ai.runtime.agentscope import serialize as serialize_module

    async def _fake_serialize(value, path=""):
        return value

    monkeypatch.setattr(serialize_module, "serialize_jsonable", _fake_serialize)

    await state_module.agent_state_store.save(
        user_id="7",
        conversation_id="conv-1",
        agent_name="ChatBI",
        agent_version="v1",
        tools_fingerprint="fp",
        model_name="m",
        state=state,
    )

    assert DB_PASSWORD not in captured["value"]
    assert captured["ex"] == state_module.DEFAULT_TTL_SECONDS


def _fake_scanner_factory():
    async def _build():
        return SecretScanner([("config.mysql_ops_password", DB_PASSWORD)])

    return _build


def _fake_sanitize():
    async def _sanitize(data, *, field=""):
        return SecretScanner([("config.mysql_ops_password", DB_PASSWORD)]).mask_data(data)

    return _sanitize
