"""契约测试：挂起中的工具行显示「已耗时 / 超时上限」。

背景（用户截图）：Bash 行右侧只有 `33.0s`，用户不知道还要等多久才算超时。
分母取「本轮工具调用的实际超时」——版本级配置优先，没配才跟随全局
（`resolve_agent_toolcall_timeout`），因此不能直接用入口下发的全局值。

行为断言在 `tool_timeout_display.mjs`（node 直接加载真实 `processTimeline.ts`）；
这里锁住「接线没被摘掉」和几条容易被手改回去的约束。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_tool_timeout_behaviour_via_node():
    node = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--no-warnings",
            "tests/frontend/tool_timeout_display.mjs",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"
    assert "all checks passed" in node.stdout


def test_timeline_renders_timeout_denominator():
    timeline = _source("frontend/src/components/chat/ChatExecutionTimeline.vue")

    # 分母由 props 传入，拼装与警示判定都走 utils（逻辑不允许再内联一份）
    assert "toolTimeoutSeconds?: number" in timeline
    assert "appendToolTimeoutSuffix(" in timeline
    assert "isToolTimeoutNearLimit(" in timeline
    # 只有挂起中的工具行需要分母：完成/失败态已有实际耗时
    assert 'if (item.status !== "pending") return null;' in timeline
    assert 'if (String(item.category || "") !== "tool") return null;' in timeline


def test_every_duration_slot_uses_the_shared_helper():
    timeline = _source("frontend/src/components/chat/ChatExecutionTimeline.vue")

    # 主行 + 旁白 children + 两级子步骤共 5 处耗时位；只改一处会让不同层级显示不一致
    assert timeline.count("timelineDurationText(") >= 6
    for legacy in (
        '{{ formatTimelineDuration(item) }}',
        '{{ formatTimelineDuration(child) }}',
        '{{ formatTimelineDuration(subStep) }}',
        '{{ formatTimelineDuration(nestedStep) }}',
    ):
        assert legacy not in timeline, f"仍有模板直接调用旧函数：{legacy}"


def test_pages_pass_resolved_tool_timeout():
    for view in (
        "frontend/src/views/EmbedChat.vue",
        "frontend/src/views/AgentDebug.vue",
    ):
        source = _source(view)
        assert ':tool-timeout-seconds="msg.agentMaxToolcallTimeoutSeconds"' in source, view


def test_runner_pushes_resolved_timeout_after_tool_resolution():
    runner = _source("app/services/ai/runners/assistant_agent_runner.py")

    # 实际值只能等工具解析出来才知道；入口的 run_config 只发了全局值
    assert "_first_tool_timeout_seconds(tools)" in runner
    assert '"agent_max_toolcall_timeout": int(resolved_tool_timeout)' in runner
    # 必须是 run_config：前端已有该事件的处理分支，复用可避免协议分叉
    assert '"type": "run_config"' in runner
