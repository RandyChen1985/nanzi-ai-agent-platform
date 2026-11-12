"""元数据数据集「业务标签」文案与说明气泡契约。

需求来源（用户）：新建 / 编辑数据集的表单里那个「标签」，看不出它是干什么用的，
要求改叫「业务标签」并补一个 `?` 说明；同时确认「这个标签是不是参与 AI 分析、
会不会影响后续 ChatBI」。

读代码得到的真实口径（后端 `app/services/ai/config.py::_format_dataset_menu` 把标签
拼成 `- Dataset: <name> [标签]` 注入 ChatBI 系统提示与数据门户导航；而
`dataset_scope_service` 全程不读该字段），因此说明文案必须同时讲清「会影响 AI」与
「不影响权限判定」——只写前者会让人以为改了标签就能放开数据，只写后者又等于没说
它真正的用途。这里把三件事一起锁住，避免以后被简化成一句「用于归类检索」。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VIEW = "frontend/src/views/MetadataDatasets.vue"
COPY_WIZARD = "frontend/src/components/metadata/CopyDatasetWizard.vue"


@pytest.fixture(scope="module")
def source() -> str:
    return (ROOT / VIEW).read_text(encoding="utf-8")


def test_both_dataset_forms_use_info_tooltip_for_business_tag(source: str) -> None:
    """新建与编辑弹窗各一处，且都指向同一份说明文案（两处各写一份必然漂移）。"""
    assert "import InfoTooltip from '../components/InfoTooltip.vue'" in source
    assert source.count('align="left" :text="businessTagHint"') == 2
    assert source.count("<span>业务标签</span>") == 2


def test_hint_bubble_opens_to_the_right(source: str) -> None:
    """气泡必须向右展开（``align="left"``）。

    InfoTooltip 默认 ``align="right"``，气泡以按钮右缘为准向左伸出 256px。这里的
    触发按钮离弹窗左边界只有几十像素，而弹窗本体带 ``overflow-hidden``，实测表现是
    气泡左半边整片被裁掉、文字只剩右半截。改成向右展开才是安全方向：按钮右缘到弹窗
    右边界还有 300px 以上余量。
    """
    assert source.count(':text="businessTagHint"') == 2
    assert source.count('class="ml-1 text-gray-400" :text="businessTagHint"') == 0, (
        "未指定 align 时会回落 right 对齐并再次被弹窗裁切"
    )


def test_old_bare_tag_label_is_gone(source: str) -> None:
    """旧的「标签」文案不得残留：留着就说明有个表单还叫旧名。"""
    assert "mb-1\">标签</label>" not in source


def test_hint_text_states_ai_usage_permission_independence_and_search(source: str) -> None:
    """说明文案的口径必须齐三层，缺一层就会误导。"""
    assert source.count("const businessTagHint") == 1
    assert "ChatBI 选库" in source
    assert "不参与数据权限判定" in source
    assert "按业务标签搜索" in source


def test_copy_wizard_uses_the_same_business_tag_wording() -> None:
    """复制新建向导里同一个字段必须同名，否则会被当成两种东西。

    但**指标**自己的标签（``metricBuf.tags``，「标签（逗号分隔）」）不受影响——那是
    另一个对象的字段，跟着改反而错。
    """
    source = (ROOT / COPY_WIZARD).read_text(encoding="utf-8")

    assert '<label class="mb-1 block text-xs font-semibold text-gray-600">业务标签</label>' in source
    assert "暂无业务标签" in source
    assert 'placeholder="输入业务标签后回车"' in source
    assert "复制业务标签" in source
    assert "保留源数据集的业务标签" in source
    assert '<span class="text-gray-400">业务标签</span>' in source

    assert "标签（逗号分隔）" in source, "指标自己的标签文案不该被一起改掉"
    assert "暂无标签" not in source
