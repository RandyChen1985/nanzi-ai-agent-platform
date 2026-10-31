"""CSV 表格单元格渲染的 XSS 回归契约。

背景：画布表格用 `v-html` 渲染单元格以高亮搜索命中。旧实现
`text.replace(regex, '<mark>$1</mark>')` 只包了命中段，**未命中的原文原样进入 DOM**，
于是模型输出 / 用户上传 CSV / 查询导出里的 `<img onerror=...>` 会被浏览器执行。

行为验证见 `tests/frontend/csv_highlight.mjs`（用 Node 真跑纯函数）；
本文件再静态锁定「渲染入口不得绕开该纯函数」这一接线事实。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CANVAS = ROOT / "frontend/src/components/embed/ChatCanvas.vue"
UTIL = ROOT / "frontend/src/utils/csvHighlight.ts"


def _run_node(script: str) -> str:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_csv_cell_highlighting_escapes_html_via_node():
    output = _run_node("tests/frontend/csv_highlight.mjs")
    assert "csv_highlight: all assertions passed" in output


def test_canvas_routes_cells_through_shared_escaper():
    canvas = CANVAS.read_text(encoding="utf-8")
    util = UTIL.read_text(encoding="utf-8")

    assert "from '@/utils/csvHighlight'" in canvas, "画布必须复用共享的转义/高亮纯函数"
    assert "highlightCsvCell(text, csvSearchQuery.value)" in canvas

    # 组件内不得再自行拼 <mark>：那是 XSS 的历史来源
    assert "<mark class=" not in canvas, "单元格 HTML 不得在组件内手工拼接"
    assert "escapeRegExp" not in canvas, "正则转义应随纯函数一起收口"

    # 纯函数本身必须真的转义五个字符，且命中段与被命中段都走 escapeHtml
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert entity in util
    assert util.count("escapeHtml(") >= 2


def test_canvas_cell_binding_still_uses_the_wrapper():
    """渲染入口仍绑定 highlightMatch，避免有人把 v-html 改成未转义的直出。"""
    canvas = CANVAS.read_text(encoding="utf-8")
    assert 'v-html="highlightMatch(cell)"' in canvas
    assert "const highlightMatch = (text: string) => highlightCsvCell" in canvas
