import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure
FOCUS_HELPER = ROOT / "frontend/src/utils/savedReportFocus.ts"


def test_loaded_saved_report_focus_opens_report_run_for_each_request():
    assert FOCUS_HELPER.exists(), "saved-report focus helper is missing"
    script = """
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync('frontend/src/utils/savedReportFocus.ts', 'utf8');
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText;
const moduleRef = { exports: {} };
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const reports = [{ id: 7, title: '月报' }];
const runs = [{ id: 11 }];
const calls = [];
const dependencies = {
  getReports: () => reports,
  loadReports: async () => calls.push('load'),
  openReport: async report => calls.push(`report:${report.id}`),
  openRunsTab: async () => calls.push('runs-tab'),
  getRuns: () => runs,
  openRun: async run => calls.push(`run:${run.id}`),
};
(async () => {
  const first = await api.focusSavedReportTarget(
    { report_id: '7', run_id: '11', request_id: 'req-1' },
    dependencies,
  );
  const second = await api.focusSavedReportTarget(
    { report_id: '7', run_id: '11', request_id: 'req-2' },
    dependencies,
  );
  process.stdout.write(JSON.stringify({ first, second, calls }));
})().catch(error => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result == {
        "first": True,
        "second": True,
        "calls": [
            "report:7",
            "runs-tab",
            "run:11",
            "report:7",
            "runs-tab",
            "run:11",
        ],
    }
