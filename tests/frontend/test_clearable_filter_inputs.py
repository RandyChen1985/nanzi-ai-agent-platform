import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_all_search_filter_inputs_use_clearable_search_type():
    """所有以「搜索」为 placeholder 的输入框都必须具备清空能力。

    规范：统一使用 `type="search"`，由浏览器提供原生清空按钮，
    并在 style.css 中统一其视觉（见下一个用例）。

    确实不能使用原生清空按钮（例如自绘了清空逻辑、或禁止浏览器注入控件）的搜索框，
    必须显式标注 `data-search-no-clear` 主动豁免，并在该组件内自行实现清空能力——
    豁免是有意识的决定，不是默认放行。
    """
    violations = []
    for source_path in Path("frontend/src").rglob("*.vue"):
        source = source_path.read_text()
        for tag in re.findall(r"<input\b[^>]*>", source, flags=re.DOTALL):
            if "搜索" not in tag or "placeholder" not in tag or "例如:" in tag:
                continue
            if "data-search-no-clear" in tag:
                continue
            if not re.search(r'\btype=["\']search["\']', tag):
                line = source[: source.index(tag)].count("\n") + 1
                violations.append(f"{source_path}:{line}")

    assert not violations, (
        "以下搜索输入框缺少清空按钮能力（请改用 type=\"search\"，"
        "或标注 data-search-no-clear 并自行实现清空）:\n" + "\n".join(violations)
    )


def test_search_cancel_button_has_shared_visual_style():
    stylesheet = Path("frontend/src/style.css").read_text()

    assert 'input[type="search"]::-webkit-search-cancel-button' in stylesheet
    assert "cursor: pointer" in stylesheet


def test_search_cancel_button_space_is_reserved_globally():
    """原生清空按钮（18px + 8px 间距）需要有右侧留白，否则有内容时会与文字拥挤。

    该留白由 style.css 全局兜底：仅在输入框有内容（按钮真正出现）时生效，
    空态不变；全项目搜索框的 padding-right 参差不齐（8～32px），逐个补齐既
    易漏又会随新代码回归，因此这里锁定全局兜底规则本身。
    """
    stylesheet = Path("frontend/src/style.css").read_text()

    assert "input::-webkit-search-cancel-button" in stylesheet, (
        "@supports 需限定在会渲染原生清空按钮的浏览器上"
    )
    assert 'input[type="search"]:not(:placeholder-shown)' in stylesheet, (
        "兜底留白必须只在有内容时生效，避免改变空态视觉"
    )
    assert "padding-right: 1.75rem" in stylesheet, "清空按钮需要约 28px 右侧空间"
