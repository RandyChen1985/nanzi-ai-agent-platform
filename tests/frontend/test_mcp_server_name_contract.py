"""前端契约：MCP 服务显示名称固定前缀 + 用户后缀。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"
NAME_HELPER = ROOT / "frontend/src/utils/mcpServerName.ts"
ACCEPT_WIZARD = ROOT / "frontend/src/components/mcp/McpShareAcceptWizard.vue"


def test_mcp_name_helper_builds_visible_user_prefix():
    helper_source = NAME_HELPER.read_text(encoding="utf-8")
    assert "buildMcpServerNamePrefix" in helper_source
    assert "composeMcpServerName" in helper_source
    assert "stripMcpServerNamePrefix" in helper_source
    # 2026-11 起：priv-<base36(user_id)>- / pub-<base36(user_id)>-
    # （原 mcp-private-<完整用户名>- 太长，典型名字 29–37 字符）
    assert "priv-${ownerPart}-" in helper_source
    assert "pub-${ownerPart}-" in helper_source
    # 归属段是用户 id 的 base36，与后端 owner_name_segment() 逐字符一致
    assert "toString(36)" in helper_source
    # 存量旧前缀仍要能剥离：数据不迁移，但编辑老服务不能被静默改名
    assert "mcp-private-${part}-" in helper_source
    assert "/^mcp-(?:public|pub)-/i" in helper_source


def test_mcp_registry_shows_fixed_prefix_and_suffix_input():
    registry_source = REGISTRY.read_text(encoding="utf-8")
    assert "namePrefix" in registry_source
    assert "serverNameSuffix" in registry_source
    # 编辑态走「保留原名」的解析函数，不再无条件重拼前缀（会静默改名平台 Echo）。
    assert "resolveEditedServerName" in registry_source
    assert "{{ namePrefix }}" in registry_source
    # 后缀为空/含非法字符的提示文案统一由 helper 产出（组件里不再各写一份）
    assert "describeMcpServerNameSuffixError" in registry_source
    assert "请填写服务名称后缀" in NAME_HELPER.read_text(encoding="utf-8")


def test_mcp_registry_shows_owner_badge():
    """名字里的归属段只是 base36 编码，「谁建的」必须由列表接口字段补出来。"""
    registry_source = REGISTRY.read_text(encoding="utf-8")
    assert "owner_display_name" in registry_source
    assert "showOwnerBadge" in registry_source
    assert "创建人" in registry_source


def test_mcp_name_suffix_rejects_non_ascii():
    """中文/全角/空格后缀必须被拒绝并说明原因，不能静默变成空名字。"""
    helper = NAME_HELPER.read_text(encoding="utf-8")
    assert "MCP_SERVER_NAME_PATTERN" in helper
    assert "describeMcpServerNameSuffixError" in helper

    registry = REGISTRY.read_text(encoding="utf-8")
    assert "describeMcpServerNameSuffixError" in registry
    assert "serverNameSuffixError" in registry
    # 回填的存量名（平台 Echo 的中文名、历史带空格名）没被改动时不得被拦
    assert "untouchedSuffix" in registry

    wizard = ACCEPT_WIZARD.read_text(encoding="utf-8")
    assert "describeMcpServerNameSuffixError" in wizard
    assert "nameSuffixError" in wizard
