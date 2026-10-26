"""「额外扩展配置 JSON」收进折叠的「高级设置」。

它本质是开发者的逃生舱口：只有 folder_structure（虚拟文件夹）与
custom_questions（门户快捷提问）会被读取，其余键写了没人用；
而保存是整份覆盖，删掉 folder_structure 会让所有文档掉回「未分类」且无法撤销。
摆在元数据一级表单里，等于请业务用户手写 JSON 并承担这个风险。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def block(page) -> str:
    """折叠起来的这一整块。"""
    start = page.index("额外扩展配置：本质是开发者的逃生舱口")
    end = page.index('placeholder="{}"', start)
    return page[start:end]


def test_collapsed_by_default(page):
    assert "const advancedConfigOpen = ref(false)" in page


def test_collapses_again_each_time_the_modal_opens(page):
    """上次展开过不能带进下一次：每次打开都收起。"""
    open_edit = page.split("const openEdit =", 1)[1].split("const aiOverwritePending", 1)[0]
    assert "advancedConfigOpen.value = false" in open_edit


def test_header_is_a_real_toggle_button(block):
    assert 'type="button"' in block
    assert ':aria-expanded="advancedConfigOpen"' in block
    assert "advancedConfigOpen = !advancedConfigOpen" in block
    assert "收起" in block and "展开" in block


def test_warns_that_saving_overwrites_everything(block):
    assert "保存是整份覆盖" in block
    assert "未分类" in block
    assert "无法撤销" in block


def test_lists_the_only_two_keys_that_matter(block):
    assert "folder_structure" in block
    assert "custom_questions" in block
    # 明确告知其余键无效，避免"我填了为什么没生效"
    assert "其余键写了也不会生效" in block


def test_textarea_is_inside_the_collapsed_area(page, block):
    assert "form.extraConfigText" in block
    # 容器用 v-show 控制显隐，textarea 必须在容器内
    assert 'v-show="advancedConfigOpen"' in page


def test_no_bare_top_level_label_left(page):
    """原来那个一级表单里的裸 label 和「扩展配置 JSON」placeholder 都不该再有。"""
    assert "额外扩展配置 JSON</label>" not in page
    assert 'placeholder="扩展配置 JSON"' not in page


# ---------------------------------------------------------------- 详情页只读那块


def test_detail_panel_json_collapsed_by_default(page):
    """详情页底部那块深色 JSON 同理：默认收起，别把左右分栏的高度吃掉。"""
    assert "const detailConfigOpen = ref(false)" in page


def test_detail_panel_collapses_when_switching_dataset(page):
    switch = page.split("() => currentDatasetId()", 1)[1].split(")", 1)[0]
    assert "detailConfigOpen.value = false" in page
    # 换库重置必须发生在换库的 watch 里，而不是别处
    assert switch is not None


def test_detail_panel_header_is_a_toggle(page):
    block = page.split("业务元数据扩展配置 (JSON)", 1)[0].rsplit("<!--", 1)[-1]
    assert ':aria-expanded="detailConfigOpen"' in page
    assert "detailConfigOpen = !detailConfigOpen" in page


def test_copy_button_only_when_expanded(page):
    """收起时没有内容可复制，按钮跟着藏起来。"""
    assert 'v-if="detailConfigOpen"' in page


def test_detail_panel_no_longer_steals_height(page):
    """原来是 flex-1 min-h-[150px]，收起后仍占一大块空白。"""
    assert 'flex-1 flex flex-col min-h-[150px]' not in page
