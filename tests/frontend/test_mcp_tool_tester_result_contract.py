from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

SOURCE = Path("frontend/src/components/system/McpToolTester.vue").read_text()


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
