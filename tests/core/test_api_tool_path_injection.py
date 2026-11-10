"""path 参数注入：`POST /refund/{applicant}` 里的 applicant 也是可以绑凭据的。

原来注入挪在路径替换**之后**，而 `apply_system_params` 会把凭据参数摘掉，
于是 `{applicant}` 拿不到值、字面量被原样发出去。这里锁住修好后的顺序。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.secrets.policy import resolve_api_tool_slot


pytestmark = pytest.mark.no_infrastructure


def _config(*, method: str, url: str, params: list[str], tool_id: str = "tool-1"):
    import json

    # 标成凭据的参数**不在** properties 里（前端保存时会移除），否则会撞
    # 「系统级参数与普通参数重名」的校验——模型本来就看不到它们。
    schema = {
        "type": "object",
        "properties": {},
        "x-nanzi-system-params": [
            {"name": name, "type": "string", "source": "credential"} for name in params
        ],
    }
    return SimpleNamespace(
        id=tool_id,
        name="退款",
        method=method,
        url_template=url,
        parameter_schema=json.dumps(schema),
        headers="{}",
        signing_config=None,
    )


@pytest.mark.parametrize(
    ("method", "url", "expected"),
    [
        ("POST", "https://api.test/refund/{applicant}", "path:applicant"),
        ("GET", "https://api.test/refund", "query:applicant"),
        ("POST", "https://api.test/refund", "body:applicant"),
        ("PUT", "https://api.test/refund", "body:applicant"),
    ],
)
def test_slot_follows_tool_config(method, url, expected):
    assert (
        resolve_api_tool_slot(name="applicant", method=method, url_template=url)
        == expected
    )


def test_path_placeholder_needs_exact_match():
    """`{applicant_id}` 不能让 `applicant` 被当成 path。"""
    assert (
        resolve_api_tool_slot(
            name="applicant",
            method="POST",
            url_template="https://api.test/refund/{applicant_id}",
        )
        == "body:applicant"
    )


async def test_credential_value_reaches_the_url_path():
    """端到端：绑定值必须出现在最终 URL 里，而不是字面量 `{applicant}`。

    这条守的是**顺序**——注入必须早于路径替换。反过来的话
    `apply_system_params` 已经把参数摘掉了，URL 里留着 `{applicant}` 发出去。
    """
    from app.services.ai.tools.generic_api import GenericApiToolFactory

    config = _config(
        method="POST", url="https://api.test/refund/{applicant}", params=["applicant"]
    )
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        content = b"{}"
        headers = {"content-type": "application/json"}
        text = "{}"

        def json(self):
            return {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def request(self, method, url, **kwargs):
            captured["url"] = str(url)
            captured["method"] = method
            return _FakeResponse()

    with patch(
        "app.services.ai.tools.generic_api.apply_api_tool_param_bindings",
        new=AsyncMock(return_value={"applicant": "APP-42"}),
    ), patch("httpx.AsyncClient", _FakeClient):
        await GenericApiToolFactory._execute_request(config, {"applicant": "ignored"})

    assert "{applicant}" not in captured["url"]
    assert "APP-42" in captured["url"]
