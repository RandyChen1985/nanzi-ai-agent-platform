"""历史落库失败必须在前端可见，不能只留在后端日志里。

后端在 ``finalize_step`` 收尾时已经能判断本轮历史是否真的写进
``ai_agent_execution_history``。若只在后端记日志，用户仍会以为「刷新后就能看到」，
所以共享事件分发器必须把 ``history_persist_failed`` 渲染成一条可见的时间线日志。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLERS = (ROOT / "frontend/src/utils/agentscopeSseHandlers.ts").read_text(
    encoding="utf-8"
)


def test_dispatcher_renders_history_persist_failure_as_visible_log():
    marker = 'case "history_persist_failed":'
    assert marker in HANDLERS, "共享事件分发器未处理 history_persist_failed"

    block = HANDLERS[HANDLERS.index(marker) : HANDLERS.index(marker) + 700]
    assert "addLog(msg" in block, "失败事件必须写入可见的时间线日志"
    assert 'status: "warning"' in block
    assert "本轮记录未保存" in block


def test_dispatcher_keeps_untouched_event_types_intact():
    """新增分支不得影响既有思考/正文事件的分发顺序。"""
    assert 'case "reasoning_content":' in HANDLERS
    assert 'case "answer_delta":' in HANDLERS
