import pytest
from types import SimpleNamespace

from app.services.ai.runtime.agentscope.workspace import (
    append_session_workspace_sandbox_to_system_prompt,
    collect_workspace_file_tool_names,
    normalize_workspace_tool_names,
)

pytestmark = pytest.mark.no_infrastructure


def test_normalize_workspace_tool_names_aliases():
    assert normalize_workspace_tool_names({"read_file", "exec_command"}) == {"Read", "Bash"}


def test_collect_workspace_file_tool_names_ignores_sql_tools():
    tools = [
        SimpleNamespace(name="get_dataset_schema"),
        SimpleNamespace(name="Read"),
    ]
    assert collect_workspace_file_tool_names(tools) == {"Read"}


@pytest.mark.asyncio
async def test_append_workspace_prompt_when_file_tools_and_conversation(monkeypatch):
    async def _root():
        return "/tmp/workspaces"

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )

    tools = [SimpleNamespace(name="Grep")]
    result = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id="conv-1",
        tools=tools,
    )
    assert "Base prompt" in result
    assert "[Session Workspace & Path Sandbox]" in result
    assert "/tmp/workspaces/u1/conv-1" in result
    assert "/app/data/uploads" in result
    assert "Grep" in result


@pytest.mark.asyncio
async def test_append_workspace_prompt_skips_without_conversation_or_file_tools():
    tools = [SimpleNamespace(name="memory_search")]
    result = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id="conv-1",
        tools=tools,
    )
    assert result == "Base prompt"

    file_tools = [SimpleNamespace(name="Read")]
    result_no_conv = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id=None,
        tools=file_tools,
    )
    assert result_no_conv == "Base prompt"
