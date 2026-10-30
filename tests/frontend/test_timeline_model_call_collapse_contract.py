"""契约测试：模型调用默认折叠成一行汇总，不再逐条占主时间线（A 方案）。

背景（用户截图）：一轮 Agent 执行里 11 次「模型调用」被 push 到时间线数组末尾，
12 个工具调用却都挂在旁白 children 下，界面因此呈现「工具全在上、模型调用全在下」，
正在跑的那条 Bash 被压在最底下，看着像时间倒挂。

行为断言在 `timeline_model_collapse.mjs`（node 直接加载真实 `processTimeline.ts`）；
这里锁住「接线没被摘掉」和几条容易被手改回去的约束。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_model_call_collapse_behaviour_via_node():
    node = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "tests/frontend/timeline_model_collapse.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"
    assert "行为测试通过" in node.stdout


def test_timeline_filters_model_calls_before_render():
    timeline = _source("frontend/src/components/chat/ChatExecutionTimeline.vue")

    # 汇总口径与过滤规则都来自 utils，组件只负责调用（逻辑不允许再内联一份）
    assert "stripModelCallLogs" in timeline
    assert "summarizeModelCalls" in timeline
    assert "stripModelCallLogs(grouped)" in timeline
    # items 必须走过滤后的结果，模板 v-for 才不会重新长出模型调用行
    assert "v-for=\"item in items\"" in timeline


def test_timeline_model_call_summary_is_opt_out():
    timeline = _source("frontend/src/components/chat/ChatExecutionTimeline.vue")

    # 默认开启（A 方案）；需要逐条观测的场景显式传 false
    assert "hideModelCalls?: boolean;" in timeline
    assert "hideModelCalls: true," in timeline
    assert "props.hideModelCalls !== false" in timeline


def test_timeline_renders_summary_row_with_stats_entry():
    timeline = _source("frontend/src/components/chat/ChatExecutionTimeline.vue")

    assert "modelCallSummaryLabel" in timeline
    assert "{{ modelCallSummaryLabel }}" in timeline
    assert "查看明细" in timeline
    # 明细入口走事件，由页面决定打开哪个弹层（组件不持有模态状态）
    assert "(e: \"open-model-stats\"): void;" in timeline
    assert "emit('open-model-stats')" in timeline
    # 只有汇总行、没有其它条目时整块仍要可见
    assert "modelCallSummary.value.count" in timeline


def test_pages_wire_model_stats_entry_to_existing_modal():
    for page in ("frontend/src/views/EmbedChat.vue", "frontend/src/views/AgentDebug.vue"):
        source = _source(page)
        assert 'ChatExecutionTimeline' in source
        assert '@open-model-stats="openModelCallStats(msg)"' in source, page
        # 弹层加载函数必须已存在（复用它，不另开一份实现）
        assert "const openModelCallStats = async (msg: any) => {" in source, page


def test_process_timeline_keeps_pending_and_fallback_visible():
    utils = _source("frontend/src/utils/processTimeline.ts")

    assert "export function isModelCallLog(" in utils
    assert "export function isModelFallbackNotice(" in utils
    assert "export function stripModelCallLogs(" in utils
    assert "export function summarizeModelCalls(" in utils
    # 进行中的那条要留着，否则模型思考期间主时间线完全静止
    assert "keepPending && item.status === \"pending\"" in utils
    # fallback 换模提示是用户必须看见的事件，不能随 model 分类一起折叠
    assert "/fallback/i.test" in utils and "log.status === \"warning\"" in utils
