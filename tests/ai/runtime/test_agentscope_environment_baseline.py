from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


ROOT = Path(__file__).resolve().parents[3]


def test_dockerfile_uses_python_311_or_newer_for_agentscope():
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim" in dockerfile or "FROM python:3.12-slim" in dockerfile


def test_requirements_declares_agentscope_runtime_extras():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "agentscope[service,storage,workspace]" in requirements


def test_checklist_tracks_agentscope_runtime_replacement():
    checklist = (ROOT / "tests" / "CHECKLIST.md").read_text(encoding="utf-8")

    assert "AgentScope 全量运行时替换" in checklist
    assert "tests/ai/runtime/test_agentscope_runtime_foundation.py" in checklist
    assert "tests/ai/runtime/test_agentscope_tooling.py" in checklist
    assert "tests/ai/runtime/test_tool_registry_runtime_specs.py" in checklist
