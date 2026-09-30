import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.ai import agent_service as agent_service_module
from app.services.ai.agent_service import AgentService
from app.services.ai.agent_service import build_current_model_answer
from app.services.ai.config import RuntimeModelInfo


pytestmark = pytest.mark.no_infrastructure


def test_build_current_model_answer_reports_configured_and_effective_identity():
    answer = build_current_model_answer(
        RuntimeModelInfo(
            configured_model="团队默认模型",
            effective_model_id="deepseek-chat",
            source="agent_config",
            phase="primary_agent",
            resolution_status="registry_resolved",
        )
    )

    assert "deepseek-chat" in answer
    assert "团队默认模型" in answer
    assert "主模型" in answer


def test_build_current_model_answer_is_honest_when_registry_resolution_failed():
    answer = build_current_model_answer(
        RuntimeModelInfo(
            configured_model="团队默认模型",
            effective_model_id="团队默认模型",
            source="agent_config",
            phase="primary_agent",
            resolution_status="registry_unresolved",
        )
    )

    assert "团队默认模型" in answer
    assert "无法确认" in answer

