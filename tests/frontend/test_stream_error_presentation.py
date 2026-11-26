import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _run_typescript(expression: str):
    module_path = "frontend/src/utils/streamErrorPresentation.ts"
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


def test_normalizer_maps_sse_error_detail_to_frontend_shape():
    result = _run_typescript(
        """
return api.normalizeStreamError({
  type: 'error',
  status: 'error',
  content: '这次读取没有完成，请检查权限。',
  error_detail: { raw_error: 'Permission denied: [internal path]', ai_status: 'success' }
});
"""
    )

    assert result == {
        "message": "这次读取没有完成，请检查权限。",
        "detail": {
            "message": "这次读取没有完成，请检查权限。",
            "rawError": "Permission denied: [internal path]",
            "aiStatus": "success",
        },
    }


def test_apply_error_message_deduplicates_repeated_terminal_events():
    result = _run_typescript(
        """
const message = { content: '已有正文' };
const payload = {
  type: 'error',
  status: 'error',
  content: '处理没有完成，请稍后重试。',
  error_detail: { raw_error: 'upstream 503', ai_status: 'fallback' }
};
const first = api.applyStreamErrorMessage(message, payload);
const second = api.applyStreamErrorMessage(message, payload);
return { first, second, content: message.content, detail: message.errorDetail };
"""
    )

    assert result == {
        "first": True,
        "second": False,
        "content": "已有正文\n\n> ✕ **处理未完成**: 处理没有完成，请稍后重试。",
        "detail": {
            "message": "处理没有完成，请稍后重试。",
            "rawError": "upstream 503",
            "aiStatus": "fallback",
        },
    }


def test_step_error_log_is_ignored_by_frontend_error_applier():
    result = _run_typescript(
        """
const message = { content: '正文' };
const changed = api.applyStreamErrorMessage(message, {
  type: 'log', status: 'error', content: '工具失败'
});
return { changed, content: message.content, detail: message.errorDetail };
"""
    )

    assert result == {"changed": False, "content": "正文"}


def test_legacy_error_without_detail_still_gets_a_friendly_error_marker():
    result = _run_typescript(
        """
const message = { content: '' };
api.applyStreamErrorMessage(message, { type: 'error', content: '旧版本错误' });
return { content: message.content, detail: message.errorDetail };
"""
    )

    assert result == {
        "content": "\n\n> ✕ **处理未完成**: 旧版本错误",
        "detail": {"message": "旧版本错误", "aiStatus": "disabled"},
    }
