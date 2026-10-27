"""会话 scope 新增的 knowledge_scope / pending_scope_gate 字段编解码。

``replace`` 是整体覆盖写，如果不显式保留新字段，门禁写入的范围会被下一次
挂载操作抹掉——这些用例就是锁住这一点。
"""
from app.services import conversation_resource_service as mod
from app.services.conversation_resource_service import ConversationResourceService


def test_decode_keeps_knowledge_scope_and_pending():
    raw = (
        '{"knowledge_scope": {"mode": "ranked", "dataset_ids": ["ds_a"],'
        ' "confirmed_at": "2026-10-27T11:00:00"},'
        ' "pending_scope_gate": {"gate_id": "sg_1", "ranked_ids": ["ds_a"]}}'
    )

    scope = ConversationResourceService._decode_scope(raw)

    assert scope["knowledge_scope"]["mode"] == "ranked"
    assert scope["knowledge_scope"]["dataset_ids"] == ["ds_a"]
    assert scope["pending_scope_gate"]["gate_id"] == "sg_1"


def test_decode_tolerates_legacy_value_without_new_fields():
    scope = ConversationResourceService._decode_scope('{"datasets": [], "skills": []}')

    assert scope["knowledge_scope"] is None
    assert scope["pending_scope_gate"] is None
    assert scope["datasets"] == []


def test_decode_rejects_non_mapping_scope_fields():
    scope = ConversationResourceService._decode_scope(
        '{"knowledge_scope": "ranked", "pending_scope_gate": ["x"]}'
    )

    assert scope["knowledge_scope"] is None
    assert scope["pending_scope_gate"] is None


def test_empty_scope_has_new_fields():
    scope = ConversationResourceService._empty_scope()

    assert scope["knowledge_scope"] is None
    assert scope["pending_scope_gate"] is None


async def test_replace_preserves_new_fields(monkeypatch):
    async def _no_redis():
        return None

    monkeypatch.setattr(mod, "get_redis", _no_redis)

    normalized = await ConversationResourceService.replace(
        1,
        "c1",
        {
            "project_name": "项目 A",
            "knowledge_scope": {"mode": "picked", "dataset_ids": ["ds_x"]},
            "pending_scope_gate": None,
        },
    )

    assert normalized["knowledge_scope"] == {"mode": "picked", "dataset_ids": ["ds_x"]}
    assert normalized["pending_scope_gate"] is None
    assert normalized["project_name"] == "项目 A"


async def test_patch_scope_merges_without_dropping_mounts(monkeypatch):
    stored = {
        "project_name": "旧项目",
        "datasets": [{"id": "ds_meta"}],
        "knowledge_bases": [],
        "skills": [{"id": "skill_1"}],
        "mcp_tools": [],
        "knowledge_scope": None,
        "pending_scope_gate": None,
    }

    async def _fake_get(user_id, conversation_id):
        return dict(stored)

    async def _fake_replace(user_id, conversation_id, scope):
        stored.clear()
        stored.update(scope)
        return dict(scope)

    monkeypatch.setattr(ConversationResourceService, "get", _fake_get)
    monkeypatch.setattr(ConversationResourceService, "replace", _fake_replace)

    result = await ConversationResourceService.patch_scope(
        1,
        "c1",
        knowledge_scope={"mode": "ranked", "dataset_ids": ["ds_a"]},
    )

    assert result["knowledge_scope"] == {"mode": "ranked", "dataset_ids": ["ds_a"]}
    assert result["skills"] == [{"id": "skill_1"}]
    assert result["datasets"] == [{"id": "ds_meta"}]
    assert result["project_name"] == "旧项目"


async def test_patch_scope_without_conversation_returns_empty(monkeypatch):
    result = await ConversationResourceService.patch_scope(1, "", knowledge_scope={"mode": "all"})

    assert result["knowledge_scope"] is None
    assert result["skills"] == []


async def test_replace_inherits_scope_fields_when_caller_omits_them(monkeypatch):
    """PUT /conversation/{id}/resource-scope 的请求模型只有 project_name + 4 个列表。

    调用方「没传」是常态，不是例外：若照旧写 None，用户挂一个技能或设一个默认数据集，
    刚确认的检索范围就被抹掉，下一轮门禁再弹一次；卡片正开着时 pending 快照
    也会一起没，点了选项还原不到 ID 又弹新卡。
    """
    stored = {
        "project_name": "",
        "datasets": [],
        "knowledge_bases": [],
        "skills": [],
        "mcp_tools": [],
        "knowledge_scope": {"mode": "ranked", "dataset_ids": ["ds_keep"]},
        "pending_scope_gate": {"gate_id": "sg_keep"},
    }

    async def _fake_get(user_id, conversation_id):
        return dict(stored)

    async def _no_redis():
        return None

    monkeypatch.setattr(ConversationResourceService, "get", _fake_get)
    monkeypatch.setattr(mod, "get_redis", _no_redis)

    # 模拟前端挂载一个 MCP 工具：只带 4 个列表，不含门禁字段
    normalized = await ConversationResourceService.replace(
        1,
        "c1",
        {"project_name": "项目 A", "mcp_tools": [{"id": "mcp_1", "name": "搜索"}]},
    )

    assert normalized["knowledge_scope"] == {"mode": "ranked", "dataset_ids": ["ds_keep"]}
    assert normalized["pending_scope_gate"] == {"gate_id": "sg_keep"}
    assert normalized["mcp_tools"] == [{"id": "mcp_1", "name": "搜索"}]


async def test_replace_can_still_clear_scope_explicitly(monkeypatch):
    """显式传 None 仍然要能清空，不能被「继承」逻辑挡住。"""
    stored = {"knowledge_scope": {"mode": "ranked"}, "pending_scope_gate": {"gate_id": "g"}}

    async def _fake_get(user_id, conversation_id):
        return dict(stored)

    async def _no_redis():
        return None

    monkeypatch.setattr(ConversationResourceService, "get", _fake_get)
    monkeypatch.setattr(mod, "get_redis", _no_redis)

    normalized = await ConversationResourceService.replace(
        1,
        "c1",
        {"project_name": "", "knowledge_scope": None},
    )

    assert normalized["knowledge_scope"] is None
    assert normalized["pending_scope_gate"] == {"gate_id": "g"}
