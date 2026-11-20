"""执行明细的「事件序」契约：按后端 `_seq` 稳定排序，缺序数据一律不动。

背景：时间线的位置长期由「事件首次到达」决定——同一 id 更新只改内容不改位置。
并发工具、断线补发、多路事件交错时到达顺序会偏离真实发生顺序，看起来就是偶发乱序。

后端每条流事件在 Redis journal 里本来就有会话维度单调递增的 `_seq`，只是没随实时帧
下发。这次把它写回事件并在前端记为 `seq`，渲染前做一次**同层**稳定排序：
- 有 seq 的按 seq 升序；
- 没有 seq 的项（本地创建的旁白/推理文本、老快照）继承「前一个已知 seq」，贴住不动；
- 本来就有序（以及完全没有 seq）时**返回原数组引用**，避免无谓的重渲染与视觉抖动。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/processTimeline.ts"
EXECUTION_TIMELINE = ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue"
SSE_HANDLERS = ROOT / "frontend/src/utils/agentscopeSseHandlers.ts"

pytestmark = pytest.mark.no_infrastructure


def _run(expression: str):
    script = f"""
(async () => {{
const {{ loadModule }} = require('./tests/frontend/ts_module_loader.cjs');
const api = loadModule({json.dumps(MODULE)});
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_upsert_records_event_seq():
    """`_seq` 落在时间线项上，作为排序键。"""
    result = _run(
        """
const target = { processTimeline: [] };
api.upsertTimelineLog(target, { id: 'tool_1', title: '调用工具', status: 'pending', _seq: 42 });
return target.processTimeline.map(item => ({ id: item.id, seq: item.seq }));
"""
    )

    assert result == [{"id": "tool_1", "seq": 42}]


def test_upsert_keeps_first_seq_and_only_fills_missing_one():
    """首帧的 seq 决定位置；后续帧不覆盖，仅在缺失时补上。"""
    result = _run(
        """
const target = { processTimeline: [] };
api.upsertTimelineLog(target, { id: 'tool_1', title: '调用工具', status: 'pending', _seq: 7 });
api.upsertTimelineLog(target, { id: 'tool_1', title: '调用工具', status: 'success', _seq: 9 });
const keptFirst = target.processTimeline[0].seq;
const target2 = { processTimeline: [] };
api.upsertTimelineLog(target2, { id: 'tool_2', title: '调用工具', status: 'pending' });
api.upsertTimelineLog(target2, { id: 'tool_2', title: '调用工具', status: 'success', _seq: 11 });
return [keptFirst, target2.processTimeline[0].seq];
"""
    )

    assert result == [7, 11]


def test_order_sorts_items_by_event_seq():
    result = _run(
        """
const items = [
  { kind: 'log', id: 'a', seq: 3 },
  { kind: 'log', id: 'b', seq: 1 },
  { kind: 'log', id: 'c', seq: 2 },
];
return api.orderTimelineBySeq(items).map(item => item.id);
"""
    )

    assert result == ["b", "c", "a"]


def test_order_keeps_already_ordered_list_by_reference():
    """已有序时不产生新数组：渲染层依赖引用稳定，避免整块重渲染。"""
    result = _run(
        """
const items = [
  { kind: 'log', id: 'a', seq: 1 },
  { kind: 'log', id: 'b', seq: 2 },
];
const ordered = api.orderTimelineBySeq(items);
return { same: ordered === items, ids: ordered.map(item => item.id) };
"""
    )

    assert result == {"same": True, "ids": ["a", "b"]}


def test_order_leaves_sequenceless_list_untouched():
    """没有 seq 的老数据（历史快照）保持原序，等于一个可回退的开关。"""
    result = _run(
        """
const items = [
  { kind: 'log', id: 'a' },
  { kind: 'log', id: 'b' },
];
const ordered = api.orderTimelineBySeq(items);
return { same: ordered === items, ids: ordered.map(item => item.id) };
"""
    )

    assert result == {"same": True, "ids": ["a", "b"]}


def test_items_without_seq_stick_to_previous_item():
    """本地创建的旁白文本没有 seq：继承前一个已知 seq，跟着它一起走。"""
    result = _run(
        """
const items = [
  { kind: 'log', id: 'b', seq: 5 },
  { kind: 'log', id: 'a', seq: 1 },
  { kind: 'text', id: 'narration_1' },
];
return api.orderTimelineBySeq(items).map(item => item.id);
"""
    )

    assert result == ["a", "narration_1", "b"]


def test_order_is_stable_for_equal_seq():
    """seq 相同的项保持原有相对顺序（V8 的 sort 是稳定的，这里锁住这个前提）。"""
    result = _run(
        """
const items = [
  { kind: 'log', id: 'first', seq: 4 },
  { kind: 'text', id: 'glued' },
  { kind: 'log', id: 'second', seq: 4 },
];
return api.orderTimelineBySeq(items).map(item => item.id);
"""
    )

    assert result == ["first", "glued", "second"]


def test_order_recurses_into_children():
    """旁白下面挂的工具行也要按 seq 排，否则父子两层各乱各的。"""
    result = _run(
        """
const items = [{
  kind: 'text',
  id: 'narration_1',
  seq: 1,
  children: [
    { kind: 'log', id: 'tool_b', seq: 8 },
    { kind: 'log', id: 'tool_a', seq: 6 },
  ],
}];
const ordered = api.orderTimelineBySeq(items);
return {
  top: ordered.map(item => item.id),
  children: ordered[0].children.map(item => item.id),
  childArrayReplaced: ordered[0].children !== items[0].children,
  itemReused: ordered[0] === items[0],
};
"""
    )

    assert result["top"] == ["narration_1"]
    assert result["children"] == ["tool_a", "tool_b"]
    assert result["childArrayReplaced"] is True
    # children 变了的那一项必须是新对象：否则 Vue 的浅比较会漏掉内部顺序变化。
    assert result["itemReused"] is False


def test_execution_timeline_applies_event_order():
    """渲染入口必须真的用上排序，否则契约只是摆设。"""
    source = EXECUTION_TIMELINE.read_text(encoding="utf-8")

    assert "orderTimelineBySeq" in source
    assert "selectTimelineRenderableItems" in source


def test_sse_handler_forwards_event_seq():
    """实时链路上 `_seq` 不能被字段挑选逻辑吃掉。"""
    source = SSE_HANDLERS.read_text(encoding="utf-8")

    assert "_seq" in source
