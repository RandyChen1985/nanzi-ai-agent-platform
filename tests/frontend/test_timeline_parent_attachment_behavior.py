"""时间线「工具行挂到哪一段旁白下」的归属契约。

线上现象（用户截图）：思考卡片里一条正在进行中的「调用工具 · Bash 4ms/300s」跑到了
上面，而本轮刚发生的准备序列（权限及上下文与能力准备 → 已启用技能 → 工具可用性检查
→ 会话运行时准备 → 主专家开始处理）排在它下面，看起来像时间倒挂。

根因：`upsertTimelineLog` 给工具行找父节点时只要求「最近一条 narration 且已提交」，
没有任何「本轮 / 本阶段」约束。于是本轮此刻还没有已提交旁白时（模型没说就动手、
旁白还在流式、或授权挂起恢复后重试），工具行会被吸附进**上一轮**那段旁白的 children
里，位置就此冻结在旧区域；本轮后续到达的准备类事件作为顶层条目继续往后追加。

这里把四种归属场景钉成行为断言：
1. 旁白是时间线最后一条（正常引出一串工具）——必须继续挂 children，保住既有观感；
2. 旁白之后已经发生过别的事（跨阶段 / 跨轮）——工具必须留在顶层；
3. 本轮旁白还在流式（pending）——不能吸附到上一轮那段已提交旁白；
4. 授权挂起后恢复（权限卡已进时间线）——恢复后到达的工具必须留在顶层。
另有「Agent 回复开始 / 结束」阶段行不得被当成工具行塞进旁白 children 的专项断言。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

MODULE = "frontend/src/utils/processTimeline.ts"


def _run_typescript(expression: str):
    script = f"""
(async () => {{
const {{ loadModule }} = require('./tests/frontend/ts_module_loader.cjs');
const api = loadModule({json.dumps(MODULE)});
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
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


def _top_level_ids(items):
    return [str(item.get("id")) for item in items]


def _child_ids(items):
    out = []
    for item in items:
        for child in item.get("children") or []:
            out.append(str(child.get("id")))
    return out


def test_tool_after_trailing_narration_still_groups_under_it():
    """旁白就排在最后时，工具继续挂它的 children（既有观感不退化）。"""
    result = _run_typescript(
        """
const target = {};
api.appendTimelineNarrationDelta(target, '我先查看负载。');
api.commitTimelineNarration(target, '我先查看负载。');
api.upsertTimelineLog(target, { id: 'bash', title: '工具完成: Bash', details: '', status: 'success', category: 'tool' });
api.upsertTimelineLog(target, { id: 'ps', title: '工具完成: list_process', details: '', status: 'success', category: 'tool' });
return target.processTimeline;
"""
    )

    assert len(result) == 1
    assert result[0]["kind"] == "text"
    assert [child["id"] for child in result[0]["children"]] == ["bash", "ps"]


def test_tool_does_not_attach_to_narration_of_a_finished_phase():
    """旁白之后已经发生过别的事：新工具属于新阶段，不能往回塞进旧旁白。"""
    result = _run_typescript(
        """
const target = {};
api.appendTimelineNarrationDelta(target, '上一轮的旁白。');
api.commitTimelineNarration(target, '上一轮的旁白。');
api.upsertTimelineLog(target, { id: 'old-bash', title: '工具完成: Bash', details: '', status: 'success', category: 'tool' });
api.upsertTimelineLog(target, {
  id: 'preparation:auth_context_capability',
  title: '权限及上下文与能力准备',
  details: '',
  status: 'success',
  category: 'system',
});
api.upsertTimelineLog(target, {
  id: 'call-bash',
  title: '调用工具: Bash',
  details: '',
  status: 'pending',
  category: 'tool',
  timeout_seconds: 300,
});
return target.processTimeline;
"""
    )

    assert _top_level_ids(result)[-1] == "call-bash"
    assert "call-bash" not in _child_ids(result)
    assert "old-bash" in _child_ids(result)


def test_tool_does_not_attach_to_previous_narration_while_current_one_is_streaming():
    """本轮旁白还在流式（pending）时，工具不能吸附到上一轮那段已提交旁白。"""
    result = _run_typescript(
        """
const target = {};
api.appendTimelineNarrationDelta(target, '上一轮的旁白。');
api.commitTimelineNarration(target, '上一轮的旁白。');
api.appendTimelineNarrationDelta(target, '本轮旁白还在打字中');
api.upsertTimelineLog(target, { id: 'call-bash', title: '调用工具: Bash', details: '', status: 'pending', category: 'tool' });
return target.processTimeline;
"""
    )

    assert "call-bash" not in _child_ids(result)
    assert _top_level_ids(result)[-1] == "call-bash"


def test_tool_after_permission_card_stays_on_top_level():
    """授权挂起恢复：权限卡进过时间线后，恢复后到达的工具不能被塞回更早的旁白。"""
    result = _run_typescript(
        """
const target = {};
api.appendTimelineNarrationDelta(target, '上一轮的旁白。');
api.commitTimelineNarration(target, '上一轮的旁白。');
api.upsertTimelineLog(target, {
  id: 'perm-1',
  title: '需要确认：执行 Bash',
  details: '',
  status: 'success',
  category: 'permission',
});
api.upsertTimelineLog(target, { id: 'call-bash', title: '调用工具: Bash', details: '', status: 'pending', category: 'tool' });
return target.processTimeline;
"""
    )

    assert "call-bash" not in _child_ids(result)
    assert _top_level_ids(result)[-1] == "call-bash"


def test_agent_lifecycle_rows_never_attach_to_narration_children():
    """「Agent 回复开始 / 结束」是阶段行，不是工具执行，不能被塞进旁白 children。"""
    result = _run_typescript(
        """
const target = {};
api.appendTimelineNarrationDelta(target, '上一轮的旁白。');
api.commitTimelineNarration(target, '上一轮的旁白。');
api.upsertTimelineLog(target, { id: 'reply-start', title: 'Agent 回复开始', details: '', status: 'success', category: 'agent' });
api.upsertTimelineLog(target, { id: 'reply-end', title: 'Agent 回复结束', details: '', status: 'success', category: 'agent' });
return target.processTimeline;
"""
    )

    assert _child_ids(result) == []
    assert _top_level_ids(result) == [result[0]["id"], "reply-start", "reply-end"]
