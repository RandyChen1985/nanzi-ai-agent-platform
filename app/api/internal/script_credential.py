"""脚本取凭据的内部端点。

流程：验票 → 限流 → 按 key 取值 → 登记回显兜底 → 返回明文。

三个刻意的选择：

1. **错误一律回 200 之外的码，但不带堆栈**。helper 要把原因说成人话给脚本作者看，
   所以响应体统一 ``{"code": <http>, "msg": "...", "deny": "..."}``，
   ``deny`` 是内部枚举，供日志检索；``msg`` 是人话，直接打到 stderr。
2. **不在响应里带任何其它凭据信息**。只给值，不给 id / 分类 / 备注——
   helper 拿到的越少，被误导着多发一个请求的机会越少。
3. **限流按票据 jti**。脚本在循环里取 300 个 key 时，拒绝点应该落在这一张票上，
   而不是落在用户身上——用户可能同时在别的会话里正常工作。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.rate_limit import hit_rate_limit
from app.services.secrets.injection import denial_hint, denial_text
from app.services.secrets.policy import SCENE_SANDBOX_SCRIPT
from app.services.secrets.redact import register_injected
from app.services.secrets.resolver import secret_resolver
from app.services.secrets.sandbox_ticket import record_consumed_key, verify_ticket

logger = logging.getLogger(__name__)

router = APIRouter()

#: 每张票据每分钟最多取几条。默认 60：够一个脚本把手上要用的 key 都取一遍
#: （正常脚本不会超过 10 条），又能挡住「循环遍历整池凭据」。
RATE_LIMIT_PER_TICKET = 60
RATE_LIMIT_WINDOW_SECONDS = 60


class ScriptCredentialListRequest(BaseModel):
    """list 不带参数。用 POST 是为了和 get 一样走「票据在头里」的形态，
    免得 GET 时浏览器/代理把空 body 的请求揉成一类可缓存的调用。"""


class ScriptCredentialRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=128, description="凭据标识，如 weather.api.key")


def _fail(status: int, message: str, *, deny: str = "") -> JSONResponse:
    body: dict[str, Any] = {"code": status, "msg": message}
    if deny:
        body["deny"] = deny
    return JSONResponse(status_code=status, content=body)


@router.post("/script-credential/get", summary="脚本按标识取一条凭据（票据鉴权）")
async def get_script_credential(
    request: Request,
    payload: ScriptCredentialRequest,
    x_nanzi_ticket: str | None = Header(default=None, alias="X-Nanzi-Ticket"),
) -> JSONResponse:
    claims = verify_ticket(x_nanzi_ticket)
    if claims is None:
        # 过期 / 伪造 / 缺失都归一类：区分它们只对攻击者有用。
        return _fail(401, "票据无效或已过期，请重新执行命令", deny="ticket_invalid")

    try:
        over_limit = await hit_rate_limit(
            f"script-cred:{claims.jti}",
            RATE_LIMIT_PER_TICKET,
            RATE_LIMIT_WINDOW_SECONDS,
        )
    except Exception:  # noqa: BLE001 - 限流自己坏了不该把功能打挂
        over_limit = False
    if over_limit:
        logger.warning(
            "[ScriptCredential] 票据超限 jti=%s user=%s conv=%s",
            claims.jti,
            claims.user_id,
            claims.conversation_id,
        )
        return _fail(
            429,
            f"取凭据太频繁（每分钟最多 {RATE_LIMIT_PER_TICKET} 次），请改成分批取或复用已取到的值",
            deny="rate_limited",
        )

    key = payload.key.strip()
    if not key:
        return _fail(400, "凭据标识不能为空", deny="bad_request")
    result = await secret_resolver.resolve_by_key(
        user_id=claims.user_id,
        secret_key=key,
        scene=SCENE_SANDBOX_SCRIPT,
        conversation_id=claims.conversation_id,
        dept_code=claims.dept_code or None,
    )

    if not result.ok or result.value is None:
        reason = result.denial or "not_found"
        hint = denial_hint(reason)
        message = denial_text(reason)
        if hint:
            message = f"{message}：{hint}"
        # 拒绝也要留痕：审计里「谁在什么时候试图取哪条」比「取成功了什么」更关键。
        logger.info(
            "[ScriptCredential] 拒绝 key=%s user=%s conv=%s reason=%s",
            key,
            claims.user_id,
            claims.conversation_id,
            reason,
        )
        return _fail(403, message, deny=reason)

    value = result.value.reveal()
    # 同一个请求上下文里的回显兜底（本进程内若是被同步调用）。
    register_injected(value)
    # 跨进程的兜底：只登记「取了哪个标识」，值不进 Redis。Bash 执行完按标识重新
    # 解密再掩码，见 workspace._redact_script_bash_result。
    await record_consumed_key(
        jti=claims.jti,
        key=key,
        user_id=claims.user_id,
        conversation_id=claims.conversation_id,
        dept_code=claims.dept_code,
    )

    return JSONResponse(
        status_code=200,
        content={"code": 0, "data": {"key": key, "value": value}},
    )


@router.post("/script-credential/list", summary="脚本列出可用的凭据标识（票据鉴权）")
async def list_script_credentials(
    request: Request,
    x_nanzi_ticket: str | None = Header(default=None, alias="X-Nanzi-Ticket"),
) -> JSONResponse:
    """只返回标识与显示名，**不含任何值**。

    没有它，脚本作者只能靠猜标识拼写，猜错的提示是「没有这条凭据」——看着像权限
    问题，实际是名字写错。过滤口径与 get 完全一致，不会出现「列出来了却取不到」。
    """
    claims = verify_ticket(x_nanzi_ticket)
    if claims is None:
        return _fail(401, "票据无效或已过期，请重新执行命令", deny="ticket_invalid")

    try:
        over_limit = await hit_rate_limit(
            f"script-cred:{claims.jti}",
            RATE_LIMIT_PER_TICKET,
            RATE_LIMIT_WINDOW_SECONDS,
        )
    except Exception:  # noqa: BLE001 - 限流自己坏了不该把功能打挂
        over_limit = False
    if over_limit:
        logger.warning(
            "[ScriptCredential] 票据超限(list) jti=%s user=%s conv=%s",
            claims.jti,
            claims.user_id,
            claims.conversation_id,
        )
        return _fail(
            429,
            f"请求凭据接口太频繁（每分钟最多 {RATE_LIMIT_PER_TICKET} 次），请降低调用频率",
            deny="rate_limited",
        )

    items = await secret_resolver.list_keys_for_script(
        user_id=claims.user_id, dept_code=claims.dept_code or None
    )
    if not items and not await secret_resolver.script_channel_open(
        user_id=claims.user_id
    ):
        # 空列表有两种原因，要做的事完全不同：一条凭据都没有 vs 开关没开。
        # 不区分的话，用户会去翻凭据列表找半天，其实是策略没开。
        return JSONResponse(
            status_code=200,
            content={
                "code": 0,
                "data": {
                    "items": [],
                    "message": (
                        "脚本读取凭据的开关没有打开：需要平台策略与个人偏好都开启，"
                        "且凭据上勾选「脚本可用」。"
                    ),
                },
            },
        )
    return JSONResponse(status_code=200, content={"code": 0, "data": {"items": items}})
