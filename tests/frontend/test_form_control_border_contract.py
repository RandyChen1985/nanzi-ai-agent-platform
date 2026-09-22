"""base 层表单控件兜底契约：边框 + 内边距。

Tailwind preflight 会同时把表单控件的 `border-width` 和 `padding` 归零，于是项目里
只写 `border-gray-300`（边框**颜色**）却没写 `border`（边框**宽度**）、也没有任何 `p-*`
的控件会同时失去边框与内边距：既看不出可编辑，又只有一行文字高（实测 20px，而正常字段
38px）。项目自身较新的代码写的是 `border border-gray-300 bg-gray-100 p-2`，
说明补回边框和内边距才是原意。

修复放在 `frontend/src/style.css` 的 base 层（不是逐个改 class），并依赖三条约束：
1. 主规则选择器**只用元素级特异性**（`input, select, textarea`），这样 utilities 层里的
   `border-0` / `border-none` / `p-0` / `py-1` / `border-2` / `border-<color>` 全部照常覆盖；
2. 原生外观控件（勾选/单选/滑块/取色/文件）两者都不该有；
3. 外层容器已经画了边框或已经给了内边距的复合控件，其内部控件必须显式 `border-0` / `p-0`，
   否则出现双线或双层留白。

本文件锁定这些约束，防止后人把兜底规则改成 `:not(...)`/类选择器（会让工具类失效）、
删掉原生控件排除、或删掉复合控件的 opt-out。
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend/src"
pytestmark = pytest.mark.no_infrastructure


def _style_css() -> str:
    return (SRC / "style.css").read_text(encoding="utf-8")


def _base_layer_block() -> str:
    css = _style_css()
    start = css.index("@layer base {")
    depth = 0
    for i, ch in enumerate(css[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[start : i + 1]
    raise AssertionError("style.css 的 @layer base 块未闭合")


def test_form_controls_get_a_real_border_from_base_layer():
    """base 层必须给 input/select/textarea 补回 1px 边框（这是本次修复的本体）。"""
    block = _base_layer_block()

    assert "input,\n  select,\n  textarea {" in block, "主规则应同时覆盖三类表单控件"
    assert "border-width: 1px" in block, "缺边框宽度正是本次的根因"
    assert "border-style: solid" in block
    assert "theme('colors.gray.300')" in block, "边框颜色应取自调色板，而不是写死十六进制"


def test_form_controls_get_default_padding_from_base_layer():
    """base 层必须同时补回内边距，否则字段只有一行文字高（实测 20px vs 38px）。"""
    block = _base_layer_block()
    main_rule = block[block.index("input,\n  select,\n  textarea {") :].split("}")[0]

    assert "padding: theme('spacing.2') theme('spacing.3')" in main_rule, (
        "缺内边距会让输入框与同页其它字段高度不一致（品牌个性化 vs 参数配置）"
    )


def test_base_rule_keeps_element_level_specificity_so_utilities_win():
    """主规则不得引入类/属性选择器，否则 `border-0` / `p-0` 这类显式退出会失效。"""
    block = _base_layer_block()
    rule = block[block.index("input,\n  select,\n  textarea {") :].split("}")[0]
    selector = rule.split("{")[0].strip()

    assert selector == "input,\n  select,\n  textarea", (
        "主规则只能用元素级选择器；加 :not()/类/属性选择器都会抬高特异性，"
        "导致 border-0、p-0、border-<color> 等工具类无法覆盖"
    )


def test_native_look_controls_are_excluded():
    """勾选框/单选/滑块/取色/文件由浏览器自绘，既不能叠加边框也不能叠加内边距。"""
    block = _base_layer_block()
    reset = block[block.index("input[type='checkbox']") :]

    for control_type in ("checkbox", "radio", "range", "color", "file"):
        assert f"input[type='{control_type}']" in reset
    assert "border-width: 0" in reset
    assert "padding: 0" in reset


def test_dark_mode_gets_a_darker_border():
    """darkMode: 'class' —— 暗色下需换成深灰，避免浅色描边在深底上过亮。"""
    block = _base_layer_block()

    assert ".dark input" in block
    assert "theme('colors.gray.700')" in block


def _form_elements(source: str):
    """产出 `(tag, attrs)` —— 引号感知扫描，避免 `:disabled="a > b"` 之类把标签截断。"""
    out, i = [], 0
    while True:
        match = re.search(r"<(input|select|textarea)\b", source[i:])
        if not match:
            return out
        start = i + match.end()
        quote, j = None, start
        while j < len(source):
            ch = source[j]
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == ">":
                break
            j += 1
        out.append((match.group(1), source[start:j]))
        i = j


def test_system_config_value_fields_use_a_white_fill_with_grey_disabled_state():
    """系统配置页的值字段统一「可编辑白底 / 禁用灰底」。

    该页此前是灰底（`rounded-md bg-gray-100 p-2`），而同页品牌个性化字段因为没写 bg class
    本来就是白底，同一页出现两套底色；改成白底后与品牌个性化一致，禁用态回落到灰底以保证
    「能不能改」仍可一眼分辨（渲染实测计算样式：可编辑 rgb(255,255,255)、禁用 rgb(243,244,246)）。

    允许保留灰底的只有非输入用途的控件：`type=button` 的按钮、工具栏 `type=search` 搜索框，
    以及原生勾选/单选等（全站搜索框统一是浅灰底，不属于本次范围）。
    """
    source = (SRC / "views/SystemConfig.vue").read_text(encoding="utf-8")
    bare_gray = re.compile(r"(?<![\w:-])bg-gray-(?:50|100)(?![\w-])")
    not_a_value_field = {"button", "submit", "reset", "search", "checkbox", "radio", "range", "file", "color"}

    offenders = []
    for tag, attrs in _form_elements(source):
        type_match = re.search(r'type="([^"]*)"', attrs)
        if type_match and type_match.group(1) in not_a_value_field:
            continue
        class_match = re.search(r'class="([^"]*)"', attrs)
        if class_match and bare_gray.search(class_match.group(1)):
            offenders.append(f"<{tag} type={type_match.group(1) if type_match else '-'}>")

    assert offenders == [], f"这些值字段应改为白底（可编辑白底 / 禁用灰底）：{offenders}"
    assert "bg-white disabled:bg-gray-100" in source, "白底值字段必须声明禁用态回落为灰底"


@pytest.mark.parametrize(
    ("relative_path", "class_fragment", "expected_count"),
    [
        ("views/SystemConfig.vue", "w-full border-0 bg-transparent p-0 text-sm text-gray-700", 1),
        ("components/embed/ChatCanvas.vue", "border-0 bg-transparent outline-none focus:ring-0 resize-y", 3),
        ("components/system/McpServerRegistry.vue", "flex-1 min-w-0 border-0 px-3 py-2 text-sm font-mono", 1),
        ("views/DataSourceManagement.vue", "min-w-0 flex-1 border-0 px-3 py-2", 1),
        ("views/PromptStudio.vue", "flex-1 border-0 pl-4 pr-12", 1),
    ],
)
def test_controls_inside_padded_wrappers_or_self_bordered_wrappers_opt_out(relative_path, class_fragment, expected_count):
    """外层容器已画边框/已给内边距的复合控件，内部控件必须显式 border-0 / p-0，否则双线或双层留白。"""
    source = (SRC / relative_path).read_text(encoding="utf-8")

    assert source.count(class_fragment) == expected_count, (
        f"{relative_path} 中该复合控件内部控制应保留 border-0 / p-0（预期 {expected_count} 处）"
    )
