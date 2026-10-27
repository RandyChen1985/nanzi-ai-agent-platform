"""数据库 session 的依赖 scope 契约：必须在响应体开始发送前归还。

为什么必须锁住：``get_db_session`` 是 yield 依赖，``commit()``/``close()`` 发生在
yield 之后。默认的 request scope 会等到**整个响应**结束——对 SSE 来说就是整条流
（producer 硬超时默认 600s、可配到 3600s），于是每个进行中的对话都白占一条池化连接、
一直开着事务：连接池被吃掉（连 producer 自己都拿不到）、MySQL 上读过的表持续持有共享
MDL（发版执行 ALTER TABLE 会被卡住）、流结束时 commit 还可能因连接被服务端回收而抛错。

这个契约此前没有任何测试守着：整整一处回退（两个文件）之后，全量回归仍然全绿。
因此这里用三层断言把它钉住：

1. 行为断言：真的让 session 在第一个 chunk 之前退出（不是读源码猜）；
2. 契约断言：四处鉴权依赖与聊天端点确实声明了 ``scope="function"``；
3. 扫描断言：将来新增的流式端点不会又把 db 注回 request scope。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient


from app.core import dependencies
from app.core.orm import get_db_session

pytestmark = pytest.mark.no_infrastructure


def _declared_db_scope(dependency_callable) -> str | None:
    """取依赖签名里 ``db`` 参数声明的 FastAPI 依赖 scope。"""
    parameter = inspect.signature(dependency_callable).parameters.get("db")
    assert parameter is not None, f"{dependency_callable!r} 没有 db 依赖"
    return getattr(parameter.default, "scope", None)


def test_auth_session_is_released_before_stream_body_starts(monkeypatch):
    """行为断言：流式响应开始前，鉴权用的 session 已经关闭。

    这里不读源码，而是搭一个只有 ``require_api_key`` 依赖的流式路由，用
    ``dependency_overrides`` 换掉真实的 session 工厂并记录进出时机——覆盖的正是
    生产用的那份依赖签名，所以一旦有人把 ``scope="function"`` 去掉，顺序立刻反过来。
    """
    events: list[str] = []

    async def fake_session():
        events.append("session_enter")
        try:
            yield object()
        finally:
            events.append("session_exit")

    monkeypatch.setattr(
        dependencies.AuthService,
        "verify_api_key",
        AsyncMock(return_value={"user_id": "7", "user_name": "tester", "role": "user"}),
    )
    monkeypatch.setattr(
        dependencies,
        "OnlinePresenceService",
        SimpleNamespace(touch=AsyncMock(return_value=True)),
        raising=False,
    )

    test_app = FastAPI()
    test_app.dependency_overrides[get_db_session] = fake_session

    @test_app.get("/stream")
    async def stream_endpoint(user=Depends(dependencies.require_api_key)):
        async def generator():
            events.append("chunk")
            yield "data: ok\n\n"

        return StreamingResponse(generator(), media_type="text/event-stream")

    with TestClient(test_app) as client:
        response = client.get("/stream", headers={"X-API-Key": "api-key"})

    assert response.status_code == 200
    assert response.text == "data: ok\n\n"
    assert events == ["session_enter", "session_exit", "chunk"], (
        "db session 必须在响应体开始发送前归还；chunk 出现在 session_exit 之前说明"
        " require_api_key 的 db 丢了 scope=\"function\"，session 会活到整条 SSE 结束"
    )


def test_require_api_key_declares_function_scoped_db():
    assert _declared_db_scope(dependencies.require_api_key) == "function"


@pytest.mark.parametrize(
    "dependency_callable",
    [
        pytest.param(dependencies.require_permission("knowledge", "kb:read"), id="require_permission"),
        pytest.param(dependencies.require_any_permission(("knowledge", "kb:read")), id="require_any_permission"),
        pytest.param(dependencies.verify_v1_api_access, id="verify_v1_api_access"),
    ],
)
def test_permission_dependencies_share_the_same_scope(dependency_callable):
    """与 require_api_key 同步：scope 不同会变成两个 session，其中一个仍活到流结束。"""
    assert _declared_db_scope(dependency_callable) == "function"


def test_chat_completion_declares_function_scoped_db():
    from app.api.v1.endpoints.chat import create_chat_completion

    assert _declared_db_scope(create_chat_completion) == "function"


# 允许「请求级 db + StreamingResponse」共存的两个导出端点。
# 它们都是「先查完整份数据、再一次性把已构造好的字符串推出去」，yield 里没有任何
# 数据库访问，事务在 return 之前就结束了，不存在长事务；改动它们没有收益。
ALLOWED_REQUEST_SCOPED_DB_STREAM_ENDPOINTS = {
    ("app/api/portal/endpoints/audit.py", "export_logs"),
    ("app/api/portal/endpoints/mcp_service.py", "export_audit"),
}


def _iter_streaming_endpoints_with_db():
    """扫 app/api 下「函数体里出现 StreamingResponse 且注入了 get_db_session」的函数。"""
    # app 是命名空间包（没有 __init__.py），app.__file__ 为 None，改从测试文件位置推仓库根。
    repo_root = Path(__file__).resolve().parents[2]
    api_root = repo_root / "app" / "api"
    for path in sorted(api_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "StreamingResponse" not in text or "get_db_session" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            source = ast.get_source_segment(text, node) or ""
            if "StreamingResponse" not in source:
                continue
            defaults = list(node.args.defaults or []) + [
                item for item in (node.args.kw_defaults or []) if item is not None
            ]
            db_defaults = [
                segment
                for segment in (ast.get_source_segment(text, item) or "" for item in defaults)
                if "get_db_session" in segment
            ]
            if db_defaults:
                yield str(path.relative_to(repo_root)), node.name, db_defaults


def test_new_streaming_endpoints_do_not_hold_request_scoped_session():
    """新增流式端点若注入 db，必须声明 function scope（或显式加入豁免清单）。"""
    seen: set[tuple[str, str]] = set()
    violations: list[str] = []

    for relative_path, function_name, db_defaults in _iter_streaming_endpoints_with_db():
        seen.add((relative_path, function_name))
        if (relative_path, function_name) in ALLOWED_REQUEST_SCOPED_DB_STREAM_ENDPOINTS:
            continue
        if any('scope="function"' in segment for segment in db_defaults):
            continue
        violations.append(f"{relative_path}:{function_name}")

    # 防空转：扫描必须真的看见聊天端点，否则说明扫描逻辑失效、上面的断言没有意义。
    assert ("app/api/v1/endpoints/chat.py", "create_chat_completion") in seen

    assert not violations, (
        "以下流式端点把 db session 留在 request scope，session（及其连接与事务）会活到"
        f"整条流结束，请改成 Depends(get_db_session, scope=\"function\")：{violations}"
    )
