"""Contract checks for authenticated identity and mutable business context separation."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_embedchat_does_not_accept_host_identity_overrides():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert "data.user_info" not in source
    assert "data.user)" not in source
    assert "currentUser.value = u" not in source
    assert "business_context" in source
    assert "const sanitizeBusinessValue =" in source
    assert "key.trim().toLowerCase()" in source
    assert "delete logData.user_info" in source


def test_mainline_chat_does_not_send_init_config_user_info():
    source = _source("frontend/src/views/Chat.vue")

    assert "localStorage.getItem('user_info')" not in source
    assert "user_info:" not in source


def test_widget_debugger_documents_business_context_protocol():
    source = _source("frontend/src/views/WidgetDebugger.vue")

    assert "business_context:" in source
    assert "user_info:" not in source


def test_backend_sanitizes_client_injected_context_before_prompt_use():
    endpoint = _source("app/api/v1/endpoints/chat.py")
    agent_service = _source("app/services/ai/agent_service.py")
    # 管道化重构后，把业务上下文拼进 Prompt 的逻辑迁到 AssembleStep
    assemble_step = _source("app/services/ai/pipeline/steps/assemble_step.py")

    assert "sanitize_injected_context" in endpoint
    assert "sanitize_injected_context" in agent_service
    assert "sanitize_injected_context" in assemble_step
    assert "business_context." in assemble_step
