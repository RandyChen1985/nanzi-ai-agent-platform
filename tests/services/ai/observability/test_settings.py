"""``settings`` 单元测试：快照内容、状态判定、刷新失败降级。"""

import pytest

from app.services.ai.observability import credentials, settings as obs_settings

pytestmark = pytest.mark.no_infrastructure


def _row(**overrides):
    row = {
        "enabled": False,
        "host": None,
        "public_key": None,
        "secret_key": None,
        "sample_rate": 1.0,
        "capture_content": True,
        "environment": None,
        "release": None,
        "timeout_seconds": 5,
        "trace_url_template": None,
    }
    row.update(overrides)
    return row


def _patch_loader(monkeypatch, row_or_exc):
    async def fake_load():
        if isinstance(row_or_exc, Exception):
            raise row_or_exc
        return row_or_exc

    monkeypatch.setattr(obs_settings.config_store, "load_config", fake_load)


@pytest.fixture(autouse=True)
def _reset():
    obs_settings.reset_for_tests()
    yield
    obs_settings.reset_for_tests()


def test_default_snapshot_is_disabled():
    snapshot = obs_settings.get_snapshot()
    assert snapshot.enabled is False
    assert snapshot.status == "disabled"
    assert snapshot.sample_rate == 1.0
    assert snapshot.capture_content is True


async def test_refresh_populates_snapshot(monkeypatch):
    _patch_loader(
        monkeypatch,
        _row(
            enabled=True,
            host="http://lf:3000",
            public_key="pk",
            secret_key="sk",
            sample_rate=0.5,
            capture_content=False,
            environment="prod",
            release="2026.09",
            timeout_seconds=9,
            trace_url_template="http://lf:3000/trace/{trace_id}",
        ),
    )
    await obs_settings.refresh_snapshot()

    snapshot = obs_settings.get_snapshot()
    assert snapshot.enabled is True
    assert snapshot.status == "enabled"
    assert snapshot.sample_rate == 0.5
    assert snapshot.capture_content is False
    assert snapshot.environment == "prod"
    assert snapshot.release == "2026.09"
    assert snapshot.timeout_seconds == 9
    assert snapshot.trace_url_template == "http://lf:3000/trace/{trace_id}"
    assert snapshot.last_error is None
    assert snapshot.client_fingerprint


async def test_snapshot_reports_not_configured_when_enabled_but_incomplete(monkeypatch):
    _patch_loader(monkeypatch, _row(enabled=True, host="http://lf:3000"))
    await obs_settings.refresh_snapshot()

    snapshot = obs_settings.get_snapshot()
    assert snapshot.status == "not_configured"
    assert snapshot.last_error


async def test_snapshot_reports_degraded_on_decrypt_failure(monkeypatch):
    async def boom():
        raise credentials.LangfuseCredentialError("密文损坏")

    monkeypatch.setattr(obs_settings.config_store, "load_config", boom)
    await obs_settings.refresh_snapshot()

    snapshot = obs_settings.get_snapshot()
    assert snapshot.status == "degraded"
    assert "密文损坏" in (snapshot.last_error or "")


async def test_refresh_failure_keeps_previous_snapshot(monkeypatch):
    _patch_loader(monkeypatch, _row(enabled=True, host="http://lf:3000",
                                    public_key="pk", secret_key="sk"))
    await obs_settings.refresh_snapshot()
    assert obs_settings.get_snapshot().status == "enabled"

    _patch_loader(monkeypatch, RuntimeError("db down"))
    await obs_settings.refresh_snapshot()  # 不抛异常

    snapshot = obs_settings.get_snapshot()
    assert snapshot.status == "enabled"
    assert "db down" in (snapshot.last_error or "")


async def test_fingerprint_tracks_connection_fields_only(monkeypatch):
    _patch_loader(monkeypatch, _row(enabled=True, host="http://lf:3000",
                                    public_key="pk", secret_key="sk"))
    await obs_settings.refresh_snapshot()
    base = obs_settings.get_snapshot().client_fingerprint

    # 采样率变化不应触发 client 重建。
    _patch_loader(monkeypatch, _row(enabled=True, host="http://lf:3000", public_key="pk",
                                    secret_key="sk", sample_rate=0.1))
    await obs_settings.refresh_snapshot()
    assert obs_settings.get_snapshot().client_fingerprint == base

    # 连接参数变化必须触发重建。
    _patch_loader(monkeypatch, _row(enabled=True, host="http://other:3000",
                                    public_key="pk", secret_key="sk"))
    await obs_settings.refresh_snapshot()
    assert obs_settings.get_snapshot().client_fingerprint != base


async def test_start_refresh_task_is_idempotent(monkeypatch):
    async def noop_refresh():
        return None

    monkeypatch.setattr(obs_settings, "refresh_snapshot", noop_refresh)
    first = obs_settings.start_refresh_task(interval_seconds=3600)
    second = obs_settings.start_refresh_task(interval_seconds=3600)
    assert first is second
    first.cancel()


async def test_is_tracing_ready_requires_enabled_snapshot():
    assert obs_settings.is_tracing_ready() is False
