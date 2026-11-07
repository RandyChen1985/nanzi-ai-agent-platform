"""知识库列表 / 文档列表的分页契约。

列表页要从「前端内存过滤 + 客户端假分页」换成后端真分页，所以这里把三件事钉死：

- ``GET /datasets``：keyword / visibility / dept_id / missing / favorite / ids / sort
  全部在 ``total`` 之前生效，``total`` 是**过滤后**的条数；
- ``GET /datasets/{id}/documents``：一次全量（60s 缓存）+ 内存过滤/排序/分页，
  ``total`` 是过滤后的真实总数，另带 status_counts / folders 供 chips 使用；
- ``RagFlowClient``：``list_documents_page`` 的 total 解析、>100 多页拼接，
  ``list_all_documents_cached`` 的缓存命中与按 id 去重。

全部用替身，不打真实 RAGFlow / 数据库。
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.api.portal.endpoints import ragflow
from app.services.ai import ragflow_client as client_mod
from app.services.ai.ragflow_client import RagFlowClient

pytestmark = pytest.mark.no_infrastructure


# --------------------------------------------------------------------------
# 通用替身
# --------------------------------------------------------------------------

_NO_RECORD = object()


async def _async_allow(*args, **kwargs):
    return None


# 知识库列表：字段形状与 KnowledgeBaseMetadataService.merge_with_ragflow 对齐
_DATASETS: List[Dict[str, Any]] = [
    {
        "id": "ds-1",
        "ragflow_dataset_id": "ds-1",
        "name": "finance",
        "platform_name": "财务制度",
        "platform_description": "财务相关制度汇编",
        "owner": "zhangsan",
        "owner_dept_id": 10,
        "visibility": "public",
        "is_missing_in_ragflow": False,
        "update_time": "1700000000000",
        "doc_count": 30,
        "tags": ["财务"],
    },
    {
        "id": "ds-2",
        "ragflow_dataset_id": "ds-2",
        "name": "hr-handbook",
        "platform_name": "HR 手册",
        "platform_description": "人事政策",
        "owner": "lisi",
        "owner_dept_id": 20,
        "visibility": "team",
        "is_missing_in_ragflow": False,
        "update_time": "1600000000000",
        "doc_count": 50,
        "tags": [],
    },
    {
        "id": "ds-3",
        "ragflow_dataset_id": "ds-3",
        "name": "private-notes",
        "platform_name": "私有笔记",
        "platform_description": "个人整理",
        "owner": "zhangsan",
        "owner_dept_id": None,
        "visibility": "private",
        "is_missing_in_ragflow": False,
        "update_time": None,
        "doc_count": 5,
        "tags": [],
    },
    {
        "id": "ds-4",
        "ragflow_dataset_id": "ds-4",
        "name": "lost-kb",
        "platform_name": "失联库",
        "platform_description": "引擎侧已删除",
        "owner": "wangwu",
        "owner_dept_id": None,
        "visibility": "public",
        "is_missing_in_ragflow": True,
        "update_time": "1500000000000",
        "doc_count": 0,
        "tags": [],
    },
    {
        "id": "ds-5",
        "ragflow_dataset_id": "ds-5",
        "name": "alpha-report",
        "platform_name": "Alpha 报告",
        "platform_description": "季度报告",
        "owner": "zhaoliu",
        "owner_dept_id": 10,
        "visibility": "public",
        "is_missing_in_ragflow": False,
        "update_time": "1800000000000",
        "doc_count": 7,
        "tags": [],
    },
]

_OWNER_USER_NAMES = {
    "ds-1": "张三",
    "ds-2": "李四",
    "ds-3": "张三",
    "ds-4": "王五",
    "ds-5": "赵六",
}

_FAVORITED = {"ds-2"}


class _FakePermissionService:
    def __init__(self, db):
        pass

    async def get_knowledge_base_access(self, user_id, user_name=None):
        return {
            "is_admin": True,
            "accessible_ids": None,
            "writable_ids": None,
            "content_writable_ids": None,
        }


class _FakeDatasetClient:
    base_url = "http://ragflow.test"

    def __init__(self, *args, **kwargs):
        pass

    async def list_all_datasets_cached(self, refresh: bool = False):
        return [dict(item) for item in _DATASETS]

    @classmethod
    def cached_datasets_snapshot(cls, base_url):
        return None


class _FakeDatasetMetadataService:
    def __init__(self, db):
        pass

    async def merge_with_ragflow(
        self, items, include_missing=True, missing_allow_ids=None
    ):
        return [dict(item) for item in _DATASETS]


async def _fake_attach_owner_user_names(db, items):
    for item in items:
        item["owner_user_name"] = _OWNER_USER_NAMES.get(item["ragflow_dataset_id"])


async def _fake_attach_agent_refs(db, items):
    for item in items:
        item["agent_ref_count"] = 0
        item["agent_refs"] = []


async def _fake_attach_grant_counts(db, items):
    for item in items:
        item["grant_user_count"] = 0
        item["grant_role_count"] = 0


async def _fake_attach_favorites(db, items, user_id):
    for item in items:
        item["is_favorited"] = item["ragflow_dataset_id"] in _FAVORITED


async def _call_datasets(monkeypatch, **kwargs):
    monkeypatch.setattr(ragflow, "PermissionService", _FakePermissionService)
    monkeypatch.setattr(ragflow, "RagFlowClient", _FakeDatasetClient)
    monkeypatch.setattr(
        ragflow, "KnowledgeBaseMetadataService", _FakeDatasetMetadataService
    )
    monkeypatch.setattr(
        ragflow, "_attach_owner_user_names", _fake_attach_owner_user_names
    )
    monkeypatch.setattr(ragflow, "_attach_agent_refs", _fake_attach_agent_refs)
    monkeypatch.setattr(ragflow, "_attach_grant_counts", _fake_attach_grant_counts)
    monkeypatch.setattr(ragflow, "_attach_favorites", _fake_attach_favorites)
    return await ragflow.list_ragflow_datasets(
        user={"user_id": 1, "user_name": "alice", "role": "admin"},
        db=SimpleNamespace(),
        **kwargs,
    )


def _ids_of(response) -> List[str]:
    return [item["ragflow_dataset_id"] for item in response["data"]]


# --------------------------------------------------------------------------
# GET /datasets：过滤、排序、分页
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dataset_keyword_matches_name_id_owner_description_and_tags(monkeypatch):
    # 平台名 / 标签 / 描述
    res = await _call_datasets(monkeypatch, keyword="财务")
    assert _ids_of(res) == ["ds-1"]
    assert res["total"] == 1

    # 负责人显示名（_attach_owner_user_names 补出来的字段）
    res = await _call_datasets(monkeypatch, keyword="张三")
    assert _ids_of(res) == ["ds-1", "ds-3"]

    # 负责人账号
    res = await _call_datasets(monkeypatch, keyword="zhangsan")
    assert _ids_of(res) == ["ds-1", "ds-3"]

    # dataset id，大小写不敏感
    res = await _call_datasets(monkeypatch, keyword="DS-4")
    assert _ids_of(res) == ["ds-4"]

    # 平台名，大小写不敏感
    res = await _call_datasets(monkeypatch, keyword="alpha")
    assert _ids_of(res) == ["ds-5"]

    # 描述
    res = await _call_datasets(monkeypatch, keyword="人事政策")
    assert _ids_of(res) == ["ds-2"]


@pytest.mark.asyncio
async def test_dataset_visibility_filter_ignores_illegal_value(monkeypatch):
    res = await _call_datasets(monkeypatch, visibility="TEAM")
    assert _ids_of(res) == ["ds-2"]
    assert res["total"] == 1

    res = await _call_datasets(monkeypatch, visibility="private")
    assert _ids_of(res) == ["ds-3"]

    # 非法值当作没传，而不是筛出空列表
    res = await _call_datasets(monkeypatch, visibility="banana")
    assert res["total"] == len(_DATASETS)


@pytest.mark.asyncio
async def test_dataset_dept_filter_and_unassigned_sentinel(monkeypatch):
    # 默认按 updated 倒序，所以 ds-5（更新）排在 ds-1 前面
    res = await _call_datasets(monkeypatch, dept_id=10)
    assert _ids_of(res) == ["ds-5", "ds-1"]

    # -1 = 未关联部门（owner_dept_id 为空）
    res = await _call_datasets(monkeypatch, dept_id=-1)
    assert _ids_of(res) == ["ds-4", "ds-3"]

    res = await _call_datasets(monkeypatch, dept_id=999)
    assert res["total"] == 0


@pytest.mark.asyncio
async def test_dataset_missing_and_favorite_filters(monkeypatch):
    res = await _call_datasets(monkeypatch, missing=1)
    assert _ids_of(res) == ["ds-4"]

    res = await _call_datasets(monkeypatch, favorite=1)
    assert _ids_of(res) == ["ds-2"]

    # 默认不过滤
    res = await _call_datasets(monkeypatch, missing=0, favorite=0)
    assert res["total"] == len(_DATASETS)


@pytest.mark.asyncio
async def test_dataset_ids_filter_keeps_requested_order(monkeypatch):
    res = await _call_datasets(monkeypatch, ids="ds-4,ds-1")
    assert _ids_of(res) == ["ds-4", "ds-1"]
    assert res["total"] == 2

    # 不在可见范围内的直接忽略；重复 id 只出现一次
    res = await _call_datasets(monkeypatch, ids="ds-4,ds-999,ds-1,ds-1")
    assert _ids_of(res) == ["ds-4", "ds-1"]


@pytest.mark.asyncio
async def test_dataset_sort_updated_name_docs(monkeypatch):
    # 默认 updated：按 update_time 倒序，没有时间的排最后
    res = await _call_datasets(monkeypatch)
    assert _ids_of(res) == ["ds-5", "ds-1", "ds-2", "ds-4", "ds-3"]

    res = await _call_datasets(monkeypatch, sort="updated")
    assert _ids_of(res) == ["ds-5", "ds-1", "ds-2", "ds-4", "ds-3"]

    # name：platform_name 升序（普通 Python 排序）
    res = await _call_datasets(monkeypatch, sort="name")
    assert _ids_of(res) == ["ds-5", "ds-2", "ds-4", "ds-3", "ds-1"]

    # docs：doc_count 倒序
    res = await _call_datasets(monkeypatch, sort="docs")
    assert _ids_of(res) == ["ds-2", "ds-1", "ds-5", "ds-3", "ds-4"]


@pytest.mark.asyncio
async def test_dataset_total_is_post_filter_and_slice_is_correct(monkeypatch):
    res = await _call_datasets(monkeypatch, visibility="public", page=2, page_size=2)
    # public = ds-1 / ds-4 / ds-5，updated 倒序 → ds-5, ds-1, ds-4
    assert res["total"] == 3
    assert _ids_of(res) == ["ds-4"]

    res = await _call_datasets(monkeypatch, keyword="张三", page=1, page_size=1)
    assert res["total"] == 2
    assert _ids_of(res) == ["ds-1"]


@pytest.mark.asyncio
async def test_dataset_existing_callers_still_get_everything(monkeypatch):
    # page_size=5000 / 1000 这类老调用方：分页窗口大于总数时行为不变
    for size in (5000, 1000, 100):
        res = await _call_datasets(monkeypatch, page_size=size)
        assert len(res["data"]) == len(_DATASETS)
        assert res["total"] == len(_DATASETS)

    # page_size<=0 仍然返回全部（保持现有语义）
    res = await _call_datasets(monkeypatch, page_size=0)
    assert len(res["data"]) == len(_DATASETS)


# --------------------------------------------------------------------------
# GET /datasets/{id}/documents：全量 + 内存过滤/排序/分页
# --------------------------------------------------------------------------

_DOCS: List[Dict[str, Any]] = []
_EXTRA_CONFIG: Any = _NO_RECORD
_REFRESH_FLAGS: List[bool] = []
_FAIL_FETCH = False


def _make_docs(count: int = 250) -> List[Dict[str, Any]]:
    """250 条：状态按 4 循环，size 只有 5 种取值（用于验证排序稳定）。"""
    docs: List[Dict[str, Any]] = []
    for index in range(count):
        docs.append(
            {
                "id": f"doc-{index:03d}",
                "name": (
                    f"文档 {index:03d}.pdf"
                    if index % 2 == 0
                    else f"Report {index:03d}.PDF"
                ),
                "run": ("DONE", "RUNNING", "FAIL", "UNSTART")[index % 4],
                "size": 1000 + (index % 5) * 10,
                "chunk_count": index,
                # 最后 10 条没有时间：排序时必须排最后
                "update_time": (
                    str(1700000000000 + index * 1000) if index < count - 10 else None
                ),
            }
        )
    return docs


class _FakeDocClient:
    def __init__(self, *args, **kwargs):
        pass

    async def list_all_documents_cached(self, dataset_id, *, refresh: bool = False):
        _REFRESH_FLAGS.append(bool(refresh))
        if _FAIL_FETCH:
            raise RuntimeError("ragflow down")
        return [dict(doc) for doc in _DOCS]


class _FakeDocMetadataService:
    def __init__(self, db):
        pass

    async def get_by_dataset_id(self, dataset_id):
        if _EXTRA_CONFIG is _NO_RECORD:
            return None
        return SimpleNamespace(extra_config=_EXTRA_CONFIG)


async def _call_documents(monkeypatch, **kwargs):
    monkeypatch.setattr(ragflow, "require_dataset_access", _async_allow)
    monkeypatch.setattr(ragflow, "RagFlowClient", _FakeDocClient)
    monkeypatch.setattr(
        ragflow, "KnowledgeBaseMetadataService", _FakeDocMetadataService
    )
    params: Dict[str, Any] = {
        "dataset_id": "ds-1",
        "user": {"user_id": 1, "user_name": "alice", "role": "user"},
        "db": SimpleNamespace(),
    }
    params.update(kwargs)
    return await ragflow.list_dataset_documents(**params)


@pytest.fixture(autouse=True)
def _reset_doc_state():
    global _DOCS, _EXTRA_CONFIG, _REFRESH_FLAGS, _FAIL_FETCH
    _DOCS = []
    _EXTRA_CONFIG = _NO_RECORD
    _REFRESH_FLAGS = []
    _FAIL_FETCH = False
    client_mod.clear_document_list_cache()
    yield
    client_mod.clear_document_list_cache()


@pytest.mark.asyncio
async def test_documents_total_is_real_total_not_page_length(monkeypatch):
    global _DOCS
    _DOCS = _make_docs(250)

    res = await _call_documents(monkeypatch, page=1, page_size=100)

    assert res["code"] == 0
    assert res["total"] == 250
    assert res["all_total"] == 250
    assert res["page"] == 1
    assert res["page_size"] == 100
    assert len(res["data"]) == 100

    # 第 3 页只剩 50 条，但 total 仍然是 250
    res = await _call_documents(monkeypatch, page=3, page_size=100)
    assert res["total"] == 250
    assert len(res["data"]) == 50


@pytest.mark.asyncio
async def test_documents_default_sort_is_updated_desc_with_missing_last(monkeypatch):
    global _DOCS
    _DOCS = _make_docs(250)

    res = await _call_documents(monkeypatch, page=3, page_size=100)

    expected = sorted(_DOCS, key=lambda doc: doc["id"])
    expected.sort(
        key=lambda doc: (
            float(doc["update_time"]) if doc["update_time"] else float("-inf")
        ),
        reverse=True,
    )
    assert [doc["id"] for doc in res["data"]] == [
        doc["id"] for doc in expected[200:250]
    ]
    # 没有 update_time 的一律沉底
    assert [doc["id"] for doc in expected[-10:]] == [
        f"doc-{index:03d}" for index in range(240, 250)
    ]


@pytest.mark.asyncio
async def test_documents_name_filter_is_case_insensitive(monkeypatch):
    global _DOCS
    _DOCS = _make_docs(250)

    res = await _call_documents(monkeypatch, name="report", page_size=500)

    assert res["total"] == 125
    assert all("Report" in doc["name"] for doc in res["data"])
    assert res["all_total"] == 250


@pytest.mark.asyncio
async def test_documents_status_filter_does_not_shrink_status_counts(monkeypatch):
    global _DOCS
    _DOCS = _make_docs(250)

    unfiltered = await _call_documents(monkeypatch, page_size=500)
    filtered = await _call_documents(monkeypatch, status="failed", page_size=500)

    assert filtered["total"] == 62
    assert all(doc["run"] == "FAIL" for doc in filtered["data"])
    # 选了状态后，其它状态的计数不能自己缩水到 0
    assert filtered["status_counts"] == unfiltered["status_counts"] == {
        "parsed": 63,
        "parsing": 63,
        "unparsed": 62,
        "failed": 62,
    }

    # 非法 status 当作没传
    res = await _call_documents(monkeypatch, status="banana", page_size=500)
    assert res["total"] == 250


@pytest.mark.asyncio
async def test_documents_status_mapping_matches_frontend(monkeypatch):
    global _DOCS
    _DOCS = [
        {"id": "d1", "name": "a.pdf", "run": None, "status": "COMPLETED"},
        {"id": "d2", "name": "b.pdf", "run": "0"},
        {"id": "d3", "name": "c.pdf", "run": 2},
        {"id": "d4", "name": "d.pdf", "run": "weird"},
        {"id": "d5", "name": "e.pdf", "run": None, "status": "PARSING"},
        {"id": "d6", "name": "f.pdf", "run": None, "status": "FAILED"},
    ]

    res = await _call_documents(monkeypatch, page_size=100)

    assert res["status_counts"] == {
        "parsed": 1,
        "parsing": 2,
        "unparsed": 2,
        "failed": 1,
    }
    parsed = await _call_documents(monkeypatch, status="parsed", page_size=100)
    assert [doc["id"] for doc in parsed["data"]] == ["d1"]


@pytest.mark.asyncio
async def test_documents_folder_filter_uses_path_prefix_semantics(monkeypatch):
    global _DOCS, _EXTRA_CONFIG
    _DOCS = [
        {"id": "d1", "name": "a.pdf", "run": "DONE", "size": 10},
        {"id": "d2", "name": "b.pdf", "run": "DONE", "size": 20},
        {"id": "d3", "name": "c.pdf", "run": "DONE", "size": 30},
        {"id": "d4", "name": "d.pdf", "run": "DONE", "size": 40},
        {"id": "d5", "name": "e.pdf", "run": "DONE", "size": 50},
    ]
    _EXTRA_CONFIG = {
        "folder_structure": {
            "A": ["d1"],
            "A/B": ["d2", "d3"],
            "C": ["d5", "ghost-doc"],
        }
    }

    # 选中 A 要命中放在 A/B 里的文档
    res = await _call_documents(monkeypatch, folder="A", sort="name", page_size=100)
    assert [doc["id"] for doc in res["data"]] == ["d1", "d2", "d3"]
    assert res["total"] == 3

    res = await _call_documents(monkeypatch, folder="A/B", sort="name", page_size=100)
    assert [doc["id"] for doc in res["data"]] == ["d2", "d3"]

    # __unclassified__ = 不在任何文件夹里的文档
    res = await _call_documents(
        monkeypatch, folder="__unclassified__", sort="name", page_size=100
    )
    assert [doc["id"] for doc in res["data"]] == ["d4"]

    # folders：按路径（含子路径）统计真实文档数；残留的 ghost-doc 不算
    assert res["folders"] == {
        "A": 3,
        "A/B": 2,
        "C": 1,
        "__unclassified__": 1,
    }

    # folder_structure 为空时 folders 返回 {}
    _EXTRA_CONFIG = None
    res = await _call_documents(monkeypatch, page_size=100)
    assert res["folders"] == {}
    assert res["total"] == 5


@pytest.mark.asyncio
async def test_documents_folder_filter_ignores_unknown_and_broken_metadata(monkeypatch):
    global _DOCS, _EXTRA_CONFIG
    _DOCS = [{"id": "d1", "name": "a.pdf", "run": "DONE"}]

    # folder_structure 里的 docId 可能残留已删除的 id：只要真实存在的
    _EXTRA_CONFIG = {"folder_structure": {"A": ["d1", "deleted-doc"]}}
    res = await _call_documents(monkeypatch, folder="A", page_size=100)
    assert [doc["id"] for doc in res["data"]] == ["d1"]
    assert res["folders"] == {"A": 1, "__unclassified__": 0}

    # extra_config 不是 dict（历史脏数据）不能炸
    _EXTRA_CONFIG = "broken-config"
    res = await _call_documents(monkeypatch, page_size=100)
    assert res["folders"] == {}
    assert res["total"] == 1

    # 没有本地元数据记录也不能炸
    _EXTRA_CONFIG = _NO_RECORD
    res = await _call_documents(monkeypatch, page_size=100)
    assert res["folders"] == {}


@pytest.mark.asyncio
async def test_documents_sort_is_stable_across_pages(monkeypatch):
    global _DOCS
    _DOCS = _make_docs(250)

    collected: List[str] = []
    for page in (1, 2, 3, 4, 5):
        res = await _call_documents(monkeypatch, page=page, page_size=50, sort="size")
        collected.extend(doc["id"] for doc in res["data"])

    # 不重不漏
    assert len(collected) == 250
    assert len(set(collected)) == 250
    # 同 size 时按 id 兜底，顺序稳定
    expected = sorted(_DOCS, key=lambda doc: doc["id"])
    expected.sort(key=lambda doc: doc["size"], reverse=True)
    assert collected == [doc["id"] for doc in expected]


@pytest.mark.asyncio
async def test_documents_sort_name_size_chunks(monkeypatch):
    global _DOCS
    _DOCS = [
        {"id": "b", "name": "B.pdf", "size": 10, "chunk_count": 1, "run": "DONE"},
        {"id": "a", "name": "A.pdf", "size": 30, "chunk_count": 7, "run": "DONE"},
        {"id": "c", "name": "C.pdf", "size": 20, "chunk_count": 3, "run": "DONE"},
    ]

    res = await _call_documents(monkeypatch, sort="name", page_size=100)
    assert [doc["id"] for doc in res["data"]] == ["a", "b", "c"]

    res = await _call_documents(monkeypatch, sort="size", page_size=100)
    assert [doc["id"] for doc in res["data"]] == ["a", "c", "b"]

    res = await _call_documents(monkeypatch, sort="chunks", page_size=100)
    assert [doc["id"] for doc in res["data"]] == ["a", "c", "b"]


@pytest.mark.asyncio
async def test_documents_refresh_flag_is_forwarded(monkeypatch):
    global _DOCS
    _DOCS = _make_docs(3)

    await _call_documents(monkeypatch)
    assert _REFRESH_FLAGS == [False]

    await _call_documents(monkeypatch, refresh=1)
    assert _REFRESH_FLAGS == [False, True]


@pytest.mark.asyncio
async def test_documents_failure_returns_500(monkeypatch):
    global _FAIL_FETCH
    _FAIL_FETCH = True

    with pytest.raises(ragflow.HTTPException) as exc:
        await _call_documents(monkeypatch)

    assert exc.value.status_code == 500
    assert "ragflow down" in exc.value.detail


# --------------------------------------------------------------------------
# RagFlowClient：分页与全量缓存
# --------------------------------------------------------------------------


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


class _DocsHttp:
    """假的 RAGFlow 文档接口：按 page/page_size 返回，total 可选。"""

    requests: List[int] = []
    total: Optional[int] = None
    per_page: int = 100
    duplicate_first_page: bool = False
    docs_by_page: Optional[Dict[int, List[Dict[str, Any]]]] = None
    raw_total: Any = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None, params=None):
        page = params["page"]
        _DocsHttp.requests.append(page)

        if _DocsHttp.docs_by_page is not None:
            items = [dict(doc) for doc in _DocsHttp.docs_by_page.get(page, [])]
        elif _DocsHttp.total is None:
            items = []
        else:
            per_page = int(params.get("page_size") or _DocsHttp.per_page)
            start = (page - 1) * per_page
            count = min(per_page, max(0, _DocsHttp.total - start))
            items = [
                {"id": f"doc-{start + index}", "name": f"文档{start + index}"}
                for index in range(count)
            ]
        if _DocsHttp.duplicate_first_page and page == 2:
            items = [{"id": "doc-0", "name": "文档0"}] + items

        payload: Dict[str, Any] = {"code": 0, "data": {"docs": items}}
        if _DocsHttp.raw_total is not None:
            payload["data"]["total"] = _DocsHttp.raw_total
        elif _DocsHttp.total is not None:
            payload["data"]["total"] = _DocsHttp.total
        return _Resp(payload)


@pytest.fixture
def _http(monkeypatch):
    async def _fake_ensure_config(self):
        self.base_url = "http://ragflow.test"
        self.api_key = "key"

    _DocsHttp.requests = []
    _DocsHttp.total = None
    _DocsHttp.duplicate_first_page = False
    _DocsHttp.docs_by_page = None
    _DocsHttp.raw_total = None
    monkeypatch.setattr(RagFlowClient, "_ensure_config", _fake_ensure_config)
    monkeypatch.setattr(client_mod.httpx, "AsyncClient", _DocsHttp)
    client_mod.clear_document_list_cache()
    yield
    client_mod.clear_document_list_cache()


def test_extract_documents_page_tolerates_bad_total():
    docs, total = RagFlowClient._extract_documents_page(
        {"docs": [{"id": "d1"}], "total": "abc"}
    )
    assert docs == [{"id": "d1"}]
    assert total is None

    docs, total = RagFlowClient._extract_documents_page({"docs": [], "total": "250"})
    assert docs == []
    assert total == 250

    # 1.0 的 ingestion_status 要归一成 run
    docs, total = RagFlowClient._extract_documents_page(
        {"docs": [{"id": "d1", "ingestion_status": "COMPLETED"}], "total": 1}
    )
    assert total == 1
    assert docs == [{"id": "d1", "ingestion_status": "COMPLETED"}]


@pytest.mark.asyncio
async def test_list_documents_page_single_request_keeps_total(_http):
    _DocsHttp.total = 250

    docs, total = await RagFlowClient().list_documents_page("ds-1", page=1, page_size=10)

    assert total == 250
    assert len(docs) == 10
    assert _DocsHttp.requests == [1]


@pytest.mark.asyncio
async def test_list_documents_page_merges_pages_above_100(_http):
    _DocsHttp.total = 250

    docs, total = await RagFlowClient().list_documents_page(
        "ds-1", page=1, page_size=250
    )

    assert len(docs) == 250
    assert total == 250
    # RAGFlow 单页上限 100：250 条要拼 3 页，total 取第一次响应
    assert _DocsHttp.requests == [1, 2, 3]


@pytest.mark.asyncio
async def test_list_documents_page_total_is_none_when_upstream_omits_it(_http):
    _DocsHttp.docs_by_page = {1: [{"id": "d1"}, {"id": "d2"}]}

    docs, total = await RagFlowClient().list_documents_page("ds-1", page=1, page_size=10)

    assert total is None
    assert [doc["id"] for doc in docs] == ["d1", "d2"]


@pytest.mark.asyncio
async def test_list_all_documents_cached_hits_cache_and_dedupes(_http):
    _DocsHttp.total = 250
    client = RagFlowClient()

    first = await client.list_all_documents_cached("ds-1")
    assert len(first) == 250
    assert sorted(_DocsHttp.requests) == [1, 2, 3]

    requests_after_first = len(_DocsHttp.requests)
    second = await client.list_all_documents_cached("ds-1")
    # 第二次命中 60s 缓存，不再打 RAGFlow
    assert len(_DocsHttp.requests) == requests_after_first
    assert [doc["id"] for doc in second] == [doc["id"] for doc in first]

    # 返回值是浅拷贝：调用方改 dict 不该污染缓存
    second[0]["name"] = "被改掉了"
    third = await client.list_all_documents_cached("ds-1")
    assert third[0]["name"] != "被改掉了"

    # refresh 强制回源
    await client.list_all_documents_cached("ds-1", refresh=True)
    assert len(_DocsHttp.requests) > requests_after_first


@pytest.mark.asyncio
async def test_list_all_documents_cached_deduplicates_ids(_http):
    _DocsHttp.total = 150
    _DocsHttp.duplicate_first_page = True

    docs = await RagFlowClient().list_all_documents_cached("ds-1")

    ids = [doc["id"] for doc in docs]
    assert len(ids) == 150
    assert len(set(ids)) == 150


@pytest.mark.asyncio
async def test_list_all_documents_pages_sequentially_without_total(_http):
    _DocsHttp.docs_by_page = {
        1: [{"id": f"doc-{index}"} for index in range(100)],
        2: [{"id": f"doc-{100 + index}"} for index in range(100)],
        3: [{"id": f"doc-{200 + index}"} for index in range(20)],
        4: [],
    }

    docs = await RagFlowClient().list_all_documents_cached("ds-1")

    assert len(docs) == 220
    # 拿不到 total 时顺序翻到「不足一页」为止
    assert _DocsHttp.requests == [1, 2, 3]


@pytest.mark.asyncio
async def test_cached_documents_snapshot_survives_failure(_http):
    _DocsHttp.total = 120
    client = RagFlowClient()
    await client.list_all_documents_cached("ds-1")

    snapshot = RagFlowClient.cached_documents_snapshot("http://ragflow.test", "ds-1")
    assert snapshot is not None
    assert len(snapshot) == 120

    # 没缓存过的库 / 地址返回 None
    assert RagFlowClient.cached_documents_snapshot("http://ragflow.test", "ds-2") is None
    assert RagFlowClient.cached_documents_snapshot("http://other.test", "ds-1") is None
