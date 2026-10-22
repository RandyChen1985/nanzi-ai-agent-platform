from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.no_infrastructure


def test_chat_run_status_route_is_authenticated_and_does_not_use_database():
    source = (ROOT / "app/api/v1/endpoints/chat.py").read_text(encoding="utf-8")
    assert '"/conversation/{conversation_id}/run-status"' in source
    route_start = source.index('"/conversation/{conversation_id}/run-status"')
    route_body = source[route_start:]
    assert "Depends(require_api_key)" in route_body
    assert "conversation_run_lane.get_status" in route_body
    assert "StandardResponse" in route_body
    assert "get_db_session" not in route_body[:route_body.index("async def", 0)]


def test_run_status_reports_availability_so_the_client_never_treats_errors_as_finished():
    """Redis/运行锁不可用时必须带 available=false。

    只返回 active=false 时，前端无法区分「本轮确实结束」与「查不到」，会把仍被占用的
    输入框解锁、并停掉恢复器。
    """
    source = (ROOT / "app/api/v1/endpoints/chat.py").read_text(encoding="utf-8")
    assert "class ConversationRunStatusResponse(BaseModel):" in source
    model_block = source[source.index("class ConversationRunStatusResponse(BaseModel):"):]
    model_block = model_block[: model_block.index("class ", 10)]
    assert "available: bool = True" in model_block
    assert "run_available" in source
