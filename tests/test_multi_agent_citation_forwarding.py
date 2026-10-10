"""委派（多智能体）场景下的引用详情转发契约。

用户反馈：路由/委派模式下 AI 消息底部不显示引用列表，点序号只提示「引用详情加载中，
请稍候」；而直接指定知识库专家（单专家）时一切正常。

根因：多智能体编排器 ``run_executor`` 用白名单把子智能体的事件转发到主聊天流，
``citation`` 不在任何分支里，被静默丢弃。单专家路径不经过这里，所以只有委派会丢。
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.services.ai import multi_agent_orchestrator as orch

CITATION_CHUNK = {
    "type": "citation",
    "data": [
        {
            "id": "1",
            "chunk_id": "chunk-1",
            "doc_name": "手册.pdf",
            "content": "片段正文",
        }
    ],
}


class _FakeExecutor:
    """按脚本吐出事件的假 executor。"""

    def __init__(self, chunks):
        self._chunks = chunks

    async def execute(self, messages):
        for chunk in self._chunks:
            yield chunk


@asynccontextmanager
async def _fake_session_local():
    yield SimpleNamespace()


def _patch(monkeypatch, chunks):
    monkeypatch.setattr(orch, "AsyncSessionLocal", _fake_session_local)

    class _Dispatcher:
        @staticmethod
        async def dispatch(config, *args, **kwargs):
            return _FakeExecutor(chunks)

    monkeypatch.setattr(orch, "AgentDispatcher", _Dispatcher)


async def _collect(monkeypatch, chunks):
    """跑一遍编排器，收集它真正吐给主聊天流的事件。"""
    _patch(monkeypatch, chunks)

    async def _synthesis(*args, **kwargs):
        yield {"type": "answer_delta", "content": "合成答案", "phase": "synthesis"}

    agent_service = SimpleNamespace(_synthesize_multi_agent_results=_synthesis)
    events = []
    async for event in orch._execute_multi_agent_impl(
        agent_service=agent_service,
        primary_config=SimpleNamespace(agent_name="主助手"),
        secondary_agent_ids=[],
        user_query="问题",
        messages=[],
        trace_id="trace-1",
        trace_buffer=[],
        debug_options={},
        permission_options=None,
        user_info=None,
        api_key=None,
        conversation_id="conv-1",
        turn_decision=None,
    ):
        events.append(event)
    return events


async def test_sub_agent_citation_reaches_main_stream(monkeypatch):
    """委派场景的引用详情必须转发到主聊天流，否则前端引用列表整块消失。"""
    events = await _collect(
        monkeypatch,
        [
            {"type": "log", "title": "检索知识库", "status": "success"},
            CITATION_CHUNK,
            {"content": "专家回答"},
        ],
    )

    citations = [e for e in events if e.get("type") == "citation"]
    assert len(citations) == 1, (
        "子智能体的 citation 事件被丢掉了：run_executor 的转发白名单缺少该分支"
    )
    assert citations[0]["data"][0]["chunk_id"] == "chunk-1"
    assert citations[0]["data"][0]["content"] == "片段正文"


async def test_expert_answer_delta_stays_internal(monkeypatch):
    """各专家答案仍只作为合成输入，不得直接进主流（既有契约不能被破坏）。"""
    events = await _collect(
        monkeypatch,
        [
            {"type": "answer_delta", "content": "专家草稿"},
            {"type": "retraction"},
        ],
    )
    leaked = [e for e in events if e.get("type") == "answer_delta" and e.get("phase") != "synthesis"]
    assert leaked == [], "专家草稿泄进了主聊天流"


async def test_unknown_event_types_are_not_forwarded(monkeypatch):
    """白名单就是白名单：未登记的类型不能借这次修复混进主流。"""
    events = await _collect(monkeypatch, [{"type": "something_unknown", "payload": 1}])
    assert [e for e in events if e.get("type") == "something_unknown"] == []


async def test_citation_keeps_its_payload_shape(monkeypatch):
    """转发必须原样透传，不能包装/裁剪 payload（前端依赖数组形状做去重合并）。"""
    events = await _collect(monkeypatch, [CITATION_CHUNK])
    citations = [e for e in events if e.get("type") == "citation"]
    assert citations, "citation 未被转发"
    assert isinstance(citations[0]["data"], list), "data 必须仍是数组，前端按数组消费"
    assert citations[0] == CITATION_CHUNK, "citation 事件被改写了"


def test_synthesis_prompt_preserves_citation_markers():
    """合成 prompt 必须要求原样保留 [ID:n]。

    回归场景：多智能体轮次的正文由主模型重写，引用列表（引用来源 N 条）能显示，
    但正文里一个 [ID:n] 都没有，用户点不到任何引用。原因是子智能体的回答本来带
    序号（知识库工具的 CRITICAL 指令），但它只作为合成素材，而合成 prompt 没有任何
    「保留引用标记」的契约，模型重写时就把序号丢掉了。
    """
    from app.services.ai.agent_prompts import AgentServicePrompts

    system_prompt = AgentServicePrompts.MULTI_AGENT_SYNTHESIS_SYSTEM
    assert "[ID:n]" in system_prompt, "合成 system prompt 未要求保留 [ID:n] 引用标记"
    assert "原样保留" in system_prompt, "缺少『原样保留』的明确措辞，模型仍可能重写序号"

    human_prompt = AgentServicePrompts.multi_agent_synthesis_human(
        "问题", "### 专家智能体: 知识库助手\n答案是 X [ID:1]\n\n"
    )
    assert "原样保留" in human_prompt, "最靠近生成位置的 human prompt 未重申引用标记契约"
