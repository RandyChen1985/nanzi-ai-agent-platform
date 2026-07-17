import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure
PROTOCOL = ROOT / "frontend/src/utils/savedReportOpenProtocol.ts"


def test_saved_report_open_protocol_dispatches_repeated_targets_as_lightweight_messages():
    assert PROTOCOL.exists(), "saved-report runtime open protocol is missing"
    script = """
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync('frontend/src/utils/savedReportOpenProtocol.ts', 'utf8');
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText;
const moduleRef = { exports: {} };
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
global.CustomEvent = class {
  constructor(type, init) { this.type = type; this.detail = init.detail; }
};
const dispatched = [];
const dispatch = event => dispatched.push({ type: event.type, detail: event.detail });
const first = api.createSavedReportOpenRequest({ report_id: 7, run_id: 11, request_id: 'req-1' });
const second = api.createSavedReportOpenRequest({ report_id: 7, run_id: 11, request_id: 'req-2' });
api.publishSavedReportOpenRequest(first, dispatch);
api.publishSavedReportOpenRequest(second, dispatch);
process.stdout.write(JSON.stringify({
  message: api.createSavedReportOpenMessage(first),
  dispatched
}));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["message"] == {
        "type": "OPEN_SAVED_REPORT",
        "open_saved_report": {"report_id": "7", "run_id": "11", "request_id": "req-1"},
    }
    assert result["dispatched"] == [
        {
            "type": "nanzi:open-saved-report",
            "detail": {"report_id": "7", "run_id": "11", "request_id": "req-1"},
        },
        {
            "type": "nanzi:open-saved-report",
            "detail": {"report_id": "7", "run_id": "11", "request_id": "req-2"},
        },
    ]
