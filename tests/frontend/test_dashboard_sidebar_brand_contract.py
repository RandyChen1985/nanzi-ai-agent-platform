import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _class_attrs(source: str) -> list[str]:
    """收集模板里所有 class / :class 属性值（含动态绑定内容）。"""
    return re.findall(r':?class="([^"]*)"', source)


def test_sidebar_brand_text_stays_vertically_centered_with_logo():
    """品牌区顶栏与其文字块必须与 logo 垂直居中。

    断言按语义拆分，不整串匹配类名：颜色类可以被主题化改造挪进 `:class` 动态绑定，
    布局类也可以在保持居中的前提下追加（例如 `min-w-0`）。
    """
    source = (ROOT / "frontend/src/views/Dashboard.vue").read_text(encoding="utf-8")
    class_attrs = _class_attrs(source)

    brand_header = [
        value
        for value in class_attrs
        if re.search(r"\bh-(?:12|14|16)\b", value) and re.search(r"\bitems-center\b", value)
    ]
    assert brand_header, "品牌区顶栏必须保持 h-12/h-16 + items-center 的垂直居中布局"

    assert any("bg-sidebar" in value for value in class_attrs), (
        "品牌区顶栏在深色主题下仍需使用 bg-sidebar"
    )

    brand_text = [
        value
        for value in class_attrs
        if re.search(r"\bml-(?:2|2\.5)\b", value)
        and (
            re.search(r"\bitems-center\b", value)
            or (re.search(r"\bflex-col\b", value) and re.search(r"\bjustify-center\b", value))
        )
    ]
    assert brand_text, "品牌文字块必须保持 items-center（单行）或 flex-col + justify-center（双行），才能与 logo 居中"

    assert "-translate-y-0.5" not in source, "不得再用位移 hack 伪装垂直居中"
