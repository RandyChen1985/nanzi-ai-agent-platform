"""工具卡片执行完自动折叠的契约。

为什么用 node 真跑：`isExpanded` 的收敛时机藏在 `upsertTimelineLog` 的合并分支里，
字符串断言只能证明「写了这段代码」，证明不了「pending → success 真的会收起、手动
展开的真的不会被收」。所以用 esbuild 把真实 `processTimeline.ts` 剥成 CJS 执行，
断言跑出来的结果。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tests/frontend/tool_card_collapse_probe.cjs"


def _probe() -> dict:
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装，跳过真实执行")
    proc = subprocess.run(
        ["node", str(PROBE)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"探针执行失败：\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_tool_card_collapses_when_it_finishes():
    """执行中展开的详情，收敛成 success 后要自动收起。"""
    result = _probe()
    assert result["collapsed_on_success"] is True


def test_tool_card_collapses_on_error_too():
    """失败也是终态，同样收起（报错原因留在标题行的错误态里）。"""
    result = _probe()
    assert result["collapsed_on_error"] is True


def test_user_expanded_card_is_left_alone():
    """用户手动点开过的卡片不能被自动折叠抽走内容。"""
    result = _probe()
    assert result["kept_when_user_toggled"] is True


def test_finished_card_stays_open_while_being_read():
    """已完成的卡片再收到终态事件时，不影响用户此刻的展开。"""
    result = _probe()
    assert result["untouched_when_already_done"] is True


def test_new_card_starts_collapsed():
    """新卡片默认收起，细节要靠点击展开。"""
    result = _probe()
    assert result["new_item_collapsed"] is True


def test_collapsing_keeps_the_details():
    """收起只改展开态，不丢内容——点开还能看到最新输出。"""
    result = _probe()
    assert result["details_kept"] is True


def test_click_entries_mark_user_toggled():
    """所有点击入口都必须置位 userToggled，否则自动折叠会误伤。"""
    timeline = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(
        encoding="utf-8"
    )
    # 顶层 toggle、子层 toggle、以及「全部展开/收起」批量操作
    assert timeline.count("userToggled = true") == 3
    # 模板里的子步骤点击必须走统一函数，不能直接改 isExpanded（那样不会打标记）
    assert "toggleChildItem(subStep)" in timeline
    assert "toggleChildItem(nestedStep)" in timeline
    assert "subStep.isExpanded = !subStep.isExpanded" not in timeline


def test_collapse_only_fires_on_pending_to_terminal():
    """只在「本轮从 pending 收敛」时收一次，避免重复事件反复打扰。"""
    source = (ROOT / "frontend/src/utils/processTimeline.ts").read_text(encoding="utf-8")
    assert 'const wasPending = existing.status === "pending";' in source
    assert '&& data.status !== "pending"' in source
    assert "&& !existing.userToggled" in source
    assert "existing.isExpanded = false;" in source
