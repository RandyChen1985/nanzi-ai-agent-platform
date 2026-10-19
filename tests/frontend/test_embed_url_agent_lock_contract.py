"""Contract: EmbedChat URL agent_id 深链锁定与引导页。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend" / "src" / "views" / "EmbedChat.vue"
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "embed" / "ChatInput.vue"
AGENTS_API = ROOT / "app" / "api" / "portal" / "endpoints" / "agents.py"


def test_embed_access_api_endpoint_exists():
    text = AGENTS_API.read_text(encoding="utf-8")
    assert '/{agent_id}/embed-access' in text
    assert "AGENT_NOT_FOUND" in text
    assert "AGENT_FORBIDDEN" in text


def test_embed_chat_url_agent_lock_and_status_label():
    text = EMBED.read_text(encoding="utf-8")
    assert "isUrlAgentPinned" in text
    assert "urlAgentAccessError" in text
    assert "resolveUrlPinnedAgent" in text
    assert "embed-access" in text
    assert "headerExpertLabel" in text
    assert "准备就绪" in text
    assert "isUrlAgentPinned && pinnedAgentLabel" not in text or "headerExpertLabel" in text
    assert "{{ pinnedAgentLabel }} 准备就绪" not in text
    assert 'v-if="!isRoutingSettingsLocked"' in text  # ExpertCapsule hidden for every integration lock
    assert "lock-expert-agent" in text
    assert "effectiveSlashCommands" in text
    assert "无权使用该智能体" in text
    assert "智能体不存在" in text
    assert "返回全能助手" not in text
    assert "clearUrlAgentPinAndReload" not in text


def test_chat_input_hides_expert_switch_but_keeps_at_for_files_when_locked():
    """链接锁定专家时，专家切换入口仍要隐藏；但 `@` 不能整体失效——
    文件 tab 与「由谁切专家」无关，锁定的只是专家切换。"""
    text = CHAT_INPUT.read_text(encoding="utf-8")
    assert "lockExpertAgent" in text
    assert "v-if=\"!lockExpertAgent\"" in text or "v-if='!lockExpertAgent'" in text
    assert "!isMobileViewport && !lockExpertAgent" in text
    # 旧行为：锁定后 @ 完全不响应；现在 @ 仍弹层，由 MentionList 把专家 tab 换成锁定提示
    assert "if (atMatch && !props.lockExpertAgent)" not in text
    assert ':expert-locked="lockExpertAgent"' in text
    assert "sys_knowledge_portal" in text
