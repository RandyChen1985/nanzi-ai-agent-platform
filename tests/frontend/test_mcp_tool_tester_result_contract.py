from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "frontend/src/components/system/McpToolTester.vue").read_text(encoding="utf-8")


@pytest.mark.parametrize("form_name", ["McpCredentialProfileForm.vue", "McpModelResultFieldsForm.vue"])
def test_mcp_result_forms_show_root_array_and_descendant_queries(form_name):
    source = (ROOT / "frontend/src/components/mcp" / form_name).read_text(encoding="utf-8")
    for query in ("$", "$.messages[*]", "$.messages[*][1]", "$..messages[*]"):
        assert f"<code>{query}</code>" in source
    assert "解包后的" in source
    assert "只显示执行状态" in source
    assert "按 JSONPath 选中的内容原样返回" in source
    owner = source if form_name == "McpCredentialProfileForm.vue" else (
        ROOT / "frontend/src/components/system/McpServerRegistry.vue"
    ).read_text(encoding="utf-8")
    assert "splitMcpResultPaths(" in owner


def test_mcp_result_queries_preserve_commas_and_multiline_whitespace():
    result = subprocess.run(
        ["node", "tests/frontend/mcp_result_field_input.mjs"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_mcp_tool_tester_explains_query_failures():
    assert "query_limit_exceeded:" in SOURCE
    assert "fields_invalid:" in SOURCE
    assert "no_matching_fields:" in SOURCE
    assert "只显示执行状态" in SOURCE


def test_mcp_tool_tester_restricts_result_before_render_and_copy():
    assert "modelResult.value?.visibility === 'restricted'" in SOURCE
    assert "? { status: 'success', result_visibility: 'restricted' }" in SOURCE


def test_mcp_tool_tester_pretty_prints_json_and_renders_markdown():
    assert "tryPrettyJson" in SOURCE
    assert "JSON.stringify" in SOURCE
    assert "looksLikeMarkdown" in SOURCE
    assert "renderSafeMarkdownPreview" in SOURCE
    assert "hljs.highlight" in SOURCE


def test_mcp_tool_tester_has_hover_copy_affordance():
    assert "copyToClipboard" in SOURCE
    assert "group/result" in SOURCE
    assert "handleCopyResult" in SOURCE
    assert "DocumentDuplicateIcon" in SOURCE
    assert "已复制到剪贴板" in SOURCE


def test_mcp_tool_tester_exposes_request_and_response_details_tab():
    assert "参数输入" in SOURCE
    assert "调用详情" in SOURCE
    assert "activeTab" in SOURCE
    assert "requestPayload" in SOURCE
    assert "requestCopied" in SOURCE
    assert "formattedRequest" in SOURCE
    assert "handleCopyRequest" in SOURCE
    assert "activeTab.value = 'details'" in SOURCE
    assert "运行测试后查看本次调用详情" in SOURCE
    assert "watch([() => props.tool, () => props.isOpen]" in SOURCE


def test_mcp_tool_tester_normalizes_nullable_scalar_types_for_inputs():
    assert "getScalarType" in SOURCE
    assert "getScalarType(prop) === 'string'" in SOURCE
    assert "getScalarType(prop) === 'integer'" in SOURCE
    assert "getScalarType(prop) === 'boolean'" in SOURCE
