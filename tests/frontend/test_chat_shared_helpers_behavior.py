import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _run_typescript(module_path: str, expression: str, require_setup: str | None = None):
    if require_setup is None:
        require_setup = f"""
const path = require('path');
const requireModule = id => {{
  if (id.startsWith('./')) {{
    const dependencyPath = path.resolve(path.dirname({json.dumps(module_path)}), id + '.ts');
    const dependencySource = fs.readFileSync(dependencyPath, 'utf8');
    const dependencyCode = ts.transpileModule(dependencySource, {{
      compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
    }}).outputText;
    const dependencyRef = {{ exports: {{}} }};
    new Function('module', 'exports', 'require', dependencyCode)(dependencyRef, dependencyRef.exports, requireModule);
    return dependencyRef.exports;
  }}
  return require(id);
}};
"""
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(module_path)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
{require_setup}
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, requireModule);
const api = moduleRef.exports;
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


def test_attachment_context_builder_keeps_all_attachment_prompt_variants():
    result = _run_typescript(
        "frontend/src/composables/chat/useChatAttachments.ts",
        """
const workflow = api.useChatAttachments({
  buildKnowledgeBaseAttachmentHint: line => `KNOWLEDGE:${line}`
});
return {
  knowledge: workflow.appendAttachmentContext('问题', [{ type: 'knowledge_base', url: 'kb-1', filename: '知识库' }]),
  image: workflow.appendAttachmentContext('', [{ type: 'local_file', url: '/data/a.png', filename: 'a.png', ext: 'png' }]),
  skill: workflow.appendAttachmentContext('执行', [{ type: 'skill', url: 's1', filename: '分析 (技能)', skillMeta: { name: 'analysis', description: '说明' } }]),
  directory: workflow.appendAttachmentContext('检查', [{ type: 'local_dir', url: '/data/jobs', filename: 'jobs' }])
};
""",
        """
const requireModule = id => {
  if (id === '@/utils/attachmentImages') return {
    getServerAttachmentPath: file => file.type === 'skill' ? `/app/data/skills/${file.url}/SKILL.md` : file.url,
    isImageAttachment: file => file.ext === 'png'
  };
  return require(id);
};
""",
    )

    assert result["knowledge"].startswith("问题\n\n---\n\nKNOWLEDGE:")
    assert "dataset_id：kb-1" in result["knowledge"]
    assert result["image"].startswith("\n\n---\n\n用户本轮已从服务器挂载图片：a.png")
    assert "/data/a.png" in result["image"]
    assert "skills meta 为：name: analysis, description: 说明" in result["skill"]
    assert "服务器本地目录：jobs" in result["directory"]


def test_agentscope_stream_dispatcher_keeps_reasoning_separate_from_answer():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '回答', reasoningContent: '' };
const consumed = api.dispatchAgentscopeStreamEvent(
  msg,
  { type: 'reasoning_content', content: '思考片段' },
  () => {}
);
return { consumed, content: msg.content, reasoningContent: msg.reasoningContent };
""",
    )

    assert result == {
        "consumed": True,
        "content": "回答",
        "reasoningContent": "思考片段",
    }


def test_agentscope_stream_dispatcher_appends_answer_delta_incrementally():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', isThinking: true };
const consumed = api.dispatchAgentscopeStreamEvent(
  msg,
  { type: 'answer_delta', content: '正文片段', phase: 'synthesis' },
  () => {}
);
return { consumed, content: msg.content, isThinking: msg.isThinking };
""",
    )

    assert result == {
        "consumed": True,
        "content": "正文片段",
        "isThinking": False,
    }


def test_run_config_event_sets_message_timeout_snapshot():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '' };
const consumed = api.dispatchAgentscopeStreamEvent(
  msg,
  { type: 'run_config', agent_max_toolcall_timeout: 300 },
  () => {}
);
return { consumed, timeout: msg.agentMaxToolcallTimeoutSeconds };
""",
    )

    assert result == {"consumed": True, "timeout": 300}


def test_stale_pending_watchdog_uses_message_timeout_snapshot():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const makeMessage = () => ({
  content: '',
  agentMaxToolcallTimeoutSeconds: 300,
  logs: [{ id: 'agent-1', title: '主 Agent', details: '', status: 'pending', category: 'agent', started_at: 1000 }],
});
const before = makeMessage();
const beforeChanged = api.markStalePendingStreamLogs(before, 300999);
const after = makeMessage();
const afterChanged = api.markStalePendingStreamLogs(after, 301000);
return {
  beforeChanged,
  beforeStatus: before.logs[0].status,
  afterChanged,
  afterStatus: after.logs[0].status,
};
""",
    )

    assert result == {
        "beforeChanged": False,
        "beforeStatus": "pending",
        "afterChanged": True,
        "afterStatus": "error",
    }


def test_stale_pending_watchdog_falls_back_to_180_seconds_without_snapshot():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = {
  content: '',
  logs: [{ id: 'agent-1', title: '主 Agent', details: '', status: 'pending', category: 'agent', started_at: 1000 }],
};
const beforeChanged = api.markStalePendingStreamLogs(msg, 180999);
const afterChanged = api.markStalePendingStreamLogs(msg, 181000);
return { beforeChanged, afterChanged, status: msg.logs[0].status };
""",
    )

    assert result == {
        "beforeChanged": False,
        "afterChanged": True,
        "status": "error",
    }


def test_agentscope_stream_dispatcher_keeps_todo_as_a_timeline_sibling():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', processTimeline: [] };
const consumed = api.dispatchAgentscopeStreamEvent(
  msg,
  { type: 'todo_update', todos: [
    { content: '检索知识库', status: 'in_progress' },
    { content: '整理答案', status: 'pending' },
  ] },
  () => {}
);
return {
  consumed,
  kinds: msg.processTimeline.map(item => item.kind),
  todo: msg.processTimeline[0],
};
""",
    )

    assert result["consumed"] is True
    assert result["kinds"] == ["todo"]
    assert result["todo"]["counts"] == {"pending": 1, "in_progress": 1, "completed": 0}


def test_agentscope_stream_dispatcher_clears_todo_card_on_empty_update():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', processTimeline: [] };
api.dispatchAgentscopeStreamEvent(msg, {
  type: 'todo_update',
  todos: [{ content: '执行任务', status: 'in_progress' }],
}, () => {});
api.dispatchAgentscopeStreamEvent(msg, { type: 'todo_update', todos: [] }, () => {});
return msg.processTimeline;
""",
    )

    assert result == []


def test_process_narration_handler_is_shared_across_chat_surfaces():
    handlers = (ROOT / "frontend/src/utils/agentscopeSseHandlers.ts").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    assert "export function applyProcessNarrationEvent" in handlers
    assert "export function collapseSecondaryFoldsOnBody" in handlers
    assert "export function appendAssistantBodyDelta" in handlers
    assert "export function isDuplicateAssistantBodyDelta" in handlers
    assert "msg.isProcessNarrationExpanded = false" in handlers
    assert "msg.isReasoningExpanded = false" in handlers
    assert "msg.isThoughtExpanded = false" in handlers
    assert "appendAssistantBodyDelta" in embed
    assert "appendAssistantBodyDelta" in debug
    assert 'case "process_narration":' in handlers
    assert 'case "process_narration_commit":' in handlers
    assert 'case "process_narration_promote":' in handlers
    assert 'case "retraction":' in handlers
    assert "data.final === false" in handlers
    assert "msg.processNarrationPending" in handlers
    assert "msg.isThinking = false" in handlers
    assert "<ChatExecutionTimeline" in embed
    assert "<ChatExecutionTimeline" in debug
    assert "processTimeline" in handlers
    assert "syncProcessTimelineLog" in embed
    assert "syncProcessTimelineLog" in debug
    timeline = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")
    assert ':title="headerTitle"' in timeline
    assert '"执行完成"' in timeline
    assert "resolveTimelineCurrentStep" in timeline
    assert "timelineHasPending" in timeline
    assert "item.kind === 'text'" in timeline
    assert "item.status === 'pending'" in timeline
    assert "process_narration_promote" in embed
    assert "process_narration_promote" in debug
    assert ':skill-badges="getSkillFlowBadgesForMessage(msg, messages)"' in embed
    assert ':skill-badges="getSkillFlowBadgesForMessage(msg, messages)"' in debug
    assert "skillNoticeLabel" in timeline
    assert "深度思考" in timeline
    assert "item.textKind === 'reasoning'" in timeline
    assert "CpuChipIcon" in timeline
    assert "isReasoningBodyOpen" in timeline
    assert "isReasoningContentExpanded" in timeline
    assert "formatDuration(item.execution_time_ms)" in timeline
    assert "item.textKind === 'reasoning' ? '🧠'" not in timeline
    assert "text-violet-500" not in timeline
    assert "<blockquote" in timeline
    assert "border-l-2 border-gray-200" in timeline
    # iconFor 仍服务于复制文本序列化，界面渲染由 WrenchScrewdriverIcon 接管。
    assert "🔧" in timeline
    assert "WrenchScrewdriverIcon" in timeline
    assert "isToolTimelineItem" in timeline
    assert 'v-if="isToolTimelineItem(child)"' in timeline
    assert 'if (item.subagent || item.status === "error" || item.category === "tool_resolution") return false;' in timeline
    assert "🛠️" not in timeline
    assert ".thought-status-dot" in timeline
    assert "shrink-0 truncate rounded-full border border-purple-100 bg-purple-50" in (
        ROOT / "frontend/src/components/chat/ChatThinkingHeader.vue"
    ).read_text(encoding="utf-8")


def test_todo_card_follows_thought_timeline_on_both_chat_surfaces():
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    for source in (embed, debug):
        timeline_at = source.find("<ChatExecutionTimeline")
        todo_at = source.find("<ChatTodoCard")
        assert timeline_at >= 0
        assert todo_at > timeline_at


def test_todo_card_supports_collapse_close_and_auto_collapse_when_completed():
    source = (ROOT / "frontend/src/components/chat/ChatTodoCard.vue").read_text(encoding="utf-8")

    assert "ref(true)" in source
    assert "aria-expanded" in source
    assert "折叠任务清单" in source
    assert "展开任务清单" in source
    assert "关闭任务清单" in source
    assert "watch(" in source
    assert "todo.value.counts" in source
    assert "todo.value.counts?.completed === todo.value.todos.length" in source


def test_embed_chat_treats_every_parsed_sse_event_as_stream_activity():
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert 'if (dataStr === "[DONE]") {\n          isChatStreamDone = true;\n          break;\n        }' in embed
    assert "// Any SSE data frame means the stream is alive" in embed
    assert "if (dataStr === \"[DONE]\") continue;\n      resetStallTimer();\n      try" in embed


def test_execution_timeline_uses_readable_width_cap_instead_of_full_column():
    timeline = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")
    header = (ROOT / "frontend/src/components/chat/ChatThinkingHeader.vue").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    assert "max-w-[42rem]" in timeline
    assert "lg:max-w-[48rem]" in timeline
    assert "2xl:max-w-[52rem]" in timeline
    assert "max-w-[90%]" not in timeline
    assert "bg-gray-50/90" not in timeline
    assert "dark:bg-gray-800/80" not in timeline
    assert 'class="min-w-0 flex-1 truncate text-[10px] font-normal text-gray-400"' in header
    assert "visibleStreamBody(msg) || msg.groundingBlocked || msg.businessConfirmation" in embed
    assert "'min-h-0 bg-transparent'" in embed
    assert "msg.content || (msg.citations && msg.citations.length) || msg.chatbiInsight" in debug
    assert "'overflow-visible bg-transparent'" in debug
    assert "w-full max-w-[90%] min-w-0" in embed
    assert "flex space-x-3 items-start max-w-[90%]" in debug


def test_execution_timeline_uses_compact_nested_step_spacing():
    timeline = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")

    assert 'class="mt-0.5 space-y-0.5 px-1 py-0.5"' in timeline
    assert "overflow-y-auto" not in timeline
    assert "max-h-[min(520px,60vh)]" not in timeline
    assert 'class="rounded-md px-1 py-0.5 text-[12px] leading-5' in timeline
    assert 'class="ml-5 mt-0.5 border-l' in timeline
    assert 'class="mb-0 flex items-center gap-1 text-[10px]' in timeline
    assert 'class="space-y-0"' in timeline
    assert 'class="rounded-md px-1 py-0.5 text-[11px]' in timeline


def test_execution_timeline_pending_rows_use_indicator_without_full_row_background():
    timeline = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")

    assert "'border-l-2 border-primary/60': child.status === 'pending'" not in timeline
    assert "'border-l-2 border-primary/60': item.status === 'pending'" not in timeline
    assert "bg-primary/[0.05]" not in timeline
    assert "dark:bg-primary/10" not in timeline


def test_execution_timeline_pending_indicator_precedes_content_and_action_rows_align_duration():
    timeline = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")

    assert timeline.count('class="flex w-full items-center gap-2 text-left"') >= 4
    assert 'class="thought-status-dot shrink-0"' in timeline
    assert timeline.index('class="thought-status-dot shrink-0"') < timeline.index('class="min-w-0 flex-1 truncate"')
    assert 'class="w-fit max-w-full whitespace-pre-wrap break-words font-sans"' in timeline


def test_execution_timeline_does_not_use_thought_shimmer():
    timeline = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")
    process_timeline = (ROOT / "frontend/src/utils/processTimeline.ts").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    assert "thought-shimmer-text" not in timeline
    assert "shouldShowThoughtShimmer" not in process_timeline
    assert "thought-shimmer-text" not in embed
    assert "thought-shimmer-text" not in debug


def test_embed_chat_pending_log_rows_do_not_use_blue_active_border():
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "border-l-2 border-primary/55" not in embed


def test_process_narration_does_not_enter_the_body_until_promote():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', processTimeline: [], isThinking: true };
api.dispatchAgentscopeStreamEvent(msg, { type: 'process_narration', content: '我先查询。' }, () => {});
const mid = { content: msg.content, pending: msg.processTimeline[0]?.pending, isThinking: msg.isThinking };
api.dispatchAgentscopeStreamEvent(msg, { type: 'process_narration_promote', content: '我先查询。' }, () => {});
return {
  mid,
  content: msg.content,
  timeline: msg.processTimeline,
  isThinking: msg.isThinking,
};
""",
    )

    assert result["mid"] == {"content": "", "pending": True, "isThinking": True}
    assert result["content"] == "我先查询。"
    assert result["timeline"] == []
    assert result["isThinking"] is False


def test_candidate_answer_delta_can_be_retracted_without_finishing_the_stream():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', processTimeline: [], isThinking: true };
api.dispatchAgentscopeStreamEvent(msg, { type: 'answer_delta', content: '候选正文', phase: 'candidate' }, () => {});
api.dispatchAgentscopeStreamEvent(msg, { type: 'retraction', content: '', final: false }, () => {});
return { content: msg.content, isThinking: msg.isThinking, timeline: msg.processTimeline };
""",
    )

    assert result == {"content": "", "isThinking": True, "timeline": []}


def test_embed_chat_invalidates_raf_buffer_before_dispatching_retraction():
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    handler = embed.index("const handleBufferedBodyEvent")
    retraction = embed.index('if (data.type === "retraction")', handler)
    dispatcher = embed.index(
        "dispatchAgentscopeStreamEvent(agentMsg.value, data",
        handler,
    )

    assert "pendingContentBuffer = \"\"" in embed[retraction:dispatcher]
    assert "cancelAnimationFrame(contentRafId)" in embed[retraction:dispatcher]
    assert embed.index("if (handleBufferedBodyEvent(data))", handler) < dispatcher


def test_process_narration_from_parallel_agents_stays_on_separate_timeline_items():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', processTimeline: [] };
api.dispatchAgentscopeStreamEvent(msg, { type: 'process_narration', content: '先查营收', agent_name: 'PrimaryAgent' }, () => {});
api.dispatchAgentscopeStreamEvent(msg, { type: 'process_narration', content: '先检索手册', agent_name: 'SecondaryAgent' }, () => {});
api.dispatchAgentscopeStreamEvent(msg, { type: 'process_narration_commit', content: '先查营收', agent_name: 'PrimaryAgent' }, () => {});
return msg.processTimeline.map((item) => ({
  content: item.content,
  pending: item.pending,
  sourceId: item.sourceId,
}));
""",
    )

    assert result == [
        {"content": "先查营收", "pending": False, "sourceId": "PrimaryAgent"},
        {"content": "先检索手册", "pending": True, "sourceId": "SecondaryAgent"},
    ]


def test_thinking_end_finishes_pending_reasoning_in_shared_dispatcher():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', processTimeline: [] };
api.dispatchAgentscopeStreamEvent(msg, { type: 'reasoning_content', content: '内部推理' }, () => {});
api.dispatchAgentscopeStreamEvent(msg, { type: 'thinking', phase: 'end' }, () => {});
return msg.processTimeline;
""",
    )

    assert result[0]["textKind"] == "reasoning"
    assert result[0]["pending"] is False
    assert result[0]["execution_time_ms"] >= 1


def test_agent_debug_routes_thinking_through_shared_dispatcher():
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")
    main_loop = debug.split("const sendMessage")[-1].split("const applyPermissionStreamEvent")[0]
    thinking_before_dispatch = main_loop.find('else if (data.type === "thinking")')
    dispatch_idx = main_loop.find("dispatchAgentscopeStreamEvent(agentMsg.value")
    assert dispatch_idx >= 0
    assert thinking_before_dispatch == -1 or thinking_before_dispatch > dispatch_idx


def test_process_narration_promote_removes_candidate_from_message_and_timeline():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', processTimeline: [] };
api.applyProcessNarrationEvent(msg, { type: 'process_narration', content: '你好，我先介绍一下能力。' });
api.applyProcessNarrationEvent(msg, { type: 'process_narration_promote', content: '你好，我先介绍一下能力。' });
return {
  content: msg.content,
  processNarration: msg.processNarration || '',
  processNarrationPending: msg.processNarrationPending || '',
  timeline: msg.processTimeline
};
""",
    )

    assert result == {
        "content": "你好，我先介绍一下能力。",
        "processNarration": "",
        "processNarrationPending": "",
        "timeline": [],
    }


def test_promote_with_leading_newline_removes_candidate_when_body_already_started():
    """正文以 \\n\\n 开头且与文字同 chunk 时，promote 也应删掉时间线候选，避免整段正文重复展示。"""
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '上一轮正文', processTimeline: [] };
api.applyProcessNarrationEvent(msg, { type: 'process_narration', content: '\\n\\n最终报告第一段' });
api.applyProcessNarrationEvent(msg, { type: 'process_narration', content: '第二段' });
api.applyProcessNarrationEvent(msg, { type: 'process_narration_promote', content: '\\n\\n最终报告第一段第二段' });
return msg.processTimeline;
""",
    )

    assert result == []


def test_promote_with_crlf_removes_candidate_when_body_already_started():
    """正文含 \\r\\n 换行时，promote 也应删掉时间线候选，避免与正文气泡重复。"""
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '上一轮正文', processTimeline: [] };
api.applyProcessNarrationEvent(msg, { type: 'process_narration', content: '最终报告\\r\\n第一段' });
api.applyProcessNarrationEvent(msg, { type: 'process_narration_promote', content: '最终报告\\r\\n第一段' });
return msg.processTimeline;
""",
    )

    assert result == []


def test_process_narration_collapses_blank_lines_without_touching_answer_text():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', processTimeline: [] };
api.applyProcessNarrationEvent(msg, { type: 'process_narration', content: '\\n\\n\\n我先查询。\\n\\n' });
api.applyProcessNarrationEvent(msg, { type: 'process_narration', content: '\\n\\n然后整理结果。\\n\\n\\n' });
api.applyProcessNarrationEvent(msg, { type: 'process_narration_commit', content: '\\n\\n\\n我先查询。\\n\\n然后整理结果。\\n\\n\\n' });
return {
  narration: msg.processNarration,
  pending: msg.processNarrationPending,
  timeline: msg.processTimeline,
  answer: msg.content,
};
""",
    )

    assert result["narration"] == "我先查询。\n\n然后整理结果。"
    assert result["pending"] == ""
    assert result["timeline"][0]["content"] == "我先查询。\n\n然后整理结果。"
    assert result["answer"] == ""


def test_plain_answer_delta_discards_only_uncommitted_narration_candidate():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const noTool = { content: '', processTimeline: [] };
api.applyProcessNarrationEvent(noTool, { type: 'process_narration', content: '你好。' });
api.appendAssistantBodyDelta(noTool, '你好。');

const withTool = { content: '', processTimeline: [] };
api.applyProcessNarrationEvent(withTool, { type: 'process_narration', content: '我先查询。' });
api.applyProcessNarrationEvent(withTool, { type: 'process_narration_commit', content: '我先查询。' });
api.appendAssistantBodyDelta(withTool, '最终结果');

return {
  noTool: noTool.processTimeline,
  withTool: withTool.processTimeline,
};
""",
    )

    assert result["noTool"] == []
    assert result["withTool"][0]["content"] == "我先查询。"
    assert result["withTool"][0]["pending"] is False


def test_append_assistant_body_delta_skips_replayed_full_answer():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const intro = '好的，根据已获取的公开信息，为您整理晋景新能 (01783.HK) 的最新情况。';
const body = intro + '\\n\\n' + '正文段落。'.repeat(12);
const msg = { content: body, processTimeline: [] };
api.appendAssistantBodyDelta(msg, 'Let me fetch detailed reports.' + body.replace(/\\n\\n/g, ''));
api.appendAssistantBodyDelta(msg, '补充一句新结论。');
const incremental = { content: intro, processTimeline: [] };
api.appendAssistantBodyDelta(incremental, '继续往下写。');
return {
  skippedReplay: msg.content,
  incremental: incremental.content,
};
""",
    )

    assert result["skippedReplay"].startswith("好的，根据已获取的公开信息")
    assert result["skippedReplay"].count("好的，根据已获取的公开信息") == 1
    assert result["skippedReplay"].endswith("补充一句新结论。")
    assert result["incremental"] == "好的，根据已获取的公开信息，为您整理晋景新能 (01783.HK) 的最新情况。继续往下写。"


def test_append_assistant_body_delta_keeps_unique_suffix_when_whitespace_differs():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const streamed = 'Hello world. More text here that is long.';
const msg = { content: streamed, processTimeline: [] };
api.appendAssistantBodyDelta(msg, 'Hello world.\\nMore text here that is long. UNIQUE CONCLUSION.');
return msg.content;
""",
    )

    assert result.startswith("Hello world.")
    assert result.count("Hello world.") == 1
    assert result.endswith("UNIQUE CONCLUSION.")


def test_process_timeline_keeps_narration_and_tool_order_and_updates_tool_details():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = {};
api.appendTimelineNarrationDelta(target, '先查时间');
api.commitTimelineNarration(target, '先查时间');
api.upsertTimelineLog(target, { id: 'tool-1', title: '调用工具: time', status: 'pending', category: 'tool' });
api.upsertTimelineLog(target, { id: 'tool-1', details: '结果: 12:00', status: 'success' });
api.appendTimelineNarrationDelta(target, '再整理结果');
return target.processTimeline;
""",
    )

    assert [item["kind"] for item in result] == ["text", "text"]
    assert result[0]["content"] == "先查时间"
    assert result[0]["children"][0]["details"] == "结果: 12:00"
    assert result[0]["children"][0]["status"] == "success"
    assert result[1]["content"] == "再整理结果"


def test_process_timeline_groups_following_tools_under_committed_narration():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = {};
api.appendTimelineNarrationDelta(target, '我先查看负载。');
api.commitTimelineNarration(target, '我先查看负载。');
api.upsertTimelineLog(target, { id: 'bash', title: '工具完成: Bash', details: '', status: 'success', category: 'tool' });
api.upsertTimelineLog(target, { id: 'process', title: '工具完成: list_process', details: '', status: 'success', category: 'tool' });
api.appendTimelineNarrationDelta(target, '我已拿到数据。');
api.commitTimelineNarration(target, '我已拿到数据。');
api.upsertTimelineLog(target, { id: 'top-level', title: '模型调用: DeepSeek', details: '', status: 'success', category: 'model' });
return target.processTimeline;
""",
    )

    assert result[0]["kind"] == "text"
    assert [child["id"] for child in result[0]["children"]] == ["bash", "process"]
    assert result[1]["kind"] == "text"
    assert result[1]["children"] == []
    assert result[2]["kind"] == "log"


def test_process_timeline_keeps_tool_at_top_level_without_committed_narration():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = {};
api.upsertTimelineLog(target, { id: 'tool-1', title: '工具完成: Bash', details: '', status: 'success', category: 'tool' });
return target.processTimeline;
""",
    )

    assert result == [{
        "kind": "log",
        "id": "tool-1",
        "title": "工具完成: Bash",
        "details": "",
        "status": "success",
        "category": "tool",
        "isExpanded": False,
        "children": [],
        "childrenExpanded": True,
    }]


def test_hydrate_history_process_timeline_drops_pending_and_appends_reasoning():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
return api.hydrateHistoryProcessTimeline(
  [
    { kind: 'text', id: 'n1', textKind: 'narration', content: '我先搜一下', pending: false, children: [] },
    { kind: 'text', id: 'n2', textKind: 'narration', content: '最终报告', pending: true },
    { kind: 'log', id: 'tool-1', title: '调用工具: search', details: 'ok', status: 'success', category: 'tool' },
  ],
  '深度思考正文',
);
""",
    )

    kinds = [(item["kind"], item.get("textKind"), item.get("pending")) for item in result]
    assert ("text", "narration", True) not in kinds
    assert result[0]["content"] == "我先搜一下"
    assert result[-1]["textKind"] == "reasoning"
    assert result[-1]["content"] == "深度思考正文"
    assert result[-1]["pending"] is False


def test_process_timeline_backfills_logs_received_outside_timeline():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = { processTimeline: [
  { kind: 'text', id: 'n1', textKind: 'narration', content: '先查一下', pending: false },
  { kind: 'log', id: 'tool-1', title: '工具完成: search', details: '', status: 'success' }
] };
const merged = api.mergeTimelineLogs(target.processTimeline, [
  { id: 'tool-1', title: '工具完成: search', details: '', status: 'success' },
  { id: 'model-2', title: '模型调用: DeepSeek', details: '', status: 'pending' }
]);
return merged;
""",
    )

    assert [item["id"] for item in result] == ["n1", "tool-1", "model-2"]


def test_process_timeline_groups_route_stages_under_resolution_parent():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const items = [
  { kind: 'log', id: 'route:target_config', title: '加载目标专家配置', status: 'success', execution_time_ms: 5900 },
  { kind: 'log', id: 'route:target_selection', title: '判断并匹配目标专家', status: 'success', execution_time_ms: 5900 },
  { kind: 'log', id: 'route:candidate_catalog', title: '获取可用专家', status: 'success', execution_time_ms: 1 },
  { kind: 'log', id: 'route:knowledge_catalog', title: '准备知识资源范围', status: 'success', execution_time_ms: 12 },
  { kind: 'log', id: 'route:router_model', title: '匹配目标专家', status: 'success', execution_time_ms: 5900 },
  { kind: 'log', id: 'route:target_permission', title: '校验目标专家权限', status: 'success', execution_time_ms: 1 },
  { kind: 'log', id: 'agent_reply_1', title: 'Agent 回复开始', status: 'success' },
];
return api.groupRouteTimelineItems(items);
""",
    )

    assert [item["id"] for item in result] == ["route:target_config", "agent_reply_1"]
    assert [child["id"] for child in result[0]["children"]] == [
        "route:target_selection",
        "route:candidate_catalog",
        "route:knowledge_catalog",
        "route:router_model",
        "route:target_permission",
    ]


def test_process_timeline_preserves_route_group_collapsed_state():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const items = [
  { kind: 'log', id: 'route:target_config', title: '加载目标专家配置', status: 'success' },
  { kind: 'log', id: 'route:target_selection', title: '判断并匹配目标专家', status: 'success' },
];
return api.groupRouteTimelineItems(items, false)[0].childrenExpanded;
""",
    )

    assert result is False


def test_process_timeline_drops_legacy_router_duplicate_when_target_selection_exists():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const items = [
  { kind: 'log', id: 'route:target_config', title: '加载目标专家配置', status: 'success' },
  { kind: 'log', id: 'route:target_selection', title: '判断并匹配目标专家', status: 'success' },
  { kind: 'log', id: 'router_7', title: '智能路由决策', status: 'success' },
];
return api.groupRouteTimelineItems(items).map((item) => item.id);
""",
    )

    assert result == ["route:target_config"]


def test_process_timeline_step_count_excludes_hidden_legacy_router_duplicate():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const items = [
  { kind: 'log', id: 'route:target_config', title: '加载目标专家配置', status: 'success' },
  { kind: 'log', id: 'route:target_selection', title: '判断并匹配目标专家', status: 'success' },
  { kind: 'log', id: 'router_7', title: '智能路由决策', status: 'success' },
];
const grouped = api.groupRouteTimelineItems(items);
return api.countTimelineSteps(grouped);
""",
    )

    assert result == 2


def test_process_timeline_finishes_reasoning_even_when_narration_is_the_last_item():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = {};
api.appendTimelineReasoningDelta(target, '思考中');
api.appendTimelineNarrationDelta(target, '开始执行');
api.finishTimelineReasoning(target);
return target.processTimeline;
""",
    )

    assert result[0]["textKind"] == "reasoning"
    assert result[0]["pending"] is False
    assert result[0]["execution_time_ms"] >= 1
    assert result[1]["textKind"] == "narration"


def test_reasoning_duration_stops_when_process_narration_starts():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = {};
api.appendTimelineReasoningDelta(target, '内部推理');
api.appendTimelineNarrationDelta(target, '开始执行');
return target.processTimeline;
""",
    )

    assert result[0]["textKind"] == "reasoning"
    assert result[0]["pending"] is False
    assert result[0]["execution_time_ms"] >= 1
    assert result[1]["textKind"] == "narration"
    assert result[1]["pending"] is True


def test_reasoning_content_stays_open_while_pending_and_collapses_when_finished():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = {};
api.appendTimelineReasoningDelta(target, '内部推理');
const pending = target.processTimeline[0];
const pendingOpen = api.isReasoningContentExpanded(pending);
api.finishTimelineReasoning(target);
const finishedClosed = api.isReasoningContentExpanded(pending);
pending.contentExpanded = true;
const forcedOpen = api.isReasoningContentExpanded(pending);
return { pendingOpen, finishedClosed, forcedOpen };
""",
    )

    assert result == {
        "pendingOpen": True,
        "finishedClosed": False,
        "forcedOpen": True,
    }


def test_process_timeline_promotes_narration_when_parallel_log_is_interleaved():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = {};
api.appendTimelineNarrationDelta(target, '你好');
api.upsertTimelineLog(target, { id: 'other-agent-log', title: '其他 Agent', status: 'success' });
api.promoteTimelineNarration(target, '你好');
return target.processTimeline;
""",
    )

    assert [item["kind"] for item in result] == ["log"]


def test_process_timeline_keeps_parallel_agent_narration_on_separate_items():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = {};
api.appendTimelineNarrationDelta(target, '先查营收', 'PrimaryAgent');
api.appendTimelineNarrationDelta(target, '先检索手册', 'SecondaryAgent');
api.appendTimelineNarrationDelta(target, '，再对比去年', 'PrimaryAgent');
api.commitTimelineNarration(target, '先查营收，再对比去年', 'PrimaryAgent');
return target.processTimeline.map((item) => ({
  kind: item.kind,
  content: item.content,
  pending: item.pending,
  sourceId: item.sourceId,
  sourceLabel: item.sourceLabel,
}));
""",
    )

    assert result == [
        {
            "kind": "text",
            "content": "先查营收，再对比去年",
            "pending": False,
            "sourceId": "PrimaryAgent",
            "sourceLabel": "PrimaryAgent",
        },
        {
            "kind": "text",
            "content": "先检索手册",
            "pending": True,
            "sourceId": "SecondaryAgent",
            "sourceLabel": "SecondaryAgent",
        },
    ]


def test_process_timeline_does_not_nest_permission_logs_under_narration():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = {};
api.appendTimelineNarrationDelta(target, '我先调用工具。');
api.commitTimelineNarration(target, '我先调用工具。');
api.upsertTimelineLog(target, {
  id: 'permission_1',
  title: '工具调用需要确认',
  details: '参数: {}',
  status: 'pending',
  category: 'permission',
});
api.upsertTimelineLog(target, {
  id: 'external_1',
  title: '需要客户端执行工具',
  details: '参数: {}',
  status: 'pending',
  category: 'external',
});
return target.processTimeline.map((item) => ({
  kind: item.kind,
  id: item.id,
  category: item.category,
  children: (item.children || []).map((child) => child.id),
}));
""",
    )

    assert result[0]["kind"] == "text"
    assert result[0]["children"] == []
    assert [item["id"] for item in result[1:]] == ["permission_1", "external_1"]
    assert [item["category"] for item in result[1:]] == ["permission", "external"]


def test_process_timeline_resolves_current_step_for_collapsed_header():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const pending = api.resolveTimelineCurrentStep([
  { kind: 'text', id: 'n1', textKind: 'narration', content: '准备搜索', pending: true },
  { kind: 'log', id: 'tool-1', title: '调用工具: search', details: '关键词', status: 'pending' }
], true);
const finished = api.resolveTimelineCurrentStep([
  { kind: 'log', id: 'tool-1', title: '工具完成: search', details: '', status: 'success' }
], false);
return { pending, finished };
""",
    )

    assert result == {"pending": "调用工具 · search · 进行中", "finished": ""}


def test_process_timeline_counts_nested_pending_tools_as_in_progress():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const committedWithChild = [
  {
    kind: 'text',
    id: 'n1',
    textKind: 'narration',
    content: '我先搜索。',
    pending: false,
    children: [{ kind: 'log', id: 'tool-1', title: '调用工具: search', details: '', status: 'pending' }],
  },
];
const idle = [
  {
    kind: 'text',
    id: 'n1',
    textKind: 'narration',
    content: '我先搜索。',
    pending: false,
    children: [{ kind: 'log', id: 'tool-1', title: '工具完成: search', details: '', status: 'success' }],
  },
];
return {
  nestedPending: api.timelineHasPending(committedWithChild),
  nestedStep: api.resolveTimelineCurrentStep(committedWithChild, api.timelineHasPending(committedWithChild)),
  idle: api.timelineHasPending(idle),
};
""",
    )

    assert result == {
        "nestedPending": True,
        "nestedStep": "调用工具 · search · 进行中",
        "idle": False,
    }


def test_process_timeline_ignores_whitespace_only_narration_delta():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const target = {};
api.appendTimelineNarrationDelta(target, ' \\n\\t\\u200b');
return target.processTimeline || [];
""",
    )

    assert result == []


def test_workspace_canvas_keeps_workspace_toggle_and_debug_title_normalization():
    result = _run_typescript(
        "frontend/src/composables/chat/useWorkspaceCanvas.ts",
        """
const canvas = api.useWorkspaceCanvas({
  getConversationId: () => 'conv-1',
  resolveFileUrl: value => value,
  showToast: () => {},
  normalizeDirectPayloadTitle: true
});
await canvas.handleWorkspaceFilePreview({ path: '/workspace/a.md', name: 'a.md' });
const firstOpen = {
  visible: canvas.canvasVisible.value,
  title: canvas.canvasData.value.title,
  pinned: canvas.canvasPinned.value
};
canvas.canvasPinned.value = false;
const manuallyUnpinned = canvas.canvasPinned.value;
await canvas.handleWorkspaceFilePreview({ path: '/workspace/a.md', name: 'a.md' });
const toggledClosed = canvas.canvasVisible.value;
await canvas.handleOpenCanvas({ type: 'html', title: '', content: '<p>x</p>', sourcePath: '/workspace/a.md' });
return {
  firstOpen,
  manuallyUnpinned,
  toggledClosed,
  direct: canvas.canvasData.value,
  reopenedPinned: canvas.canvasPinned.value
};
""",
        """
const ref = initial => {
  let current = initial;
  const target = { watchers: [] };
  Object.defineProperty(target, 'value', {
    get: () => current,
    set: value => { current = value; target.watchers.forEach(watcher => watcher(value)); }
  });
  return target;
};
const requireModule = id => {
  if (id === 'vue') return { ref, watch: (target, callback) => target.watchers.push(callback), onUnmounted: () => {} };
  if (id === '@/utils/axios') return { default: { get: async () => ({ data: '' }) } };
  if (id === '@/utils/workspaceFilePreview') return {
    isSameWorkspacePreviewPath: (left, right) => left === right,
    shouldAttachWorkspaceSourcePath: () => true,
    openWorkspaceFileInCanvas: async options => options.onOpen({ type: 'code', title: options.name, content: 'preview' })
  };
  return require(id);
};
""",
    )

    assert result["firstOpen"] == {"visible": True, "title": "a.md", "pinned": True}
    assert result["manuallyUnpinned"] is False
    assert result["toggledClosed"] is False
    assert result["direct"] == {"type": "html", "title": "文件预览", "content": "<p>x</p>"}
    assert result["reopenedPinned"] is True


def test_workspace_canvas_mobile_keeps_workspace_and_skips_pin():
    result = _run_typescript(
        "frontend/src/composables/chat/useWorkspaceCanvas.ts",
        """
const canvas = api.useWorkspaceCanvas({
  getConversationId: () => 'conv-1',
  resolveFileUrl: value => value,
  showToast: () => {},
  isMobile: () => true
});
await canvas.handleWorkspaceFilePreview({ path: '/workspace/b.html', name: 'b.html' });
return {
  visible: canvas.canvasVisible.value,
  pinned: canvas.canvasPinned.value,
  fromWorkspace: canvas.canvasFromWorkspace.value
};
""",
        """
const ref = initial => {
  let current = initial;
  const target = { watchers: [] };
  Object.defineProperty(target, 'value', {
    get: () => current,
    set: value => { current = value; target.watchers.forEach(watcher => watcher(value)); }
  });
  return target;
};
const requireModule = id => {
  if (id === 'vue') return { ref, watch: (target, callback) => target.watchers.push(callback), onUnmounted: () => {} };
  if (id === '@/utils/axios') return { default: { get: async () => ({ data: '' }) } };
  if (id === '@/utils/workspaceFilePreview') return {
    isSameWorkspacePreviewPath: (left, right) => left === right,
    shouldAttachWorkspaceSourcePath: () => true,
    openWorkspaceFileInCanvas: async options => options.onOpen({ type: 'html', title: options.name, content: '<p>x</p>' })
  };
  return require(id);
};
""",
    )

    assert result == {
        "visible": True,
        "pinned": False,
        "fromWorkspace": True,
    }


def test_saved_report_shared_helpers_keep_parameter_and_error_behavior():
    result = _run_typescript(
        "frontend/src/composables/chat/useSavedReportWorkflow.ts",
        """
return {
  dateParams: api.buildSavedReportRunParams(
    { params_schema: [{ name: 'date_range', type: 'date_range' }] },
    { dateRange: 'custom_range', startDate: '2026-07-01', endDate: '2026-07-15', monthRange: '', startMonth: '', endMonth: '' }
  ),
  monthParams: api.buildSavedReportRunParams(
    { params_schema: [{ name: 'month_range', type: 'month_range' }] },
    { dateRange: '', startDate: '', endDate: '', monthRange: 'custom_month_range', startMonth: '2026-01', endMonth: '2026-06' }
  ),
  tags: api.parseSavedReportTags('经营, 异常，经营 重点'),
  permissionError: api.extractSavedReportExecuteErrorMessage({ response: { status: 403 } }),
  markdown: api.renderSavedReportDataToMarkdown({ rows: [{ name: 'A|B', value: 3 }] })
};
""",
    )

    assert result["dateParams"] == {
        "date_range": "custom_range",
        "start_date": "2026-07-01",
        "end_date": "2026-07-15",
    }
    assert result["monthParams"] == {
        "month_range": "custom_month_range",
        "start_month": "2026-01",
        "end_month": "2026-06",
    }
    assert result["tags"] == ["经营", "异常", "重点"]
    assert "暂无该报表所需数据权限" in result["permissionError"]
    assert "A\\|B" in result["markdown"]


def test_saved_report_shared_helpers_keep_custom_runtime_parameters():
    result = _run_typescript(
        "frontend/src/composables/chat/useSavedReportWorkflow.ts",
        """
return api.buildSavedReportRunParams(
  { params_schema: [
    { name: 'department', type: 'text' },
    { name: 'min_amount', type: 'number' },
    { name: 'region', type: 'select', options: ['华东', '华南'] }
  ] },
  { dateRange: '', startDate: '', endDate: '', monthRange: '', startMonth: '', endMonth: '', customParams: { department: '研发部', min_amount: 100, region: '华东' } }
);
""",
    )

    assert result == {"department": "研发部", "min_amount": 100, "region": "华东"}


def test_history_date_grouping_keeps_existing_boundaries_and_order():
    result = _run_typescript(
        "frontend/src/composables/chat/useChatHistoryGroups.ts",
        """
const now = new Date('2026-07-15T12:00:00');
const items = [
  { id: 'today', created_at: '2026-07-15T10:00:00' },
  { id: 'yesterday', created_at: '2026-07-14T10:00:00' },
  { id: 'three', created_at: '2026-07-12T10:00:00' },
  { id: 'older', created_at: null }
];
return api.groupChatHistoryByDate(items, now).map(group => ({
  id: group.id,
  itemIds: group.items.map(item => item.id)
}));
""",
    )

    assert result == [
        {"id": "today", "itemIds": ["today"]},
        {"id": "yesterday", "itemIds": ["yesterday"]},
        {"id": "threeDays", "itemIds": ["three"]},
        {"id": "older", "itemIds": ["older"]},
    ]


def test_stream_normalizers_preserve_nested_trace_type_and_citation_dedupe():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const message = { content: '', citations: [{ chunk_id: '1', content: 'old', doc_name: 'a' }] };
api.applyStreamTraceId(message, { trace_id: 'outer', data: { trace_id: 42 } });
api.mergeStreamCitations(message, {
  type: 'citation',
  data: [
    { chunk_id: '1', content: 'duplicate', doc_name: 'b' },
    { chunk_id: '2', content: 'new', doc_name: 'b' },
    { chunk_id: '3', content: 'new', doc_name: 'b' }
  ]
});
return { traceId: message.trace_id, citations: message.citations };
""",
    )

    assert result["traceId"] == 42
    assert [citation["chunk_id"] for citation in result["citations"]] == ["1", "2"]


def test_tool_permission_display_summarizes_read_only_bash_as_low_risk():
    result = _run_typescript(
        "frontend/src/utils/toolPermissionDisplay.ts",
        """
const display = api.getToolPermissionDisplay({
  toolName: 'Bash',
  args: {
    command: 'uptime; nproc; free -h; df -h | grep -v tmpfs; ps aux --sort=-%cpu | head -11'
  },
  details: '参数: {...}',
});
return display;
""",
    )

    assert result["displayTitle"] == "读取服务器状态"
    assert result["summary"] == "读取当前运行环境的 CPU、内存、磁盘和进程信息"
    assert result["riskLabel"] == "低风险 · 只读"
    assert result["scopeLabel"] == "当前运行环境"
    assert result["commandCount"] == 5
    assert result["isReadOnly"] is True


def test_tool_permission_display_is_conservative_for_unknown_tools():
    result = _run_typescript(
        "frontend/src/utils/toolPermissionDisplay.ts",
        """
return api.getToolPermissionDisplay({
  toolName: 'send_message',
  args: { message: 'hello' },
  details: '参数: {"message":"hello"}',
});
""",
    )

    assert result["displayTitle"] == "执行 send_message"
    assert result["summary"] == "将调用该工具，并根据工具参数执行对应操作"
    assert result["riskLabel"] == "需要确认"
    assert result["isReadOnly"] is False
    assert '"message": "hello"' in result["parameterText"]


def test_tool_permission_display_does_not_count_echo_separators_as_checks():
    result = _run_typescript(
        "frontend/src/utils/toolPermissionDisplay.ts",
        """
return api.getToolPermissionDisplay({
  toolName: 'Bash',
  args: {
    command: 'echo "负载"; uptime; echo "CPU"; nproc; echo "内存"; free -h; echo "磁盘"; df -h | grep -v tmpfs; echo "进程"; ps aux --sort=-%cpu | head -11'
  },
});
""",
    )

    assert result["commandCount"] == 5


def test_tool_permission_display_compacts_safe_proc_inspection_without_lowering_risk():
    result = _run_typescript(
        "frontend/src/utils/toolPermissionDisplay.ts",
        """
return api.getToolPermissionDisplay({
  toolName: 'Bash',
  args: { command: 'cat /proc/meminfo' },
});
""",
    )

    assert result["isCompact"] is True
    assert result["riskLabel"] == "需要确认"


def test_tool_permission_display_keeps_sensitive_cat_path_in_full_layout():
    result = _run_typescript(
        "frontend/src/utils/toolPermissionDisplay.ts",
        """
return api.getToolPermissionDisplay({
  toolName: 'Bash',
  args: { command: 'cat /etc/shadow' },
});
""",
    )

    assert result["isCompact"] is False


def test_collapse_secondary_folds_preserves_thought_expanded_when_timeline_pending():
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const pendingMsg = {
  isProcessNarrationExpanded: true,
  isReasoningExpanded: true,
  isThoughtExpanded: true,
  processTimeline: [{ id: 'step-1', kind: 'log', title: 'Bash', status: 'pending' }]
};
api.collapseSecondaryFoldsOnBody(pendingMsg);

const completedMsg = {
  isProcessNarrationExpanded: true,
  isReasoningExpanded: true,
  isThoughtExpanded: true,
  processTimeline: [{ id: 'step-1', kind: 'log', title: 'Bash', status: 'success' }]
};
api.collapseSecondaryFoldsOnBody(completedMsg);

return {
  pendingThought: pendingMsg.isThoughtExpanded,
  pendingProcess: pendingMsg.isProcessNarrationExpanded,
  completedThought: completedMsg.isThoughtExpanded,
};
""",
    )

    assert result["pendingThought"] is True
    assert result["pendingProcess"] is False
    assert result["completedThought"] is False


def test_chat_execution_timeline_watch_and_meta_guard_premature_fold():
    timeline_code = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")
    embed_code = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug_code = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    # ChatExecutionTimeline: pending 状态必定展开，且仅在无 pending 时响应 hasAnswer 折叠
    assert "if (pending) {" in timeline_code
    assert "expanded.value = true;" in timeline_code
    assert "if (answer && !hasPending.value) expanded.value = false;" in timeline_code

    # EmbedChat: meta 事件不因非 data_query 强制将 isThoughtExpanded 抹杀为 false
    assert "if (data.thought_expanded_default === true) {" in embed_code
    assert "agentMsg.value.isThoughtExpanded = true;" in embed_code
    assert "!timelineHasPending(agentMsg.value.processTimeline)" in embed_code

    # AgentDebug: 包含 pending 守卫
    assert "!timelineHasPending(agentMsg.value.processTimeline)" in debug_code