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
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
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


SAMPLE_LOGS = [
    {"title": "智能路由决策", "category": "router", "status": "pending"},
    {"title": "分析用户请求并进行意图识别", "category": "intent", "status": "success"},
    {"title": "模型调用: DeepSeek-V3.2", "category": "llm", "status": "success"},
]


def test_embed_thought_summary_title_uses_business_progress_not_model_names():
    result = _run_typescript(
        "frontend/src/utils/embedThoughtStages.ts",
        f"""
const logs = {json.dumps(SAMPLE_LOGS, ensure_ascii=False)};
return {{
  thinking: api.getEmbedThoughtSummaryTitle({{ logs, isThinking: true, thinkingText: '', turnType: 'general' }}),
  done: api.getEmbedThoughtSummaryTitle({{ logs, isThinking: false, thinkingText: '', turnType: 'general' }}),
  emptyThinking: api.getEmbedThoughtSummaryTitle({{ logs: [], isThinking: true, thinkingText: '', turnType: 'general' }}),
}};
""",
    )

    assert "DeepSeek" not in result["thinking"]
    assert result["thinking"] in {
        "正在理解问题…",
        "正在选择处理方式…",
        "正在调用工具…",
        "正在生成回答…",
        "思考中…",
    }
    assert result["done"] == "思考完成"
    assert result["emptyThinking"] == "思考中…"


def test_embed_thought_summary_only_in_embed_chat_header():
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    assert "getEmbedThoughtSummaryTitle" in embed
    assert "getDisplayLogs(msg)" in embed
    assert "getThoughtStages" not in embed
    assert "buildEmbedThoughtStages" not in embed
    assert 'step-label="阶段"' not in embed
    assert "getEmbedThoughtSummaryTitle" not in debug
    assert "buildEmbedThoughtStages" not in debug
