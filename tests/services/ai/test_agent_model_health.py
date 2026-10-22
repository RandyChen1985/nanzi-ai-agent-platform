"""智能体模型健康检查的判定规则测试。

对应设计文档 ``docs/superpowers/specs/2026-10-22-agent-model-health-design.md``。

现场故障：智能体版本里存了一个目标环境并不存在的模型名，UI 上完全看不出来，
只有运行时才报 ``LLM API Key is missing``。这里锁定「哪些模型配置算异常」。
"""

from dataclasses import dataclass

import pytest

from app.services.ai.agent_model_health import (
    ModelRegistrySnapshot,
    evaluate_model_issue,
)


@dataclass
class _Row:
    """模拟 ai_models 行，只保留判定用到的字段。"""

    model_id: str
    name: str
    is_active: bool = True
    api_key: str | None = "sk-test"


def _snapshot(*rows: _Row) -> ModelRegistrySnapshot:
    return ModelRegistrySnapshot.from_rows(rows)


def test_missing_model_name_is_flagged():
    issue = evaluate_model_issue(None, registry=_snapshot(), has_global_api_key=True)

    assert issue is not None
    assert issue.code == "missing"


def test_blank_model_name_is_flagged():
    issue = evaluate_model_issue("   ", registry=_snapshot(), has_global_api_key=True)

    assert issue is not None
    assert issue.code == "missing"


def test_exact_model_id_match_passes():
    registry = _snapshot(_Row(model_id="qwen3.6-35b", name="Qwen3.6 35B"))

    assert evaluate_model_issue(
        "qwen3.6-35b", registry=registry, has_global_api_key=False
    ) is None


def test_display_name_alias_match_passes():
    registry = _snapshot(_Row(model_id="deepseek-chat", name="DeepSeek Default"))

    assert evaluate_model_issue(
        "DeepSeek Default", registry=registry, has_global_api_key=False
    ) is None


def test_disabled_model_is_flagged():
    registry = _snapshot(_Row(model_id="qwen3.6-35b", name="Qwen3.6 35B", is_active=False))

    issue = evaluate_model_issue(
        "qwen3.6-35b", registry=registry, has_global_api_key=True
    )

    assert issue is not None
    assert issue.code == "disabled"
    assert issue.model_name == "qwen3.6-35b"


def test_disabled_model_flagged_even_when_matched_by_name():
    registry = _snapshot(_Row(model_id="deepseek-chat", name="DeepSeek Default", is_active=False))

    issue = evaluate_model_issue(
        "DeepSeek Default", registry=registry, has_global_api_key=True
    )

    assert issue is not None
    assert issue.code == "disabled"


def test_unregistered_model_is_flagged():
    registry = _snapshot(_Row(model_id="qwen3.6-35b", name="Qwen3.6 35B"))

    issue = evaluate_model_issue(
        "DeepSeek-V3.2", registry=registry, has_global_api_key=True
    )

    assert issue is not None
    assert issue.code == "unregistered"
    assert "DeepSeek-V3.2" in issue.message


def test_ambiguous_display_name_is_flagged_as_unregistered():
    registry = _snapshot(
        _Row(model_id="model-a", name="同名模型"),
        _Row(model_id="model-b", name="同名模型"),
    )

    issue = evaluate_model_issue("同名模型", registry=registry, has_global_api_key=True)

    assert issue is not None
    assert issue.code == "unregistered"
    assert "多个" in issue.message


def test_model_without_key_and_without_global_key_is_flagged():
    registry = _snapshot(_Row(model_id="qwen3.6-35b", name="Qwen3.6 35B", api_key=None))

    issue = evaluate_model_issue(
        "qwen3.6-35b", registry=registry, has_global_api_key=False
    )

    assert issue is not None
    assert issue.code == "missing_api_key"


def test_model_without_key_but_global_key_present_passes():
    """模型自身没配 key 时仍可回落到全局 llm_api_key，不该误报。"""
    registry = _snapshot(_Row(model_id="qwen3.6-35b", name="Qwen3.6 35B", api_key="  "))

    assert evaluate_model_issue(
        "qwen3.6-35b", registry=registry, has_global_api_key=True
    ) is None


def test_model_with_key_passes():
    registry = _snapshot(_Row(model_id="qwen3.6-35b", name="Qwen3.6 35B", api_key="sk-real"))

    assert evaluate_model_issue(
        "qwen3.6-35b", registry=registry, has_global_api_key=False
    ) is None


@pytest.mark.parametrize("code", ["missing", "unregistered", "disabled", "missing_api_key"])
def test_every_issue_code_has_a_message(code):
    """告警文案必须能直接展示给用户，不能是空串。"""
    from app.services.ai.agent_model_health import MODEL_ISSUE_LABELS

    assert MODEL_ISSUE_LABELS[code].strip()


@pytest.mark.asyncio
async def test_default_model_issue_is_none_when_configured_and_usable(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from app.core.config import settings
    from app.services.ai import agent_model_health as mh

    monkeypatch.setattr(settings, "LLM_MODEL_NAME", None, raising=False)
    registry = _snapshot(_Row(model_id="qwen3.6-35b", name="Qwen3.6 35B"))
    with patch(
        "app.services.config_service.ConfigService.get",
        new_callable=AsyncMock,
        return_value="qwen3.6-35b",
    ), patch.object(mh, "load_model_registry", AsyncMock(return_value=registry)), patch.object(
        mh, "load_global_llm_api_key", AsyncMock(return_value=True)
    ):
        assert await mh.load_default_model_issue(None) is None


@pytest.mark.asyncio
async def test_default_model_issue_flags_unconfigured_system_default(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from app.core.config import settings
    from app.services.ai import agent_model_health as mh

    monkeypatch.setattr(settings, "LLM_MODEL_NAME", None, raising=False)
    with patch(
        "app.services.config_service.ConfigService.get",
        new_callable=AsyncMock,
        return_value=None,
    ), patch.object(mh, "load_model_registry", AsyncMock(return_value=_snapshot())), patch.object(
        mh, "load_global_llm_api_key", AsyncMock(return_value=False)
    ):
        issue = await mh.load_default_model_issue(None)

    assert issue is not None
    assert issue.code == "missing"


@pytest.mark.asyncio
async def test_default_model_issue_flags_unregistered_system_default(monkeypatch):
    """系统配置写了一个模型表里没有的模型名——正是现场那种情况。"""
    from unittest.mock import AsyncMock, patch

    from app.core.config import settings
    from app.services.ai import agent_model_health as mh

    monkeypatch.setattr(settings, "LLM_MODEL_NAME", None, raising=False)
    registry = _snapshot(_Row(model_id="qwen3.6-35b", name="Qwen3.6 35B"))
    with patch(
        "app.services.config_service.ConfigService.get",
        new_callable=AsyncMock,
        return_value="DeepSeek-V3.2",
    ), patch.object(mh, "load_model_registry", AsyncMock(return_value=registry)), patch.object(
        mh, "load_global_llm_api_key", AsyncMock(return_value=True)
    ):
        issue = await mh.load_default_model_issue(None)

    assert issue is not None
    assert issue.code == "unregistered"
    assert issue.model_name == "DeepSeek-V3.2"
