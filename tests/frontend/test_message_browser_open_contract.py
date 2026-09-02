"""AI 消息 HTTP(S) 链接右侧打开按钮的 Task 1 契约测试。"""

import json
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/messageBrowserLinks.ts"


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


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


def test_browser_url_helper_accepts_only_http_and_https():
    result = _run_typescript(
        MODULE,
        """
return {
  http: api.isBrowserOpenableUrl('http://example.com/a'),
  https: api.isBrowserOpenableUrl('https://example.com/a?token=x#top'),
  upper: api.isBrowserOpenableUrl('HTTPS://example.com/a'),
  javascript: api.isBrowserOpenableUrl('javascript:alert(1)'),
  data: api.isBrowserOpenableUrl('data:text/html,<script>alert(1)</script>'),
  local: api.isBrowserOpenableUrl('/api/v1/chat/generated-files/a'),
  quick: api.isBrowserOpenableUrl('quick:查询订单')
};
""",
    )
    assert result == {
        "http": True,
        "https": True,
        "upper": True,
        "javascript": False,
        "data": False,
        "local": False,
        "quick": False,
    }


def test_browser_link_helper_adds_only_http_sibling_action_and_escapes_attribute():
    result = _run_typescript(
        MODULE,
        """
const html = api.appendBrowserOpenActions(
  '<a href="https://example.com/a?x=1&amp;y=2">外部</a>' +
  '<a href="quick:查询">快捷</a>' +
  '<a href="javascript:alert(1)">脚本</a>'
);
return {
  html,
  actionCount: (html.match(/data-open-browser-url=/g) || []).length,
};
""",
    )
    assert result["actionCount"] == 1
    assert 'class="message-link-open"' in result["html"]
    assert 'data-open-browser-url="https://example.com/a?x=1&amp;y=2"' in result["html"]
    assert "quick:查询" in result["html"]
    assert "javascript:alert(1)" in result["html"]


def test_browser_link_helper_is_idempotent_for_existing_action():
    result = _run_typescript(
        MODULE,
        """
const once = api.appendBrowserOpenActions('<a href=\"https://example.com/a\">外部</a>');
return api.appendBrowserOpenActions(once);
""",
    )
    assert result.count('data-open-browser-url=') == 1


def test_message_renderer_exposes_browser_open_prop_event_and_click_proxy():
    source = _source("frontend/src/components/MessageRenderer.vue")

    assert "enableBrowserOpen?: boolean;" in source
    assert "enableBrowserOpen: false" in source
    assert "(e: 'open-browser-url', url: string): void;" in source
    assert "appendBrowserOpenActions" in source
    assert "isBrowserOpenableUrl" in source
    assert "if (props.enableBrowserOpen)" in source
    assert "const browserOpenButton = target.closest<HTMLButtonElement>('[data-open-browser-url]');" in source
    assert "emit('open-browser-url', href)" in source
    assert "event.preventDefault();" in source
    assert "event.stopPropagation();" in source


def test_message_renderer_browser_open_action_has_compact_non_shrinking_style():
    source = _source("frontend/src/components/MessageRenderer.vue")

    assert ".message-link-open" in source
    for token in (
        "display: inline-flex",
        "flex: 0 0 auto",
        "white-space: nowrap",
        "border",
        "background",
        "hover",
    ):
        assert token in source
