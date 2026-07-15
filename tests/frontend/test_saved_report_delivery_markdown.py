import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _render_safe_markdown(markdown: str) -> str:
    script = f"""
const esbuild = require('./frontend/node_modules/esbuild');
const result = esbuild.buildSync({{
  entryPoints: ['frontend/src/utils/safeMarkdown.ts'],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  write: false,
}});
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', result.outputFiles[0].text)(moduleRef, moduleRef.exports, require);
process.stdout.write(moduleRef.exports.renderSafeMarkdownPreview({json.dumps(markdown)}));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_safe_markdown_preview_renders_structure_without_raw_html():
    html = _render_safe_markdown(
        "### 核心结论\n\n- **增长** 16 次\n\n<script>alert('x')</script>"
    )

    assert "<h3>核心结论</h3>" in html
    assert "<li><strong>增长</strong> 16 次</li>" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_safe_markdown_preview_blocks_dangerous_links_and_secures_external_links():
    html = _render_safe_markdown(
        "[危险链接](javascript:alert(1)) [完整报表](https://example.com/report)"
    )

    assert 'href="javascript:' not in html
    assert 'href="https://example.com/report"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


def test_saved_report_delivery_uses_safe_markdown_preview_instead_of_plain_pre():
    source = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")

    assert 'from "@/utils/safeMarkdown"' in source
    assert "renderSafeMarkdownPreview(delivery.content || '')" in source
    assert 'class="saved-report-delivery-markdown markdown-body' in source
    assert "{{ delivery.content }}</pre>" not in source
