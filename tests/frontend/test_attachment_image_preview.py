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


def test_attachment_preview_auth_urls_need_blob_fetch():
    result = _run_typescript(
        "frontend/src/utils/attachmentImages.ts",
        """
const privateUpload = api.getAttachmentPreviewUrl({
  url: '/app/data/agent_workspaces/u1/uploads/a.jpeg',
  ext: 'jpeg',
});
const staticLegacy = api.getAttachmentPreviewUrl({
  url: '/static/uploads/a.jpeg',
  ext: 'jpeg',
});
const remote = api.getAttachmentPreviewUrl({
  url: 'https://cdn.example/a.jpeg',
  ext: 'jpeg',
});
return {
  privateUpload,
  staticLegacy,
  remote,
  privateNeedsAuth: api.attachmentPreviewNeedsAuthFetch(privateUpload),
  staticDirect: api.attachmentPreviewNeedsAuthFetch(staticLegacy),
  remoteDirect: api.attachmentPreviewNeedsAuthFetch(remote),
};
""",
    )

    assert result["privateUpload"].startswith("/api/v1/chat/fs/preview?path=")
    assert result["privateNeedsAuth"] is True
    assert result["staticDirect"] is False
    assert result["remoteDirect"] is False


def test_attachment_image_thumb_fetches_auth_preview_with_axios():
    thumb = (ROOT / "frontend/src/components/embed/AttachmentImageThumb.vue").read_text(
        encoding="utf-8"
    )
    assert "attachmentPreviewNeedsAuthFetch" in thumb
    assert 'responseType: "blob"' in thumb
    assert "@error=" in thumb
