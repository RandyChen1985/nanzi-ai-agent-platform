"""``POST /api/portal/tools/preview``：保存前试跑。

三条不可退让的约束：
1. **只读**——不写 ``sys_api_tools``，请求体里的 ``params`` 是测试值、绝不落库；
2. **同权**——权限与创建/更新工具一致（``element:system:config_save``），不放宽也不新增；
3. **同口径**——走 ``GenericApiToolFactory._perform_request``，与运行时同一段代码。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import func, select

from app.models.tool import SysApiTool
from app.services.ai.tools.generic_api import GenericApiToolFactory, UnsupportedMethodError
from app.services.ai.tools.user_placeholders import UserPlaceholderError

PREVIEW_URL = "/api/portal/tools/preview"


def _payload(**overrides):
    payload = {
        "method": "GET",
        "url_template": "https://api.test/v1/items",
        "headers": {"Authorization": "Bearer sk-x"},
        "parameter_schema": {"id": {"type": "string"}},
        "params": {"id": "7"},
    }
    payload.update(overrides)
    return payload


def _response(status_code=200, *, json_body=None, text=None, content=None):
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        text=None if json_body is not None else text,
        content=content,
        request=httpx.Request("GET", "https://api.test/v1/items"),
    )


@pytest.mark.asyncio
async def test_preview_requires_system_config_save(client, valid_api_key):
    response = await client.post(
        PREVIEW_URL, json=_payload(), headers={"X-API-Key": valid_api_key},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_preview_returns_status_and_body(client, admin_api_key):
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(_response(200, json_body={"ok": True}), 8, "https://api.test/v1/items")),
    ):
        response = await client.post(
            PREVIEW_URL, json=_payload(), headers={"X-API-Key": admin_api_key},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status_code"] == 200
    assert body["elapsed_ms"] == 8
    assert "ok" in body["body"]


@pytest.mark.asyncio
async def test_preview_does_not_touch_tool_table(client, admin_api_key, db_session):
    before = (await db_session.execute(select(func.count()).select_from(SysApiTool))).scalar_one()

    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(_response(200, json_body={"ok": True}), 8, "https://api.test/v1/items")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(), headers={"X-API-Key": admin_api_key})

    # 先确认这次请求真的打到了端点——否则「行数没变」是假绿（405 也不会写库）
    assert response.status_code == 200
    assert response.json()["ok"] is True

    after = (await db_session.execute(select(func.count()).select_from(SysApiTool))).scalar_one()
    assert after == before, "试跑不得写入工具表"


@pytest.mark.asyncio
async def test_preview_refuses_unfilled_placeholders_without_sending(client, admin_api_key):
    with patch.object(GenericApiToolFactory, "_perform_request", new=AsyncMock()) as mocked:
        response = await client.post(
            PREVIEW_URL,
            json=_payload(url_template="https://api.test/v1/orders/{order_id}"),
            headers={"X-API-Key": admin_api_key},
        )
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "missing_param"
    assert "order_id" in body["message"]
    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_preview_accepts_path_placeholders_that_already_have_values(client, admin_api_key):
    """用户实测报的 bug：明明把 ``order_id`` 填成 11333，却被回一句「地址里还有没填的参数」。

    预检只扫模板、不看 ``params``，就会把已经给了值的路径参数一起误杀。
    """
    captured: dict = {}

    async def fake(config, params, **kwargs):
        captured["config"] = config
        captured["params"] = params
        return _response(200, json_body={"ok": True}), 5, "https://api.test/v1/orders/11333/refund"

    with patch.object(GenericApiToolFactory, "_perform_request", new=fake):
        response = await client.post(
            PREVIEW_URL,
            json=_payload(
                method="POST",
                url_template="https://api.test/v1/orders/{order_id}/refund",
                params={"order_id": "11333", "amount": 100, "reason": "客户申请退款"},
            ),
            headers={"X-API-Key": admin_api_key},
        )

    body = response.json()
    assert body["ok"] is True, body.get("message")
    # 占位符要原样交给内核去替换（替换逻辑与运行时是同一段代码）
    assert captured["config"].url_template == "https://api.test/v1/orders/{order_id}/refund"
    assert captured["params"]["order_id"] == "11333"


@pytest.mark.asyncio
async def test_preview_refuses_only_the_placeholders_that_lack_values(client, admin_api_key):
    """两个占位符只填了一个：只报没填的那个，不要把已填的也一起点名。"""
    with patch.object(GenericApiToolFactory, "_perform_request", new=AsyncMock()) as mocked:
        response = await client.post(
            PREVIEW_URL,
            json=_payload(
                url_template="https://api.test/v1/orders/{order_id}/items/{item_id}",
                params={"order_id": "11333"},
            ),
            headers={"X-API-Key": admin_api_key},
        )

    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "missing_param"
    assert "item_id" in body["message"]
    assert "order_id" not in body["message"]
    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_preview_treats_blank_and_zero_values_correctly(client, admin_api_key):
    """空白串算「没填」；数字 ``0`` 是**有效值**，不能被 falsy 判断吃掉。"""
    with patch.object(GenericApiToolFactory, "_perform_request", new=AsyncMock()) as mocked:
        response = await client.post(
            PREVIEW_URL,
            json=_payload(
                url_template="https://api.test/v1/orders/{order_id}",
                params={"order_id": "   "},
            ),
            headers={"X-API-Key": admin_api_key},
        )
    assert response.json()["error_kind"] == "missing_param"
    mocked.assert_not_called()

    captured: dict = {}

    async def fake(config, params, **kwargs):
        captured["params"] = params
        return _response(200, json_body={"ok": True}), 3, "https://api.test/v1/orders/0"

    with patch.object(GenericApiToolFactory, "_perform_request", new=fake):
        response = await client.post(
            PREVIEW_URL,
            json=_payload(
                url_template="https://api.test/v1/orders/{order_id}",
                params={"order_id": 0},
            ),
            headers={"X-API-Key": admin_api_key},
        )
    assert response.json()["ok"] is True
    assert captured["params"]["order_id"] == 0


@pytest.mark.asyncio
async def test_preview_classifies_http_error(client, admin_api_key):
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(_response(404, text="not found"), 9, "https://api.test/v1/items")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(), headers={"X-API-Key": admin_api_key})
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "http_error"
    assert body["status_code"] == 404
    assert "404" in body["message"]
    assert body["body"] == "not found"


@pytest.mark.asyncio
async def test_preview_classifies_timeout(client, admin_api_key):
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(side_effect=httpx.TimeoutException("timed out")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(), headers={"X-API-Key": admin_api_key})
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "timeout"


@pytest.mark.asyncio
async def test_preview_classifies_connection_failure(client, admin_api_key):
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(side_effect=httpx.ConnectError("dns boom")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(), headers={"X-API-Key": admin_api_key})
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "connection"


@pytest.mark.asyncio
async def test_preview_rejects_unsupported_method_without_sending(client, admin_api_key):
    with patch.object(GenericApiToolFactory, "_perform_request", new=AsyncMock()) as mocked:
        response = await client.post(
            PREVIEW_URL, json=_payload(method="HEAD"), headers={"X-API-Key": admin_api_key},
        )
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "unsupported_method"
    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_preview_classifies_unsupported_method_from_kernel(client, admin_api_key):
    """即使绕过了入口预检，内核抛出的同类异常也要归到同一类。"""
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(side_effect=UnsupportedMethodError("HEAD")),
    ):
        response = await client.post(
            PREVIEW_URL, json=_payload(method="HEAD"), headers={"X-API-Key": admin_api_key},
        )
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "unsupported_method"


@pytest.mark.asyncio
async def test_preview_truncates_long_body(client, admin_api_key):
    long_text = "x" * 5000
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(_response(200, text=long_text), 10, "https://api.test/v1/items")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(), headers={"X-API-Key": admin_api_key})
    body = response.json()
    assert body["ok"] is True
    assert body["body_truncated"] is True
    assert len(body["body"]) == 2000


@pytest.mark.asyncio
async def test_preview_passes_in_memory_draft_shaped_like_runtime(client, admin_api_key):
    """内核收到的必须是内存草稿，且 headers/schema 已序列化成字符串（与运行时同形）。"""
    captured = {}

    async def fake_perform(config, params, **kwargs):
        captured["config"] = config
        captured["params"] = params
        captured["kwargs"] = kwargs
        return _response(200, json_body={"ok": True}), 5, "https://api.test/v1/items"

    with patch.object(GenericApiToolFactory, "_perform_request", new=fake_perform):
        await client.post(PREVIEW_URL, json=_payload(), headers={"X-API-Key": admin_api_key})

    config = captured["config"]
    assert isinstance(config.headers, str)
    assert isinstance(config.parameter_schema, str)
    assert captured["params"] == {"id": "7"}
    # 试跑必须拿到非 2xx 的原始响应用于分类展示，而不是让它抛异常
    assert captured["kwargs"].get("raise_on_http_error") is False
    # 试跑没有 AgentContext，必须显式把当前登录用户交给内核替换 {user.xxx}
    assert captured["kwargs"].get("user_info", {}).get("user_name")


@pytest.mark.asyncio
async def test_preview_classifies_missing_user_variable(client, admin_api_key):
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(side_effect=UserPlaceholderError(["dept_code"])),
    ):
        response = await client.post(
            PREVIEW_URL, json=_payload(), headers={"X-API-Key": admin_api_key},
        )

    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "missing_user_variable"
    assert "{user.dept_code}" in body["message"]


@pytest.mark.asyncio
async def test_preview_request_lands_in_audit_trail(client, admin_api_key, monkeypatch):
    """目标要求「审计留痕」：试跑必须落进审计，且归类到「工具中心」。

    审计不靠端点自己写代码——``AccessLogMiddleware`` 对每个 ``/api/`` 请求自动落库。
    这条用例把那个「自动」钉住：一旦有人给中间件加路径排除、改了 ``FEATURE_MAP``、
    或者把 preview 挪到 ``/api/`` 之外，试跑就会**悄悄**从审计里消失。
    """
    from app.services.audit_service import AuditService

    captured = []

    async def fake_log_request_data(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(AuditService, "log_request_data", fake_log_request_data)

    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(_response(200, json_body={"ok": True}), 7, "https://api.test/v1/items")),
    ):
        response = await client.post(
            PREVIEW_URL, json=_payload(), headers={"X-API-Key": admin_api_key},
        )
    assert response.status_code == 200

    # 审计走 BackgroundTask，给它一点时间落地
    for _ in range(40):
        if captured:
            break
        await asyncio.sleep(0.05)

    assert captured, "试跑请求没有被审计中间件记录"
    entry = captured[-1]
    assert entry["endpoint"] == PREVIEW_URL
    assert entry["method"] == "POST"
    assert entry["status_code"] == 200
    # 归类：前缀匹配 /api/portal/tools -> 工具中心
    assert AuditService.get_feature_name(PREVIEW_URL) == "工具中心"


def test_preview_credentials_are_masked_before_landing_in_audit():
    """试跑请求体里的凭据必须在落库前被抹掉（含连字符写法）。"""
    from app.utils.masking import mask_sensitive_data

    body = {
        "method": "GET",
        "url_template": "https://api.test/v1/items",
        "headers": {"Authorization": "Bearer sk-live-abc", "X-Api-Key": "secret-abc"},
        "params": {"id": "7"},
    }
    masked = mask_sensitive_data(body)

    assert masked["headers"]["Authorization"] == "******"
    assert masked["headers"]["X-Api-Key"] == "******"
    assert "sk-live-abc" not in str(masked)
    assert "secret-abc" not in str(masked)
