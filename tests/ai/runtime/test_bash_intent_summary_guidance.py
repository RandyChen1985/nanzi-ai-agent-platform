"""Bash 意图摘要（description）的平台侧强制要求。

背景：AgentScope 内置 Bash 与沙箱 MCP 的 schema 都只把 ``description`` 当可选参数，
模型在简单命令上经常省略，执行时间线于是只剩一句「工具完成 · Bash」。这里改为在
工具描述层写入要求（方案 1：只改描述，不动 schema 的 required），并由工作区绑定
统一收口，保证 lazy / 沙箱 / local 三条路径口径一致。
"""

from __future__ import annotations

import ast
from unittest.mock import AsyncMock

import pytest

from app.services.ai.runtime.agentscope.stream_reconcile import extract_tool_summary
from app.services.ai.runtime.agentscope.workspace import (
    BASH_INTENT_SUMMARY_GUIDANCE,
    BASH_INTENT_SUMMARY_MARKER,
    DEFAULT_BASH_INPUT_SCHEMA,
    _augment_bash_parameter_schema,
    _augment_bash_tool_description,
    bind_configured_tools_to_workspace,
    clear_workspace_cache,
    get_local_workspace,
)

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _local_policy_without_redis(monkeypatch):
    """避免 workspace 构造依赖 Redis 读取 sandbox_policy。"""

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value="local"),
    )


def test_augment_bash_tool_description_appends_requirement_once():
    base = "Execute a bash command in the sandbox environment."

    augmented = _augment_bash_tool_description(base)

    assert augmented.startswith(base)
    assert BASH_INTENT_SUMMARY_MARKER in augmented
    # 幂等：重复绑定不能叠加第二份要求
    assert _augment_bash_tool_description(augmented) == augmented


def test_augment_bash_tool_description_handles_empty_base():
    assert _augment_bash_tool_description("") == BASH_INTENT_SUMMARY_GUIDANCE


def test_augment_bash_parameter_schema_rewrites_copy_only():
    schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command"},
            "description": {
                "type": "string",
                "description": "Clear, concise description (5-10 words)",
            },
        },
        "required": ["command"],
    }

    augmented = _augment_bash_parameter_schema(schema)

    assert augmented is not schema
    assert BASH_INTENT_SUMMARY_MARKER in augmented["properties"]["description"]["description"]
    # 只改文案：required 不变，避免把可选参数变成强校验字段
    assert augmented["required"] == ["command"]
    # 不能就地修改原 schema：MCP 的 schema 可能被多处共享
    assert schema["properties"]["description"]["description"] == (
        "Clear, concise description (5-10 words)"
    )


def test_augment_bash_parameter_schema_is_noop_without_description_property():
    schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }

    assert _augment_bash_parameter_schema(schema) is schema
    assert _augment_bash_parameter_schema(None) is None


def test_default_bash_input_schema_exposes_description_parameter():
    assert "description" in DEFAULT_BASH_INPUT_SCHEMA["properties"]
    assert DEFAULT_BASH_INPUT_SCHEMA["required"] == ["command"]


@pytest.mark.asyncio
async def test_bind_adds_bash_intent_summary_requirement(tmp_path, monkeypatch):
    """绑定出口对 Bash 生效，且不误伤其它工具。"""

    from app.services.ai.tools.registry import ToolRegistry

    clear_workspace_cache()

    async def _root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )

    workspace = await get_local_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="conv-bash-intent-summary",
    )
    specs = await ToolRegistry.get_runtime_tools(
        ["exec_command", "search_knowledge_base"]
    )

    bound = await bind_configured_tools_to_workspace(workspace, specs)

    bound_bash = next(spec for spec in bound if spec.name == "Bash")
    assert BASH_INTENT_SUMMARY_MARKER in bound_bash.description
    assert bound_bash.parameters_schema["required"] == ["command"]
    description_property = bound_bash.parameters_schema["properties"]["description"]
    assert BASH_INTENT_SUMMARY_MARKER in str(
        description_property.get("description") or ""
    )

    for spec in bound:
        if spec.name == "Bash":
            continue
        assert BASH_INTENT_SUMMARY_MARKER not in (spec.description or "")

    clear_workspace_cache()


def _inline_bash_function(source: str) -> "ast.FunctionDef":
    """从内联 MCP 脚本里取出 ``bash`` 的函数定义节点。"""

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "bash":
            return node
    raise AssertionError("inline server does not define bash tool")


@pytest.mark.parametrize("backend", ["container", "ssh"])
def test_inline_bash_declares_optional_description(backend):
    """沙箱 MCP 的 bash 要声明 description，且保持可选（默认空串）。

    声明了才可能出现该字段——模型不会主动传一个 schema 里没有的参数；
    必须带默认值，否则 FastMCP/pydantic 会对漏填直接报校验错，变成硬失败。
    """

    from app.services.ai.runtime.agentscope import workspace_container_mcp
    from app.services.ai.runtime.agentscope import workspace_ssh

    source = (
        workspace_container_mcp._INLINE_SERVER
        if backend == "container"
        else workspace_ssh._SSH_INLINE_SERVER
    )

    node = _inline_bash_function(source)
    argument_names = [argument.arg for argument in node.args.args]
    assert argument_names[:2] == ["command", "cwd"]
    assert argument_names[2:] == ["description"]
    assert [ast.literal_eval(default) for default in node.args.defaults] == [".", ""]


def test_augment_fills_mcp_parameter_description_without_docstring():
    """MCP 只给出 properties.description 空壳时，平台侧要补上说明。

    实测 FastMCP 生成 schema 时不会把 docstring 的 Args 文案带进参数说明，
    因此仅靠内联脚本的 docstring 不够，模型最终看到的话术由平台绑定层补。
    """

    schema = {
        "properties": {
            "command": {"title": "Command", "type": "string"},
            "cwd": {"default": ".", "title": "Cwd", "type": "string"},
            "description": {"default": "", "title": "Description", "type": "string"},
        },
        "required": ["command"],
        "type": "object",
        "title": "bashArguments",
    }

    augmented = _augment_bash_parameter_schema(schema)

    parameter_description = augmented["properties"]["description"]["description"]
    assert "REQUIRED on every call" in parameter_description
    assert BASH_INTENT_SUMMARY_MARKER in parameter_description
    assert augmented["required"] == ["command"]


def test_extract_tool_summary_matches_mcp_prefixed_tool_name():
    """沙箱 MCP 的工具名是 ``sandbox::bash``，意图摘要必须命中。

    回归：早期只按裸名 ``bash`` / ``Bash`` 精确匹配，沙箱场景恒为空，
    时间线只剩「工具完成 · sandbox::bash」。
    """

    args = {"command": "ls -la", "description": "检查工作区文件"}

    assert extract_tool_summary(args, tool_name="sandbox::bash") == "检查工作区文件"
    assert extract_tool_summary(args, tool_name="sandbox::Bash") == "检查工作区文件"
    assert extract_tool_summary(args, tool_name="bash") == "检查工作区文件"
    assert extract_tool_summary(args, tool_name="Bash") == "检查工作区文件"


def test_extract_tool_summary_ignores_unrelated_tool_names():
    """非命令类工具不追加意图摘要，避免给所有工具都挂一行说明。"""

    args = {"command": "ls", "description": "检查工作区文件"}

    assert extract_tool_summary(args, tool_name="sandbox::read_file") == ""
    assert extract_tool_summary(args, tool_name="") == ""


def test_native_name_resolves_mcp_prefixed_bash():
    """``sandbox::bash`` 必须解析成 ``Bash``，否则绑定层的摘要文案不会生效。"""

    from app.services.ai.runtime.agentscope.workspace import (
        _workspace_native_name_for_spec,
    )

    class _Spec:
        name = "sandbox::bash"
        native_tool = None

    assert _workspace_native_name_for_spec(_Spec()) == "Bash"

    class _SshSpec:
        name = "sandbox::Bash"
        native_tool = None

    assert _workspace_native_name_for_spec(_SshSpec()) == "Bash"
