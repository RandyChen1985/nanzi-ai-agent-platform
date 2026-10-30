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
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(MODULE)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
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


def test_running_checklist_is_not_duplicated_inside_execution_timeline():
    """banner 正在展示的当前轮清单，时间线里不再重复渲染。"""
    result = _run(
        """
const items = api.selectTimelineRenderableItems([
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '执行步骤', status: 'in_progress' }], counts: { pending: 0, in_progress: 1, completed: 0 } }
], { hideTodo: true });
return items.map(item => item.kind);
"""
    )

    assert result == []


def test_settled_checklist_yields_to_banner_inside_execution_timeline():
    """终态清单已在横幅常驻时，执行明细不再重复渲染同一张卡。"""
    result = _run(
        """
const items = api.selectTimelineRenderableItems([
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '执行步骤', status: 'completed' }], counts: { pending: 0, in_progress: 0, completed: 1 }, settled: true, settledStatus: 'success' }
], { hideTodo: true });
return items.map(item => item.kind);
"""
    )

    assert result == []


def test_settled_checklist_archives_into_execution_timeline():
    """已终结的清单要能从历史消息里查回来，不能被一并过滤掉。"""
    result = _run(
        """
const items = api.selectTimelineRenderableItems([
  { kind: 'log', id: 'tool_1', title: '检索', status: 'success' },
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '未完成步骤', status: 'in_progress' }], counts: { pending: 0, in_progress: 1, completed: 0 }, settled: true, settledStatus: 'cancelled' }
]);
return items.map(item => item.kind);
"""
    )

    assert result == ["log", "todo"]


def test_execution_timeline_renders_settled_todo_card():
    source = EXECUTION_TIMELINE.read_text(encoding="utf-8")

    assert "selectTimelineRenderableItems" in source
    assert "ChatTodoCard" in source


def test_archived_checklist_is_deduplicated_in_execution_timeline():
    """旧快照可能残留多条 todo 项：归档时只保留最后一份，避免重复卡片与 key 冲突。"""
    result = _run(
        """
const items = api.selectTimelineRenderableItems([
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '旧步骤', status: 'in_progress' }], counts: { pending: 0, in_progress: 1, completed: 0 }, settled: true, settledStatus: 'error' },
  { kind: 'log', id: 'tool_1', title: '检索', status: 'success' },
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '新步骤', status: 'completed' }], counts: { pending: 0, in_progress: 0, completed: 1 }, settled: true, settledStatus: 'success' }
]);
return items.map(item => item.kind === 'todo' ? item.todos[0].content : item.kind);
"""
    )

    assert result == ["log", "新步骤"]


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


def test_unsettled_checklist_still_archives_when_not_the_live_turn():
    """旧快照或提问轮没有 settled：banner 已隐藏，执行过程里必须还能查到。"""
    result = _run(
        """
const items = api.selectTimelineRenderableItems([
  { kind: 'log', id: 'tool_1', title: '检索', status: 'success' },
  { kind: 'todo', id: 'todo_current', title: '任务清单', todos: [{ content: '老数据步骤', status: 'in_progress' }], counts: { pending: 0, in_progress: 1, completed: 0 } }
]);
return items.map(item => item.kind);
"""
    )

    assert result == ["log", "todo"]


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
