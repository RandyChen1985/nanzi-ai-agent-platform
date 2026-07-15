import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _run_typescript(module_path: str, expression: str, require_setup: str = "const requireModule = require;"):
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
const firstOpen = { visible: canvas.canvasVisible.value, title: canvas.canvasData.value.title };
await canvas.handleWorkspaceFilePreview({ path: '/workspace/a.md', name: 'a.md' });
const toggledClosed = canvas.canvasVisible.value;
await canvas.handleOpenCanvas({ type: 'html', title: '', content: '<p>x</p>', sourcePath: '/workspace/a.md' });
return {
  firstOpen,
  toggledClosed,
  direct: canvas.canvasData.value
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

    assert result["firstOpen"] == {"visible": True, "title": "a.md"}
    assert result["toggledClosed"] is False
    assert result["direct"] == {"type": "html", "title": "文件预览", "content": "<p>x</p>"}


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
