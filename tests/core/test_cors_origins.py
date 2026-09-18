"""CORS 白名单配置卫生：通配符与凭据的组合必须被显式告警，而不是静默生效。"""

import logging

import pytest

from app.core.config import resolve_cors_origins


pytestmark = pytest.mark.no_infrastructure


def test_explicit_origins_are_preserved():
    origins = ["https://console.example.com", "https://ops.example.com"]

    assert resolve_cors_origins(origins) == origins


def test_blank_entries_are_dropped():
    assert resolve_cors_origins(["", " https://console.example.com ", "  "]) == [
        "https://console.example.com"
    ]


def test_missing_configuration_falls_back_to_wildcard_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_cors_origins(None) == ["*"]

    assert "ALLOWED_ORIGINS" in caplog.text


def test_wildcard_conflicting_with_credentials_is_warned(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_cors_origins(["*"]) == ["*"]

    assert "ALLOWED_ORIGINS" in caplog.text


def test_explicit_whitelist_logs_nothing(caplog):
    with caplog.at_level(logging.WARNING):
        resolve_cors_origins(["https://console.example.com"])

    assert caplog.text == ""
