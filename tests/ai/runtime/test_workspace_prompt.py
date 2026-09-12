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


def test_workspace_prompt_mentions_common_container_commands():
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/tmp/workspaces/u1/sessions/conv-1",
        docs_dir="/tmp/workspaces/u1/docs",
        file_tool_names=["Bash"],
    )

    assert "容器常见基础命令" in prompt
    assert "`netstat`" in prompt
    assert "`ping`" in prompt
    assert "`dig`" in prompt
    assert "`npm`" in prompt
    assert "`command -v <cmd>`" in prompt
    assert "不要凭记忆断言未安装" in prompt


def test_workspace_prompt_distinguishes_tool_paths_from_user_delivery_paths():
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/tmp/workspaces/u1/sessions/conv-1",
        docs_dir="/tmp/workspaces/u1/docs",
        file_tool_names=["Read", "Write"],
    )

    assert "工具调用路径" in prompt
    assert "最终展示给用户" in prompt


def test_workspace_prompt_enforces_host_file_tools_and_docs_rules():
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/tmp/workspaces/u1/sessions/conv-1",
        docs_dir="/tmp/workspaces/u1/docs",
        file_tool_names=["Read", "Write", "Bash", "Grep"],
    )

    assert "文件读写与搜索工具优先原则" in prompt
    assert "文件读写与搜索一律走宿主侧文件工具（Read/Write/Edit/Glob/Grep），不要用 Bash 访问文件" in prompt
    assert "平台公共文档（如 `data/docs/` 手册、FAQ.md）仅宿主侧可读，沙箱 Bash 不可见；请用 Read/Glob/Grep 直接读取" in prompt
    assert "沙箱内 Bash 文件操作边界" in prompt
    assert "只有在触发沙箱内的运行环境操作" in prompt



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
    assert "/tmp/workspaces/u1/sessions/conv-1" in result or "/tmp/workspaces/u1/sessions/conv_1" in result
    assert "/tmp/workspaces/u1/docs" in result
    assert "/uploads/" in result
    assert "/sandbox/" in result
    assert "Grep" in result


@pytest.mark.asyncio
async def test_append_docker_workspace_prompt_uses_real_user_workspace_paths(monkeypatch):
    async def _root():
        return "/tmp/workspaces"

    async def _config(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        _config,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._resolve_docker_public_docs_source",
        lambda: None,
    )

    result = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id="conv-1",
        tools=[SimpleNamespace(name="Read")],
    )

    assert "/tmp/workspaces/u1/sessions/conv-1" in result
    assert "/tmp/workspaces/u1/docs" in result
    assert "Bash 使用沙箱路径" in result
    assert "Read/Write/Edit/Glob/Grep 使用文件工具路径" in result
    assert "不要将 `/tmp` 等容器专属路径交给 Read" in result
    assert "Bash 的相对路径默认相对会话目录" in result
    assert "文件工具的相对路径默认相对用户工作区根目录" in result
    assert "公共 docs 未挂载" in result


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
