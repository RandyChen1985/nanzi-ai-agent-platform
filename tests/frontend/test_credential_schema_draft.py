"""Graphical schema edits preserve the JSON definition used by the type API."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node 运行字段编辑行为测试")
def test_credential_schema_draft_round_trip():
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "tests/frontend/credential_schema_draft.mjs"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
