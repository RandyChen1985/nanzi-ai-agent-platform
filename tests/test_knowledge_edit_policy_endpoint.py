"""`PUT /datasets/{id}/edit-policy` 接口级测试（不依赖真实 DB / Redis）。

纯函数单测挡不住端点内部写错（例如 `datetime.now()` 这类 AttributeError），
所以这里真的把请求打进路由，只把「数据库访问」和「权限校验」换成假的：

- `KnowledgeBaseMetadataService` 换成内存实现；
- `require_element_permission` / `require_dataset_write_access` 换成 no-op；
- `get_db_session` / `get_current_user` 用 dependency_overrides 覆盖。
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


class FakeMetadataService:
    """内存版元数据服务：只实现端点用到的两个方法。"""

    records: dict[str, SimpleNamespace] = {}
    writes: list[tuple[str, str, dict]] = []

    def __init__(self, _db=None):
        pass

    async def get_by_dataset_id(self, dataset_id: str):
        record = self.records.get(dataset_id)
        return record

    async def update_extra_config_key(self, dataset_id: str, *, key: str, value, user_name=None):
        FakeMetadataService.writes.append((dataset_id, key, value))
        record = self.records[dataset_id]
        config = dict(record.extra_config or {})
        config[key] = value
        record.extra_config = config
        return record


@pytest.fixture()
def app_client(monkeypatch):
    FakeMetadataService.records = {}
    FakeMetadataService.writes = []

    base = {
        "private": "d-private",
        "team": "d-team",
        "public": "d-public",
    }
    for visibility, dataset_id in base.items():
        FakeMetadataService.records[dataset_id] = SimpleNamespace(
            ragflow_dataset_id=dataset_id,
            visibility=visibility,
            status="active",
            extra_config={"folder_structure": {"a": ["doc-1"]}},
        )

    async def _noop(*args, **kwargs):
        return None

    async def _noop_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetadataService)
    monkeypatch.setattr(ragflow, "require_element_permission", _noop)
    monkeypatch.setattr(ragflow, "require_dataset_write_access", _noop)
    monkeypatch.setattr(ragflow, "clear_dataset_list_cache", lambda: None)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _noop_audit)

    app = FastAPI()
    app.include_router(ragflow.router, prefix="/api/portal/ragflow")
    app.dependency_overrides[ragflow.get_current_user] = lambda: {
        "user_id": 1,
        "user_name": "tester",
        "role": "admin",
    }
    app.dependency_overrides[ragflow.get_db_session] = lambda: None

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.anyio
async def test_update_edit_policy_persists_subkey_only(app_client):
    """成功路径：写 edit_policy 子键，其他子键原样保留。"""
    async with app_client as client:
        res = await client.put(
            "/api/portal/ragflow/datasets/d-public/edit-policy",
            json={"mode": "granted", "editors": []},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["data"]["edit_policy"]["mode"] == "granted"

    dataset_id, key, value = FakeMetadataService.writes[-1]
    assert dataset_id == "d-public"
    assert key == "edit_policy"
    assert value["mode"] == "granted"
    # 只替换子键：folder_structure 不能被冲掉
    assert FakeMetadataService.records["d-public"].extra_config["folder_structure"] == {"a": ["doc-1"]}
    # 时间戳能正常生成（datetime.now() 写错时这里会直接 500）
    assert value["updated_at"]


@pytest.mark.anyio
async def test_update_edit_policy_rejects_mode_not_allowed_for_visibility(app_client):
    async with app_client as client:
        res = await client.put(
            "/api/portal/ragflow/datasets/d-public/edit-policy",
            json={"mode": "team_members", "editors": []},
        )
    assert res.status_code == 422
    assert "team_members" in res.text


@pytest.mark.anyio
async def test_update_edit_policy_rejects_private_dataset(app_client):
    async with app_client as client:
        res = await client.put(
            "/api/portal/ragflow/datasets/d-private/edit-policy",
            json={"mode": "readonly", "editors": []},
        )
    assert res.status_code == 400
    assert "个人私有库" in res.text


@pytest.mark.anyio
async def test_update_edit_policy_selected_requires_users(app_client):
    async with app_client as client:
        empty = await client.put(
            "/api/portal/ragflow/datasets/d-public/edit-policy",
            json={"mode": "selected", "editors": []},
        )
        with_role = await client.put(
            "/api/portal/ragflow/datasets/d-public/edit-policy",
            json={"mode": "selected", "editors": [{"type": "role", "id": 3}]},
        )
    assert empty.status_code == 422
    assert with_role.status_code == 422


@pytest.mark.anyio
async def test_team_dataset_accepts_team_members(app_client):
    async with app_client as client:
        res = await client.put(
            "/api/portal/ragflow/datasets/d-team/edit-policy",
            json={"mode": "team_members", "editors": []},
        )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["edit_policy"]["mode"] == "team_members"


def test_list_items_read_policy_from_local_metadata():
    """列表项的策略在 local_metadata.extra_config 里，不在顶层。

    merge_with_ragflow 把本地元数据整体挂在 local_metadata 下，早先后端直接读
    item["extra_config"] 永远是 None，列表就永远显示默认策略 —— 表现为
    「保存成功了，刷新又还原」。这条测试锁住取值路径。
    """
    items = [
        {
            "ragflow_dataset_id": "d1",
            "visibility": "public",
            "local_metadata": {
                "extra_config": {"edit_policy": {"mode": "granted", "editors": []}}
            },
        }
    ]
    ragflow._apply_knowledge_permission_flags(items, {"is_admin": True})
    assert items[0]["edit_policy"]["mode"] == "granted"


def test_list_items_fall_back_to_default_policy():
    """没有策略 / 元数据缺失时按可见范围默认值兜底，不能抛错。"""
    items = [
        {"ragflow_dataset_id": "d1", "visibility": "team", "local_metadata": None},
        {"ragflow_dataset_id": "d2", "visibility": "public"},
    ]
    ragflow._apply_knowledge_permission_flags(items, {"is_admin": True})
    assert items[0]["edit_policy"]["mode"] == "team_members"
    assert items[1]["edit_policy"]["mode"] == "readonly"
