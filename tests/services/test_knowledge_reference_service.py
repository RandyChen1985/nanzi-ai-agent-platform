import pytest

from app.services.knowledge_reference_service import (
    KnowledgeReferenceService,
    engine_dataset_ids,
)

pytestmark = pytest.mark.no_infrastructure

DS_1 = "a" * 32
DS_2 = "b" * 32
DS_9 = "c" * 32


def test_engine_dataset_ids_accepts_json_string_and_list():
    assert engine_dataset_ids('{"dataset_ids": ["%s", "%s"]}' % (DS_1, DS_2)) == [DS_1, DS_2]
    assert engine_dataset_ids({"dataset_ids": f"{DS_1},{DS_2}"}) == [DS_1, DS_2]
    assert engine_dataset_ids({"dataset_ids": [DS_1, DS_1, DS_2]}) == [DS_1, DS_2]
    assert engine_dataset_ids(None) == []
    assert engine_dataset_ids("not-json") == []


@pytest.mark.asyncio
async def test_agents_by_dataset_groups_by_dataset():
    class FakeResult:
        def all(self):
            return [
                ("agent-1", "kb_qa", "制度问答", True, {"dataset_ids": [DS_1, DS_2]}),
                ("agent-2", "kb_qa_2", "制度问答二", False, {"dataset_ids": [DS_1]}),
                ("agent-3", "other", "无关", True, {"dataset_ids": [DS_9]}),
            ]

    class FakeDb:
        async def execute(self, stmt):
            return FakeResult()

    service = KnowledgeReferenceService(db=FakeDb())
    grouped = await service.agents_by_dataset([DS_1, DS_2, "d" * 32])

    # 启用的排前面，其次按显示名
    assert [a["id"] for a in grouped[DS_1]] == ["agent-1", "agent-2"]
    assert [a["id"] for a in grouped[DS_2]] == ["agent-1"]
    assert "d" * 32 not in grouped
    assert grouped[DS_1][0]["is_enabled"] is True
