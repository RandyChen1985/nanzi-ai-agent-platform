import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.source_reclassification import (
    SchemaMissDisposition,
    reclassify_schema_miss_source,
)


@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("联网查一下今天行业新闻", SchemaMissDisposition.DELEGATE_WEB),
        ("查一下有孚网络公司信息", SchemaMissDisposition.DELEGATE_WEB),
        ("退款审批流程是什么", SchemaMissDisposition.DELEGATE_MAIN),
        ("模型怎么配置", SchemaMissDisposition.DELEGATE_MAIN),
        ("帮我翻译这段话", SchemaMissDisposition.DELEGATE_MAIN),
        ("查询本月各区域销售额", SchemaMissDisposition.KEEP_DATA_FAILURE),
    ],
)
def test_reclassify_schema_miss_source(query, expected):
    assert reclassify_schema_miss_source(query).disposition == expected


@pytest.mark.asyncio
async def test_schema_miss_handoff_replaces_fatal_response(monkeypatch):
    from app.services.ai.runners.chatbi import react_stream

    runner = SimpleNamespace(
        _standalone_query="联网查一下今天行业新闻",
        _active_history=[{"role": "user", "content": "联网查一下今天行业新闻"}],
        trace_id="trace-source",
        trace_buffer=[],
        execution_options={},
        permission_options={},
        user_info={"user_id": 7},
        conversation_id="conv-1",
        _runtime_agent_name=lambda: "chat-bi",
        step_counter=0,
        _schema_fatal_response=lambda state: ("连续未命中数据集定义", "旧错误"),
    )

    async def fake_handoff(*args, **kwargs):
        yield {"content": "联网结果", "status": "success"}

    monkeypatch.setattr(
        "app.services.ai.runners.chatbi.handoff.stream_to_routed_assistant",
        fake_handoff,
    )
    events = [
        event
        async for event in react_stream.yield_schema_fatal_abort(
            runner,
            DataRunState(schema_miss=True, schema_miss_count=2),
        )
    ]
    assert any(event.get("title") == "重新判断请求来源" for event in events)
    assert any(event.get("content") == "联网结果" for event in events)
    assert not any(event.get("content") == "旧错误" for event in events)

