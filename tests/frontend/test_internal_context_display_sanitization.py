import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _run_typescript(module_path: str, expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(module_path)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', code)(moduleRef, moduleRef.exports);
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


def test_internal_context_markers_are_removed_but_sql_protocol_is_preserved():
    result = _run_typescript(
        "frontend/src/utils/streamContentSanitize.ts",
        """
const strip = api.stripInternalContextBlocks;
return {
  tool: strip('正文\\n<backend_tool_run_summary>工具结果</backend_tool_run_summary>\\n结尾'),
  attachments: strip('<backend_injected_attachments>服务器路径</backend_injected_attachments>回答'),
  systemAttachments: strip('<system_injected_attachments>系统附件</system_injected_attachments>回答'),
  reasoning: strip('前置<reasoning>内部推理</reasoning>正文'),
  redactedReasoning: strip('<redacted_reasoning>已脱敏推理</redacted_reasoning>正文'),
  systemBlock: strip('回答<!-- SYSTEM_BLOCK_START: 当前用户画像 -->内部画像<!-- SYSTEM_BLOCK_END: 当前用户画像 -->结尾'),
  historical: strip('回答\\n<historical_context executable="false">历史任务：分析国产算力芯片</historical_context>\\n结尾'),
  historicalChinese: strip('回答\\n<历史上下文 executable="false">历史任务：分析国产算力芯片</历史上下文>\\n结尾'),
  historicalStream: api.sanitizeStreamContent('<historical_context executable="false">历史任务：分析国产算力芯片</historical_context>结尾'),
  plainMarkers: strip('[本回复由智能体「主助手(Main)」生成]\\n[早前对话摘录]\\n[上一轮可复用工具结果]\\n回答'),
  sqlPlan: strip('执行计划：<sql_plan>{"goal":"统计"}</sql_plan>'),
};
""",
    )

    assert result["tool"] == "正文\n\n结尾"
    assert result["attachments"] == "回答"
    assert result["systemAttachments"] == "回答"
    assert result["reasoning"] == "前置正文"
    assert result["redactedReasoning"] == "正文"
    assert result["systemBlock"] == "回答结尾"
    assert result["historical"] == "回答\n\n结尾"
    assert result["historicalChinese"] == "回答\n\n结尾"
    assert result["historicalStream"] == "结尾"
    assert result["plainMarkers"] == "回答"
    assert result["sqlPlan"] == '执行计划：<sql_plan>{"goal":"统计"}</sql_plan>'


def test_unclosed_internal_block_is_hidden_from_the_visible_tail():
    result = _run_typescript(
        "frontend/src/utils/streamContentSanitize.ts",
        """
 return {
   xml: api.stripInternalContextBlocks('正常回答\\n<backend_tool_run_summary>尚未闭合的内部内容'),
   system: api.stripInternalContextBlocks('正常回答\\n<!-- SYSTEM_BLOCK_START: 当前用户画像 -->\\n内部画像'),
   historical: api.stripInternalContextBlocks('正常回答\\n<历史上下文 executable="false">尚未闭合的历史任务'),
 };
""",
    )

    assert result == {"xml": "正常回答", "system": "正常回答", "historical": "正常回答"}


def test_stream_sanitizer_preserves_markdown_boundary_whitespace():
    result = _run_typescript(
        "frontend/src/utils/streamContentSanitize.ts",
        """
const nl = String.fromCharCode(10);
const chunks = [
  '查询成功。' + nl,
  nl + '## 📊 上海各园区' + nl,
  '| 排名 | 园区 |' + nl,
  '| --- | --- |' + nl,
  '| 1 | EW1 |' + nl + nl,
  '```chart' + nl,
  '{\"series\":[]}' + nl,
  '```' + nl,
];
return chunks.map((chunk) => api.sanitizeStreamContent(chunk)).join('');
""",
    )

    assert result == (
        "查询成功。\n\n"
        "## 📊 上海各园区\n"
        "| 排名 | 园区 |\n"
        "| --- | --- |\n"
        "| 1 | EW1 |\n\n"
        "```chart\n"
        "{\"series\":[]}\n"
        "```\n"
    )


def test_stream_sanitizer_hides_split_internal_block_from_cumulative_display():
    result = _run_typescript(
        "frontend/src/utils/streamContentSanitize.ts",
        """
const chunks = [
  '可见前文\\n',
  '<backend_tool_run_summary>内部\\n',
  '结果</backend_tool_run_summary>\\n## 结论\\n',
];
let accumulated = '';
const visibleByChunk = [];
for (const chunk of chunks) {
  accumulated += api.sanitizeStreamContent(chunk);
  visibleByChunk.push(api.stripInternalContextBlocks(accumulated));
}
return { visibleByChunk, accumulated };
""",
    )

    assert result["visibleByChunk"] == [
        "可见前文",
        "可见前文",
        "可见前文\n\n## 结论",
    ]
    assert "内部" in result["accumulated"]


def test_execution_timeline_sanitizes_visible_text_and_copy_payloads():
    timeline = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(
        encoding="utf-8"
    )

    assert 'from "@/utils/streamContentSanitize"' in timeline
    assert "function visibleTimelineText" in timeline
    assert "visibleTimelineText(item.content)" in timeline
    assert "visibleTimelineText(child.details)" in timeline
    assert "visibleTimelineText(subStep.details)" in timeline
    assert "visibleTimelineText(nestedStep.details)" in timeline
    assert "hasVisibleTimelineText(nestedStep.details)" in timeline
    assert '@click="nestedStep.details ?' not in timeline
    assert 'v-if="nestedStep.details"' not in timeline
    assert "visibleTimelineText(item.details)" in timeline
    assert "const visibleText = visibleTimelineText(text)" in timeline
