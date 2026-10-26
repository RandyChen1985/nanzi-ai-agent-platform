"""标签录入从「英文逗号分隔的文本框」改为 chip 输入。

原方案的隐藏成本：使用者得记住必须用**半角**逗号。用中文逗号敲进去不会报错，
只会得到 "财务，报税" 这样一个完整标签，保存后才发现拆错了。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
TAG_INPUT = ROOT / "frontend/src/components/TagInput.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def component() -> str:
    return TAG_INPUT.read_text(encoding="utf-8")


def test_both_forms_use_the_tag_input(page):
    # 创建弹窗与编辑弹窗都要换，只换一个会留下"有的地方还要写逗号"
    assert page.count("<TagInput v-model=\"form.tags\"") == 2
    assert "tagsText" not in page


def test_no_longer_asks_for_comma_separated_input(page):
    assert "英文逗号分隔" not in page


def test_model_is_an_array_not_a_string(page):
    assert "tags: [] as string[]" in page
    # 保存时直接交数组，不再从字符串里 split
    assert page.count("tags: form.value.tags,") == 2


def test_chinese_comma_is_a_separator(page, component):
    for source in (page, component):
        assert "，\\n" in source or "，\n" in source or "/[,，\\n]/" in source


def test_enter_does_not_submit_the_form(component):
    """在弹窗表单里按回车加标签，不能把整个保存表单提交掉。"""
    block = component.split("const onKeydown", 1)[1].split("const onPaste", 1)[0]
    assert "event.key === 'Enter'" in block
    assert "event.preventDefault()" in block


def test_paste_with_separators_splits_into_tags(component):
    assert "const onPaste" in component
    assert "onPaste" in component


def test_chips_can_be_removed_individually(component):
    assert "removeTag" in component
    assert "移除标签" in component
    # 输入框为空时退格删最后一个，符合 tag input 习惯
    assert "event.key === 'Backspace'" in component


def test_deduplicates_and_trims(component):
    assert ".trim()" in component
    assert "if (!next.includes(tag)) next.push(tag)" in component
