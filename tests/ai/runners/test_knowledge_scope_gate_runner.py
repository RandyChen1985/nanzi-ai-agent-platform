"""Runner 侧门禁：判定入口、卡片构造与失败放行。

这里不构造完整 runner，只针对新增的两个私有方法做单元验证；
完整链路（真的不发起检索）留给集成测试与手工验收。
"""
from types import SimpleNamespace

from app.services.ai.knowledge_catalog import (
    AuthorizedKnowledgeCatalog,
    KnowledgeBaseCatalogItem,
)
from app.services.ai.knowledge_scope_gate import (
    SCOPE_ALL,
    SCOPE_PICK,
    SCOPE_RANKED,
    ScopeGate,
    build_scope_options,
)
from app.services.ai.runners import knowledge_agent_runner as mod


def _runner() -> mod.KnowledgeAgentRunner:
    runner = object.__new__(mod.KnowledgeAgentRunner)
    runner.conversation_id = "c1"
    runner.execution_options = {}
    runner.user_info = {"user_id": 7, "user_name": "tester"}
    return runner


def _ctx(dataset_ids, *, bound=None, explicit=None, engine_config=None) -> SimpleNamespace:
    return SimpleNamespace(
        dataset_ids=list(dataset_ids),
        agent_dataset_ids=list(bound or []),
        knowledge_dataset_ids=list(explicit or []),
        engine_config=engine_config or {},
        is_admin=False,
    )


def _catalog(dataset_ids) -> AuthorizedKnowledgeCatalog:
    return AuthorizedKnowledgeCatalog(
        status="available",
        items=tuple(
            KnowledgeBaseCatalogItem(
                ragflow_dataset_id=dataset_id,
                name=f"财务制度{dataset_id}",
                description="报销 差旅 标准",
            )
            for dataset_id in dataset_ids
        ),
    )


def _install_scope_gate_fakes(monkeypatch, *, conversation_scope, catalog, settings=(True, 5, 3)):
    calls = {"patched": []}

    async def _fake_get(user_id, conversation_id):
        return conversation_scope

    async def _fake_patch(user_id, conversation_id, **fields):
        calls["patched"].append(fields)
        return {}

    async def _fake_settings(engine_config):
        return settings

    async def _fake_catalog(**kwargs):
        return catalog

    monkeypatch.setattr(mod.ConversationResourceService, "get", _fake_get)
    monkeypatch.setattr(mod.ConversationResourceService, "patch_scope", _fake_patch)
    monkeypatch.setattr(mod, "resolve_scope_gate_settings", _fake_settings)
    monkeypatch.setattr(
        "app.services.ai.knowledge_catalog.load_authorized_knowledge_catalog",
        _fake_catalog,
    )
    return calls


async def test_gate_opens_and_records_pending_snapshot(monkeypatch):
    ids = [f"ds_{index}" for index in range(8)]
    calls = _install_scope_gate_fakes(
        monkeypatch,
        conversation_scope={"knowledge_scope": None, "pending_scope_gate": None},
        catalog=_catalog(ids),
    )

    gate = await _runner()._maybe_open_scope_gate(query="报销 差旅 标准", ctx=_ctx(ids))

    assert gate is not None
    assert gate.total_count == 8
    assert calls["patched"], "应当把待回答快照写入会话"
    pending = calls["patched"][0]["pending_scope_gate"]
    assert pending["gate_id"] == gate.gate_id
    assert set(pending["all_ids"]) == set(ids)


async def test_gate_skipped_when_scope_already_confirmed(monkeypatch):
    ids = [f"ds_{index}" for index in range(8)]
    _install_scope_gate_fakes(
        monkeypatch,
        conversation_scope={"knowledge_scope": {"mode": "ranked", "dataset_ids": ["ds_0"]}},
        catalog=_catalog(ids),
    )

    gate = await _runner()._maybe_open_scope_gate(query="报销", ctx=_ctx(ids))

    assert gate is None


async def test_gate_skipped_when_explicit_selection_present(monkeypatch):
    ids = [f"ds_{index}" for index in range(8)]
    _install_scope_gate_fakes(
        monkeypatch,
        conversation_scope={"knowledge_scope": None},
        catalog=_catalog(ids),
    )

    gate = await _runner()._maybe_open_scope_gate(
        query="报销",
        ctx=_ctx(ids, explicit=["ds_0"]),
    )

    assert gate is None


async def test_gate_skipped_when_switch_off(monkeypatch):
    ids = [f"ds_{index}" for index in range(8)]
    _install_scope_gate_fakes(
        monkeypatch,
        conversation_scope={"knowledge_scope": None},
        catalog=_catalog(ids),
        settings=(False, 5, 3),
    )

    gate = await _runner()._maybe_open_scope_gate(query="报销", ctx=_ctx(ids))

    assert gate is None


async def test_gate_failure_never_blocks_request(monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("catalog down")

    monkeypatch.setattr(
        "app.services.ai.knowledge_catalog.load_authorized_knowledge_catalog",
        _boom,
    )

    gate = await _runner()._maybe_open_scope_gate(
        query="报销",
        ctx=_ctx([f"ds_{index}" for index in range(8)]),
    )

    assert gate is None


async def test_build_scope_gate_chunk_passes_disabled_options_through(monkeypatch):
    persisted = {}

    async def _fake_persist(*, event, user_id, conversation_id):
        persisted["event"] = event
        return {}

    monkeypatch.setattr(
        "app.services.ai.user_question.persist_user_question_event", _fake_persist
    )

    runner = _runner()
    gate = ScopeGate(
        gate_id="sg_test",
        options=[
            {"id": SCOPE_RANKED, "label": "在 3 个最相关库中检索", "description": "", "disabled": False},
            {"id": "__scope_all__", "label": "全部 8 个库", "description": "", "disabled": False},
            {"id": "__scope_bound__", "label": "仅智能体默认（未绑定）", "description": "", "disabled": True},
            {"id": "__scope_pick__", "label": "指定其他库…", "description": "", "disabled": False},
        ],
        ranked_ids=("ds_0",),
        all_ids=tuple(f"ds_{index}" for index in range(8)),
        total_count=8,
        bound_count=0,
    )

    chunk = await runner._build_scope_gate_chunk(gate=gate)

    assert chunk is not None
    assert chunk["type"] == "user_question"
    assert chunk["purpose"] == "knowledge_scope_gate"
    assert chunk["is_multi_select"] is False
    # 禁用项透传而不是删掉：用户能看到「有这一项、当前用不了」，前端负责置灰
    assert [option["id"] for option in chunk["options"]] == [
        SCOPE_RANKED,
        "__scope_all__",
        "__scope_bound__",
        "__scope_pick__",
    ]
    bound_option = next(item for item in chunk["options"] if item["id"] == "__scope_bound__")
    assert bound_option["disabled"] is True
    assert chunk["options"][0]["disabled"] is False


async def test_build_scope_gate_chunk_registers_pending_question(monkeypatch):
    """卡片发出前必须登记 pending，否则回执会被 preflight 判为「问题不存在」。"""
    persisted = {}

    async def _fake_persist(*, event, user_id, conversation_id):
        persisted["question_id"] = event["question_id"]
        persisted["user_id"] = user_id
        persisted["conversation_id"] = conversation_id
        return {}

    monkeypatch.setattr(
        "app.services.ai.user_question.persist_user_question_event", _fake_persist
    )

    gate = ScopeGate(
        gate_id="sg_test",
        options=[
            {"id": SCOPE_RANKED, "label": "A", "description": "", "disabled": False},
            {"id": "__scope_all__", "label": "B", "description": "", "disabled": False},
        ],
        ranked_ids=("ds_0",),
        all_ids=("ds_0", "ds_1"),
        total_count=2,
    )

    chunk = await _runner()._build_scope_gate_chunk(gate=gate)

    assert chunk is not None
    assert persisted["question_id"] == chunk["question_id"]
    assert persisted["user_id"] == "7"
    assert persisted["conversation_id"] == "c1"


async def test_build_scope_gate_chunk_returns_none_when_persist_fails(monkeypatch):
    """登记失败就不要发卡——发出去也是一张点了必然失败的卡。"""

    async def _boom(**kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.services.ai.user_question.persist_user_question_event", _boom)

    gate = ScopeGate(
        gate_id="sg_test",
        options=[
            {"id": SCOPE_RANKED, "label": "A", "description": "", "disabled": False},
            {"id": "__scope_all__", "label": "B", "description": "", "disabled": False},
        ],
        all_ids=("ds_0", "ds_1"),
        total_count=2,
    )

    assert await _runner()._build_scope_gate_chunk(gate=gate) is None


def test_original_query_before_receipt_skips_receipt_text():
    """回执轮要拿原问题当检索词，而不是整段【用户回答】文本。"""
    history = [
        {"role": "user", "content": "报销标准是多少"},
        {"role": "assistant", "content": "我需要你补充确认：本轮可检索的知识库有 10 个"},
        {"role": "user", "content": "【用户回答】\n所选选项: 仅智能体默认 (__scope_bound__)\nquestion_id: uq_x"},
    ]

    assert mod.KnowledgeAgentRunner._original_query_before_receipt(history) == "报销标准是多少"


def test_original_query_before_receipt_returns_empty_without_original():
    history = [{"role": "user", "content": "【用户回答】\n所选选项: A (__scope_all__)"}]

    assert mod.KnowledgeAgentRunner._original_query_before_receipt(history) == ""
    assert mod.KnowledgeAgentRunner._original_query_before_receipt([]) == ""


async def test_gate_skipped_without_conversation_id(monkeypatch):
    """没有会话就无法登记 pending，此时不该弹出一张点了必然失败的卡。"""
    runner = object.__new__(mod.KnowledgeAgentRunner)
    runner.conversation_id = ""
    runner.user_info = {"user_id": 7}

    gate = await runner._maybe_open_scope_gate(
        query="报销",
        ctx=_ctx([f"ds_{index}" for index in range(8)]),
    )

    assert gate is None


async def test_gate_skipped_without_user_id(monkeypatch):
    runner = object.__new__(mod.KnowledgeAgentRunner)
    runner.conversation_id = "c1"
    runner.execution_options = {}
    runner.user_info = {}

    gate = await runner._maybe_open_scope_gate(
        query="报销",
        ctx=_ctx([f"ds_{index}" for index in range(8)]),
    )

    assert gate is None


def test_receipt_never_short_circuits_as_followup():
    """【用户回答】回执必须走预检，否则范围恢复会静默失效。"""
    runner = _runner()
    runner.turn_decision = None

    decision = type("D", (), {"mode": "fresh", "reason": ""})()
    receipt = "【用户回答】\n所选选项: 仅智能体默认 (__scope_bound__)\n请根据以上用户回答继续处理原问题。"

    skipped = runner._should_skip_knowledge_prefetch(
        is_catalog_query=False,
        reusable_decision=decision,
        reusable_knowledge_result=False,
        user_question=receipt,
        history=[{"role": "user", "content": "上一个问题"}, {"role": "assistant", "content": "回答"}],
    )

    assert skipped is False


async def test_apply_confirmed_scope_narrows_next_turn(monkeypatch):
    """确认过的范围必须真的作用到下一轮检索，而不只是「不再弹卡」。"""
    stored = {"knowledge_scope": {"mode": "ranked", "dataset_ids": ["ds_1", "ds_3"]}}

    async def _fake_get(user_id, conversation_id):
        return stored

    monkeypatch.setattr(mod.ConversationResourceService, "get", _fake_get)

    narrowed = await _runner()._apply_confirmed_scope([f"ds_{index}" for index in range(6)])

    assert narrowed == ["ds_1", "ds_3"]


async def test_apply_confirmed_scope_keeps_resolved_without_scope(monkeypatch):
    async def _fake_get(user_id, conversation_id):
        return {"knowledge_scope": None}

    monkeypatch.setattr(mod.ConversationResourceService, "get", _fake_get)

    resolved = [f"ds_{index}" for index in range(3)]
    assert await _runner()._apply_confirmed_scope(resolved) == resolved


async def test_apply_confirmed_scope_falls_back_when_stale(monkeypatch):
    """库被删或权限收回后，旧范围可能整段失效，此时退回本轮解析出的范围。"""

    async def _fake_get(user_id, conversation_id):
        return {"knowledge_scope": {"mode": "picked", "dataset_ids": ["ds_gone"]}}

    monkeypatch.setattr(mod.ConversationResourceService, "get", _fake_get)

    resolved = ["ds_0", "ds_1"]
    assert await _runner()._apply_confirmed_scope(resolved) == resolved


async def test_scope_cleared_when_agent_changed(monkeypatch):
    """切换智能体后旧范围必须清空重判，否则会检索到该智能体不该搜的库。"""
    cleared = {}

    async def _fake_patch(user_id, conversation_id, **fields):
        cleared.update(fields)
        return {}

    monkeypatch.setattr(mod.ConversationResourceService, "patch_scope", _fake_patch)

    runner = _runner()
    runner.config = SimpleNamespace(agent_id="agent_b")
    scope = {
        "knowledge_scope": {"mode": "ranked", "dataset_ids": ["ds_1"], "agent_id": "agent_a"},
        "pending_scope_gate": {"gate_id": "sg_old"},
    }

    result = await runner._drop_scope_if_agent_changed(
        scope=scope,
        agent_id="agent_b",
        user_id=7,
        conversation_id="c1",
    )

    assert result["knowledge_scope"] is None
    assert result["pending_scope_gate"] is None
    assert cleared["knowledge_scope"] is None


async def test_scope_kept_when_same_agent(monkeypatch):
    async def _fake_patch(user_id, conversation_id, **fields):
        raise AssertionError("同一智能体不应该清理范围")

    monkeypatch.setattr(mod.ConversationResourceService, "patch_scope", _fake_patch)

    runner = _runner()
    runner.config = SimpleNamespace(agent_id="agent_a")
    scope = {"knowledge_scope": {"mode": "ranked", "dataset_ids": ["ds_1"], "agent_id": "agent_a"}}

    result = await runner._drop_scope_if_agent_changed(
        scope=scope,
        agent_id="agent_a",
        user_id=7,
        conversation_id="c1",
    )

    assert result["knowledge_scope"]["dataset_ids"] == ["ds_1"]


# ── 委派场景：没有交互通道，必须让路给自动收窄 ─────────────────────────────

def _delegated(monkeypatch, value=True):
    """只替换「是否委派」：不弹卡走 _has_no_interactive_user，收窄走 _is_delegated。"""
    monkeypatch.setattr(
        mod.KnowledgeAgentRunner,
        "_is_delegated",
        lambda self: value,
    )
    monkeypatch.setattr(
        mod.KnowledgeAgentRunner,
        "_has_no_interactive_user",
        lambda self: value,
    )


async def test_delegated_turn_never_opens_gate(monkeypatch):
    """委派链里卡片会被流白名单吞掉，子会话又与回执会话不一致：弹卡只会让委派拿到空结果。"""
    ids = [f"ds_{index}" for index in range(8)]
    calls = _install_scope_gate_fakes(
        monkeypatch,
        conversation_scope={"knowledge_scope": None, "pending_scope_gate": None},
        catalog=_catalog(ids),
    )
    _delegated(monkeypatch)

    gate = await _runner()._maybe_open_scope_gate(query="报销 差旅 标准", ctx=_ctx(ids))

    assert gate is None
    assert not calls["patched"], "委派轮不应该写待回答快照"


async def test_delegated_turn_narrows_to_ranked_without_asking(monkeypatch):
    ids = [f"ds_{index}" for index in range(8)]
    _install_scope_gate_fakes(
        monkeypatch,
        conversation_scope={"knowledge_scope": None, "pending_scope_gate": None},
        catalog=_catalog(ids),
    )
    _delegated(monkeypatch)

    narrowed = await _runner()._narrow_scope_without_asking(
        query="报销 差旅 标准",
        dataset_ids=ids,
        ctx=_ctx(ids),
    )

    assert 0 < len(narrowed) <= 3
    assert set(narrowed) <= set(ids)


async def test_delegated_narrowing_keeps_scope_when_nothing_matches(monkeypatch):
    """一个都没匹配上时不能猜：宁可多检索，也不要因猜错而漏掉答案。"""
    ids = [f"ds_{index}" for index in range(8)]
    _install_scope_gate_fakes(
        monkeypatch,
        conversation_scope={"knowledge_scope": None, "pending_scope_gate": None},
        catalog=_catalog(ids),
    )
    _delegated(monkeypatch)
    monkeypatch.setattr(
        "app.services.ai.knowledge_catalog.rank_knowledge_catalog",
        lambda *a, **k: [],
    )

    narrowed = await _runner()._narrow_scope_without_asking(
        query="完全不相关的问题",
        dataset_ids=ids,
        ctx=_ctx(ids),
    )

    assert narrowed == ids


async def test_non_delegated_turn_keeps_scope_untouched(monkeypatch):
    """直连场景的收窄由用户确认，不该被自动收窄悄悄替代。"""
    ids = [f"ds_{index}" for index in range(8)]
    _install_scope_gate_fakes(
        monkeypatch,
        conversation_scope={"knowledge_scope": None, "pending_scope_gate": None},
        catalog=_catalog(ids),
    )
    _delegated(monkeypatch, False)

    narrowed = await _runner()._narrow_scope_without_asking(
        query="报销 差旅 标准",
        dataset_ids=ids,
        ctx=_ctx(ids),
    )

    assert narrowed == ids


async def test_delegated_narrowing_respects_threshold(monkeypatch):
    """范围本来就不大时不必收窄，否则会无谓地缩小召回面。"""
    ids = [f"ds_{index}" for index in range(2)]
    _install_scope_gate_fakes(
        monkeypatch,
        conversation_scope={"knowledge_scope": None, "pending_scope_gate": None},
        catalog=_catalog(ids),
        settings=(True, 5, 3),
    )
    _delegated(monkeypatch)

    narrowed = await _runner()._narrow_scope_without_asking(
        query="报销 差旅 标准",
        dataset_ids=ids,
        ctx=_ctx(ids),
    )

    assert narrowed == ids


async def test_scheduled_task_suppresses_gate_but_keeps_full_scope(monkeypatch):
    """定时报表不能弹卡，但也不该被静默砍到 3 个库——漏数据比慢更糟。"""
    ids = [f"ds_{index}" for index in range(8)]
    _install_scope_gate_fakes(
        monkeypatch,
        conversation_scope={"knowledge_scope": None, "pending_scope_gate": None},
        catalog=_catalog(ids),
    )
    runner = _runner()
    runner.user_info = {"user_id": 7, "is_scheduled_task": True}

    gate = await runner._maybe_open_scope_gate(query="本月销售汇总", ctx=_ctx(ids))
    narrowed = await runner._narrow_scope_without_asking(
        query="本月销售汇总",
        dataset_ids=ids,
        ctx=_ctx(ids),
    )

    assert gate is None, "定时任务同样没有可交互用户，不该弹卡"
    assert narrowed == ids, "定时任务的召回范围必须保持全量"


def test_scope_written_back_to_context(monkeypatch):
    """确认的范围必须落到 ctx：模型二次检索读的就是 ctx.dataset_ids。"""
    from app.core.context import AgentContext, agent_context

    token = agent_context.set(
        AgentContext(
            agent_id="agent_kb",
            agent_name="知识库助手",
            dataset_ids=["a" * 32, "b" * 32, "c" * 32],
        )
    )
    try:
        mod.KnowledgeAgentRunner._apply_scope_to_context(["a" * 32])
        from app.core.context import get_current_agent_context

        assert get_current_agent_context().dataset_ids == ["a" * 32]
    finally:
        agent_context.reset(token)


def test_scope_write_back_skips_empty_result(monkeypatch):
    """空范围不能写回：那会把「没有配置」和「用户选了零个库」混为一谈。"""
    from app.core.context import AgentContext, agent_context, get_current_agent_context

    token = agent_context.set(
        AgentContext(agent_id="agent_kb", agent_name="知识库助手", dataset_ids=["a" * 32])
    )
    try:
        mod.KnowledgeAgentRunner._apply_scope_to_context([])
        assert get_current_agent_context().dataset_ids == ["a" * 32]
    finally:
        agent_context.reset(token)


def test_confirmed_scope_expires_after_ttl():
    """范围钉死太久会让用户换话题后静默搜不到别的库。"""
    from datetime import datetime, timedelta

    fresh = {"confirmed_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    stale = {"confirmed_at": (datetime.now() - timedelta(seconds=mod.CONFIRMED_SCOPE_TTL_SECONDS + 60)).strftime("%Y-%m-%dT%H:%M:%S")}

    assert mod.KnowledgeAgentRunner._scope_expired(fresh) is False
    assert mod.KnowledgeAgentRunner._scope_expired(stale) is True
    # 老记录没有时间戳时按有效处理，不要当场作废
    assert mod.KnowledgeAgentRunner._scope_expired({"mode": "ranked"}) is False


async def test_scope_gate_chunk_carries_open_dataset_picker_action(monkeypatch):
    """「我自己指定」的页面动作要活到 SSE 事件里，否则前端拉不起知识库门户。

    这条是端到端字段链路：build_scope_options → ScopeGate → user_question 事件。
    中间任何一层剥掉未知字段，动作就静默失效，而且前端看不出异常（只是不弹面板）。
    """
    async def _fake_persist(*, event, user_id, conversation_id):
        return {}

    monkeypatch.setattr(
        "app.services.ai.user_question.persist_user_question_event", _fake_persist
    )

    options = build_scope_options(
        total_count=8, bound_count=0, ranked_names=["A", "B"], ranked_count=2
    )
    gate = ScopeGate(
        gate_id="sg_action",
        options=options,
        ranked_ids=("ds_0", "ds_1"),
        all_ids=tuple(f"ds_{index}" for index in range(8)),
        total_count=8,
        bound_count=0,
    )

    chunk = await _runner()._build_scope_gate_chunk(gate=gate)

    assert chunk is not None
    pick_option = next(item for item in chunk["options"] if item["id"] == SCOPE_PICK)
    assert pick_option["action"] == "open_dataset_picker"
    all_option = next(item for item in chunk["options"] if item["id"] == SCOPE_ALL)
    assert "action" not in all_option or all_option["action"] is None
