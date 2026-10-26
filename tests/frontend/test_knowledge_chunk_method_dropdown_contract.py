"""解析算法下拉：说明文案必须内联在选项里，不再靠下拉外的一条蓝色提示。

改造前选完算法还要低头看下面的提示框才知道它是什么意思；现在每项双行
（名称 + 算法码 / 说明），选项本身就是说明书。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"

# RAGFlow 支持的解析算法，顺序与原下拉一致
EXPECTED_METHODS = [
    "naive",
    "qa",
    "table",
    "laws",
    "paper",
    "resume",
    "book",
    "manual",
    "one",
]


def _page() -> str:
    assert PAGE.exists()
    return PAGE.read_text(encoding="utf-8")


def test_chunk_methods_are_a_data_structure_with_descriptions():
    page = _page()
    assert "CHUNK_METHOD_OPTIONS" in page
    for method in EXPECTED_METHODS:
        assert f"value: '{method}'" in page, f"缺少解析算法 {method}"
    # 每档都要有说明，且不能只剩一个名字
    assert page.count("desc:") >= len(EXPECTED_METHODS)
    # 算法码单独展示（Naive / QA / Table …），与名称分开
    assert page.count("code:") >= len(EXPECTED_METHODS)


def test_chunk_method_dropdown_renders_two_lines():
    page = _page()
    assert "<div data-chunk-select" in page
    # 收起态显示「名称 + 算法码 + 说明」
    assert "chunkMethodOptionOf(form.chunk_method).desc" in page
    assert "chunkMethodOptionOf(form.chunk_method).code" in page
    # 展开项显示说明
    assert "{{ option.desc }}" in page
    # 下拉要能滚动：9 项 × 三行会超出弹窗
    assert "max-h-72 overflow-y-auto" in page


def test_legacy_blue_hint_box_is_gone():
    page = _page()
    # 旧的 computed 与蓝色提示框都必须移除，否则会出现"两处说明"的重复信息
    assert "chunkMethodDescription" not in page
    assert "动态解析指引说明" not in page


def test_unknown_chunk_method_falls_back_to_naive():
    page = _page()
    assert "FALLBACK_CHUNK_OPTION" in page
    assert "chunkMethodOptionOf" in page
