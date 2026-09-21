"""Tests for ChatBI result Markdown export helpers."""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_chatbi_result_markdown_export_contract():
    util = _source("frontend/src/utils/chatbiResultExport.ts")
    panel = _source("frontend/src/components/chatbi/ChatBIInsightPanel.vue")

    assert "export function buildChatBIResultMarkdown" in util
    assert "export function exportChatBIResultMarkdown" in util
    assert "downloadMarkdownFile" in util
    assert "ChatBI 查询结果明细" in util
    # 面板明细页的「导出完整明细」下拉新增 Markdown 格式选项，改走直链导出通道
    #（见 37286d11）；本地 build/export 工具仍保留供复用。
    assert "导出完整明细" in panel
    assert "exportFormatOptions" in panel
    assert "pickExportFormat" in panel
    assert '{ value: "md", label: "Markdown (.md)"' in panel
