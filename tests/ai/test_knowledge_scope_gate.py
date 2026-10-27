"""范围门禁的开关、四道与门、选项构造与回执解析。"""
from app.services.ai.knowledge_catalog import (
    AuthorizedKnowledgeCatalog,
    KnowledgeBaseCatalogItem,
)
from app.services.ai.knowledge_scope_gate import (
    SCOPE_ALL,
    SCOPE_BOUND,
    SCOPE_PICK,
    SCOPE_RANKED,
    ScopeGate,
    evaluate_scope_gate,
    parse_scope_choice,
    resolve_dataset_ids_for_choice,
    scope_mode_for_choice,
)


def _ids(count: int) -> list[str]:
    return [f"ds_{index}" for index in range(count)]


def _catalog(dataset_ids: list[str]) -> AuthorizedKnowledgeCatalog:
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


async def test_no_gate_when_scope_already_confirmed():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(10),
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope={
            "knowledge_scope": {"mode": "ranked", "dataset_ids": ["ds_0"]},
        },
        catalog=_catalog(_ids(10)),
    )

    assert gate is None


async def test_no_gate_when_explicit_selection_present():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(10),
        bound_ids=[],
        explicit_request_ids=["ds_0"],
        conversation_scope=None,
        catalog=_catalog(_ids(10)),
    )

    assert gate is None


async def test_no_gate_at_threshold_boundary():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(5),
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_catalog(_ids(5)),
        enabled=True,
        threshold=5,
    )

    assert gate is None


async def test_gate_opens_above_threshold():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(6),
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_catalog(_ids(6)),
        enabled=True,
        threshold=5,
    )

    assert isinstance(gate, ScopeGate)
    assert gate.total_count == 6
    assert gate.all_ids == tuple(_ids(6))


async def test_gate_disabled_by_switch():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(50),
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_catalog(_ids(50)),
        enabled=False,
        threshold=5,
    )

    assert gate is None


async def test_gate_exposes_four_options_with_bound_count():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(8),
        bound_ids=["ds_0", "ds_1"],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_catalog(_ids(8)),
        enabled=True,
        threshold=5,
    )

    assert gate is not None
    assert gate.bound_count == 2
    assert gate.bound_ids == ("ds_0", "ds_1")
    option_ids = [option["id"] for option in gate.options]
    assert option_ids == [SCOPE_RANKED, SCOPE_ALL, SCOPE_BOUND, SCOPE_PICK]


async def test_bound_option_disabled_when_agent_has_no_binding():
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=_ids(8),
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_catalog(_ids(8)),
        enabled=True,
        threshold=5,
    )

    assert gate is not None
    bound_option = next(option for option in gate.options if option["id"] == SCOPE_BOUND)
    assert bound_option["disabled"] is True


async def test_gate_carries_ranked_ids_from_catalog():
    gate = await evaluate_scope_gate(
        query="报销 差旅 标准",
        effective_ids=_ids(6),
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_catalog(_ids(6)),
        enabled=True,
        threshold=5,
        ranked_limit=3,
    )

    assert gate is not None
    assert len(gate.ranked_ids) == 3
    assert all(dataset_id.startswith("ds_") for dataset_id in gate.ranked_ids)


async def test_pending_payload_round_trip():
    gate = await evaluate_scope_gate(
        query="报销 差旅 标准",
        effective_ids=_ids(6),
        bound_ids=["ds_0"],
        explicit_request_ids=[],
        conversation_scope=None,
        catalog=_catalog(_ids(6)),
        enabled=True,
        threshold=5,
    )

    payload = gate.to_pending_payload()

    assert set(payload) == {"gate_id", "ranked_ids", "bound_ids", "all_ids"}
    assert resolve_dataset_ids_for_choice(SCOPE_BOUND, payload) == ["ds_0"]
    assert resolve_dataset_ids_for_choice(SCOPE_ALL, payload) == _ids(6)
    assert resolve_dataset_ids_for_choice(SCOPE_PICK, payload) == []


def test_parse_scope_choice_reads_receipt_marker():
    receipt = (
        "【用户回答】\n"
        "所选选项: 财务0 (__scope_ranked__)\n"
        'selected_option_ids: ["__scope_ranked__"]\n'
        "custom_input: \n"
    )

    assert parse_scope_choice(receipt) == SCOPE_RANKED
    assert parse_scope_choice("普通提问") is None
    assert parse_scope_choice(None) is None


def test_scope_mode_mapping():
    assert scope_mode_for_choice(SCOPE_RANKED) == "ranked"
    assert scope_mode_for_choice(SCOPE_ALL) == "all"
    assert scope_mode_for_choice(SCOPE_BOUND) == "bound"
    assert scope_mode_for_choice(SCOPE_PICK) == "picked"


# ── 全局总闸 vs 智能体级 on ────────────────────────────────────────────────

def _install_config(monkeypatch, values):
    from app.services.ai import knowledge_scope_gate as gate_mod

    async def _fake_get(key, default=None):
        return values.get(key, default)

    class _FakeConfigService:
        get = staticmethod(_fake_get)

    monkeypatch.setattr(
        "app.services.config_service.ConfigService",
        _FakeConfigService,
    )
    return gate_mod


async def test_global_switch_off_overrides_agent_forced_on(monkeypatch):
    """全站总闸必须能关住配了「强制开启」的智能体，否则出事故时根本止不住血。"""
    _install_config(
        monkeypatch,
        {
            "knowledge_scope_gate_enabled": "false",
            "knowledge_scope_gate_threshold": "5",
            "knowledge_scope_gate_ranked_limit": "3",
        },
    )
    from app.services.ai.knowledge_scope_gate import resolve_scope_gate_settings

    for mode in ("on", "inherit", "off"):
        enabled, _, _ = await resolve_scope_gate_settings({"scope_gate": mode})
        assert enabled is False, f"全局关闭时 scope_gate={mode} 仍应停止"


async def test_agent_forced_on_applies_when_global_open(monkeypatch):
    _install_config(
        monkeypatch,
        {
            "knowledge_scope_gate_enabled": "true",
            "knowledge_scope_gate_threshold": "5",
            "knowledge_scope_gate_ranked_limit": "3",
        },
    )
    from app.services.ai.knowledge_scope_gate import resolve_scope_gate_settings

    on_enabled, _, _ = await resolve_scope_gate_settings({"scope_gate": "on"})
    off_enabled, _, _ = await resolve_scope_gate_settings({"scope_gate": "off"})
    inherit_enabled, _, _ = await resolve_scope_gate_settings({})

    assert on_enabled is True
    assert off_enabled is False, "智能体级关闭仍然有效，不必等全局"
    assert inherit_enabled is True


async def test_zero_threshold_does_not_gate_a_single_dataset(monkeypatch):
    """配置页允许填 0；把它当阈值会让「只要有一个库」就弹卡，等于每轮都问一句。"""
    _install_config(
        monkeypatch,
        {
            "knowledge_scope_gate_enabled": "true",
            "knowledge_scope_gate_threshold": "0",
            "knowledge_scope_gate_ranked_limit": "3",
        },
    )
    from app.services.ai.knowledge_scope_gate import resolve_scope_gate_settings

    _enabled, threshold, _limit = await resolve_scope_gate_settings({})
    gate = await evaluate_scope_gate(
        query="报销标准",
        effective_ids=["a" * 32],
        bound_ids=[],
        explicit_request_ids=[],
        conversation_scope={"knowledge_scope": None},
        catalog=_catalog(["a" * 32]),
        enabled=True,
        threshold=threshold,
      )
    assert gate is None


async def test_bound_only_mode_never_gates(monkeypatch):
    """「仅用绑定库」是四态之一：范围由配置钉死，问也没得选，因此一律不弹卡。"""
    _install_config(
        monkeypatch,
        {
            "knowledge_scope_gate_enabled": "true",
            "knowledge_scope_gate_threshold": "1",
            "knowledge_scope_gate_ranked_limit": "3",
        },
    )
    from app.services.ai.knowledge_scope_gate import resolve_scope_gate_settings

    enabled, _, _ = await resolve_scope_gate_settings({"scope_gate": "bound_only"})
    assert enabled is False
