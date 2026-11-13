"""RAGFlow 1.0（Go 版）与 0.25+ 的「文档 / 切片」写入契约。

背景（生产实测）：点知识库文档详情里的「参与检索」保存时，生产环境报

    RAGFlow Update Document 错误: Field: <enabled> - Message:
    <Input should be a valid integer> - Value: <False>

本地（RAGFlow 0.23/0.24，`api/apps/sdk/doc.py`）能过，是因为那条老实现直接
``int(req["enabled"])``，布尔值顺手就能转成整数；而 RAGFlow 从 0.25 起把文档
更新搬到 `api/apps/restful_apis/document_api.py`（Pydantic `UpdateDocumentReq`，
``enabled: int``），1.0 的 Go 实现则用 ``Enabled *int``，两者都**不再接受 JSON
布尔**（pydantic v2 的 lax 模式也不做 bool→int）。同一批改动还把方法从 PUT
换成了 PATCH。所以平台侧必须：

1. ``enabled`` 一律发 0/1 整数（不是 true/false）；
2. 方法优先 PATCH，被上游以「方法不支持」拒绝时回退 PUT 并记住结论，
   兼容仍只有 PUT 的 0.23/0.24 发行版。

切片更新（`available`）有同样的方法漂移（PUT → PATCH），但 ``available`` 本身
在 Go 侧是严格布尔（``raw.(bool)``）、Python 侧 `_parse_available_flag` 也吃布尔，
因此**必须保持布尔**，不能跟着 enabled 一起转整数。
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.ai import ragflow_client as ragflow_client_module
from app.services.ai.ragflow_client import RagFlowClient

pytestmark = pytest.mark.no_infrastructure

BASE = "http://mock-ragflow"


def _client() -> RagFlowClient:
    return RagFlowClient(override_url=BASE, override_key="mock-key")


@pytest.fixture(autouse=True)
def _reset_contract_cache():
    """方法契约按 base_url 缓存，测试之间必须隔离。"""
    ragflow_client_module.clear_write_method_contract_cache()
    yield
    ragflow_client_module.clear_write_method_contract_cache()


def _ok_response():
    return httpx.Response(200, json={"code": 0, "data": {}})


def _method_not_allowed_response():
    """0.23/0.24 的 Werkzeug 405：RAGFlow 包成 HTTP 200 + code 100。"""
    return httpx.Response(
        200,
        json={"code": 100, "message": "<MethodNotAllowed '405: Method Not Allowed'>"},
    )


def _patch_request(responses):
    """按序返回 responses，并记录每次 (method, url, json)。"""
    calls: list = []
    responses = list(responses)

    async def _side_effect(method, url, **kwargs):
        calls.append({"method": method, "url": url, "json": kwargs.get("json")})
        if not responses:
            raise AssertionError(f"超出预期的额外请求：{method} {url}")
        return responses.pop(0)

    patcher = patch(
        "httpx.AsyncClient.request", new_callable=AsyncMock, side_effect=_side_effect
    )
    started = patcher.start()
    return calls, started, patcher


@pytest.mark.asyncio
async def test_update_document_patches_with_integer_enabled():
    calls, _, patcher = _patch_request([_ok_response()])
    try:
        await _client().update_document("ds-1", "doc-1", enabled=False)
    finally:
        patcher.stop()

    assert len(calls) == 1, calls
    assert calls[0]["method"] == "PATCH"
    payload = calls[0]["json"]
    assert payload == {"enabled": 0}
    # 关键：必须是整数，不是布尔 —— RAGFlow 1.0 的 *int 与 0.25+ 的
    # `enabled: int` 都拒绝 JSON false。
    assert isinstance(payload["enabled"], int)
    assert not isinstance(payload["enabled"], bool)


@pytest.mark.asyncio
async def test_update_document_enable_true_sends_one():
    calls, _, patcher = _patch_request([_ok_response()])
    try:
        await _client().update_document("ds-1", "doc-1", enabled=True)
    finally:
        patcher.stop()

    assert calls[0]["json"] == {"enabled": 1}


@pytest.mark.asyncio
async def test_update_document_keeps_name_and_only_sends_given_fields():
    calls, _, patcher = _patch_request([_ok_response()])
    try:
        await _client().update_document("ds-1", "doc-1", name="制度手册.pdf")
    finally:
        patcher.stop()

    assert calls[0]["json"] == {"name": "制度手册.pdf"}


@pytest.mark.asyncio
async def test_update_document_falls_back_to_put_on_method_not_allowed():
    calls, _, patcher = _patch_request(
        [_method_not_allowed_response(), _ok_response()]
    )
    try:
        await _client().update_document("ds-1", "doc-1", enabled=False)
    finally:
        patcher.stop()

    assert [c["method"] for c in calls] == ["PATCH", "PUT"]
    # 回退时 enabled 依然是整数：老版的 int(req["enabled"]) 也接受 0/1
    assert calls[1]["json"] == {"enabled": 0}


@pytest.mark.asyncio
async def test_update_document_remembers_put_contract_for_later_calls():
    client = _client()
    calls, _, patcher = _patch_request(
        [_method_not_allowed_response(), _ok_response(), _ok_response()]
    )
    try:
        await client.update_document("ds-1", "doc-1", enabled=False)
        await client.update_document("ds-1", "doc-2", enabled=True)
    finally:
        patcher.stop()

    assert [c["method"] for c in calls] == ["PATCH", "PUT", "PUT"]


@pytest.mark.asyncio
async def test_update_document_propagates_real_errors_without_fallback():
    """非「方法不支持」的错误不得被当成方法问题重试，否则会掩盖真实原因。"""
    calls, _, patcher = _patch_request(
        [httpx.Response(200, json={"code": 102, "message": "Duplicated document name in the same dataset."})]
    )
    try:
        with pytest.raises(Exception) as exc_info:
            await _client().update_document("ds-1", "doc-1", name="dup.pdf")
    finally:
        patcher.stop()

    assert len(calls) == 1
    assert "Duplicated document name" in str(exc_info.value)


@pytest.mark.asyncio
async def test_update_chunk_patches_and_keeps_available_boolean():
    calls, _, patcher = _patch_request([_ok_response()])
    try:
        await _client().update_chunk("ds-1", "doc-1", "chunk-1", available=False)
    finally:
        patcher.stop()

    assert calls[0]["method"] == "PATCH"
    payload = calls[0]["json"]
    # available 必须保持布尔：Go 侧 raw.(bool) 严格校验，转整数反而会被拒
    assert payload == {"available": False}
    assert isinstance(payload["available"], bool)


@pytest.mark.asyncio
async def test_update_chunk_falls_back_to_put_on_method_not_allowed():
    calls, _, patcher = _patch_request(
        [_method_not_allowed_response(), _ok_response()]
    )
    try:
        await _client().update_chunk("ds-1", "doc-1", "chunk-1", content="新内容")
    finally:
        patcher.stop()

    assert [c["method"] for c in calls] == ["PATCH", "PUT"]
    assert calls[1]["json"] == {"content": "新内容"}


@pytest.mark.asyncio
async def test_method_not_allowed_detection_covers_both_flavours():
    """Go 1.0 的 NoRoute 是 404 JSON，0.23/0.24 的 Werkzeug 是 405 文案。"""
    assert RagFlowClient.is_method_not_allowed_error(
        "RAGFlow Update Document 错误: <MethodNotAllowed '405: Method Not Allowed'>"
    )
    assert RagFlowClient.is_method_not_allowed_error(
        'RAGFlow Update Document 错误: {"code":404,"message":"Not Found: /api/v1/datasets/ds-1/documents/doc-1"}'
    )
    # 业务错误不是方法问题
    assert not RagFlowClient.is_method_not_allowed_error(
        "RAGFlow Update Document 错误: The dataset doesn't own the document."
    )
    assert not RagFlowClient.is_method_not_allowed_error(
        "RAGFlow Update Document 错误: Dataset not found"
    )


@pytest.mark.asyncio
async def test_delete_chunk_uses_document_chunks_endpoint_with_chunk_ids():
    """删除切片没有 `/chunks/{chunk_id}` 这个 DELETE 路由（所有发行版都没有）。

    RAGFlow 0.24 把 PUT 挂在 `<chunk_id>` 上、0.25+/1.0 换成 PATCH，删除走的是
    「文档级 + chunk_ids」批量接口。发旧路径只会拿到 405 MethodNotAllowed ——
    这正是「删除切片本地也失败」的原因。
    """
    calls, _, patcher = _patch_request([_ok_response()])
    try:
        await _client().delete_chunk("ds-1", "doc-1", "chunk-1")
    finally:
        patcher.stop()

    assert len(calls) == 1
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/api/v1/datasets/ds-1/documents/doc-1/chunks")
    assert calls[0]["json"] == {"chunk_ids": ["chunk-1"]}
