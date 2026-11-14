"""前端契约：会话级 MCP 工具挂载与技能中心对齐。"""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
CHAT_INPUT = (ROOT / "frontend/src/components/embed/ChatInput.vue").read_text(encoding="utf-8")
MCP_MENU = (ROOT / "frontend/src/components/embed/McpCascadeMenu.vue").read_text(encoding="utf-8")
EMBED = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
RESOURCE_SVC = (ROOT / "app/services/conversation_resource_service.py").read_text(encoding="utf-8")


def test_mcp_cascade_menu_exists_and_lists_portal_tools():
    assert "MCP 工具" in MCP_MENU or "我的 MCP" in MCP_MENU
    assert "/api/portal/tools/mcp" in MCP_MENU
    assert "attachedToolIds" in MCP_MENU
    assert "/dashboard/personal?tab=mcp" in MCP_MENU
    assert "groupedTools" in MCP_MENU
    assert "toggleGroupSelectAll" in MCP_MENU
    assert "挂载已选" in MCP_MENU
    assert "w-[min(28rem" in MCP_MENU or "min(28rem" in MCP_MENU
    # 会话挂载仅个人 MCP，不展示平台 Tab
    assert 'activeScope === \'global\'' not in MCP_MENU
    assert "personalTools" in MCP_MENU
    assert "scope === 'personal'" in MCP_MENU or 'scope || \'\').toLowerCase() === \'personal\'' in MCP_MENU
    assert "mcpToolDisplayName" in MCP_MENU
    assert "fillHeight" in MCP_MENU
    assert "flex-1 min-h-0" in MCP_MENU
    assert "left-full top-0 bottom-0" in CHAT_INPUT
    assert "fill-height" in CHAT_INPUT


def test_chat_input_has_mcp_center_entry():
    assert "MCP 工具" in CHAT_INPUT
    assert "McpCascadeMenu" in CHAT_INPUT
    assert "openMcpCascade" in CHAT_INPUT
    assert "select-mcp-tool" in CHAT_INPUT


def test_embed_chat_persists_mcp_tools_in_resource_scope():
    assert "mcp_tools" in EMBED
    assert "mountMcpToolToSession" in EMBED
    assert "attached-mcp-tool-ids" in EMBED
    assert "'mcp_tools'" in EMBED or '"mcp_tools"' in EMBED
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")
    for source in (EMBED, debug):
        assert "/resource-scope/mcp-tools" in source
        assert "mcpMountFeedback(tools, saved.mounted_mcp_tools)" in source
    assert "mcpMountFeedback(requested.mcp_tools, saved.mcp_tools)" in EMBED
    assert "toLowerCase() === 'personal'" in EMBED
    assert "仅可选择个人已发布 MCP" in EMBED


def test_mount_feedback_matches_server_receipt():
    result = subprocess.run(
        ["node", "tests/frontend/session_mcp_mount_feedback.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_conversation_resource_service_supports_mcp_tools():
    assert "mcp_tools" in RESOURCE_SVC
    assert '"mcp_tools"' in RESOURCE_SVC or "'mcp_tools'" in RESOURCE_SVC
