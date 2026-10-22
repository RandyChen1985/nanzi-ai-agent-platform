"""分享接受时若改地址，前端必须提示“认证凭据未复制”。"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

SOURCE = Path("frontend/src/components/mcp/McpShareAcceptWizard.vue").read_text(
    encoding="utf-8"
)


def test_accept_wizard_warns_when_credentials_are_dropped():
    assert "credentials_dropped" in SOURCE
    assert "原认证凭据未复制" in SOURCE
    assert "'warning'" in SOURCE
