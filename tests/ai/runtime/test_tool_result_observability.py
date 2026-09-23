from types import SimpleNamespace

import pytest

from app.services.ai.runtime.agentscope.tool_result import (
    extract_tool_result_error_reason,
    is_tool_result_error,
)
from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_display
from app.services.ai.runners.assistant_agent_runner import (
    AssistantAgentRunner,
    _extract_agentscope_tool_call_input,
    _resolve_agentscope_tool_args,
)


pytestmark = pytest.mark.no_infrastructure


def _runner_for_observation() -> AssistantAgentRunner:
    runner = object.__new__(AssistantAgentRunner)
    runner.config = SimpleNamespace(
        agent_name="Assistant",
        model_name="test-model",
        temperature=0.0,
    )
    runner.step_counter = 1
    return runner


def test_agentscope_tool_args_can_be_recovered_from_saved_tool_call():
    from agentscope.message import AssistantMsg, ToolCallBlock

    agent = SimpleNamespace(
        state=SimpleNamespace(
            context=[
                AssistantMsg(
                    name="Assistant",
                    content=[
                        ToolCallBlock(
                            id="read-1",
                            name="Read",
                            input='{"file_path": "/workspace/public/docs/FAQ.md"}',
                        )
                    ],
                )
            ]
        )
    )

    assert _extract_agentscope_tool_call_input(agent, "read-1") == (
        '{"file_path": "/workspace/public/docs/FAQ.md"}'
    )
    assert _resolve_agentscope_tool_args(agent, "read-1", "") == {
        "file_path": "/workspace/public/docs/FAQ.md"
    }


def test_agentscope_tool_args_prefer_saved_call_when_stream_preview_is_invalid():
    from agentscope.message import AssistantMsg, ToolCallBlock

    agent = SimpleNamespace(
        state=SimpleNamespace(
            context=[
                AssistantMsg(
                    name="Assistant",
                    content=[
                        ToolCallBlock(
                            id="glob-1",
                            name="Glob",
                            input='{"path": "/workspace/public/docs", "pattern": "*.md"}',
                        )
                    ],
                )
            ]
        )
    )

    assert _resolve_agentscope_tool_args(agent, "glob-1", "not-json") == {
        "path": "/workspace/public/docs",
        "pattern": "*.md",
    }


def test_successful_bash_output_containing_error_is_not_a_failure():
    assert is_tool_result_error(
        "Bash",
        "grep found the word Error in the file",
        result_state="success",
    ) is False

    result = _runner_for_observation()._build_tool_observation(
        tool_id="bash-1",
        tool_name="Bash",
        tool_args={"command": "grep Error app.log"},
        tool_output="grep found the word Error in the file",
        duration_tool=12,
        target_tool=None,
        tool_index=0,
        tool_result_state="success",
    )

    assert result["log"]["status"] == "success"
    assert "error_reason" not in result["log"]

    assert is_tool_result_error("bash", "Command failed: exit 1") is True


def test_tool_observation_advances_trace_step_number():
    runner = _runner_for_observation()

    result = runner._build_tool_observation(
        tool_id="bash-step",
        tool_name="Bash",
        tool_args={"command": "uptime"},
        tool_output="up 1 day",
        duration_tool=12,
        target_tool=None,
        tool_index=0,
        tool_result_state="success",
    )

    assert result["trace"].step_number == 2
    assert runner.step_counter == 2


def test_failed_bash_exposes_a_sanitized_concrete_reason():
    output = "Command failed: cat /Users/demo/secret.txt\n\nStderr:\nPermission denied"

    assert is_tool_result_error("Bash", output, result_state="error") is True
    assert extract_tool_result_error_reason(
        "Bash",
        output,
        result_state="error",
    ) == "Permission denied"

    result = _runner_for_observation()._build_tool_observation(
        tool_id="bash-2",
        tool_name="Bash",
        tool_args={"command": "cat /Users/demo/secret.txt"},
        tool_output=output,
        duration_tool=12,
        target_tool=None,
        tool_index=0,
        tool_result_state="error",
    )

    assert result["log"]["status"] == "error"
    assert result["log"]["error_reason"] == "Permission denied"
    assert "/Users/demo" not in result["log"]["error_reason"]


def test_tool_log_truncation_is_labeled_as_display_preview():
    preview = truncate_for_display("x" * 20, max_len=10)

    assert preview.endswith("… [日志预览已截断]")


def test_failed_bash_without_stderr_does_not_echo_the_command():
    output = "Command failed: curl -H 'Authorization: Bearer secret-token' https://internal.example"

    assert extract_tool_result_error_reason(
        "Bash",
        output,
        result_state="error",
    ) == "命令执行失败（退出码非 0）"


@pytest.mark.parametrize(
    ("tool_name", "tool_args", "expected"),
    [
        (
            "Read",
            {"file_path": "/workspace/docs/report.md", "offset": 20, "limit": 60},
            {"operation": "read", "path": "/workspace/docs/report.md", "range": {"start": 20, "limit": 60}},
        ),
        (
            "Write",
            {"file_path": "/workspace/docs/report.md", "content": "hello"},
            {"operation": "write", "path": "/workspace/docs/report.md"},
        ),
        (
            "Grep",
            {"path": "/workspace", "glob": "*.md", "pattern": "HOST_DATA_DIR"},
            {"operation": "search", "path": "/workspace", "pattern": "HOST_DATA_DIR", "glob": "*.md"},
        ),
        (
            "Read",
            {"file_path": "/app/data/docs/FAQ.md"},
            {"operation": "read", "path": "/workspace/public/docs/FAQ.md"},
        ),
        (
            "Glob",
            {"path": "/app/data/docs", "pattern": "*.md"},
            {"operation": "search", "path": "/workspace/public/docs", "pattern": "*.md"},
        ),
        (
            "Read",
            {"file_path": "/app/data/agent_workspaces/admin__1/sessions/a/context.json"},
            {"operation": "read", "path": "/workspace/sessions/a/context.json"},
        ),
    ],
)
def test_file_tools_expose_safe_file_metadata(tool_name, tool_args, expected):
    result = _runner_for_observation()._build_tool_observation(
        tool_id=f"{tool_name.lower()}-metadata",
        tool_name=tool_name,
        tool_args=tool_args,
        tool_output="ok",
        duration_tool=12,
        target_tool=None,
        tool_index=0,
        tool_result_state="success",
    )

    metadata = result["log"]["file_metadata"]
    for key, value in expected.items():
        assert metadata[key] == value
    assert "content" not in metadata


def test_file_tool_metadata_does_not_expose_unknown_host_absolute_path():
    result = _runner_for_observation()._build_tool_observation(
        tool_id="read-host-path",
        tool_name="Read",
        tool_args={"file_path": "/Users/example/private/context.json"},
        tool_output="ok",
        duration_tool=12,
        target_tool=None,
        tool_index=0,
        tool_result_state="success",
    )

    assert "file_metadata" not in result["log"]


@pytest.mark.parametrize(
    ("tool_name", "tool_args", "tool_output", "expected"),
    [
        (
            "word_document_read",
            {"action": "read_content", "path": "/app/data/uploads/report.docx", "start": 20, "limit": 10},
            {"status": "ok", "summary": "已读取 10 个段落", "data": {"start": 20}},
            {"document_type": "word", "path": "report.docx", "paragraph_range": {"start": 20, "limit": 10}},
        ),
        (
            "excel_document_read",
            {"action": "read_range", "path": "/app/data/uploads/sales.xlsx", "sheet_name": "八月", "cell_range": "A1:H30"},
            {"status": "ok", "summary": "已读取 八月!A1:H30"},
            {"document_type": "excel", "path": "sales.xlsx", "sheet_name": "八月", "cell_range": "A1:H30"},
        ),
        (
            "excel_document_write",
            {"action": "write_cells", "output_filename": "sales_updated.xlsx", "path": "/app/data/uploads/sales.xlsx", "sheet_name": "八月", "cells": [{"address": "A1", "value": 1}]},
            {"status": "ok", "changes": {"written_cells": 1}, "artifact": {"filename": "sales_updated.xlsx", "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "size": 2048}},
            {"document_type": "excel", "path": "sales_updated.xlsx", "sheet_name": "八月", "changes": {"written_cells": 1}, "size_bytes": 2048},
        ),
    ],
)
def test_word_and_excel_tools_expose_document_metadata(tool_name, tool_args, tool_output, expected):
    result = _runner_for_observation()._build_tool_observation(
        tool_id=f"{tool_name}-metadata",
        tool_name=tool_name,
        tool_args=tool_args,
        tool_output=tool_output,
        duration_tool=12,
        target_tool=None,
        tool_index=0,
        tool_result_state="success",
    )

    metadata = result["log"]["file_metadata"]
    for key, value in expected.items():
        assert metadata[key] == value
    assert "/app/data" not in str(metadata)


def test_format_tool_args_for_display_renders_bash_command_verbatim():
    from app.services.ai.runtime.agentscope.stream_reconcile import (
        format_tool_args_for_display,
    )

    assert format_tool_args_for_display(
        {"command": "git status --short"}, tool_name="Bash"
    ) == "git status --short"
    assert format_tool_args_for_display(
        {"command": ["git", "status"]}, tool_name="Bash"
    ) == "git status"
    assert format_tool_args_for_display(
        {"command": "ls -la"}, tool_name="bash"
    ) == "ls -la"
    assert format_tool_args_for_display(
        {"command": "ls"}, tool_name="exec_command"
    ) == "ls"


def test_format_tool_args_for_display_falls_back_to_json_and_truncates():
    from app.services.ai.runtime.agentscope.stream_reconcile import (
        format_tool_args_for_display,
    )

    assert format_tool_args_for_display(
        {"query": "营收"}, tool_name="search_knowledge_base"
    ) == '{\n  "query": "营收"\n}'
    # JSON 文本必须先还原成对象，才能和 dict 入参给出同样格式化的结果；
    # 原断言要求原样返回，等于固化了「进行中的卡片显示整串 JSON」这个缺陷。
    assert format_tool_args_for_display(
        '{"query": "营收"}', tool_name="search_knowledge_base"
    ) == '{\n  "query": "营收"\n}'

    assert format_tool_args_for_display(None, tool_name="Bash") == ""
    assert format_tool_args_for_display({}, tool_name="Bash") == ""

    long_command = "echo " + "x" * 100
    truncated = format_tool_args_for_display(
        {"command": long_command}, tool_name="Bash", max_len=20
    )
    assert truncated.startswith("echo ")
    assert len(truncated) < len(long_command)
    assert "截断" in truncated


def test_tool_observation_exposes_executed_command_for_timeline_card():
    result = _runner_for_observation()._build_tool_observation(
        tool_id="bash-cmd",
        tool_name="Bash",
        tool_args={"command": "npm run build"},
        tool_output="build ok",
        duration_tool=12,
        target_tool=None,
        tool_index=0,
        tool_result_state="success",
    )

    # 命令进独立字段，不挤占 details 的工具输出截断预算
    assert result["log"]["tool_args"] == "npm run build"
    assert result["log"]["details"] == "build ok"


def test_format_tool_args_for_display_decodes_json_string_arguments():
    """TOOL_CALL_START 的 arguments 直取事件对象，收不到 dict 时会拿到 JSON 文本。

    不还原就只能把整串 `{"command": "git status"}` 当成命令显示给用户。
    """
    from app.services.ai.runtime.agentscope.stream_reconcile import (
        format_tool_args_for_display,
    )

    assert (
        format_tool_args_for_display('{"command": "git status"}', tool_name="Bash")
        == "git status"
    )
    assert (
        format_tool_args_for_display('  {"command": "git status"}  ', tool_name="Bash")
        == "git status"
    )


def test_format_tool_args_for_display_keeps_non_json_string_intact():
    """纯命令文本或残缺 JSON 不能被反序列化改写。"""
    from app.services.ai.runtime.agentscope.stream_reconcile import (
        format_tool_args_for_display,
    )

    assert format_tool_args_for_display("npm run build", tool_name="Bash") == "npm run build"
    assert format_tool_args_for_display("{not json", tool_name="Bash") == "{not json"
