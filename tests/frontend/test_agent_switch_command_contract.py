from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_agent_switch_command_normalizer_contract():
    source = _source("frontend/src/utils/agentSwitchCommands.ts")
    assert "SwitchableAgent" in source
    assert "buildAgentSwitchCommand" in source
    assert "normalizeAgentSwitchCommand" in source
    assert "切换到" in source
    assert "CAPABILITY_ALIASES" in source
    assert "data_query" in source
    assert "knowledge_base" in source
    assert "human_resources" in source
    assert "sys-agent-chatbi" not in source
    assert "CHATBI_EXPERT_SWITCH_COMMAND" not in source


def test_chat_surfaces_normalize_switch_text_before_system_command_handling():
    debug_source = _source("frontend/src/views/AgentDebug.vue")
    assert "normalizeAgentSwitchCommand" in debug_source
    assert "const normalizedCmd = normalizeAgentSwitchCommand(cmd, agents.value);" in debug_source
    assert 'cmd.startsWith("/switch_agent_expert?agent_id=")' not in debug_source
    assert 'normalizedCmd.startsWith("/switch_agent_expert?agent_id=")' in debug_source

    embed_source = _source("frontend/src/views/EmbedChat.vue")
    assert "normalizeAgentSwitchCommand" in embed_source
    assert "const normalizedCmd = normalizeAgentSwitchCommand(cmd, allowedAgents.value);" in embed_source
    assert 'cmd.startsWith("/switch_agent_expert?agent_id=")' not in embed_source
    assert 'normalizedCmd.startsWith("/switch_agent_expert?agent_id=")' in embed_source
