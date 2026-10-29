"""契约测试：MCP 工具卡的远端进度展示。

行为断言在 `mcp_tool_progress_card.mjs`（解析模板 AST + 数据链路源码）；
这里锁住测试确实在跑，以及几条重构中最容易丢掉的约束。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMELINE_VUE = "frontend/src/components/chat/ChatExecutionTimeline.vue"
HANDLERS = "frontend/src/utils/agentscopeSseHandlers.ts"
TIMELINE_TS = "frontend/src/utils/processTimeline.ts"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_progress_card_behaviour_via_node():
    node = subprocess.run(
        ["node", "tests/frontend/mcp_tool_progress_card.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"
    assert "OK: MCP 工具卡进度展示" in node.stdout


def test_progress_only_updates_existing_card():
    """进度帧必须「只更新、不新建」：否则乱序事件会留下没有标题的空卡片。"""
    source = _read(TIMELINE_TS)
    body = source.split("export function applyTimelineLogProgress", 1)[1].split("\n}\n", 1)[0]

    assert "if (!item) return false" in body, body
    assert "kind: \"log\"" not in body, "进度写入不能走新建条目的分支"


def test_progress_hides_after_completion():
    """工具结束后进度条必须让位给耗时/结果，否则历史回放会看到一堆 100% 残影。

    兜底条把「何时显示」从「有远端进度」放宽成「工具还在跑」，所以这里按容器的
    标记类反查它的 v-if，而不是依赖某个字段名。
    """
    source = _read(TIMELINE_VUE)
    marker = 'class="timeline-progress-shell'
    assert marker in source, "找不到兜底进度条容器"
    head = source.split(marker, 1)[0]
    guard = head.rsplit('v-if="', 1)[1].split('"', 1)[0]

    assert "item.status === 'pending'" in guard, guard
    assert "isToolTimelineItem(item)" in guard, guard


def test_backend_frame_field_names_match_frontend():
    """后端帧用 id/progress/total/message，前端不能改名，否则线上静默丢进度。"""
    handlers = _read(HANDLERS)
    body = handlers.split("export function syncProcessTimelineToolProgress", 1)[1].split("\n}\n", 1)[0]

    assert "data.id" in body
    assert "data.progress" in body
    assert "data.total" in body
    assert "data.message" in body
