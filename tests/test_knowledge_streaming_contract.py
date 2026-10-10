"""契约：知识库问答在模型生成期间必须持续推送过程事件，并给出语义化状态项。

背景：`KnowledgeAgentRunner` 的幻觉网关重试循环此前把**所有** chunk 都塞进
`chunks_buffer`，直到网关判定通过才一次性 flush。于是模型生成期间前端收不到任何
事件，思考卡片停在最后一个直接 yield 的「工具完成: search_knowledge_base」上，
答案也整段跳出。这些断言锁住四处接入点不被后续重构改回去：

1. 过程事件（不含 content）实时转发，只有正文进缓冲区；
2. error 事件用独立标志位识别（它可能携带 content，只在非正文分支判断会漏）；
3. 预检索后立刻发出「检索完成，正在组织回答」pending 项，并在首个正文 chunk 收尾；
4. 落库收尾覆盖该状态项的类别，避免历史回放永远显示「进行中」。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_RUNNER = ROOT / "app/services/ai/runners/knowledge_agent_runner.py"
TIMELINE_SNAPSHOT = ROOT / "app/services/ai/runtime/agentscope/process_timeline_snapshot.py"

SYNTHESIS_PENDING_CALL = 'yield self._knowledge_synthesis_event(status="pending"'
MODEL_EVENT_LOOP = "async for chunk in self._execute_with_agentscope_native_agent(**execute_kwargs):"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _knowledge_synthesis_helper_source() -> str:
    """截取 helper 定义体，避免把调用处的字面量误当实现。"""
    source = _read(KNOWLEDGE_RUNNER)
    start = source.index("def _knowledge_synthesis_event(")
    return source[start:source.index("async def execute(", start)]


def _model_execution_loop_source() -> str:
    """截取网关重试循环内遍历模型事件的循环体。"""
    source = _read(KNOWLEDGE_RUNNER)
    start = source.index(MODEL_EVENT_LOOP)
    return source[start:source.index("if saw_error:", start)]


def test_knowledge_synthesis_helper_has_stable_id_title_and_category():
    helper = _knowledge_synthesis_helper_source()

    assert '"id": "knowledge_synthesis"' in helper, "前端按 id 归并，必须是稳定 id"
    assert '"title": "检索完成，正在组织回答"' in helper
    assert '"category": "knowledge"' in helper, "knowledge 类别才有秒表与超时保护"


def test_prefetch_emits_knowledge_synthesis_pending_after_service_check():
    source = _read(KNOWLEDGE_RUNNER)

    prefetch = source.index("async for chunk in self._auto_invoke_search_knowledge_base(")
    service_check = source.index("if knowledge_service_unavailable:", prefetch)
    emit = source.index(SYNTHESIS_PENDING_CALL, service_check)
    assert emit > service_check, "状态项必须在服务可用性检查之后发出（不可用时直接 abort）"


def test_knowledge_synthesis_details_never_claims_zero_citations():
    source = _read(KNOWLEDGE_RUNNER)
    block = source[
        source.index("knowledge_synthesis_details = (") : source.index(MODEL_EVENT_LOOP)
    ]

    assert "未检索到相关资料，正在组织回复…" in block, "0 条引用时不得谎报「已获取 0 条资料」"
    assert "prefetch_had_citations" in block, "文案必须按有无引用分流"


def test_only_content_chunks_are_buffered_and_process_events_forwarded():
    loop = _model_execution_loop_source()

    content_branch = loop.index('if "content" in chunk:')
    else_branch = loop.index("else:", content_branch)
    assert loop.count("chunks_buffer.append(chunk)") == 1, "缓冲区只允许装正文"
    assert loop.index("chunks_buffer.append(chunk)") > content_branch
    assert "yield chunk" not in loop[content_branch:else_branch], (
        "正文不得直接 yield：网关判定前对用户不可见是安全语义"
    )
    assert "yield chunk" in loop[else_branch:], "过程事件必须实时转发"


def test_error_chunks_use_flag_instead_of_buffer_scan():
    source = _read(KNOWLEDGE_RUNNER)
    loop = _model_execution_loop_source()

    assert "saw_error = True" in loop
    assert "if saw_error:" in source
    # error 判定必须在分流之前：带 content 的错误事件（安全拦截）也要被识别
    assert loop.index("saw_error = True") < loop.index('if "content" in chunk:')
    assert 'any(chunk.get("type") == "error" for chunk in chunks_buffer)' not in source, (
        "error chunk 不再进缓冲区，扫缓冲区找 error 会永久失效"
    )


def test_first_content_chunk_closes_knowledge_synthesis_item():
    loop = _model_execution_loop_source()

    content_branch = loop.index('if "content" in chunk:')
    else_branch = loop.index("else:", content_branch)
    body = loop[content_branch:else_branch]
    assert "if synthesis_pending:" in body, "收尾必须在正文分支内（首个正文 chunk 处）"
    assert body.index("if synthesis_pending:") > body.index("chunks_buffer.append(chunk)")
    assert 'status="success"' in body
    assert "synthesis_pending = False" in body, "收尾后必须清标志，重试轮不得重复关闭"


def test_synthesis_closing_is_not_tied_to_flush():
    source = _read(KNOWLEDGE_RUNNER)

    flush = source.index("if passed_guard:")
    assert 'status="success"' not in source[flush:], (
        "flush 发生在网关判定之后，那时才收尾会让卡片再次停住"
    )


def test_persisted_timeline_closes_pending_for_model_and_knowledge_only():
    source = _read(TIMELINE_SNAPSHOT)
    finalize = source[source.index("def finalize_process_timeline(") :]

    assert 'copied.get("category") in ("model", "knowledge")' in source, (
        "knowledge 状态项若异常中断仍是 pending，历史回放会永远显示「进行中」"
    )
    assert '"permission"' not in finalize, "等待用户操作的类别必须保持待处理"
    assert '"external"' not in finalize, "等待外部执行的类别必须保持待处理"
