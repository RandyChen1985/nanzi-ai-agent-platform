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
