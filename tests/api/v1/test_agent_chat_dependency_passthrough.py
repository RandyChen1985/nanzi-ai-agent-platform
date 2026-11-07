"""``POST /api/v1/chat/agents/{agent_id}/chat`` 的依赖透传契约。

该端点只是给 ``create_chat_completion`` 换了个 RESTful 入口，却直接以普通函数方式
调用它。``create_chat_completion`` 的 ``user_info`` / ``db`` 是 FastAPI 注入形参，
直调时依赖根本不会被解析——函数收到的是 ``Depends`` 占位对象，随即在
``_require_chat_user_id`` 里恒返 401。这里把「本端点自己声明依赖并显式透传」钉死：
既断言签名用的是与同文件其他聊天端点一致的 ``require_api_key``，也用依赖覆盖
实跑一次路由，确认真正传到下游的是解析后的用户信息与 session。
"""
import inspect

import pytest
from fastapi.params import Depends as DependsParam
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import chat as chat_endpoint
from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.main import app

pytestmark = pytest.mark.no_infrastructure


def _create_agent_chat_source() -> str:
    source = inspect.getsource(chat_endpoint.create_agent_chat)
    return source


def _depends_dependency(default):
    """``Depends(...)`` 返回的是 ``fastapi.params.Depends`` 实例。"""
    assert isinstance(default, DependsParam), f"期望 Depends(...)，实际是 {default!r}"
    return default.dependency


def test_endpoint_declares_same_auth_dependency_as_other_chat_endpoints():
    """鉴权强度不变：与 ``create_chat_completion`` 一样走 ``require_api_key``。"""
    params = inspect.signature(chat_endpoint.create_agent_chat).parameters
    assert _depends_dependency(params["user_info"].default) is require_api_key


def test_endpoint_declares_db_dependency_with_function_scope():
    """db 的 scope 必须与 ``create_chat_completion`` 一致。

    SSE 响应会持续到整段生成结束；session 若活到那时会把连接与事务一直占着，
    还会让读过的表保持共享 MDL。``scope="function"`` 让 session 在路径函数返回后、
    响应发出前归还。
    """
    params = inspect.signature(chat_endpoint.create_agent_chat).parameters
    default = params["db"].default
    assert _depends_dependency(default) is get_db_session
    assert default.scope == "function"


def test_endpoint_passes_resolved_dependencies_to_completion_handler():
    """不能只声明依赖：直调处必须把解析后的值显式传进去。"""
    source = _create_agent_chat_source()
    call = source.split("return await create_chat_completion(", 1)[1]
    assert "user_info=user_info" in call
    assert "db=db" in call


@pytest.mark.asyncio
async def test_route_injects_real_user_info_and_db(monkeypatch):
    """走真实路由：下游拿到的是解析后的 dict / session，而不是 Depends 占位对象。"""
    captured = {}
    fake_session = object()

    async def _fake_create_chat_completion(
        completion_request,
        request,
        user_info,
        db,
    ):
        captured["agent_id"] = completion_request.agent_id
        captured["user_info"] = user_info
        captured["db"] = db
        return JSONResponse({"code": 200, "message": "ok", "data": {}})

    async def _fake_require_api_key():
        return {"user_id": "7", "user_name": "tester", "role": "user"}

    async def _fake_db():
        yield fake_session

    monkeypatch.setattr(
        chat_endpoint, "create_chat_completion", _fake_create_chat_completion
    )
    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key
    app.dependency_overrides[get_db_session] = _fake_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/chat/agents/sys-agent-chatbi/chat",
                headers={"X-API-Key": "test-key"},
                json={
                    "messages": [{"role": "user", "content": "你好"}],
                    "stream": False,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # 路径上的 agent_id 覆盖请求体，保持原有语义
    assert captured["agent_id"] == "sys-agent-chatbi"
    assert captured["user_info"] == {
        "user_id": "7",
        "user_name": "tester",
        "role": "user",
    }
    assert not isinstance(captured["user_info"], DependsParam)
    assert captured["db"] is fake_session
    assert not isinstance(captured["db"], DependsParam)
