"""网页预览内嵌实测接口（``/api/portal/web-preview/embed-check``）契约测试。

探测函数一律打桩：真实用例不得向外网发起请求。这里只锁定三件事——
鉴权、**SSRF 校验必须先于任何出站请求**、以及三态结论（可嵌 / 受限 / 未知）
如何映射到 HTTP 响应。
"""

from typing import Any, Dict, List

import pytest
from httpx import AsyncClient

from app.api.portal.endpoints import web_preview
from app.services.web_preview_embed_service import EmbedProbeResult

URL = "/api/portal/web-preview/embed-check"


def _result(
    embeddable,
    *,
    reason: str = "",
    signals: Dict[str, str] | None = None,
    final_url: str = "https://example.com/",
) -> EmbedProbeResult:
    return EmbedProbeResult(
        embeddable=embeddable,
        reason=reason,
        signals=signals or {},
        final_url=final_url,
    )


@pytest.fixture
def probe_spy(monkeypatch):
    """记录探测调用，避免测试真的出网。"""
    calls: List[str] = []

    def _install(result: EmbedProbeResult):
        async def _fake_probe(url: str, **_kwargs) -> EmbedProbeResult:
            calls.append(url)
            return result

        monkeypatch.setattr(web_preview, "probe_embed_policy", _fake_probe)
        return calls

    return _install


@pytest.mark.asyncio
async def test_embed_check_requires_api_key(client: AsyncClient):
    response = await client.post(URL, json={"url": "https://example.com"})

    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_embed_check_reports_restricted_site(client: AsyncClient, valid_api_key: str, probe_spy):
    calls = probe_spy(
        _result(
            False,
            reason="目标站点通过 X-Frame-Options: deny 禁止内嵌",
            signals={"x-frame-options": "deny"},
        )
    )

    response = await client.post(
        URL,
        json={"url": "https://github.com"},
        headers={"X-API-Key": valid_api_key},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["embeddable"] is False
    assert body["signals"]["x-frame-options"] == "deny"
    assert "X-Frame-Options" in body["reason"]
    assert calls == ["https://github.com"]


@pytest.mark.asyncio
async def test_embed_check_reports_embeddable_site(client: AsyncClient, valid_api_key: str, probe_spy):
    probe_spy(_result(True, reason="目标站点未声明防嵌套策略", final_url="https://www.baidu.com/"))

    response = await client.post(
        URL,
        json={"url": "http://www.baidu.com"},
        headers={"X-API-Key": valid_api_key},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["embeddable"] is True
    assert body["final_url"] == "https://www.baidu.com/"


@pytest.mark.asyncio
async def test_embed_check_returns_null_for_unreachable_target(
    client: AsyncClient, valid_api_key: str, probe_spy, monkeypatch
):
    """探测不出结论时必须回 null，让前端降级——绝不能默认成「受限」再次误拦。"""
    probe_spy(_result(None, reason="探测超时"))

    # 地址校验会真的做 DNS 解析，而这里只是想模拟「探测无结论」这一种结果。用编造的
    # 域名会让测试成败取决于能否解析（CI/无外网环境直接 400 而不是 200），所以打桩掉。
    # SSRF 本身另有专门用例 test_embed_check_rejects_internal_addresses_before_probing。
    monkeypatch.setattr(web_preview, "validate_public_navigation", lambda url: url)

    response = await client.post(
        URL,
        json={"url": "https://slow.example.com"},
        headers={"X-API-Key": valid_api_key},
    )

    assert response.status_code == 200
    assert response.json()["embeddable"] is None


@pytest.mark.asyncio
async def test_embed_check_rejects_internal_addresses_before_probing(
    client: AsyncClient, valid_api_key: str, probe_spy
):
    """SSRF 防线：内网/环回地址必须在出站前被拒，且不得调用探测函数。"""
    calls = probe_spy(_result(True))

    response = await client.post(
        URL,
        json={"url": "http://127.0.0.1:8001/api/portal/system/langfuse"},
        headers={"X-API-Key": valid_api_key},
    )

    assert response.status_code == 400
    assert calls == [], "内网地址不得触发出站探测"


@pytest.mark.asyncio
async def test_embed_check_rejects_non_http_scheme(client: AsyncClient, valid_api_key: str, probe_spy):
    calls = probe_spy(_result(True))

    response = await client.post(
        URL,
        json={"url": "file:///etc/passwd"},
        headers={"X-API-Key": valid_api_key},
    )

    assert response.status_code == 400
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"url": ""}, {"url": "   "}])
async def test_embed_check_requires_non_empty_url(
    client: AsyncClient, valid_api_key: str, payload: Dict[str, Any]
):
    """入参不合法一律 400（与 ``app/main.py`` 全局校验异常处理保持一致）。"""
    response = await client.post(URL, json=payload, headers={"X-API-Key": valid_api_key})

    assert response.status_code == 400