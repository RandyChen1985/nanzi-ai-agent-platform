"""RAGFlow 全量列表：翻页、缓存、降级快照。

RAGFlow 的 page_size 上限是 100。原来端点取 page_size=1000，client 就只拼 10 页，
库超过 1000 个时会**静默丢库**——用户看不到报错，只会觉得「怎么少了几个」。

翻页期间可能有人建库，导致同一页重复出现，所以还要按 id 去重。
"""

import httpx
import pytest

from app.services.ai import ragflow_client as mod
from app.services.ai.ragflow_client import RagFlowClient

pytestmark = pytest.mark.no_infrastructure


class _Resp:
    status_code = 200
    reason_phrase = "OK"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return ""


class _FakeHttp:
    requests: list = []
    total: int | None = None
    per_page: int = 100
    fail: bool = False
    duplicate_first_page: bool = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None, params=None):
        page = params["page"]
        _FakeHttp.requests.append(page)
        if _FakeHttp.fail:
            raise httpx.ConnectError("boom")

        start = (page - 1) * _FakeHttp.per_page
        remaining = _FakeHttp.total
        count = min(_FakeHttp.per_page, max(0, remaining - start)) if remaining is not None else _FakeHttp.per_page
        items = [
            {"id": f"ds-{start + i}", "name": f"库{start + i}"}
            for i in range(count)
        ]
        if _FakeHttp.duplicate_first_page and page == 2:
            items = [{"id": "ds-0", "name": "库0"}] + items
        payload = {"code": 0, "data": {"datasets": items}}
        if _FakeHttp.total is not None:
            payload["data"]["total"] = _FakeHttp.total
        return _Resp(payload)


async def _fake_ensure(self):
    self.base_url = "http://ragflow.test"
    self.api_key = "key"


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    mod.clear_dataset_list_cache()
    _FakeHttp.requests = []
    _FakeHttp.total = None
    _FakeHttp.fail = False
    _FakeHttp.duplicate_first_page = False
    monkeypatch.setattr(RagFlowClient, "_ensure_config", _fake_ensure)
    monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeHttp)
    yield
    mod.clear_dataset_list_cache()


@pytest.mark.asyncio
async def test_fetches_every_page_when_total_is_known():
    _FakeHttp.total = 250

    items = await RagFlowClient().list_all_datasets()

    assert len(items) == 250
    assert sorted(_FakeHttp.requests) == [1, 2, 3]


@pytest.mark.asyncio
async def test_falls_back_to_sequential_paging_without_total():
    _FakeHttp.total = None  # 部分版本不回 total
    _FakeHttp.per_page = 100

    # 只有第一页有数据，之后为空
    original_get = _FakeHttp.get

    async def only_first_page(self, url, headers=None, params=None):
        if params["page"] > 1:
            _FakeHttp.requests.append(params["page"])
            return _Resp({"code": 0, "data": {"datasets": []}})
        return await original_get(self, url, headers=headers, params=params)

    _FakeHttp.get = only_first_page
    try:
        items = await RagFlowClient().list_all_datasets()
    finally:
        _FakeHttp.get = original_get

    assert len(items) == 100
    assert _FakeHttp.requests == [1, 2]


@pytest.mark.asyncio
async def test_deduplicates_items_seen_on_overlapping_pages():
    _FakeHttp.total = 150
    _FakeHttp.duplicate_first_page = True

    items = await RagFlowClient().list_all_datasets()

    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_cache_avoids_second_round_trip_and_refresh_bypasses_it():
    _FakeHttp.total = 250
    client = RagFlowClient()

    await client.list_all_datasets_cached()
    first = len(_FakeHttp.requests)
    assert first >= 3

    await client.list_all_datasets_cached()
    assert len(_FakeHttp.requests) == first  # 命中缓存

    await client.list_all_datasets_cached(refresh=True)
    assert len(_FakeHttp.requests) > first


@pytest.mark.asyncio
async def test_snapshot_survives_failure_for_degraded_mode():
    _FakeHttp.total = 120
    client = RagFlowClient()
    await client.list_all_datasets_cached()

    _FakeHttp.fail = True

    snapshot = RagFlowClient.cached_datasets_snapshot("http://ragflow.test")
    assert snapshot is not None
    assert len(snapshot) == 120

    with pytest.raises(Exception):
        await client.list_all_datasets_cached(refresh=True)
