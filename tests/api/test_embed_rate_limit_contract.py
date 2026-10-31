"""嵌入端点限流的接线契约。

`POST /embed/tickets` 与 `POST /embed/miniprogram/session`（按 openid **自动开户**）
此前没有任何频率约束——这是"对第三方站点开放"的入口，被刷的代价是垃圾账号 + Redis 灌满。

这里锁定三件事：
1. 限流调用必须在业务 `try` **之前**（`except Exception` 会把 HTTPException 吞成 500）；
2. 小程序会话既限 openid 也限 appid（前者防单用户刷号，后者防整包被打爆）；
3. 超限用 429 表达。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
EMBED_ENDPOINT = ROOT / "app/api/v1/endpoints/embed.py"


def _src() -> str:
    return EMBED_ENDPOINT.read_text(encoding="utf-8")


def test_rate_limit_helper_returns_429():
    src = _src()
    assert "from app.core.rate_limit import hit_rate_limit" in src
    assert "status_code=429" in src


def test_ticket_creation_is_rate_limited_before_try():
    src = _src()
    body = src.split("async def create_embed_ticket(", 1)[1]
    rate_pos = body.index("_enforce_rate_limit(")
    try_pos = body.index("    try:")
    assert rate_pos < try_pos, "限流必须在业务 try 之前，否则 429 会被转成 500"
    assert "embed:ticket:" in body


def test_miniprogram_account_creation_is_limited_by_openid_and_appid():
    src = _src()
    body = src.split("async def create_miniprogram_session(", 1)[1]

    openid_pos = body.index("embed:mp:openid:")
    appid_pos = body.index("embed:mp:appid:")
    # 两道限流都要在开户（resolve_or_create_user）之前生效
    signup_pos = body.index("resolve_or_create_user(")
    assert openid_pos < signup_pos, "openid 维度限流必须在自动开户之前"
    assert appid_pos < signup_pos, "appid 维度限流必须在自动开户之前"

    # 开户前的开关校验仍要在限流之前：未开启微端时不该消耗配额
    enabled_pos = body.index('ConfigService.get("miniprogram_enabled")')
    assert enabled_pos < openid_pos


def test_limits_documented_as_constants():
    src = _src()
    assert "EMBED_TICKET_RATE_LIMIT_PER_MINUTE" in src
    assert "MINIPROGRAM_OPENID_RATE_LIMIT_PER_MINUTE" in src
    assert "MINIPROGRAM_APPID_RATE_LIMIT_PER_MINUTE" in src
