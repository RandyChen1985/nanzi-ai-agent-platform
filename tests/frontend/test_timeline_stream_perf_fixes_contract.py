"""委派场景流式卡顿修复的回归契约。

覆盖四类行为：
1. 历史时间线的 `pending` 必须落定为独立的 `interrupted`（不是 `warning`，
   否则历史里未完成的条目会被当成换模提示）。
2. 旁白首条 delta 仍做全量规范化（CRLF / 连续空行），快路径只用于无语义差异的续写。
3. 旁白合帧缓冲在并行代理切换与非旁白事件前落盘，正文开始前先落盘再丢弃候选。
4. 动效与滚动：reduced-motion 下进度条清掉位移；Embed 消息裁剪有 intrinsic-size 兜底，
   贴底带收敛校正。
"""

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _run_typescript(module_path: str, expression: str):
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


def test_history_pending_settles_as_interrupted():
    """历史里缺终态的步骤要显示「未完成」，而不是被当成正常完成。"""
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const items = api.hydrateHistoryProcessTimeline([
  { kind: 'log', id: 'tool-1', title: '调用工具: Bash', details: 'ok', status: 'pending', category: 'tool' },
  { kind: 'log', id: 'model-1', title: '模型调用: X', details: '', status: 'pending', category: 'model' },
  { kind: 'text', id: 'n1', textKind: 'narration', content: '我先查', pending: false, children: [
    { kind: 'log', id: 'c1', title: '子步骤', details: '', status: 'pending', category: 'tool' },
  ] },
]);
return {
  statuses: items.map(item => item.status || null),
  nested: items.find(item => item.kind === 'text').children[0].status,
};
""",
    )

    assert result["statuses"][0] == "interrupted"
    assert result["statuses"][1] == "interrupted"
    assert result["nested"] == "interrupted"


def test_first_narration_delta_is_normalized_before_fast_path_takes_over():
    """首条 delta 自带 CRLF / 多空行时，时间线文案要与全量规范化一致。"""
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
return {
  crlf: api.appendProcessNarrationText('', 'a\\r\\nb'),
  blankLines: api.appendProcessNarrationText('', 'a\\n\\n\\n\\nb'),
  fastPath: api.appendProcessNarrationText('已有', '续写'),
};
""",
    )

    assert result == {
        "crlf": "a\nb",
        "blankLines": "a\n\nb",
        "fastPath": "已有续写",
    }


def test_narration_buffer_flushes_on_agent_switch_and_before_other_events():
    """合帧不能让两个并行代理的旁白串段，也不能让旁白排到工具/配置事件之后。"""
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', processTimeline: [] };
const dispatch = payload => api.dispatchAgentscopeStreamEvent(msg, payload, () => {});
dispatch({ type: 'process_narration', content: 'A段', agent_name: 'A' });
dispatch({ type: 'process_narration', content: 'A2', agent_name: 'A' });
dispatch({ type: 'process_narration', content: 'B段', agent_name: 'B' });
dispatch({ type: 'process_narration', content: 'B2', agent_name: 'B' });
dispatch({ type: 'run_config', agent_max_toolcall_timeout: 60 });
return msg.processTimeline.map(item => [item.sourceId, item.content]);
""",
    )

    assert result == [["A", "A段A2"], ["B", "B段B2"]]


def test_body_delta_flushes_buffer_then_discards_the_candidate():
    """正文开始时先落盘再丢弃旁白候选，避免缓冲残留在后续事件里复活成重复条目。"""
    result = _run_typescript(
        "frontend/src/utils/agentscopeSseHandlers.ts",
        """
const msg = { content: '', processTimeline: [] };
api.applyProcessNarrationEvent(msg, { type: 'process_narration', content: '第一段' });
api.applyProcessNarrationEvent(msg, { type: 'process_narration', content: '缓冲中' });
api.appendAssistantBodyDelta(msg, '最终正文');
return { timeline: msg.processTimeline, pending: msg.processNarrationPending, content: msg.content };
""",
    )

    assert result == {"timeline": [], "pending": "", "content": "最终正文"}


def test_reduced_motion_progress_bar_clears_slide_transform():
    """降级态要铺满整条轨道；残留 translateX(-100%) 会把进度条整体移出可视区。"""
    vue = (ROOT / "frontend/src/components/chat/TimelinePendingHint.vue").read_text(encoding="utf-8")
    reduced = vue.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    rule = reduced.split(".workspace-prewarm-bar::after", 1)[1].split("}", 1)[0]

    assert "transform: none" in rule
    assert "width: 100%" in rule


def test_embed_message_list_keeps_intrinsic_size_fallback_and_settles_scroll():
    """content-visibility 的估算高度必须有兜底声明，贴底要带收敛校正。"""
    vue = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "contain-intrinsic-size: 180px; contain-intrinsic-size: auto 180px" in vue
    assert "const settleScrollToBottom" in vue

    run_scroll = vue.split("const runScrollToBottom", 1)[1].split("const scrollToBottom", 1)[0]
    assert "settleScrollToBottom(el)" in run_scroll
    # 收敛循环必须保留上限，避免离屏内容持续增长时无限贴着底部
    settle = vue.split("const settleScrollToBottom", 1)[1].split("const runScrollToBottom", 1)[0]
    assert "attempt >= 6" in settle
    # 流式跟底只贴一次，不能把多帧收敛挂在每条事件上
    assert "if (force) settleScrollToBottom(el)" in vue


def test_stream_scroll_is_coalesced_per_frame_not_per_event():
    """流循环每个事件都会调 scrollToBottom：必须合并到一帧一次，否则每条事件读一次
    scrollHeight 就是一次整页强制同步布局（委派场景上千条事件 → 主线程被打满）。"""
    vue = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    scroll = vue.split("const scrollToBottom = (force = false) =>", 1)[1].split("const handleScroll", 1)[0]
    assert "requestAnimationFrame" in scroll
    assert "scrollToBottomFrame !== null" in scroll

    # 程序性滚动自己触发的 scroll 事件不得再读布局
    handle = vue.split("const handleScroll = (e: Event) => {", 1)[1].split("\nconst ", 1)[0]
    code_only = "\n".join(line for line in handle.splitlines() if not line.strip().startswith("//"))
    assert code_only.index("programmaticScrollUntil.value") < code_only.index("scrollHeight")


def test_timeline_text_sanitize_is_cached_per_string():
    """`stripInternalContextBlocks` 有十来条跨行正则，500ms tick 会把整棵树重渲一遍。"""
    vue = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")

    assert "const timelineTextCache = new Map<string, string>()" in vue
    assert "TIMELINE_TEXT_CACHE_MAX_BYTES" in vue
    # 缓存命中必须在清洗之前返回，且空串不占缓存
    body = vue.split("function visibleTimelineText(text?: string | null): string {", 1)[1].split("function hasVisibleTimelineText", 1)[0]
    assert body.index("timelineTextCache.get(raw)") < body.index("stripInternalContextBlocks(raw)")


def test_tool_summary_survives_cycles():
    """工具汇总跑递归遍历：委派树成环时必须能返回，且不重复计数。"""
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const toolLog = { kind: 'log', id: 't1', title: 't', details: '', status: 'pending', category: 'tool', execution_time_ms: 300, children: [] };
const narration = { kind: 'text', id: 'n1', textKind: 'narration', content: '', children: [toolLog] };
// 委派容器会按 parent_id / subagent 元数据重挂，形成自引用环
toolLog.children.push(narration);
return {
  tool: api.summarizeToolCalls([narration]),
};
""",
    )

    assert result["tool"]["count"] == 1
    assert result["tool"]["totalMs"] == 300

def test_message_list_scroll_listener_is_passive():
    """滚动监听不能阻塞滚动线程（内部不调用 preventDefault）。"""
    vue = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    assert '@scroll.passive="handleScroll"' in vue


def test_merge_timeline_logs_still_updates_nested_entries():
    """改成一次建索引后，嵌套在 children 下的条目仍要被就地更新，新增条目仍进顶层。"""
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const parent = { kind: 'log', id: 'p', title: 'p', details: '', status: 'success', children: [] };
const child = { kind: 'log', id: 'c', title: 'c', details: '', status: 'pending', children: [] };
parent.children.push(child);
const merged = api.mergeTimelineLogs([parent], [
  { id: 'c', title: 'c2', details: 'd', status: 'success' },
  { id: 'fresh', title: 'fresh', details: '', status: 'pending' },
]);
return {
  childTitle: merged[0].children[0].title,
  childStatus: merged[0].children[0].status,
  topLevelIds: merged.map(item => item.id),
  sameRef: merged[0] === parent,
};
""",
    )

    assert result["childTitle"] == "c2"
    assert result["childStatus"] == "success"
    assert result["topLevelIds"] == ["p", "fresh"]
    # 顶层条目保持原引用（引用稳定是 v-for 不重建的前提）
    assert result["sameRef"] is True


def test_thinking_timer_does_not_force_full_message_list_refresh():
    """思考计时器原来 100ms 一次 triggerRef(messages)：委派 16 秒要跑 160 次全列表 diff。"""
    vue = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    timer = vue.split("thoughtTimer = setInterval(() => {", 1)[1].split("}, 500);", 1)[0]
    assert "triggerRef" not in timer
    assert "}, 500);" in vue
    # 调用必须彻底消失（注释里提到不算）：全列表强制刷新是这次的主要卡顿源之一
    assert "triggerRef(messages);" not in vue




def test_timeline_keeps_model_calls_out_of_render_list():
    """模型调用条目不进渲染列表：从源头排除，而不是「先显示再移除」（那会抖列表）。"""
    timeline = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")

    assert "const isHiddenModelCall = (item: ProcessTimelineItem): boolean =>" in timeline
    assert 'item.category === "model"' in timeline
    # fallback 换模提示必须保留可见
    assert 'item.status !== "warning"' in timeline
    assert "/fallback/i.test(String(item.title || \"\"))" in timeline
    # items 是过滤后的结果，v-for 不会再长出模型调用行
    assert ".filter((item) => !isHiddenModelCall(item))" in timeline
    # 底部不再有任何汇总行：统计信息在高频刷新下本身就是负担
    assert "modelCallSummary" not in timeline
    assert "summarySnapshot" not in timeline
    assert "open-model-stats" not in timeline
