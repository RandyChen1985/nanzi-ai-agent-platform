"""「上下文能力准备」父级执行完后自动收起的契约。

规则：准备好之后**默认收起**，只有出错/被中断、或用户手动点开过才展开。
收起判定必须从第一帧就成立——早先是「进行中展开、跑完收起」，同一个节点先摊开
再收起来，视觉上会闪一下。

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


def test_collapsed_by_default_even_while_running():
    """默认收起：从出现的第一帧就收，不等跑完。

    早先的规则是「进行中展开、跑完收起」，同一个节点会先摊开再收起来，视觉上闪一下。
    """
    out = _run_probe()
    assert out["pending_collapses"] is True
    assert out["finished_collapses"] is True
    assert out["finished_without_status_collapses"] is True


def test_judgement_stable_across_status_transition():
    """pending -> success 判定不变，这是「不闪」的充要条件。"""
    assert _run_probe()["stable_across_pending_to_success"] is True


def test_keeps_open_when_something_went_wrong():
    """出错 / 被中断必须展开——收起等于把故障藏起来。"""
    out = _run_probe()
    assert out["error_keeps_open"] is False
    assert out["interrupted_keeps_open"] is False


def test_user_toggle_wins_over_auto_collapse():
    """用户点开正在看的内容，不能被自动收走。"""
    out = _run_probe()
    assert out["user_toggled_keeps_open"] is False
    assert out["user_toggled_while_pending_keeps_open"] is False


def test_other_parents_are_untouched():
    """路由组、工具组等父级不受这条规则影响。"""
    out = _run_probe()
    assert out["other_parent_untouched"] is False
    assert out["other_parent_untouched_when_pending"] is False


def test_missing_input_tolerated():
    """缺参数按「默认收起」处理，不能崩。"""
    assert _run_probe()["finished_without_status_collapses"] is True


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
    # 收起判定不看「是否还在跑」，否则会先展开再收起
    assert "childrenPending" not in body
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


def test_rule_has_no_pending_branch():
    """判定里不能有「进行中 -> 展开」的分支，否则同一个节点会先摊开再收起。"""
    util = UTIL.read_text(encoding="utf-8")
    fn = util[util.index("export function shouldCollapsePreparationChildren"):]
    fn = fn[: fn.index("\n}")]
    assert 'status === "pending"' not in fn
    assert "childrenPending" not in fn


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
