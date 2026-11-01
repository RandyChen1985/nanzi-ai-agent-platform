"""过程旁白的默认展开态契约。

背景：模型先写出一段文字、随后又调用工具时，这段文字会被后端撤回并降级成
「过程旁白」（`process_narration`），不再进入最终答案。原来前端对超过 120 字的
旁白默认收起，等于把答案级的内容藏进折叠行里——用户只能在执行过程中点多一下才
看得到。现在改为默认展开，由用户主动收起；过长的旁白靠限高内滚控制占屏。
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue"


def _component() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def test_narration_is_expanded_by_default():
    """默认态只看用户是否手动收起过，不再按字数决定。"""
    source = _component()
    body = source[source.index("function isNarrationBodyOpen"):]
    body = body[: body.index("}", body.index("return"))]
    assert "return item.contentExpanded !== false;" in body
    assert "!narrationNeedsCollapse(item)" not in source


def test_user_can_still_collapse_manually():
    """默认展开不等于不能收起：点标题行仍然切换。"""
    source = _component()
    assert '@click="item.contentExpanded = !isNarrationBodyOpen(item)"' in source


def test_collapse_affordance_stays_for_long_narration():
    """字数徽章与折叠按钮仍然只在「长到需要收」的旁白上出现。"""
    source = _component()
    assert 'v-if="narrationNeedsCollapse(item)"' in source
    assert "narrationCharCountOf(item) }} 字" in source


def test_long_narration_is_not_height_limited():
    """旁白不做限高内滚。

    流式时最后一条旁白还在逐字增长，套固定高度的滚动盒会让新字落在可视区外，
    用户看不到打字过程；而且模型不再调工具时这段会直接提升为正文，
    本就应该按正文的完整形态展示。
    """
    source = _component()
    body = source[source.index('v-show="isNarrationBodyOpen(item)"'):]
    body = body[: body.index("</pre>")]
    assert "max-h-" not in body
    assert "overflow-y-auto" not in body
    assert "custom-scrollbar" not in body


def test_bulk_toggle_still_covers_narration():
    """「全部展开 / 收起」对长旁白依然生效。"""
    source = _component()
    assert 'if (it.textKind === "reasoning" || narrationNeedsCollapse(it)) it.contentExpanded = target;' in source


def test_reasoning_default_untouched():
    """深度思考的默认态是另一套规则，不能被这次改动带跑。"""
    util = (ROOT / "frontend/src/utils/processTimeline.ts").read_text(encoding="utf-8")
    assert "export function isReasoningContentExpanded(" in util
    assert "return item.pending;" in util


def test_the_component_compiles():
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    script = (
        "const {parse}=require('@vue/compiler-sfc');const fs=require('fs');"
        "const r=parse(fs.readFileSync('src/components/chat/ChatExecutionTimeline.vue','utf8'));"
        "if(r.errors.length){console.error(r.errors.map(String).join(String.fromCharCode(10)));process.exit(1)}"
    )
    proc = subprocess.run(
        ["node", "-e", script], cwd=str(ROOT / "frontend"), capture_output=True, text=True
    )
    assert proc.returncode == 0, f"组件模板无法编译：\n{proc.stderr}"
