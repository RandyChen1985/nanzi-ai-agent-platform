"""「仅检索智能体绑定的知识库」范围策略。

开启后范围必须钉死在绑定上：既不接受本轮传入的知识库 ID，也不扩大到用户可访问的库。
这是对外发布智能体时约束召回边界的开关，放宽即等于越权检索。
"""
import pytest

from app.core.context import agent_context, get_current_agent_context
from app.schemas.agent import ChatConfig
from app.services.ai.context_manager import AgentContextManager

# RAGFlow 的 dataset id 是 32 位十六进制，用别的形式会被 normalize_dataset_ids 过滤掉，
# 那样测试会「因为 ID 非法而通过」，测不到真实分支。
BOUND = "a" * 32
EXTERNAL = "b" * 32


@pytest.fixture(autouse=True)
def _clean_context():
    token = agent_context.set(None)
    yield
    agent_context.reset(token)


def _config(engine_config=None, agent_dataset_ids=None) -> ChatConfig:
    return ChatConfig(
        agent_id="agent_kb",
        agent_name="知识库助手",
        model_name="dummy",
        temperature=0.0,
        system_prompt="",
        tools=[],
        engine_config=engine_config if engine_config is not None else {},
        agent_dataset_ids=agent_dataset_ids,
    )


async def test_bound_only_ignores_external_dataset_ids():
    await AgentContextManager.setup_context(
        config=_config({"dataset_ids": [BOUND], "scope_gate": "bound_only"}),
        knowledge_dataset_ids=[EXTERNAL],
    )

    ctx = get_current_agent_context()
    assert ctx.dataset_ids == [BOUND]
    # 外部传入要被忽略，否则门禁的第二道与门会误判「本轮用户已显式选库」而跳过确认
    assert ctx.knowledge_dataset_ids == []


async def test_bound_only_keeps_empty_when_agent_has_no_binding():
    """没有绑定就不要悄悄扩大到全量——那样等于把开关变成摆设。"""
    await AgentContextManager.setup_context(
        config=_config({"scope_gate": "bound_only"}),
        knowledge_dataset_ids=[EXTERNAL],
    )

    ctx = get_current_agent_context()
    assert ctx.dataset_ids == []
    assert ctx.knowledge_dataset_ids == []


async def test_bound_only_mode_is_case_insensitive():
    await AgentContextManager.setup_context(
        config=_config({"dataset_ids": [BOUND], "scope_gate": "BOUND_ONLY"}),
        knowledge_dataset_ids=[EXTERNAL],
    )

    assert get_current_agent_context().dataset_ids == [BOUND]


async def test_explicit_selection_still_wins_when_flag_off():
    """默认行为不变：用户显式选择仍是硬范围。"""
    await AgentContextManager.setup_context(
        config=_config({"dataset_ids": [BOUND]}),
        knowledge_dataset_ids=[EXTERNAL],
    )

    ctx = get_current_agent_context()
    assert ctx.dataset_ids == [EXTERNAL]
    assert ctx.knowledge_dataset_ids == [EXTERNAL]


# ── 工具层：模型自己传 ID 也不能绕过绑定约束 ───────────────────────────────

def _install_ctx(engine_config=None, *, bound=None, dataset_ids=None):
    from app.core.context import AgentContext, set_agent_context

    set_agent_context(
        AgentContext(
            agent_id="agent_kb",
            agent_name="知识库助手",
            dataset_ids=list(dataset_ids or []),
            agent_dataset_ids=list(bound or []),
            engine_config=engine_config or {},
        )
    )


def test_tool_explicit_ids_are_intersected_with_binding():
    """模型传一个别的库就绕过「仅检索绑定库」，那这个开关等于摆设。"""
    from app.services.ai.knowledge_utils import resolve_bound_dataset_ids

    _install_ctx({"scope_gate": "bound_only"}, bound=[BOUND])
    resolved = resolve_bound_dataset_ids(explicit_tool_ids=[EXTERNAL])

    assert resolved == [BOUND]


def test_tool_explicit_ids_pass_through_when_flag_off():
    from app.services.ai.knowledge_utils import resolve_bound_dataset_ids

    _install_ctx({}, bound=[BOUND])
    assert resolve_bound_dataset_ids(explicit_tool_ids=[EXTERNAL]) == [EXTERNAL]


def test_tool_explicit_ids_keep_allowed_subset():
    """显式传的库里有一部分合法时，只保留合法那部分，而不是整段丢弃。"""
    from app.services.ai.knowledge_utils import resolve_bound_dataset_ids

    _install_ctx({"scope_gate": "BOUND_ONLY"}, bound=[BOUND])
    assert resolve_bound_dataset_ids(explicit_tool_ids=[BOUND, EXTERNAL]) == [BOUND]


async def test_out_of_scope_explicit_ids_are_rejected_instead_of_silently_swapped():
    """越界 ID 必须明确阻断，不能静默换成绑定库。

    静默换库比直接拒绝更糟：模型以为检索了自己指定的库，实际搜的是另一个库，
    最后告诉用户「没找到相关内容」——用户既不知道选择被忽略，也不知道搜了哪里。
    """
    from app.services.ai.knowledge_utils import resolve_knowledge_dataset_ids

    _install_ctx({"scope_gate": "bound_only"}, bound=[BOUND])
    ids, error = await resolve_knowledge_dataset_ids(explicit_tool_ids=[EXTERNAL])

    assert ids == []
    assert error and "不在绑定范围内" in error


async def test_partially_valid_explicit_ids_are_narrowed_not_rejected(monkeypatch):
    """部分越界只收窄、不整体拒绝；越界的那个不该让整次检索失败。"""
    from app.services.ai import knowledge_utils

    async def _alive(ids):
        return list(ids)

    monkeypatch.setattr(knowledge_utils, "filter_alive_knowledge_dataset_ids", _alive)
    _install_ctx({"scope_gate": "bound_only"}, bound=[BOUND])
    ids, error = await knowledge_utils.resolve_knowledge_dataset_ids(
        explicit_tool_ids=[BOUND, EXTERNAL]
    )

    assert error is None
    assert ids == [BOUND]
