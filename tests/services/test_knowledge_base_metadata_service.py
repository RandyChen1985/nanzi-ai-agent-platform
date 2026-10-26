import pytest

from app.models.knowledge import KnowledgeBaseMetadata
from app.services.knowledge_base_service import KnowledgeBaseMetadataService


@pytest.mark.asyncio
async def test_merge_with_ragflow_prefers_local_metadata():
    service = KnowledgeBaseMetadataService(db=None)
    local = KnowledgeBaseMetadata(
        ragflow_dataset_id="ds-1",
        name="平台名称",
        description="平台描述",
        owner="数据团队",
        owner_dept_id=42,
        visibility="team",
        tags=["faq", "policy"],
        notes="内部备注",
        status="active",
    )

    async def fake_list_metadata(include_deleted=False):
        return [local]

    service.list_metadata = fake_list_metadata

    merged = await service.merge_with_ragflow([
        {
            "id": "ds-1",
            "name": "RAGFlow 名称",
            "description": "RAGFlow 描述",
            "doc_count": 2,
            "chunk_count": 12,
        }
    ])

    assert merged[0]["platform_name"] == "平台名称"
    assert merged[0]["platform_description"] == "平台描述"
    assert merged[0]["owner"] == "数据团队"
    # 业务归属部门随合并结果透出，供前端左侧树按部门分组
    assert merged[0]["owner_dept_id"] == 42
    assert merged[0]["local_metadata"]["owner_dept_id"] == 42
    assert merged[0]["tags"] == ["faq", "policy"]
    assert merged[0]["doc_count"] == 2
    assert merged[0]["is_missing_in_ragflow"] is False


@pytest.mark.asyncio
async def test_merge_with_ragflow_marks_local_record_missing():
    service = KnowledgeBaseMetadataService(db=None)
    local = KnowledgeBaseMetadata(
        ragflow_dataset_id="lost-ds",
        name="失联知识库",
        description="本地存在但 RAGFlow 不存在",
        tags=[],
        status="active",
    )

    async def fake_list_metadata(include_deleted=False):
        return [local]

    service.list_metadata = fake_list_metadata

    merged = await service.merge_with_ragflow([])

    assert merged[0]["id"] == "lost-ds"
    assert merged[0]["status"] == "missing"
    assert merged[0]["is_missing_in_ragflow"] is True


class _FakeSession:
    """最小 db 会话：update_metadata 只会调用 add() 与 flush()。"""

    def __init__(self):
        self.flush_count = 0

    def add(self, obj):
        pass

    async def flush(self):
        self.flush_count += 1


@pytest.mark.asyncio
async def test_update_metadata_keeps_owner_fields_when_not_provided():
    service = KnowledgeBaseMetadataService(db=_FakeSession())
    record = KnowledgeBaseMetadata(
        ragflow_dataset_id="ds-keep",
        name="知识库",
        owner="数据团队",
        owner_dept_id=42,
        status="active",
    )

    async def fake_get_by_dataset_id(dataset_id):
        return record

    service.get_by_dataset_id = fake_get_by_dataset_id

    updated = await service.update_metadata("ds-keep", name="新名称")

    # PATCH 语义：未出现的字段必须保持原值，不能被 None 覆盖
    assert updated.owner == "数据团队"
    assert updated.owner_dept_id == 42
    assert updated.name == "新名称"


@pytest.mark.asyncio
async def test_update_metadata_can_clear_owner_department():
    service = KnowledgeBaseMetadataService(db=_FakeSession())
    record = KnowledgeBaseMetadata(
        ragflow_dataset_id="ds-clear",
        name="知识库",
        owner="数据团队",
        owner_dept_id=42,
        status="active",
    )

    async def fake_get_by_dataset_id(dataset_id):
        return record

    service.get_by_dataset_id = fake_get_by_dataset_id

    updated = await service.update_metadata("ds-clear", owner_dept_id=0)

    # 传 0 表示显式清空归属部门；owner 文本未被显式清空，因此保留为历史文本
    assert updated.owner_dept_id is None
    assert updated.owner == "数据团队"


@pytest.mark.asyncio
async def test_sync_from_ragflow_creates_missing_and_preserves_local_fields():
    service = KnowledgeBaseMetadataService(db=None)
    existing = KnowledgeBaseMetadata(
        ragflow_dataset_id="ds-existing",
        name="旧名称",
        description="旧描述",
        owner="业务负责人",
        visibility="team",
        tags=["local"],
        notes="本地备注",
        extra_config={"keep": True},
        status="missing",
    )
    created_payloads = []

    async def fake_get_by_dataset_id(dataset_id):
        return existing if dataset_id == "ds-existing" else None

    async def fake_upsert_metadata(**kwargs):
        created_payloads.append(kwargs)
        return KnowledgeBaseMetadata(
            ragflow_dataset_id=kwargs["ragflow_dataset_id"],
            name=kwargs["name"],
            description=kwargs["description"],
            status=kwargs["status"],
        )

    service.get_by_dataset_id = fake_get_by_dataset_id
    service.upsert_metadata = fake_upsert_metadata

    result = await service.sync_from_ragflow_datasets(
        [
            {"id": "ds-existing", "name": "新名称", "description": "新描述"},
            {"id": "ds-new", "name": "新知识库", "description": "新知识库描述"},
            {"id": "meta-hidden", "name": "meta-system", "description": "should skip"},
        ],
        user_name="tester",
    )

    assert result == {"created": 1, "updated": 1, "skipped": 1}
    assert existing.name == "新名称"
    assert existing.description == "新描述"
    assert existing.status == "active"
    assert existing.owner == "业务负责人"
    assert existing.tags == ["local"]
    assert existing.notes == "本地备注"
    assert existing.extra_config == {"keep": True}
    assert created_payloads[0]["ragflow_dataset_id"] == "ds-new"
    assert created_payloads[0]["status"] == "active"
    # 同步新建的库必须是公共级：否则一次同步就把 RAGFlow 侧存量库
    # 全变成执行同步者的个人私有库，其他人连访问入口都没有
    assert created_payloads[0]["visibility"] == "public"


class _FakeDb:
    """transfer_owner 只依赖 flush；这里给个最小实现，避免引入真库。"""

    def __init__(self):
        self.flushed = 0

    async def flush(self):
        self.flushed += 1


@pytest.mark.asyncio
async def test_transfer_owner_moves_owner_and_keeps_created_by():
    db = _FakeDb()
    service = KnowledgeBaseMetadataService(db=db)
    local = KnowledgeBaseMetadata(
        ragflow_dataset_id="ds-handover",
        name="待交接知识库",
        created_by="alice",
        owner_user_id=1,
        status="active",
    )

    async def fake_get(dataset_id):
        return local if dataset_id == "ds-handover" else None

    service.get_by_dataset_id = fake_get

    result = await service.transfer_owner(
        "ds-handover", target_user_id=2, user_name="admin"
    )

    assert result is not None
    record, previous_owner_user_id = result
    assert record.owner_user_id == 2
    # 创建人快照不能被转移覆盖：否则再也查不清这个库最初是谁建的
    assert record.created_by == "alice"
    assert previous_owner_user_id == 1
    assert record.updated_by == "admin"
    assert db.flushed == 1


@pytest.mark.asyncio
async def test_transfer_owner_returns_none_for_missing_or_deleted():
    service = KnowledgeBaseMetadataService(db=_FakeDb())

    async def missing(dataset_id):
        return None

    service.get_by_dataset_id = missing
    assert await service.transfer_owner("ds-gone", target_user_id=2) is None

    deleted = KnowledgeBaseMetadata(
        ragflow_dataset_id="ds-deleted",
        name="已删除",
        status="deleted",
    )

    async def soft_deleted(dataset_id):
        return deleted

    service.get_by_dataset_id = soft_deleted
    assert await service.transfer_owner("ds-deleted", target_user_id=2) is None
