from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.ai_model import AIModelCreate, AIModelResponse, AIModelUpdate


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]


def model_payload(**overrides):
    payload = {
        "name": "Thinking model",
        "model_id": "thinking-model",
        "provider": "openai",
        "type": "llm",
    }
    payload.update(overrides)
    return payload


def test_thinking_configuration_defaults_to_agent_scope_values():
    model = AIModelCreate(**model_payload())

    assert model.thinking_enable is False
    assert model.thinking_only is False
    assert model.allow_disable_thinking is True
    assert model.reasoning_effort is None
    assert model.supported_reasoning_efforts == [
        "none", "minimal", "low", "medium", "high", "xhigh",
    ]


def test_builtin_volcengine_provider_is_accepted_by_model_registry_schema():
    model = AIModelCreate(
        **model_payload(
            model_id="doubao-seed-1-6-251015",
            provider="volcengine",
        )
    )

    assert model.provider == "volcengine"


def test_model_temperature_is_optional_and_limited_to_extended_platform_range():
    assert AIModelCreate(**model_payload()).temperature is None
    assert AIModelCreate(**model_payload(temperature=0)).temperature == 0
    assert AIModelCreate(**model_payload(temperature=0.8)).temperature == 0.8
    assert AIModelCreate(**model_payload(temperature=1.2)).temperature == 1.2

    with pytest.raises(ValidationError):
        AIModelCreate(**model_payload(temperature=2.01))

    with pytest.raises(ValidationError):
        AIModelUpdate(temperature=-0.01)


def test_thinking_configuration_normalizes_effort_order_and_legacy_response_text():
    model = AIModelCreate(
        **model_payload(
            reasoning_effort="xhigh",
            supported_reasoning_efforts=["xhigh", "low"],
        )
    )
    assert model.supported_reasoning_efforts == ["low", "xhigh"]

    now = datetime.now(timezone.utc)
    response = AIModelResponse.from_orm_custom(
        SimpleNamespace(
            id="model-id",
            name="Thinking model",
            model_id="thinking-model",
            provider="openai",
            type="llm",
            api_base_url=None,
            context_size=None,
            max_output_tokens=None,
            thinking_enable=True,
            thinking_only=False,
            allow_disable_thinking=True,
            reasoning_effort="xhigh",
            supported_reasoning_efforts='["xhigh", "low"]',
            is_active=True,
            created_at=now,
            updated_at=now,
            api_key="encrypted-key",
        )
    )
    assert response.supported_reasoning_efforts == ["low", "xhigh"]
    assert response.has_api_key is True


def test_legacy_model_response_maps_removed_reasoning_values():
    now = datetime.now(timezone.utc)

    response = AIModelResponse.from_orm_custom(
        SimpleNamespace(
            id="legacy-model-id",
            name="Legacy thinking model",
            model_id="legacy-thinking-model",
            provider="openai",
            type="llm",
            api_base_url=None,
            context_size=None,
            max_output_tokens=None,
            thinking_enable=True,
            thinking_only=True,
            allow_disable_thinking=True,
            reasoning_effort="max",
            supported_reasoning_efforts='["low", "high", "max"]',
            is_active=True,
            created_at=now,
            updated_at=now,
            api_key=None,
        )
    )

    assert response.reasoning_effort == "xhigh"
    assert response.supported_reasoning_efforts == ["low", "high", "xhigh"]


def test_legacy_model_response_falls_back_when_reasoning_values_are_invalid():
    now = datetime.now(timezone.utc)

    response = AIModelResponse.from_orm_custom(
        SimpleNamespace(
            id="invalid-model-id",
            name="Invalid thinking model",
            model_id="invalid-thinking-model",
            provider="openai",
            type="llm",
            api_base_url=None,
            context_size=None,
            max_output_tokens=None,
            thinking_enable=True,
            thinking_only=True,
            allow_disable_thinking=True,
            reasoning_effort="auto",
            supported_reasoning_efforts='["retired-effort"]',
            is_active=True,
            created_at=now,
            updated_at=now,
            api_key=None,
        )
    )

    assert response.reasoning_effort is None
    assert response.supported_reasoning_efforts == [
        "none", "minimal", "low", "medium", "high", "xhigh",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        model_payload(reasoning_effort="unsupported"),
        model_payload(reasoning_effort="max"),
        model_payload(supported_reasoning_efforts=[]),
        model_payload(reasoning_effort="high", supported_reasoning_efforts=["low"]),
        model_payload(supported_reasoning_efforts=["unsupported"]),
    ],
)
def test_thinking_configuration_rejects_unsupported_values(payload):
    with pytest.raises(ValidationError):
        AIModelCreate(**payload)


def test_thinking_update_validates_default_against_effective_supported_values():
    with pytest.raises(ValidationError):
        AIModelUpdate(
            reasoning_effort="high",
            supported_reasoning_efforts=["low"],
        )


def test_thinking_migrations_define_agent_scope_efforts():
    mysql = (ROOT / "db-prod/V117-use_agentscope_reasoning_fields.sql").read_text(
        encoding="utf-8"
    )
    postgres = (ROOT / "db-prod-pg/V16-use_agentscope_reasoning_fields.sql").read_text(
        encoding="utf-8"
    )

    for migration in (mysql, postgres):
        for column in ("thinking_enable", "reasoning_effort"):
            assert column in migration
        for effort in ("none", "minimal", "low", "medium", "high", "xhigh"):
            assert effort in migration
