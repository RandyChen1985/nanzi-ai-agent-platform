"""``extra_config`` 的键级写：改一个键不能动到别的键。

这个不变式是「隐藏 JSON 入口」之后唯一的安全网。``extra_config`` 里同时住着
``folder_structure``（虚拟文件夹）和 ``custom_questions``（门户快捷提问），
而元数据弹窗只该改后者——它手里并没有前者的最新值。

历史上这里踩过一次：移动文档走整份 ``PUT /metadata``，前端把缓存的
name/visibility 一起回写，并发时是 lost update。后来的修法是把「改目录结构」
收敛成键级写；本文件守的是同一条路的另一半——「改快捷提问」也不能碰目录结构。
"""

from types import SimpleNamespace

import pytest

from app.services.knowledge_base_service import KnowledgeBaseMetadataService

pytestmark = pytest.mark.no_infrastructure


class _FakeDb:
    def __init__(self) -> None:
        self.flushed = 0

    async def flush(self) -> None:
        self.flushed += 1


def _service(extra_config: dict) -> tuple[KnowledgeBaseMetadataService, SimpleNamespace, _FakeDb]:
    record = SimpleNamespace(
        extra_config=dict(extra_config),
        status="active",
        updated_by=None,
        updated_at=None,
    )
    db = _FakeDb()
    service = KnowledgeBaseMetadataService.__new__(KnowledgeBaseMetadataService)
    service.db = db

    async def _get_by_dataset_id(_dataset_id: str):
        return record

    service.get_by_dataset_id = _get_by_dataset_id  # type: ignore[method-assign]
    return service, record, db


@pytest.mark.asyncio
async def test_updating_custom_questions_keeps_folder_structure():
    service, record, db = _service(
        {
            "folder_structure": {"制度文件": ["doc-1", "doc-2"]},
            "custom_questions": [{"label": "旧", "query": "旧的"}],
        }
    )

    await service.update_extra_config_key(
        "ds-1", key="custom_questions", value=[{"label": "新", "query": "新的"}]
    )

    assert record.extra_config["custom_questions"] == [{"label": "新", "query": "新的"}]
    assert record.extra_config["folder_structure"] == {"制度文件": ["doc-1", "doc-2"]}
    assert db.flushed == 1


@pytest.mark.asyncio
async def test_clearing_custom_questions_keeps_folder_structure():
    """清空快捷提问是合法操作，但不能顺手把目录结构也删了。"""
    service, record, _ = _service(
        {
            "folder_structure": {"制度文件": ["doc-1"]},
            "custom_questions": [{"label": "旧", "query": "旧的"}],
        }
    )

    await service.update_extra_config_key("ds-1", key="custom_questions", value=[])

    assert "custom_questions" not in record.extra_config
    assert record.extra_config["folder_structure"] == {"制度文件": ["doc-1"]}


@pytest.mark.asyncio
async def test_unrelated_key_survives_untouched():
    """只认传入的那个 key，其余（包括将来新增的）一律原样保留。"""
    service, record, _ = _service(
        {"folder_structure": {"A": ["d1"]}, "future_key": {"keep": True}}
    )

    await service.update_extra_config_key("ds-1", key="custom_questions", value=[{"label": "x"}])

    assert record.extra_config["future_key"] == {"keep": True}
    assert record.extra_config["folder_structure"] == {"A": ["d1"]}


@pytest.mark.asyncio
async def test_mutates_a_copy_not_the_original_mapping():
    """SQLAlchemy 的 JSON 列靠引用变化检测变更，原地改不会落库。"""
    original = {"folder_structure": {"A": ["d1"]}}
    service, record, _ = _service(original)

    await service.update_extra_config_key("ds-1", key="custom_questions", value=[{"label": "x"}])

    assert record.extra_config is not original
