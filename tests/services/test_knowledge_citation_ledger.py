"""知识库引用编号台账：编号必须整轮全局唯一。

若工具每次调用都从 1 重新编号，一轮内的多次检索会让模型看到重复的 `[ID:n]`，
回答里的引用无法反查属于哪一批切片，引用量会被安到错误的文档上。
"""

from app.services.ai.knowledge_citation_ledger import KnowledgeCitationLedger


def test_ledger_issues_monotonic_refs():
    ledger = KnowledgeCitationLedger()

    assert ledger.register({"doc_id": "a"}) == "1"
    assert ledger.register({"doc_id": "b"}) == "2"
    assert ledger.register({"doc_id": "c"}) == "3"
    assert ledger.next_ref == 4


def test_ledger_across_two_searches_never_reuses_numbers():
    """两次检索的编号空间必须连续——这正是 `[ID:n]` 不产生歧义的前提。"""
    ledger = KnowledgeCitationLedger()

    first = [ledger.register({"doc_id": f"a{i}"}) for i in range(3)]
    second = [ledger.register({"doc_id": f"b{i}"}) for i in range(2)]

    assert first == ["1", "2", "3"]
    assert second == ["4", "5"]
    assert len(set(first + second)) == 5


def test_ledger_entries_expose_slice_metadata():
    ledger = KnowledgeCitationLedger()

    ref = ledger.register({"doc_id": "d1", "doc_name": "制度.pdf", "dataset_id": "ds1"})

    assert ledger.entries[ref] == {
        "source_type": "knowledge",
        "doc_id": "d1",
        "doc_name": "制度.pdf",
        "dataset_id": "ds1",
    }


def test_ledger_defaults_missing_source_type_to_knowledge():
    ledger = KnowledgeCitationLedger()

    ref = ledger.register({"doc_id": "d1"})

    assert ledger.entries[ref]["source_type"] == "knowledge"


def test_ledger_entries_returns_a_copy():
    ledger = KnowledgeCitationLedger()
    ledger.register({"doc_id": "d1"})

    ledger.entries.clear()

    assert len(ledger) == 1
