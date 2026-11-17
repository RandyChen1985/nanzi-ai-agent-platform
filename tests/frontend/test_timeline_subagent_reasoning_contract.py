"""子代理思考日志不得被当成委派容器。

背景（`b87ce97b` 引入「子代理思考回流」后暴露）：后端把子代理的 reasoning 作为
`type="log"`、`category="reasoning"`、id 为 `subagent_reasoning:{run_id}:{seq}`
的日志投给父会话，设计上应由前端折叠挂进委派容器的 children。

但前端有三处「按 id 前缀认委派容器」的判定，写的都是 `startsWith("subagent_")`，
思考日志的 id 恰好也以 `subagent_` 开头，于是被当成容器：

1. `upsertTimelineLog` 的 `isIncomingSubagentLifecycle` —— 合并进已有委派卡并改写
   它的 `status` / `details` / 标题。思考日志的 `status` 是 `success`，所以**委派还在
   跑、卡片已经变绿**；`details` 被最多 4000 字的思考全文覆盖；标题被改成
   「委派智能体 · [某某] 子代理思考」。
2. `reorganizeSubagentItems` 的 `isContainer` —— 本该作为子步骤挂进容器的日志，
   被提升成并列容器，还会把容器的 `status`/`details` 再覆盖一次。
3. Vue 层 `ChatExecutionTimeline.isSubagentLog` —— 渲染成委派容器而不是容器内的一行。

行为验证见 `tests/frontend/timeline_subagent_reasoning.mjs`（回退任一处排除都会失败）。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
TIMELINE = ROOT / "frontend/src/utils/processTimeline.ts"
TIMELINE_VIEW = ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue"


def _run_node(script: str) -> str:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_reasoning_log_does_not_rewrite_delegation_card():
    assert "timeline_subagent_reasoning: all assertions passed" in _run_node(
        "tests/frontend/timeline_subagent_reasoning.mjs"
    )


def test_every_container_predicate_excludes_reasoning_logs():
    """所有「按 id 前缀认委派容器」的判定都必须先排除思考日志。

    第一次修的时候只改了 3 处，grep 一遍才发现 `startsWith("subagent_")` 全仓有 8 处：
    `isToolLog`、`isIncomingSubagentLifecycle`、`isIncomingSubagentTool` 的容器查找
    （3 个 lambda）、新增节点的挂载判定、`reorganizeSubagentItems` 的 `isContainer`、
    以及 Vue 层的 `isSubagentLog`。漏掉的每一处症状都不同（状态被改 / 结果写错节点 /
    思考落到顶层 / 渲染成容器），所以按**出现次数**断言，防止再漏。
    """
    timeline = TIMELINE.read_text(encoding="utf-8")
    view = TIMELINE_VIEW.read_text(encoding="utf-8")

    # 共享判定必须导出，供 Vue 层复用（否则各处会自己写一份、再次跑偏）
    assert "export function isSubagentReasoningLog(" in timeline

    # ① isIncomingSubagentLifecycle + 新增节点挂载判定：两处都以 data 为主体
    assert timeline.count("!isSubagentReasoningLog(data) &&") == 2
    # ② reorganizeSubagentItems 的 isContainer
    assert "!isSubagentReasoningLog(log) &&" in timeline
    # ③ isToolLog：老快照没有 category，靠这里挡住
    assert "if (isSubagentReasoningLog(data)) return false;" in timeline
    # ④ isIncomingSubagentTool 的容器查找收敛到同一个 helper
    assert "function isDelegationContainerLike(" in timeline
    assert timeline.count("isDelegationContainerLike(") == 4  # 1 处定义 + 3 处使用
    # ⑤ Vue 层 isSubagentLog
    assert "if (isSubagentReasoningLog(log)) return false;" in view
    assert "isSubagentReasoningLog," in view


def test_no_container_predicate_is_left_with_a_bare_prefix_check():
    """兜底：任何 `startsWith("subagent_")` 都必须处在排除判断的保护范围内。

    这条不是重复上面的断言，而是防止以后**新增**判定时只抄前缀、忘了排除——
    那种改动不会被上面的计数断言发现（计数只数已知位置）。
    """
    timeline = TIMELINE.read_text(encoding="utf-8")
    lines = timeline.split("\n")

    for idx, line in enumerate(lines):
        if 'startsWith("subagent_")' not in line:
            continue
        # 往前找 3 行，必须能看到排除判定
        window = "\n".join(lines[max(0, idx - 3) : idx + 1])
        assert "isSubagentReasoningLog" in window, (
            f"第 {idx + 1} 行的前缀判定没有排除思考日志：{line.strip()}"
        )


def test_reasoning_log_id_prefix_is_still_matched():
    """排除靠的是「更强的前缀」，不能把 `subagent_` 这个前缀整个改掉。

    老数据里的委派容器 id 就是 `subagent_*`，改掉前缀会让历史回放认不出容器。
    """
    timeline = TIMELINE.read_text(encoding="utf-8")

    assert 'startsWith("subagent_reasoning:")' in timeline
    # 委派容器的原始前缀仍在
    assert 'String(log.id).startsWith("subagent_")' in timeline
