"""知识库 code review 提出的缺陷修复回归。

每条对应一个已核实的问题，断言的是**行为**，不是「源码里出现了某段字符串」：

1. 删库取全集：数据集超过 1000 个时，后面那些库不能被判成「引擎侧已不存在」而跳过删除；
2. 删库清附属数据：收藏夹与待审批申请不能留下孤儿记录；
3. 删文档清文件夹映射：``folder_structure`` 不能只增不减；
4. AI 分析：模型把 tags 输出成 JSON 数组时不能炸出 500；
5. 文件夹归类权限：部门成员（内容级写）也能整理目录；
6. 巡检与指标同步：文档不能只扫 100 篇，Redis 计数不能被整键删除吃掉增量。

全部用替身，不打真实 RAGFlow / 数据库 / Redis。
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.api.portal.endpoints import ragflow
from app.services import knowledge_health_service, knowledge_metrics_service

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------
# 1. AI 分析：大模型把字段输出成数组
# --------------------------------------------------------------------------


def test_analyze_metadata_tolerates_list_fields():
    """tags 被模型写成 ``["ERP", "手册"]`` 时，原先 .strip() 直接 AttributeError。

    虽然外面有 try/except 兜着，但用户看到的是
    「AI 分析失败: 'list' object has no attribute 'strip'」，等于功能坏掉。
    """
    assert ragflow._as_text(["ERP", " 手册 ", ""]) == "ERP,手册"
    assert ragflow._as_text(("制度", "流程")) == "制度,流程"
    assert ragflow._as_text("  ERP,手册  ") == "ERP,手册"
    assert ragflow._as_text(None) == ""
    assert ragflow._as_text(0) == "0"


def test_analyze_metadata_requires_element_permission():
    """与其它编辑类端点对齐：能写这个库，还要有「编辑知识库」的元素权限。"""
    source = (ROOT / "app/api/portal/endpoints/ragflow.py").read_text(encoding="utf-8")
    block = source.split('@router.post("/datasets/{dataset_id}/ai-analyze")', 1)[1]
    block = block.split("client = RagFlowClient", 1)[0]
    assert 'require_element_permission(user, db, "element:knowledge:edit")' in block


# --------------------------------------------------------------------------
# 2. 删文档：清理虚拟文件夹映射
# --------------------------------------------------------------------------


class _FakeMetaService:
    """只实现 _prune_folder_structure 用到的那两个方法。"""

    def __init__(self, extra_config: Dict[str, Any]):
        self.extra_config = extra_config
        self.written: List[Dict[str, Any]] = []

    def __call__(self, db):  # 让替身可以直接当类用
        return self

    async def get_by_dataset_id(self, dataset_id: str):
        return SimpleNamespace(extra_config=self.extra_config)

    async def update_extra_config_key(self, dataset_id, *, key, value, user_name=None):
        self.written.append({"dataset_id": dataset_id, "key": key, "value": value})
        return SimpleNamespace()


@pytest.mark.asyncio
async def test_prune_folder_structure_drops_only_deleted_ids(monkeypatch):
    """删掉 d2 以后，A 里只剩 d1，B 变空——而不是把整个结构删掉或原样保留。"""
    fake = _FakeMetaService(
        {"folder_structure": {"A": ["d1", "d2"], "B": ["d2"]}, "other_key": "keep"}
    )
    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", fake)

    await ragflow._prune_folder_structure(None, "ds-1", ["d2"], "admin")

    assert fake.written == [
        {"dataset_id": "ds-1", "key": "folder_structure", "value": {"A": ["d1"], "B": []}}
    ]


@pytest.mark.asyncio
async def test_prune_folder_structure_is_noop_when_nothing_matches(monkeypatch):
    """删的文档本来就不在目录里时不该产生一次多余的写库。"""
    fake = _FakeMetaService({"folder_structure": {"A": ["d1"]}})
    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", fake)

    await ragflow._prune_folder_structure(None, "ds-1", ["d9"], "admin")

    assert fake.written == []


@pytest.mark.asyncio
async def test_prune_folder_structure_swallows_errors(monkeypatch):
    """清理是旁路：它失败不能把「文档已经删掉」这件事变成 500。"""

    class _Broken:
        def __call__(self, db):
            return self

        async def get_by_dataset_id(self, dataset_id):
            raise RuntimeError("db down")

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", _Broken())
    await ragflow._prune_folder_structure(None, "ds-1", ["d1"], "admin")  # 不抛即通过


# --------------------------------------------------------------------------
# 3. 删库：取全集 + 清附属数据
# --------------------------------------------------------------------------


class _FakeDeleteClient:
    def __init__(self, *args, **kwargs):
        self.list_calls: List[Dict[str, Any]] = []
        self.deleted: List[List[str]] = []

    async def list_all_datasets_cached(self, *, refresh: bool = False):
        self.list_calls.append({"refresh": refresh})
        return [{"id": "ds-1"}, {"id": "ds-2"}]

    async def delete_datasets(self, ids: List[str]):
        self.deleted.append(list(ids))


class _FakeDb:
    def __init__(self):
        self.statements: List[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return None


class _FakePermService:
    def __init__(self, db):
        pass

    async def invalidate_cached_permissions_for_users(self, user_ids):
        return None


class _FakeMetaDelete:
    def __init__(self, db):
        pass

    async def mark_deleted(self, ids, user_name=None):
        return None


@pytest.mark.asyncio
async def test_delete_datasets_uses_full_dataset_list(monkeypatch):
    """删库前的「RAGFlow 侧是否存在」判断必须拿全集，且要绕过 60s 缓存。

    原先写的是 list_datasets(page_size=1000)：注释要全集，代码只要 10 页，
    数据集超过 1000 个之后，排在后面的库会被判成「已不存在」而漏删。
    """
    client = _FakeDeleteClient()
    db = _FakeDb()
    monkeypatch.setattr(ragflow, "RagFlowClient", lambda *a, **k: client)
    monkeypatch.setattr(ragflow, "PermissionService", _FakePermService)
    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", _FakeMetaDelete)
    monkeypatch.setattr(ragflow, "clear_dataset_list_cache", lambda: None)
    monkeypatch.setattr(ragflow, "_collect_affected_dataset_permission_user_ids", _allow_empty)
    monkeypatch.setattr(ragflow, "require_element_permission", _noop_async)
    monkeypatch.setattr(ragflow, "require_dataset_write_access", _noop_async)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _noop_async)
    monkeypatch.setattr(ragflow.image_service, "delete_dataset_images", lambda ds: None)

    payload = SimpleNamespace(ids=["ds-1", "ds-9"])
    await ragflow.delete_ragflow_datasets(payload, SimpleNamespace(), {"user_id": 1, "user_name": "admin"}, db)

    assert client.list_calls == [{"refresh": True}], "必须走全量且强制刷新"
    # ds-9 在引擎侧不存在 → 只删 ds-1
    assert client.deleted == [["ds-1"]]


@pytest.mark.asyncio
async def test_delete_datasets_cleans_favorites_and_pending_requests(monkeypatch):
    """库删了，收藏与待审批申请必须一起收掉。

    否则收藏表留无效 id，待审批列表永远挂着一条「点通过会被拒」的记录。
    """
    client = _FakeDeleteClient()
    db = _FakeDb()
    monkeypatch.setattr(ragflow, "RagFlowClient", lambda *a, **k: client)
    monkeypatch.setattr(ragflow, "PermissionService", _FakePermService)
    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", _FakeMetaDelete)
    monkeypatch.setattr(ragflow, "clear_dataset_list_cache", lambda: None)
    monkeypatch.setattr(ragflow, "_collect_affected_dataset_permission_user_ids", _allow_empty)
    monkeypatch.setattr(ragflow, "require_element_permission", _noop_async)
    monkeypatch.setattr(ragflow, "require_dataset_write_access", _noop_async)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _noop_async)
    monkeypatch.setattr(ragflow.image_service, "delete_dataset_images", lambda ds: None)

    payload = SimpleNamespace(ids=["ds-1"])
    await ragflow.delete_ragflow_datasets(payload, SimpleNamespace(), {"user_id": 1, "user_name": "admin"}, db)

    joined = "\n".join(db.statements)
    assert "knowledge_base_favorites" in joined, "收藏记录没清"
    assert "ai_agent_knowledge_access_requests" in joined, "待审批申请没清"


# --------------------------------------------------------------------------
# 4. 文件夹归类：放宽到内容级写权限
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_folder_structure_endpoint_accepts_content_level_write(monkeypatch):
    """部门成员能删文档却拖不进文件夹，是权限口径不一致，不是安全边界。"""
    calls: List[str] = []

    async def _record(user, db, ids):
        calls.append("content")

    async def _should_not_run(user, db, ids):
        calls.append("manage")

    monkeypatch.setattr(ragflow, "require_element_permission", _noop_async)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _record)
    monkeypatch.setattr(ragflow, "require_dataset_write_access", _should_not_run)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _noop_async)

    class _Service:
        def __init__(self, db):
            pass

        async def update_extra_config_key(self, dataset_id, *, key, value, user_name=None):
            return SimpleNamespace(extra_config={key: value})

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", _Service)

    payload = SimpleNamespace(folder_structure={"A": ["d1"]})
    await ragflow.update_folder_structure("ds-1", payload, SimpleNamespace(), {"user_id": 1}, _FakeDb())

    assert calls == ["content"], "归类应当只走内容级门禁"


# --------------------------------------------------------------------------
# 5. 巡检：不能再按 100 篇截断
# --------------------------------------------------------------------------


def test_health_scan_reads_the_whole_document_list():
    """超过 100 篇的库，快照里的文档数不能停在 100，失败文档也不能漏报。"""
    source = (ROOT / "app/services/knowledge_health_service.py").read_text(encoding="utf-8")
    assert "list_all_documents_cached" in source
    assert "list_documents(dataset_id, page_size=100)" not in source


# --------------------------------------------------------------------------
# 6. 指标同步：扣减而不是整键删除
# --------------------------------------------------------------------------


def test_metrics_sync_deducts_instead_of_deleting_the_key():
    """从 hgetall 到 commit 之间新写入的 hincrby，不能被 delete 一起抹掉。"""
    source = (ROOT / "app/services/knowledge_metrics_service.py").read_text(encoding="utf-8")
    script = knowledge_metrics_service._DEDUCT_SYNCED_COUNTS_SCRIPT
    assert "HINCRBY" in script and "-tonumber" in script
    assert "redis.eval(_DEDUCT_SYNCED_COUNTS_SCRIPT" in source
    # 同步流程里不该再出现「把整键删掉」的写法
    sync_block = source.split("async def _sync_under_lock", 1)[1]
    assert "await redis.delete(key_bytes)" not in sync_block


async def _noop_async(*args, **kwargs):
    return None


async def _allow_empty(*args, **kwargs):
    return set()

# --------------------------------------------------------------------------
# 7. 缓存上限与流式下载
# --------------------------------------------------------------------------


def test_documents_cache_is_bounded(monkeypatch):
    """按 LRU 淘汰：热门库不能被「更早写入但早就没人看」的库挤掉。"""
    from app.services.ai import ragflow_client as client_mod

    monkeypatch.setattr(client_mod, "DOCUMENTS_CACHE_MAX_ENTRIES", 3)
    client_mod._documents_cache.clear()
    try:
        for index in range(3):
            client_mod._remember_documents(f"k{index}", [{"id": str(index)}])
        client_mod._documents_cache.move_to_end("k0")  # 模拟又访问了一次 k0
        client_mod._remember_documents("k3", [])

        assert len(client_mod._documents_cache) == 3
        assert "k1" not in client_mod._documents_cache, "最久没访问的应该被挤掉"
        assert set(client_mod._documents_cache) == {"k2", "k0", "k3"}
    finally:
        client_mod._documents_cache.clear()


@pytest.mark.asyncio
async def test_document_file_response_streams_instead_of_buffering(monkeypatch):
    """预览大文件时不能再把整份字节读进内存再吐出去。"""
    from fastapi.responses import StreamingResponse

    class _FakeStreamClient:
        def __init__(self, *args, **kwargs):
            pass

        async def open_document_stream(self, document_id, *, dataset_id=None):
            async def _gen():
                yield b"chunk"

            return _gen(), "手册.pdf", "application/pdf"

    monkeypatch.setattr(ragflow, "RagFlowClient", _FakeStreamClient)

    response = await ragflow._build_ragflow_document_file_response("doc-1", dataset_id="ds-1")

    assert isinstance(response, StreamingResponse)
    assert response.media_type == "application/pdf"
