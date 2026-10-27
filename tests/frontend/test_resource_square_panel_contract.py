"""广场面板：两段独立加载、统计合并、缓存降级提示、section 定位。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "frontend/src/components/resource-square/ResourceSquarePanel.vue"


def _panel() -> str:
    assert PANEL.exists(), f"文件不存在：{PANEL}"
    return PANEL.read_text(encoding="utf-8")


def test_panel_renders_two_sections_and_summary():
    panel = _panel()
    assert "知识库" in panel and "数据集" in panel
    assert "可申请" in panel and "审批中" in panel and "已有权限" in panel


def test_sections_load_independently():
    panel = _panel()
    assert "kbError" in panel and "dsError" in panel
    assert "fetchSquareKnowledge" in panel and "fetchSquareDatasets" in panel
    # 两段各自有重试入口，一段失败不该让另一段也没法刷新
    assert 'loadKnowledge()' in panel and 'loadDatasets()' in panel


def test_summary_merges_both_sections():
    panel = _panel()
    # 只取其中一个接口的 summary 会让顶部统计少一半
    assert "kbSummary" in panel and "dsSummary" in panel
    assert "apply: kbSummary.value.apply + dsSummary.value.apply" in panel


def test_panel_warns_when_knowledge_metrics_are_stale():
    panel = _panel()
    assert "kbStale" in panel
    assert "知识库规模与更新时间暂时读不到" in panel


def test_panel_respects_datasets_switch():
    panel = _panel()
    assert "datasetsEnabled" in panel
    assert "管理员暂未开放数据集目录" in panel


def test_panel_supports_section_query():
    panel = _panel()
    assert "route.query.section" in panel
    assert "scrollIntoView" in panel
