import pytest

from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.ai.runtime.agentscope.workspace import (
    build_workspace_toolkit,
    is_workspace_managed_tool_spec,
)


pytestmark = pytest.mark.no_infrastructure


async def _noop_tool(**kwargs):
    return kwargs


@pytest.mark.asyncio
async def test_build_workspace_toolkit_uses_workspace_builtins_and_keeps_platform_tools(
    tmp_path,
    monkeypatch,
):
    from app.services.ai.runtime.agentscope.workspace import get_local_workspace

    async def _root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.discover_platform_skill_paths",
        lambda: [],
    )

    workspace = await get_local_workspace(user_id="u1", conversation_id="c1")
    assert workspace is not None

    builtin_spec = RuntimeToolSpec(
        name="Bash",
        description="bash",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=_noop_tool,
        permission_scope="ask",
    )
    platform_spec = RuntimeToolSpec(
        name="search_knowledge_base",
        description="kb",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=_noop_tool,
        permission_scope="read",
    )
    skill_spec = RuntimeToolSpec(
        name="list_available_skills",
        description="skills",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=_noop_tool,
        permission_scope="read",
    )

    toolkit = await build_workspace_toolkit(
        workspace,
        [builtin_spec, platform_spec, skill_spec],
    )

    schemas = await toolkit.get_tool_schemas()
    tool_names = [item["function"]["name"] for item in schemas]
    assert "Bash" in tool_names
    assert "Read" in tool_names
    assert "search_knowledge_base" in tool_names
    assert "list_available_skills" not in tool_names
    assert tool_names.count("Bash") == 1


def test_is_workspace_managed_tool_spec_matches_aliases():
    spec = RuntimeToolSpec(
        name="exec_command",
        description="bash alias",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=_noop_tool,
        permission_scope="ask",
    )
    assert is_workspace_managed_tool_spec(spec) is True
