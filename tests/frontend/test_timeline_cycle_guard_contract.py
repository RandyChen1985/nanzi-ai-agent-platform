"""时间线递归遍历的防环契约。

背景（2026-10-31 的「委派必卡」排查）：

`d5dabcf9`（同日 14:16）为「执行时间线汇总行」新增了工具耗时统计
（`summarizeToolCalls` → `collectLogsByPredicate`）。这个函数在**每次 props 变化**
时深度优先递归遍历整棵时间线树，而委派场景的树是「旁白 → 子代理容器 → 步骤 →
子步骤」的多层嵌套，且容器会按 `subagent` 元数据合并、按 `parent_id` 重挂。

递归原先**没有防环保护**：一旦某个节点被挂到自己下面（或两个节点互指），
`collectLogsByPredicate` 与 `findTimelineLog` 都会无限递归 → Chrome 报
「页面无响应」，刷新后重新 hydrate 同一批数据继续卡，只能新开标签页。

行为验证见 `tests/frontend/timeline_cycle_guard.mjs`（用带环结构真跑汇总函数；
去掉 WeakSet 后该脚本会栈溢出）。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
TIMELINE = ROOT / "frontend/src/utils/processTimeline.ts"


def _run_node(script: str) -> str:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_cyclic_timeline_does_not_hang():
    assert "timeline_cycle_guard: all assertions passed" in _run_node(
        "tests/frontend/timeline_cycle_guard.mjs"
    )


def test_all_recursive_walkers_carry_a_visited_set():
    """三处递归都必须带 WeakSet：汇总遍历、按 id 查找、查找的顶层入口。"""
    src = TIMELINE.read_text(encoding="utf-8")

    assert "const visited = new WeakSet<object>();" in src
    assert "visited.has(item)" in src
    assert "visited.has(log)" in src
    # 查找入口与内层递归都要把 visited 传下去，否则内层重新开一个集合等于没防护
    assert "findTimelineLogInLogs(item.children, id, visited)" in src
    assert "findTimelineLogInLogs(child.children, id, visited)" in src
    assert "findTimelineLogInLogs(log.children, id, visited)" in src

