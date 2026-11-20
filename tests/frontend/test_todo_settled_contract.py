"""Todo 清单终态语义的前端契约：banner 只服务活跃/挂起轮次，settled 要能落库回放。"""

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/processTimeline.ts"
TODO_CARD = ROOT / "frontend/src/components/chat/ChatTodoCard.vue"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = ROOT / "frontend/src/views/AgentDebug.vue"

pytestmark = pytest.mark.no_infrastructure


def _run(expression: str):
    script = f"""
(async () => {{
// 统一走 ts_module_loader：被测模块 import 了运行时依赖时，单文件转译会在 require 处断掉。
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


def test_upsert_timeline_todo_keeps_settled_flags():
    result = _run(
        """
const target = { processTimeline: [] };
api.upsertTimelineTodo(target, {
  todos: [{ content: '未完成步骤', status: 'in_progress' }],
  settled: true,
  settledStatus: 'cancelled'
});
return target.processTimeline[0];
"""
    )

    assert result["kind"] == "todo"
    assert result["settled"] is True
    assert result["settledStatus"] == "cancelled"


def test_upsert_timeline_todo_does_not_invent_settled_flag():
    result = _run(
        """
const target = { processTimeline: [] };
api.upsertTimelineTodo(target, { todos: [{ content: '执行步骤', status: 'in_progress' }] });
const item = target.processTimeline[0];
return { settled: item.settled === undefined ? null : item.settled };
"""
    )

    assert result["settled"] is None


def test_history_hydration_keeps_settled_flags():
    result = _run(
        """
const items = api.hydrateHistoryProcessTimeline([
  {
    kind: 'todo',
    id: 'todo_current',
    title: '任务清单',
    todos: [{ content: '未完成步骤', status: 'in_progress' }],
    counts: { pending: 0, in_progress: 1, completed: 0 },
    settled: true,
    settledStatus: 'error'
  }
], '');
return items[0];
"""
    )

    assert result["settled"] is True
    assert result["settledStatus"] == "error"


def test_settled_checklist_no_longer_counts_as_pending():
    result = _run(
        """
return api.timelineHasPending([
  {
    kind: 'todo',
    id: 'todo_current',
    title: '任务清单',
    todos: [{ content: '未完成步骤', status: 'in_progress' }],
    counts: { pending: 0, in_progress: 1, completed: 0 },
    settled: true,
    settledStatus: 'cancelled'
  }
]);
"""
    )

    assert result is False


def test_running_checklist_still_counts_as_pending():
    result = _run(
        """
return api.timelineHasPending([
  {
    kind: 'todo',
    id: 'todo_current',
    title: '任务清单',
    todos: [{ content: '执行步骤', status: 'in_progress' }],
    counts: { pending: 0, in_progress: 1, completed: 0 }
  }
]);
"""
    )

    assert result is True


def test_banner_keeps_settled_checklist_with_terminal_state():
    """刷新后 run 已结束：清单仍常驻，但只作为终态回顾（卡片显示「已中断」等徽标）。"""
    result = _run(
        """
const timeline = api.resolveBannerTodoTimeline([
  { role: 'user', processTimeline: [] },
  {
    role: 'assistant',
    processTimeline: [{
      kind: 'todo',
      id: 'todo_current',
      title: '任务清单',
      todos: [{ content: '未完成步骤', status: 'in_progress' }],
      counts: { pending: 0, in_progress: 1, completed: 0 },
      settled: true,
      settledStatus: 'cancelled'
    }]
  }
], { runActive: false });
return timeline && timeline.map(item => item.kind);
"""
    )

    # 不再「一终结就消失」：消失会让清单只剩执行明细里夹在步骤中间的那份
    assert result == ["todo"]


def test_banner_hides_unsettled_legacy_checklist_when_run_is_over():
    """老快照没有 settled 字段：只要本轮不在跑，同样不能常驻。"""
    result = _run(
        """
return api.resolveBannerTodoTimeline([
  {
    role: 'assistant',
    processTimeline: [{
      kind: 'todo',
      id: 'todo_current',
      title: '任务清单',
      todos: [{ content: '老数据步骤', status: 'in_progress' }],
      counts: { pending: 0, in_progress: 1, completed: 0 }
    }]
  }
], { runActive: false });
"""
    )

    assert result is None


def test_banner_shows_checklist_while_run_is_active():
    result = _run(
        """
const timeline = api.resolveBannerTodoTimeline([
  {
    role: 'assistant',
    processTimeline: [{
      kind: 'todo',
      id: 'todo_current',
      title: '任务清单',
      todos: [{ content: '执行步骤', status: 'in_progress' }],
      counts: { pending: 0, in_progress: 1, completed: 0 }
    }]
  }
], { runActive: true });
return timeline && timeline[0].kind;
"""
    )

    assert result == "todo"


def test_banner_shows_suspended_checklist_without_active_run():
    """等待确认/外部执行时机器没在跑，但清单必须留在界面上。"""
    result = _run(
        """
const timeline = api.resolveBannerTodoTimeline([
  {
    role: 'assistant',
    pendingPermission: { status: 'pending' },
    processTimeline: [{
      kind: 'todo',
      id: 'todo_current',
      title: '任务清单',
      todos: [{ content: '等确认后继续', status: 'in_progress' }],
      counts: { pending: 0, in_progress: 1, completed: 0 }
    }]
  }
], { runActive: false });
return timeline && timeline[0].kind;
"""
    )

    assert result == "todo"


def test_banner_prefers_current_draft_over_history():
    result = _run(
        """
const draft = {
  role: 'assistant',
  processTimeline: [{
    kind: 'todo',
    id: 'todo_current',
    title: '任务清单',
    todos: [{ content: '当前草稿步骤', status: 'in_progress' }],
    counts: { pending: 0, in_progress: 1, completed: 0 }
  }]
};
const timeline = api.resolveBannerTodoTimeline([
  {
    role: 'assistant',
    processTimeline: [{
      kind: 'todo',
      id: 'todo_current',
      title: '任务清单',
      todos: [{ content: '历史步骤', status: 'completed' }],
      counts: { pending: 0, in_progress: 0, completed: 1 },
      settled: true,
      settledStatus: 'success'
    }]
  }
], { runActive: true, draft });
return timeline[0].todos[0].content;
"""
    )

    assert result == "当前草稿步骤"


def test_banner_ignores_history_messages_after_the_last_agent_turn():
    """最后一轮没有清单时，更早轮次的清单不能顶上来。"""
    result = _run(
        """
return api.resolveBannerTodoTimeline([
  {
    role: 'assistant',
    processTimeline: [{
      kind: 'todo',
      id: 'todo_current',
      title: '任务清单',
      todos: [{ content: '上一轮步骤', status: 'in_progress' }],
      counts: { pending: 0, in_progress: 1, completed: 0 }
    }]
  },
  { role: 'user' },
  { role: 'assistant', processTimeline: [] }
], { runActive: true });
"""
    )

    assert result is None


def test_todo_card_renders_settled_state_without_spinner():
    source = TODO_CARD.read_text(encoding="utf-8")

    assert "settled" in source
    assert "已结束" in source
    assert "已中断" in source
    # settled 时默认折叠，且不再渲染「进行中」徽标/旋转图标
    assert "isSettled" in source
    assert "animate-spin" in source


def test_chat_surfaces_use_shared_banner_resolver():
    for path in (EMBED_CHAT, AGENT_DEBUG):
        source = path.read_text(encoding="utf-8")
        assert "resolveBannerTodoTimeline" in source, path.name


EXECUTION_TIMELINE = ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue"


def test_running_checklist_never_enters_execution_timeline():
    """运行中的清单由横幅承载，明细里不再出现（这条曾是 `hideTodo` 开关，现已无条件）。"""
    result = _run(
        """
const items = api.selectTimelineRenderableItems([
  { kind: 'log', id: 'tool_1', title: '检索', status: 'success' },
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '执行步骤', status: 'in_progress' }], counts: { pending: 0, in_progress: 1, completed: 0 } }
]);
return items.map(item => item.kind);
"""
    )

    assert result == ["log"]


def test_unsettled_checklist_in_history_is_hidden_too():
    """历史消息里没有 settled 的清单也不显示。

    用户实测反馈：往回翻到上一次提问的那一轮，执行明细里还挂着「任务清单 2 项未完成」。
    提问轮不会把清单结算成终态（本轮还可能被回执接着跑），所以「按 settled 判断」
    在历史里等于没生效——清单属于任务级概览，历史消息不该再拿它占一行。
    """
    result = _run(
        """
const items = api.selectTimelineRenderableItems([
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '剩余步骤', status: 'pending' }], counts: { total: 3, pending: 2, in_progress: 0, completed: 1 } },
  { kind: 'log', id: 'tool_1', title: '工具完成 · todo_write', status: 'success' },
  { kind: 'log', id: 'tool_2', title: '需要用户回答', status: 'pending' }
]);
return items.map(item => item.kind);
"""
    )

    assert result == ["log", "log"]


def test_settled_checklist_is_hidden_from_execution_timeline():
    """已终结的清单不再进执行明细。

    用户实测反馈：任务跑完了，清单还挂在「执行明细链路」第一条，看着像没收尾。
    清单是**任务级概览**，明细是**过程记录**，两个层级不该混；终结后由输入框上方
    那张卡片负责终态回顾（见 `resolveBannerTodoTimeline`），过程信息里
    `工具完成 · todo_write` 那条仍在，不缺什么。
    """
    result = _run(
        """
const items = api.selectTimelineRenderableItems([
  { kind: 'log', id: 'tool_1', title: '检索', status: 'success' },
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '未完成步骤', status: 'in_progress' }], counts: { pending: 0, in_progress: 1, completed: 0 }, settled: true, settledStatus: 'cancelled' }
]);
return items.map(item => item.kind);
"""
    )

    assert result == ["log"]


def test_settled_checklist_hidden_even_when_banner_serves_other_turn():
    """横幅只服务最后一条回答，但别的轮次终结后也不该把清单留在明细里。

    这正是用户截图那一幕：往上翻到的历史轮次，清单没有横幅接手，于是常驻在
    执行明细第一条，还带着「1 项未完成」。
    """
    result = _run(
        """
const settled = { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '剩余步骤', status: 'pending' }], counts: { total: 2, pending: 1, in_progress: 0, completed: 1 }, settled: true, settledStatus: 'completed' };
const timeline = [settled, { kind: 'log', id: 'tool_1', title: '步骤一', status: 'success' }];
return api.selectTimelineRenderableItems(timeline).map(item => item.kind);
"""
    )

    assert result == ["log"]


def test_execution_timeline_renders_settled_todo_card():
    source = EXECUTION_TIMELINE.read_text(encoding="utf-8")

    assert "selectTimelineRenderableItems" in source
    assert "ChatTodoCard" in source


def test_select_keeps_only_non_todo_items_in_order():
    """明细只留过程记录，且顺序原样不动（清单的增删不影响其它项的相对次序）。"""
    result = _run(
        """
const items = api.selectTimelineRenderableItems([
  { kind: 'log', id: 'tool_1', title: '步骤一', status: 'success' },
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '新步骤', status: 'in_progress' }], counts: { pending: 0, in_progress: 1, completed: 0 } },
  { kind: 'log', id: 'tool_2', title: '步骤二', status: 'success' }
]);
return items.map(item => item.id);
"""
    )

    assert result == ["tool_1", "tool_2"]


def test_checklist_is_not_counted_as_an_execution_step():
    """清单是任务级概览，不该让「执行过程 · N 步骤」多算一步。"""
    result = _run(
        """
return api.countTimelineSteps([
  { kind: 'log', id: 'tool_1', title: '检索', status: 'success' },
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '步骤', status: 'completed' }], counts: { pending: 0, in_progress: 0, completed: 1 }, settled: true, settledStatus: 'success' }
]);
"""
    )

    assert result == 1


def test_multi_todo_snapshots_all_hidden():
    """旧快照可能残留多条 todo：现在一条都不进明细（去重逻辑随之一并失效）。"""
    result = _run(
        """
const items = api.selectTimelineRenderableItems([
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '旧步骤', status: 'in_progress' }], counts: { pending: 0, in_progress: 1, completed: 0 } },
  { kind: 'log', id: 'tool_1', title: '检索', status: 'success' },
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '新步骤', status: 'in_progress' }], counts: { pending: 0, in_progress: 1, completed: 0 } }
]);
return items.map(item => item.kind);
"""
    )

    assert result == ["log"]


def test_settled_checklist_does_not_drive_current_step_label():
    """已终结的清单不该让思考卡 header 继续显示「· 进行中」。"""
    result = _run(
        """
return api.resolveTimelineCurrentStep([
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '未完成步骤', status: 'in_progress' }], counts: { pending: 0, in_progress: 1, completed: 0 }, settled: true, settledStatus: 'cancelled' }
], true);
"""
    )

    assert result == ""


def test_submitted_confirmation_card_does_not_keep_banner_alive():
    """已提交的业务确认卡不该让清单继续常驻 banner。"""
    result = _run(
        """
return api.resolveBannerTodoTimeline([
  {
    role: 'assistant',
    businessConfirmation: { status: 'submitted' },
    processTimeline: [{
      kind: 'todo',
      id: 'todo_current',
      title: '任务清单',
      todos: [{ content: '未完成步骤', status: 'in_progress' }],
      counts: { pending: 0, in_progress: 1, completed: 0 }
    }]
  }
], { runActive: false });
"""
    )

    assert result is None


def test_pending_confirmation_card_keeps_banner_alive():
    result = _run(
        """
const timeline = api.resolveBannerTodoTimeline([
  {
    role: 'assistant',
    businessConfirmation: { status: 'pending' },
    processTimeline: [{
      kind: 'todo',
      id: 'todo_current',
      title: '任务清单',
      todos: [{ content: '等确认后继续', status: 'in_progress' }],
      counts: { pending: 0, in_progress: 1, completed: 0 }
    }]
  }
], { runActive: false });
return timeline && timeline[0].kind;
"""
    )

    assert result == "todo"


def test_archived_card_skips_dismiss_and_progress_badges():
    """归档卡片不能沿用 banner 的「关闭后永久隐藏」语义，也不能谎称进行中。"""
    source = TODO_CARD.read_text(encoding="utf-8")

    assert "archived" in source
    assert "isArchived" in source
    assert "showLiveProgress" in source
