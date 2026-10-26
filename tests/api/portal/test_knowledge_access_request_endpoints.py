"""访问申请端点的边界：私库不露出、已有权限不必申请、只有归属人能审批。"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def execute(self, stmt):
        return _Result(self.rows)


class _PermService:
    def __init__(self, db, *, is_admin=False, accessible=None, writable=None):
        self.is_admin = is_admin
        # 管理员场景要保留 None 语义（"不受限"），不能悄悄变成空集合
        self.accessible = accessible
        self.writable = writable or set()

    async def get_knowledge_base_access(self, user_id, user_name=None):
        return {
            "is_admin": self.is_admin,
            "accessible_ids": self.accessible if self.is_admin else (self.accessible or set()),
            "writable_ids": self.writable,
        }


def _meta(**kwargs):
    base = SimpleNamespace(
        ragflow_dataset_id="ds-1",
        name="制度库",
        description="公司制度",
        owner="财务部",
        visibility="public",
        status="active",
        owner_dept_id=3,
        updated_at=None,
    )
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


class _MetaService:
    def __init__(self, db):
        pass

    async def list_metadata(self, include_deleted=False):
        return [
            _meta(),
            _meta(ragflow_dataset_id="ds-2", name="私有库", visibility="private"),
            _meta(
                ragflow_dataset_id="ds-meta",
                name="chatbi-example-meta",
                visibility="public",
            ),
        ]

    async def get_by_dataset_id(self, dataset_id):
        if dataset_id == "ds-1":
            return _meta()
        if dataset_id == "ds-2":
            return _meta(ragflow_dataset_id="ds-2", name="私有库", visibility="private")
        return None


async def _allow(*args, **kwargs):
    return None


def _patch(monkeypatch, perm: _PermService, *, engine_ids=None):
    monkeypatch.setattr(ragflow, "PermissionService", lambda db: perm)
    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", _MetaService)

    async def fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_audit)

    if engine_ids is None:
        # 默认模拟「引擎读不出来」：发现列表应降级为不过滤，而不是变成空列表
        class _BrokenClient:
            def __init__(self, **kwargs):
                pass

            async def list_all_datasets_cached(self, *args, **kwargs):
                raise RuntimeError("engine down")

        monkeypatch.setattr(ragflow, "RagFlowClient", _BrokenClient)
    else:
        class _Client:
            def __init__(self, **kwargs):
                pass

            async def list_all_datasets_cached(self, *args, **kwargs):
                return [
                    {
                        "id": dataset_id,
                        "document_count": 12,
                        "chunk_count": 345,
                        "update_time": "2026-10-20 10:00:00",
                    }
                    for dataset_id in engine_ids
                ]

        monkeypatch.setattr(ragflow, "RagFlowClient", _Client)


def _request():
    return SimpleNamespace(method="GET", client=SimpleNamespace(host="127.0.0.1"))


@pytest.mark.asyncio
async def test_discover_hides_private_and_already_accessible(monkeypatch):
    _patch(monkeypatch, _PermService(None, accessible={"ds-9"}))

    res = await ragflow.discover_datasets(
        user={"user_id": 9, "user_name": "bob"},
        db=_Session(),
    )

    names = [item["name"] for item in res["data"]["items"]]
    assert names == ["制度库"]


@pytest.mark.asyncio
async def test_discover_excludes_datasets_i_already_have(monkeypatch):
    _patch(monkeypatch, _PermService(None, accessible={"ds-1", "ds-9"}))

    res = await ragflow.discover_datasets(
        user={"user_id": 9, "user_name": "bob"},
        db=_Session(),
    )

    assert res["data"]["items"] == []


@pytest.mark.asyncio
async def test_cannot_request_private_dataset(monkeypatch):
    _patch(monkeypatch, _PermService(None))

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.create_access_request(
            dataset_id="ds-2",
            payload=ragflow.AccessRequestBody(reason="想看"),
            request=_request(),
            user={"user_id": 9, "user_name": "bob"},
            db=_Session(),
        )

    assert exc.value.status_code == 400
    assert "私有" in exc.value.detail


@pytest.mark.asyncio
async def test_cannot_request_what_i_already_have(monkeypatch):
    _patch(monkeypatch, _PermService(None, accessible={"ds-1"}))

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.create_access_request(
            dataset_id="ds-1",
            payload=ragflow.AccessRequestBody(),
            request=_request(),
            user={"user_id": 9, "user_name": "bob"},
            db=_Session(),
        )

    assert exc.value.status_code == 400
    assert "已经可以访问" in exc.value.detail


@pytest.mark.asyncio
async def test_reviewer_must_be_owner_or_admin(monkeypatch):
    _patch(monkeypatch, _PermService(None, writable={"ds-other"}))

    row = SimpleNamespace(
        id=5,
        dataset_id="ds-1",
        status="pending",
        applicant_user_id=9,
        applicant_user_name="bob",
    )

    class _Service:
        def __init__(self, db):
            pass

        async def get(self, request_id):
            return row

        async def decide(self, **kwargs):  # pragma: no cover - 不应走到
            raise AssertionError("越权审批不该进入 decide")

    monkeypatch.setattr(ragflow, "KnowledgeAccessRequestService", _Service)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.approve_access_request(
            request_id=5,
            payload=ragflow.AccessReviewBody(comment="同意"),
            request=_request(),
            user={"user_id": 1, "user_name": "alice"},
            db=_Session(),
        )

    assert exc.value.status_code == 403


class _ReviewedSession:
    """审批记录查询：只关心拼出来的条件与返回结构。"""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.statement = None

    async def execute(self, stmt):
        self.statement = stmt
        return _Result(self.rows)


def _reviewed_row(**kwargs):
    base = SimpleNamespace(
        id=1,
        dataset_id="ds-1",
        dataset_name="制度库",
        applicant_user_id=9,
        applicant_user_name="bob",
        reason="看制度",
        status="approved",
        reviewer_user_name="alice",
        review_comment="同意",
        created_at=None,
        reviewed_at=None,
    )
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


@pytest.mark.asyncio
async def test_reviews_for_admin_returns_all(monkeypatch):
    _patch(monkeypatch, _PermService(None, is_admin=True))
    db = _ReviewedSession([_reviewed_row(), _reviewed_row(id=2, dataset_id="ds-2", name=None)])

    res = await ragflow.list_reviewed_access_requests(
        user={"user_id": 1, "user_name": "root"}, db=db
    )

    assert res["data"]["total"] == 2
    assert res["data"]["items"][0]["review_comment"] == "同意"
    assert res["data"]["items"][0]["status"] == "approved"


@pytest.mark.asyncio
async def test_reviews_scoped_to_manageable_datasets(monkeypatch):
    _patch(monkeypatch, _PermService(None, is_admin=False, writable={"ds-1"}))
    db = _ReviewedSession([])

    res = await ragflow.list_reviewed_access_requests(
        user={"user_id": 8, "user_name": "owner"}, db=db
    )

    assert res["data"]["total"] == 0
    # 非管理员必须带上库范围条件，否则能翻到别人库的申请理由
    assert "ds-1" in str(db.statement.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_reviews_returns_empty_when_nothing_manageable(monkeypatch):
    _patch(monkeypatch, _PermService(None, is_admin=False))

    res = await ragflow.list_reviewed_access_requests(
        user={"user_id": 8, "user_name": "nobody"}, db=_ReviewedSession()
    )

    assert res["data"] == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_admin_discover_is_empty(monkeypatch):
    """管理员的 accessible_ids 是 None（不受限），按集合算会得出"一个库都进不去"，
    于是整站库全变成可申请对象——这条锁住别退化成那样。"""
    _patch(monkeypatch, _PermService(None, is_admin=True, accessible=None))

    res = await ragflow.discover_datasets(
        user={"user_id": 1, "user_name": "root"},
        db=_Session(),
    )

    assert res["data"]["items"] == []
    assert res["data"]["is_admin"] is True


@pytest.mark.asyncio
async def test_admin_cannot_file_access_request(monkeypatch):
    _patch(monkeypatch, _PermService(None, is_admin=True, accessible=None))

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.create_access_request(
            dataset_id="ds-1",
            payload=ragflow.AccessRequestBody(),
            request=_request(),
            user={"user_id": 1, "user_name": "root"},
            db=_Session(),
        )

    assert exc.value.status_code == 400
    assert "无需申请" in exc.value.detail


@pytest.mark.asyncio
async def test_discover_skips_datasets_missing_in_engine(monkeypatch):
    """失联库申请了也打不开，不该出现在发现列表里。"""
    _patch(monkeypatch, _PermService(None), engine_ids={"ds-9"})

    res = await ragflow.discover_datasets(
        user={"user_id": 9, "user_name": "bob"},
        db=_Session(),
    )

    assert res["data"]["items"] == []


@pytest.mark.asyncio
async def test_discover_keeps_listing_when_engine_is_unreachable(monkeypatch):
    """引擎抖动时不过滤：宁可多列几条，也别让整个列表凭空消失。"""
    _patch(monkeypatch, _PermService(None))

    res = await ragflow.discover_datasets(
        user={"user_id": 9, "user_name": "bob"},
        db=_Session(),
    )

    assert [item["name"] for item in res["data"]["items"]] == ["制度库"]


@pytest.mark.asyncio
async def test_discover_exposes_scale_and_freshness(monkeypatch):
    """只有名字和描述，用户没法定「值不值得申请」；规模与更新时间要一起给。"""
    _patch(monkeypatch, _PermService(None), engine_ids={"ds-1"})

    res = await ragflow.discover_datasets(
        user={"user_id": 9, "user_name": "bob"},
        db=_Session(),
    )

    item = res["data"]["items"][0]
    assert item["doc_count"] == 12
    assert item["chunk_count"] == 345
    assert item["engine_update_time"] == "2026-10-20 10:00:00"


@pytest.mark.asyncio
async def test_discover_scale_is_none_when_engine_unreachable(monkeypatch):
    """引擎读不出来时计数要显示为「—」，不能假装是 0 个文档。"""
    _patch(monkeypatch, _PermService(None))

    res = await ragflow.discover_datasets(
        user={"user_id": 9, "user_name": "bob"},
        db=_Session(),
    )

    item = res["data"]["items"][0]
    assert item["doc_count"] is None
    assert item["chunk_count"] is None


class _QueuedSession:
    """按调用顺序返回结果：pending 端点先查申请、再查元数据。"""

    def __init__(self, *result_sets):
        self._queue = list(result_sets)

    async def execute(self, stmt):
        return _Result(self._queue.pop(0) if self._queue else [])


@pytest.mark.asyncio
async def test_pending_items_carry_dataset_scale(monkeypatch):
    """审批人判断「该不该批」要看库本身，不能只有一个库名。"""
    _patch(monkeypatch, _PermService(None, is_admin=True), engine_ids={"ds-1"})

    request_row = SimpleNamespace(
        id=7,
        dataset_id="ds-1",
        dataset_name="制度库",
        applicant_user_id=9,
        applicant_user_name="bob",
        reason="要看考勤制度",
        created_at=None,
    )
    meta_row = SimpleNamespace(
        ragflow_dataset_id="ds-1", visibility="public", owner="财务部/张三"
    )

    res = await ragflow.list_pending_access_requests(
        user={"user_id": 1, "user_name": "root"},
        db=_QueuedSession([request_row], [meta_row]),
    )

    item = res["data"]["items"][0]
    assert item["visibility"] == "public"
    assert item["owner"] == "财务部/张三"
    assert item["doc_count"] == 12
    assert item["chunk_count"] == 345
    assert item["dataset_exists"] is True


@pytest.mark.asyncio
async def test_pending_flags_dataset_missing_in_engine(monkeypatch):
    """库在引擎侧已经没了：批准也没用，界面要先标出来。"""
    _patch(monkeypatch, _PermService(None, is_admin=True), engine_ids={"ds-other"})

    request_row = SimpleNamespace(
        id=8,
        dataset_id="ds-1",
        dataset_name="制度库",
        applicant_user_id=9,
        applicant_user_name="bob",
        reason=None,
        created_at=None,
    )

    res = await ragflow.list_pending_access_requests(
        user={"user_id": 1, "user_name": "root"},
        db=_QueuedSession([request_row], []),
    )

    item = res["data"]["items"][0]
    assert item["dataset_exists"] is False
    assert item["doc_count"] is None


@pytest.mark.asyncio
async def test_discover_hides_platform_builtin_datasets(monkeypatch):
    """chatbi-example-meta 是平台自动建的，用户申请了也用不上，不该占列表位。"""
    _patch(monkeypatch, _PermService(None), engine_ids={"ds-1", "ds-meta"})

    res = await ragflow.discover_datasets(
        user={"user_id": 9, "user_name": "bob"},
        db=_Session(),
    )

    names = [item["name"] for item in res["data"]["items"]]
    assert names == ["制度库"]
