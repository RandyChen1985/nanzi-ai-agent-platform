"""「上下文能力准备」父级执行完后自动收起的契约。

规则：这一步跑完就收起（不等整轮回答结束）；自身或子步骤还在跑、出错/被中断、
用户手动点开过，这三种情况保持展开。

历史实现是在「已有回答且无 pending」那一刻把 `childrenExpanded` 改成 `false`。
时间线对象在流式合并与历史恢复时会被重建，这个一次性标志随之被冲掉，用户看到的
就是「跑完了准备项还摊着」。改为每次渲染重算的派生判定后，结果不再依赖写入时机。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue"
UTIL = ROOT / "frontend/src/utils/processTimeline.ts"
PROBE = ROOT / "tests/frontend/preparation_collapse_probe.cjs"


def _component() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def _run_probe() -> dict:
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    proc = subprocess.run(["node", str(PROBE)], cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"探针执行失败：\n{proc.stderr}"
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# 真跑：判定真值表
# --------------------------------------------------------------------------


def test_collapses_as_soon_as_this_step_finishes():
    """这一步跑完就收，不等整轮回答结束——摊着会吃掉大半个思考卡片。"""
    out = _run_probe()
    assert out["finished_collapses"] is True
    # 历史数据可能没有 status，同样视为已完成
    assert out["finished_without_status_collapses"] is True


def test_keeps_open_while_still_running():
    """自身或任一子步骤还在跑时保持展开：准备阶段是此刻唯一能看到的进展。"""
    out = _run_probe()
    assert out["self_pending_keeps_open"] is False
    assert out["child_pending_keeps_open"] is False


def test_keeps_open_when_something_went_wrong():
    """出错 / 被中断必须留着——收起等于把故障藏起来。"""
    out = _run_probe()
    assert out["error_keeps_open"] is False
    assert out["interrupted_keeps_open"] is False
    assert out["error_with_pending_child_keeps_open"] is False


def test_user_toggle_wins_over_auto_collapse():
    """用户点开正在看的内容，不能被自动收走。"""
    assert _run_probe()["user_toggled_keeps_open"] is False


def test_other_parents_are_untouched():
    """路由组、工具组等父级不受这条规则影响。"""
    out = _run_probe()
    assert out["other_parent_untouched"] is False
    assert out["other_parent_untouched_when_pending"] is False


def test_missing_input_tolerated():
    """缺参数按「已完成且无 pending」处理，不能崩。"""
    assert _run_probe()["undefined_tolerated"] is True


def test_result_is_deterministic():
    """派生判定的核心性质：同样输入永远同样结果，不依赖调用次数或时机。"""
    assert _run_probe()["deterministic"] is True


# --------------------------------------------------------------------------
# 组件侧：不再依赖一次性副作用
# --------------------------------------------------------------------------


def test_no_one_shot_mutation_of_children_expanded():
    """回归保护：不能再出现「遍历时间线把准备父级 childrenExpanded 改成 false」。"""
    source = _component()
    assert "item.childrenExpanded = false" not in source
    assert "shouldCollapsePreparationChildren" in source


def test_expansion_judgements_go_through_the_derived_check():
    source = _component()
    body = source[source.index("function isChildrenOpen"):]
    body = body[: body.index("function isTimelineItemExpanded")]
    assert "shouldCollapsePreparationChildren({" in body
    assert "isPreparation: isPreparationParent(item)" in body
    assert "userToggled: item.userToggled" in body
    assert "status: item.status" in body
    # 只看这一棵子树：整轮其它步骤还在跑不该拖住准备项收起
    assert "childrenPending: timelineHasPending(item.children)" in body
    # 折叠与「整轮是否答完」解耦
    assert "hasAnswer: props.hasAnswer" not in body

    expanded = source[source.index("function isTimelineItemExpanded"):]
    expanded = expanded[: expanded.index("function isTimelineItemDetailsOpen")]
    assert "isChildrenOpen(item)" in expanded

    details = source[source.index("function isTimelineItemDetailsOpen"):]
    details = details[: details.index("function toggleTimelineItem")]
    assert "isChildrenOpen(item)" in details


def test_template_render_paths_use_the_derived_check():
    source = _component()
    assert ':aria-expanded="isChildrenOpen(item)"' in source
    assert "'rotate-180': isChildrenOpen(item)" in source
    assert 'v-if="isChildrenOpen(item)"' in source
    assert 'v-if="item.children?.length && isChildrenOpen(item)"' in source


def test_manual_toggle_still_marks_user_intent():
    """手动点击要留下意图标记，否则下一次渲染会被自动收起覆盖。"""
    source = _component()
    body = source[source.index("function toggleTimelineItem"):]
    body = body[: body.index("function isChildDetailsOpen")]
    assert "item.userToggled = true" in body


def test_util_keeps_the_rule_documented():
    """纯函数就在 defaultChildrenExpandedForLog 旁边，避免两处规则各说各话。"""
    util = UTIL.read_text(encoding="utf-8")
    assert "export function shouldCollapsePreparationChildren(" in util
    assert "userToggled?: boolean;" in util


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
