"""`PUT /datasets/{id}/metadata` 的键级写：只动 custom_questions，不碰别的键。

纯源码断言挡不住端点内部写错，所以这里真的把请求打进路由，只把「数据库访问」和
「权限校验」换成假的。

守的是这一条：**保存元数据永远不会覆盖 extra_config 的其它键**。

`extra_config` 里住着 `folder_structure`（虚拟文件夹），而保存元数据的人手里只有
打开弹窗那一刻的快照。整份回写 = 把别人（或自己另一个标签页）刚做的目录调整静默
回滚，且界面上已经没有 JSON 编辑框，用户根本看不到"是谁改了我的目录"。
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


class FakeMetadataService:
    """内存版元数据服务：只实现端点用到的方法。"""

    records: dict[str, SimpleNamespace] = {}
    key_writes: list[tuple[str, str, object]] = []
    metadata_calls: list[dict] = []

    def __init__(self, _db=None):
        pass

    async def get_by_dataset_id(self, dataset_id: str):
        return self.records.get(dataset_id)

    async def update_metadata(self, dataset_id: str, **kwargs):
        FakeMetadataService.metadata_calls.append(kwargs)
        record = self.records[dataset_id]
        for field in ("name", "description", "visibility", "tags", "notes"):
            if kwargs.get(field) is not None:
                setattr(record, field, kwargs[field])
        # 与真实实现一致：None = 不改，只有显式给值才整份覆盖
        if kwargs.get("extra_config") is not None:
            record.extra_config = dict(kwargs["extra_config"])
        return record

    async def update_extra_config_key(self, dataset_id: str, *, key: str, value, user_name=None):
        FakeMetadataService.key_writes.append((dataset_id, key, value))
        record = self.records[dataset_id]
        config = dict(record.extra_config or {})
        if value in (None, {}, []):
            config.pop(key, None)
        else:
            config[key] = value
        record.extra_config = config
        return record

    def _serialize_metadata(self, record):
        return {
            "dataset_id": record.ragflow_dataset_id,
            "name": record.name,
            "visibility": record.visibility,
            "extra_config": record.extra_config,
        }


@pytest.fixture()
def app_client(monkeypatch):
    FakeMetadataService.records = {}
    FakeMetadataService.key_writes = []
    FakeMetadataService.metadata_calls = []

    FakeMetadataService.records["d-team"] = SimpleNamespace(
        ragflow_dataset_id="d-team",
        name="运维库",
        description="",
        visibility="team",
        tags=[],
        notes="",
        owner_dept_id=None,
        status="active",
        extra_config={"folder_structure": {"制度文件": ["doc-1", "doc-2"]}},
    )

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetadataService)
    monkeypatch.setattr(ragflow, "require_element_permission", _noop)
    monkeypatch.setattr(ragflow, "require_dataset_write_access", _noop)
    monkeypatch.setattr(ragflow, "clear_dataset_list_cache", lambda: None)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _noop)

    app = FastAPI()
    app.include_router(ragflow.router, prefix="/api/portal/ragflow")
    app.dependency_overrides[ragflow.get_current_user] = lambda: {
        "user_id": 1,
        "user_name": "tester",
        "role": "admin",
    }
    app.dependency_overrides[ragflow.get_db_session] = lambda: None

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_custom_questions_are_written_by_key(app_client):
    """只提交 custom_questions → 只写这一个键，目录结构原样保留。"""
    async with app_client as client:
        res = await client.put(
            "/api/portal/ragflow/datasets/d-team/metadata",
            json={"name": "运维库", "custom_questions": [{"label": "怎么排查", "query": "怎么排查"}]},
        )

    assert res.status_code == 200, res.text
    assert FakeMetadataService.key_writes == [
        ("d-team", "custom_questions", [{"label": "怎么排查", "query": "怎么排查"}])
    ]
    assert FakeMetadataService.records["d-team"].extra_config["folder_structure"] == {
        "制度文件": ["doc-1", "doc-2"]
    }
    # 整份覆盖那条路完全没被走过
    assert all(call.get("extra_config") is None for call in FakeMetadataService.metadata_calls)


@pytest.mark.anyio
async def test_metadata_only_request_does_not_touch_extra_config(app_client):
    """只改名称的请求不该写出任何 extra_config 变更。"""
    async with app_client as client:
        res = await client.put(
            "/api/portal/ragflow/datasets/d-team/metadata",
            json={"name": "新名字"},
        )

    assert res.status_code == 200, res.text
    assert FakeMetadataService.key_writes == []
    assert FakeMetadataService.records["d-team"].extra_config == {
        "folder_structure": {"制度文件": ["doc-1", "doc-2"]}
    }


@pytest.mark.anyio
async def test_clearing_custom_questions_is_explicit(app_client):
    """传空数组 = 明确清空；不传 = 不动。两者必须区分开。

    端点用 `payload.custom_questions or []` 把它归一成空列表再交给键级写，
    后者对空值执行"删键"。若这里误用「值非空才处理」，清空就成了 no-op。
    """
    async with app_client as client:
        res = await client.put(
            "/api/portal/ragflow/datasets/d-team/metadata",
            json={"custom_questions": []},
        )

    assert res.status_code == 200, res.text
    assert FakeMetadataService.key_writes == [("d-team", "custom_questions", [])]
    assert "custom_questions" not in FakeMetadataService.records["d-team"].extra_config
    assert FakeMetadataService.records["d-team"].extra_config["folder_structure"] == {
        "制度文件": ["doc-1", "doc-2"]
    }
