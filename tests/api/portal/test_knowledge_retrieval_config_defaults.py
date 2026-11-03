"""检索测试参数默认值改由系统配置提供的后端契约。

``GET /api/portal/ragflow/config`` 的 ``retrieval_defaults`` 必须与
``knowledge_ragflow_*`` 三项系统配置同源，缺省时退回 5 / 0.2 / 0.3。
"""

import pytest

from app.api.portal.endpoints import ragflow
from app.services.config_service import ConfigService


@pytest.mark.asyncio
async def test_config_returns_retrieval_defaults_from_system_config(monkeypatch):
    values = {
        "knowledge_ragflow_api_url": "http://ragflow.local",
        "knowledge_ragflow_api_key": "secret",
        "metadata_provider": "local",
        "knowledge_ragflow_similarity_threshold": "0.35",
        "knowledge_ragflow_vector_weight": "0.45",
        "knowledge_ragflow_metadata_top_k": "12",
    }

    async def fake_get(key, default=None):
        return values.get(key, default)

    async def fake_enabled():
        return True

    monkeypatch.setattr(ConfigService, "get", staticmethod(fake_get))
    monkeypatch.setattr(
        "app.services.ai.knowledge_utils.is_knowledge_base_enabled",
        fake_enabled,
    )

    result = await ragflow.get_ragflow_config_summary()

    defaults = result["data"]["retrieval_defaults"]
    assert defaults["top_n"] == 12
    assert defaults["similarity_threshold"] == pytest.approx(0.35)
    assert defaults["vector_similarity_weight"] == pytest.approx(0.45)


@pytest.mark.asyncio
async def test_config_retrieval_defaults_fall_back_when_unset(monkeypatch):
    values = {"knowledge_ragflow_api_url": "", "knowledge_ragflow_api_key": ""}

    async def fake_get(key, default=None):
        return values.get(key, default)

    async def fake_enabled():
        return True

    monkeypatch.setattr(ConfigService, "get", staticmethod(fake_get))
    monkeypatch.setattr(
        "app.services.ai.knowledge_utils.is_knowledge_base_enabled",
        fake_enabled,
    )

    result = await ragflow.get_ragflow_config_summary()

    defaults = result["data"]["retrieval_defaults"]
    assert defaults["top_n"] == 5
    assert defaults["similarity_threshold"] == pytest.approx(0.2)
    assert defaults["vector_similarity_weight"] == pytest.approx(0.3)
