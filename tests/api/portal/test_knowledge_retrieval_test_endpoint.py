"""检索测试端点的行为契约。

1. 参数语义严格对齐 RAGFlow：``top_n`` 映射 ``page_size``（返回条数），
   ``top_k`` 保持官方默认 1024（参与向量余弦计算的候选池），不能跟着「前 n 条」走。
   把 top_k 当成返回条数会把召回池压到个位数，检索质量明显低于控制台。
2. 客户端断开即取消上游：用户点「取消检索」后，后端不能继续等 RAGFlow
   返回，也不能把这次检索记进效果日志（用户没拿到结果）。
"""

import asyncio

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import ragflow
from app.services.ai import ragflow_client as ragflow_client_module
from app.services.ai.ragflow_client import RagFlowClient


class _NoopAsync:
    def __init__(self, *args, **kwargs):
        pass

    async def __call__(self, *args, **kwargs):
        return None


class _FakeResponse:
    status_code = 200
    reason_phrase = "OK"
    text = ""

    def json(self):
        return {"code": 0, "data": [{"content": "x", "similarity": 0.5}]}


class _FakeHttpxClient:
    def __init__(self, captured, *args, **kwargs):
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        self._captured["url"] = url
        self._captured["json"] = json
        return _FakeResponse()


def _client_with_captured_payload(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        ragflow_client_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeHttpxClient(captured),
    )

    client = RagFlowClient(config_prefix="knowledge_ragflow")
    client.base_url = "http://ragflow.local"
    client.api_key = "test-key"
    monkeypatch.setattr(client, "_ensure_config", _NoopAsync())

    async def _timeout():
        return 5.0

    monkeypatch.setattr(client, "_resolve_retrieve_timeout", _timeout)
    return client, captured


@pytest.mark.asyncio
async def test_retrieve_maps_top_n_to_page_size(monkeypatch):
    client, captured = _client_with_captured_payload(monkeypatch)

    await client.retrieve("q", ["ds-1"], top_n=10)

    assert captured["json"]["page_size"] == 10


@pytest.mark.asyncio
async def test_retrieve_keeps_default_vector_candidate_pool(monkeypatch):
    """「前 n 条」不能压缩向量候选池：top_k 必须保持 RAGFlow 官方默认 1024。"""
    client, captured = _client_with_captured_payload(monkeypatch)

    await client.retrieve("q", ["ds-1"], top_n=5)

    assert captured["json"]["top_k"] == 1024
    assert captured["json"]["page_size"] == 5


@pytest.mark.asyncio
async def test_retrieval_test_passes_top_n_and_keeps_candidate_pool(monkeypatch):
    captured = {}

    class FakeRagFlowClient:
        def __init__(self, config_prefix="ragflow", **kwargs):
            captured["config_prefix"] = config_prefix

        async def retrieve(self, query, dataset_ids, **kwargs):
            captured["query"] = query
            captured["dataset_ids"] = dataset_ids
            captured["kwargs"] = kwargs
            return [{"similarity": 0.42}]

    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)
    monkeypatch.setattr(ragflow, "require_element_permission", _NoopAsync())
    monkeypatch.setattr(ragflow, "require_dataset_access", _NoopAsync())
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _NoopAsync())
    monkeypatch.setattr(ragflow.KnowledgeRetrievalLogService, "log", _NoopAsync())

    payload = ragflow.RetrievalTestRequest(
        query="预算怎么看",
        dataset_ids=["ds-1"],
        top_n=10,
        similarity_threshold=0.2,
        vector_similarity_weight=0.3,
    )
    result = await ragflow.retrieval_test(
        payload=payload,
        request=_AlwaysConnectedRequest(),
        user={"user_id": "2", "user_name": "tester", "role": "user"},
        db=None,
    )

    assert result["code"] == 0
    assert result["total"] == 1
    assert captured["config_prefix"] == "knowledge_ragflow"
    assert captured["kwargs"]["top_n"] == 10
    # 端点不能自己设候选池倍数：交给客户端默认值（1024）
    assert "candidate_pool_multiplier" not in captured["kwargs"]


class _AlwaysConnectedRequest:
    async def is_disconnected(self):
        return False


class _DisconnectingRequest:
    """第一次探测仍在线，之后都返回断开：模拟用户点「取消检索」。"""

    def __init__(self):
        self.calls = 0

    async def is_disconnected(self):
        self.calls += 1
        return self.calls > 1


@pytest.mark.asyncio
async def test_retrieval_test_cancels_upstream_on_client_disconnect(monkeypatch):
    state = {"cancelled": False, "logged": False, "audit_status": None}

    async def slow_retrieve(query, dataset_ids, **kwargs):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        return []

    class FakeRagFlowClient:
        def __init__(self, config_prefix="ragflow", **kwargs):
            pass

        async def retrieve(self, query, dataset_ids, **kwargs):
            return await slow_retrieve(query, dataset_ids, **kwargs)

    async def fake_log(**kwargs):
        state["logged"] = True
        return True

    async def fake_audit(request, user, action, status_code, payload, **kwargs):
        state["audit_status"] = status_code

    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)
    monkeypatch.setattr(ragflow, "require_element_permission", _NoopAsync())
    monkeypatch.setattr(ragflow, "require_dataset_access", _NoopAsync())
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_audit)
    monkeypatch.setattr(ragflow.KnowledgeRetrievalLogService, "log", fake_log)
    monkeypatch.setattr(ragflow, "DISCONNECT_POLL_INTERVAL_SECONDS", 0.01)

    payload = ragflow.RetrievalTestRequest(
        query="预算怎么看",
        dataset_ids=["ds-1"],
        top_n=5,
    )

    with pytest.raises(HTTPException) as exc_info:
        await asyncio.wait_for(
            ragflow.retrieval_test(
                payload=payload,
                request=_DisconnectingRequest(),
                user={"user_id": "2", "user_name": "tester", "role": "user"},
                db=None,
            ),
            timeout=5,
        )

    assert exc_info.value.status_code == 499
    assert state["cancelled"] is True
    # 没拿到结果就不该计入检索效果统计
    assert state["logged"] is False
    assert state["audit_status"] == 499
