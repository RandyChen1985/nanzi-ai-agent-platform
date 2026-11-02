"""知识库详情页头部布局：可见范围进徽标、时间进右上角、其余压成一行。

原先 7 张等宽卡片要占三行高度，把真正的内容（文档列表）挤到首屏之外；
改版后头部只保留「名称 + 徽标」「描述」「一行元信息」。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


def _page() -> str:
    assert PAGE.exists()
    return PAGE.read_text(encoding="utf-8")


def test_visibility_is_a_badge_next_to_the_title():
    page = _page()
    # 徽标渲染在 h2 之后的同一行容器里
    assert "visibilityBadgeClass(selectedDataset)" in page
    assert "visibilityLabel(selectedDataset)" in page
    # 三档配色与下拉图标色、权限页徽标统一
    assert "bg-blue-50 text-blue-600 border-blue-100" in page
    assert "bg-violet-50 text-violet-600 border-violet-100" in page
    assert "bg-amber-50 text-amber-700 border-amber-100" in page


def test_cards_grid_is_gone():
    page = _page()
    # 旧的等宽卡片与其标签都必须彻底移除，避免两套展示并存
    assert "RAG Parameters cards grid" not in page
    assert "切片方法 (Chunk Method)" not in page
    assert "可见范围 (Visibility)" not in page
    assert "最后同步/更新时间" not in page
    # 也不该再出现 5 列卡片网格
    assert "xl:grid-cols-5" not in page


def test_update_time_moves_to_the_top_right():
    page = _page()
    assert "更新时间" in page
    assert "formatTime(selectedDataset.update_time || selectedDataset.updated_at)" in page
    # 时间与刷新/编辑按钮同处右侧操作区
    assert "whitespace-nowrap" in page


def test_remaining_metadata_collapses_into_one_line():
    page = _page()
    # 元信息压成一行：份文档 · 文件夹 · 切片 · 更新时间 · 负责人
    for text in ("份文档", "个文件夹", "个切片", "更新于", "负责人"):
        assert text in page
    # 次要信息（ID、切片方法）不再挤在头部；表单里的归属部门下拉不算
    assert "切片方法" not in page
    assert "复制知识库 ID" not in page
    # 失联标记仍然跟着标题走
    assert "RAGFlow 侧已失联" in page


def test_team_scope_wording_says_read_only_everywhere():
    """可读 ≠ 只读：三处（tab / 下拉 / 徽标提示）必须都说只读。"""
    page = _page()
    assert "归属部门（含下级部门）成员自动只读" in page
    assert "成员自动可读" not in page
    # 徽标提示直接取下拉文案，不再自己拼一份
    assert "visibilityOptionOf(visibilityOf(dataset)).hint" in page


def test_library_header_has_breadcrumb():
    """库头上方要有「全部知识库 / 部门 / 库名」：没有它用户不知道自己在哪一层，也退不回去。"""
    page = PAGE.read_text(encoding="utf-8")
    assert 'aria-label="当前位置"' in page
    assert "openLibraryList('all')" in page
    assert "datasetOwnerLabel(selectedDataset)" in page
