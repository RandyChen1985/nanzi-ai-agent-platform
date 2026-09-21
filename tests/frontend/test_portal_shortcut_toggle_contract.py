"""Contract: 快捷指令栏数据门户/知识库/工作空间胶囊支持开合切换。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend" / "src" / "views" / "EmbedChat.vue"
DEBUG = ROOT / "frontend" / "src" / "views" / "AgentDebug.vue"


def _assert_portal_toggle(source: str):
    assert "isDatasetPortalSlashCommand" in source
    assert "isKnowledgePortalSlashCommand" in source
    assert "isWorkspaceSlashCommand" in source
    # 已打开则关闭
    assert "if (showPortalDrawer.value)" in source
    assert "closePortalDrawer()" in source
    assert "if (showKnowledgePortal.value)" in source
    assert "closeKnowledgePortal()" in source
    # 工作空间胶囊改由 toggleWorkspaceDrawer 统一开合（已打开则关闭，否则打开）
    assert "toggleWorkspaceDrawer();" in source
    workspace_toggle = source.split("const toggleWorkspaceDrawer = () =>", 1)[1].split("};", 1)[0]
    assert "showWorkspaceDrawer.value = false" in workspace_toggle
    assert "openWorkspaceDrawer()" in workspace_toggle


def test_embed_chat_portal_shortcuts_toggle():
    _assert_portal_toggle(EMBED.read_text(encoding="utf-8"))


def test_agent_debug_portal_shortcuts_toggle():
    _assert_portal_toggle(DEBUG.read_text(encoding="utf-8"))
