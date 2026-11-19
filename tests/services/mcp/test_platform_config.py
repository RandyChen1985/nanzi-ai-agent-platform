from pathlib import Path

import pytest

from app.services.mcp.platform_config import (
    MCP_PLATFORM_CONFIG_FIELDS,
    PlatformMcpConfigService,
)


pytestmark = pytest.mark.no_infrastructure


def test_platform_mcp_config_has_singleton_fields_and_safe_defaults():
    assert MCP_PLATFORM_CONFIG_FIELDS == (
        "platform_enabled",
        "agent_enabled",
        "conversation_enabled",
        "knowledge_enabled",
        "metadata_enabled",
        # 速率限制两个字段后加入单例配置；0 表示不限流（安全默认）。
        "rate_limit_client_per_minute",
        "rate_limit_user_per_minute",
    )
    assert PlatformMcpConfigService.to_dict(None) == {
        "platform_enabled": False,
        "agent_enabled": False,
        "conversation_enabled": False,
        "knowledge_enabled": False,
        "metadata_enabled": False,
        "rate_limit_client_per_minute": 0,
        "rate_limit_user_per_minute": 0,
    }


def test_platform_mcp_config_is_not_backed_by_generic_system_configs():
    source = Path("app/services/mcp/platform_config.py").read_text(encoding="utf-8")
    model_source = Path("app/models/platform_mcp.py").read_text(encoding="utf-8")

    assert "from app.services.config_service import" not in source
    assert "sys_mcp_platform_config" in model_source
