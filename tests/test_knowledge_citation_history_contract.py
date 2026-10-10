"""契约：引用详情必须被累积、落 Redis，并能在两个历史接口回填。

背景：引用详情此前只活在实时 SSE 流里，刷新页面后历史消息的 [ID:n] 徽章点不开。
这些断言锁住「累积 → 写入 → 读取」三处接入点不被后续重构删掉。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
EXECUTION_STEP = ROOT / "app/services/ai/pipeline/steps/execution_step.py"
FINALIZE_STEP = ROOT / "app/services/ai/pipeline/steps/finalize_step.py"
CHAT_ENDPOINT = ROOT / "app/api/v1/endpoints/chat.py"
AGENT_SCHEMA = ROOT / "app/schemas/agent.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_execution_step_accumulates_citation_events():
    source = _read(EXECUTION_STEP)

    assert "accumulate_citation_details(shared_state, chunk)" in source, "execution_step 必须累积引用详情"
    # 累积调用必须在遍历 executor 事件的循环内——只 import 了函数不算接入。
    # 注意不能用 source.index("accumulate_citation_details")：import 行出现在文件顶部，
    # 会先于循环命中，让断言变成假绿。
    loop_index = source.index("async for chunk in executor_stream:")
    call_index = source.index('if chunk.get("type") == "citation":')
    assert call_index > loop_index


def test_finalize_step_persists_citations_only_when_turn_is_persisted():
    """写入必须与消息落库同一判定，保证「有历史消息才有引用详情」的对称性。"""
    source = _read(FINALIZE_STEP)

    persist_branch = source.index("if should_persist:")
    save_index = source.index("knowledge_citation_store.save")
    assert save_index > persist_branch, "引用详情必须在 should_persist 分支内写入"
    assert "shared_state.get(\"knowledge_citations\")" in source


def test_history_schema_exposes_citations():
    source = _read(AGENT_SCHEMA)

    assert "class AgentExecutionHistoryResponse(BaseModel):" in source
    assert "citations: Optional[list] = None" in source


def test_db_history_endpoint_backfills_citations_by_trace_id():
    source = _read(CHAT_ENDPOINT)
    fetch = source[source.index("async def get_history("):source.index("async def delete_history(")]

    assert "knowledge_citation_store" in fetch, "DB 历史必须回填引用详情"
    assert "load_many" in fetch
    assert "update={\"citations\"" in fetch, "回填必须并入响应对象"


def test_conversation_history_endpoint_backfills_citations_for_all_sources():
    """该接口有三条来源（Redis / 审计合并 / DB 兜底），回填必须在汇合点统一做一次。"""
    source = _read(CHAT_ENDPOINT)
    fetch = source[
        source.index("async def get_conversation_history("):source.index("async def get_conversation_context_usage(")
    ]

    assert "load_many" in fetch
    assert 'message["citations"] = found' in fetch
    # 回填必须在 return 之前
    assert fetch.index('message["citations"] = found') < fetch.index("return StandardResponse(data=ConversationHistoryResponse(")


def test_delete_endpoints_clean_up_citation_details():
    """删除会话/历史后引用详情不得残留，否则与「删除即删除」语义不符。"""
    source = _read(CHAT_ENDPOINT)

    single = source[source.index("async def delete_history("):source.index("class BatchDeleteHistoryRequest")]
    batch = source[source.index("async def batch_delete_history("):source.index("class TruncateHistoryRequest")]
    truncate = source[source.index("async def truncate_history_endpoint("):source.index('@router.get("/logs/{trace_id}"')]

    assert "delete_many" in single, "单条删除要清理引用详情"
    assert "delete_many" in batch, "批量删除要清理引用详情"
    assert "delete_many" in truncate, "截断历史要清理引用详情"
