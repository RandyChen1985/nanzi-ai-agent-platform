"""沙箱票据：给「脚本取凭据」一次执行用的短期通行证。

脚本在沙箱（或本地工作区）里跑，手上没有 session，也没法带 API key。它唯一能
证明「这次调用属于哪个用户、哪个会话」的东西，就是平台在执行命令前临时写进
工作区的那张票。

三条设计取舍：

* **无状态 HMAC，不落库**。TTL 只有 5 分钟，为它建表就得再配一个清理任务，
  而票据本身不承载任何可变状态——签名自证，到点自然失效。
* **`aud` 参与签名**。否则这张票能被挪去调别的内部接口：签的是「取凭据」，
  就必须只在「取凭据」这个接口上有效。
* **必须带 `conversation_id`**。少了它，A 会话的票能拿到 B 会话里接着用，
  审计里只剩一个用户 id，事后说不清是哪次对话取的。

密钥从 ``ENCRYPTION_KEY`` 派生而不是直接复用：同一份密钥材料做两种用途
（数据加密 + 消息签名），任何一边的算法弱点都会串到另一边。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

#: 这张票只对「取凭据」接口有效。改接口就要跟着改这里，否则验签不过。
AUDIENCE = "script-get"

#: 一次 bash 执行的生命周期远短于这个值；留足余量给慢命令。
DEFAULT_TTL_SECONDS = 300

_VERSION = "v1"

#: 派生密钥用的上下文串。改动它等于让所有在途票据作废，属于破坏性变更。
_KEY_CONTEXT = b"nanzi:sandbox-ticket:v1"

#: 允许的时钟偏差：签发方与校验方在同一台机器，但容器时间可能被 NTP 微调。
_CLOCK_SKEW_SECONDS = 5


@dataclass(frozen=True)
class TicketClaims:
    """验签通过后解出的声明。字段名短，是为了让票据本身短。"""

    user_id: str
    conversation_id: str
    sandbox_id: str
    expires_at: int
    jti: str
    audience: str
    #: 用户所属部门。部门授权的系统级凭据只在 dept_code 非空时才匹配得上，
    #: 所以它必须跟着票走，不能在接口侧猜或干脆丢掉。
    dept_code: str = ""


def _signing_key() -> bytes:
    """从平台加密密钥派生出本模块专用的签名密钥。

    ``ENCRYPTION_KEY`` 缺失时这里会得到空字节流的派生值——不抛异常，因为票据
    校验的失败路径必须是「验签不过」（fail-closed），而不是让调用方拿到一个
    500 再猜是什么问题。签发侧会先做一次显式检查（见 :func:`issue_ticket`）。
    """
    base = (getattr(settings, "ENCRYPTION_KEY", None) or "").encode("utf-8")
    return hmac.new(base, _KEY_CONTEXT, hashlib.sha256).digest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    # 去掉 padding 的那份要补回来才能解；长度不对时由 b64decode 自己抛。
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(body: str, audience: str) -> str:
    message = f"{_VERSION}.{body}.{audience}".encode("utf-8")
    return hmac.new(_signing_key(), message, hashlib.sha256).hexdigest()


def issue_ticket(
    *,
    user_id: str,
    conversation_id: str,
    sandbox_id: str = "",
    dept_code: str = "",
    audience: str = AUDIENCE,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """签一张票。返回 ``v1.<payload>.<sig>``。

    调用方拿到后应立刻写进工作区（0600），命令结束就删。
    """
    if not user_id:
        raise ValueError("沙箱票据必须绑定用户")
    if not conversation_id:
        raise ValueError("沙箱票据必须绑定会话，否则无法追溯是哪次对话取的")
    if not getattr(settings, "ENCRYPTION_KEY", None):
        raise RuntimeError("ENCRYPTION_KEY 未配置，无法签发沙箱票据")

    issued_at = time.time() if now is None else now
    payload: dict[str, Any] = {
        "aud": audience,
        "cid": conversation_id,
        "exp": int(issued_at) + int(ttl_seconds),
        "dept": dept_code or "",
        "jti": secrets.token_hex(8),
        # 沙箱 id 只是辅助信息：本地执行时为空。它不参与授权判定——判定只认
        # 用户与会话，沙箱 id 变了（重建）不该让在途的命令突然失去权限。
        "sid": sandbox_id or "",
        "uid": user_id,
    }
    body = _b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{_VERSION}.{body}.{_sign(body, audience)}"


def verify_ticket(
    token: str | None,
    *,
    audience: str = AUDIENCE,
    now: float | None = None,
) -> TicketClaims | None:
    """验票。任何一步不过都返回 ``None``——调用方不必区分「过期」还是「伪造」，
    那种区分只对攻击者有用。人话提示由接口层按本节常量给出。"""
    if not token:
        return None
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    version, body, signature = parts
    if version != _VERSION:
        return None

    expected = _sign(body, audience)
    # compare_digest：普通 == 会因为「第几个字符开始不同」而泄露签名信息。
    if not hmac.compare_digest(expected, signature):
        return None

    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except Exception:  # noqa: BLE001 - 编码坏了等同于伪造
        return None
    if not isinstance(payload, dict):
        return None

    # 票据里也存了一份 aud：签名里已经有，这里再比一次，是为了挡住
    # 「同一个密钥签发、但 aud 字段被篡改成别的值」这种构造（签名能过，
    # 因为签名覆盖的是传入的 audience，而携带的是 payload 里的 aud）。
    if str(payload.get("aud") or "") != audience:
        return None

    try:
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError):
        return None

    current = time.time() if now is None else now
    if current > expires_at + _CLOCK_SKEW_SECONDS:
        return None

    user_id = str(payload.get("uid") or "")
    conversation_id = str(payload.get("cid") or "")
    if not user_id or not conversation_id:
        return None

    return TicketClaims(
        user_id=user_id,
        conversation_id=conversation_id,
        sandbox_id=str(payload.get("sid") or ""),
        expires_at=expires_at,
        jti=str(payload.get("jti") or ""),
        audience=audience,
        dept_code=str(payload.get("dept") or ""),
    )


#: 「这张票取过哪些凭据」的记录键前缀。**只存凭据标识，从不存值**——
#: 值的脱敏由 Bash 侧按标识重新解密完成（见 workspace 的 _redact_script_bash_result）。
_CONSUMED_KEY_PREFIX = "script_cred_consumed:"

#: 记录比票据本身多活一会儿：命令可能跑得比 TTL 更久，结果回来时票已过期，
#: 但那一次的脱敏还得做。
_CONSUMED_TTL_SECONDS = 900


async def record_consumed_key(
    *,
    jti: str,
    key: str,
    user_id: str,
    conversation_id: str,
    dept_code: str = "",
) -> None:
    """登记「这张票取了一条凭据」。失败只告警：审计记录不该阻断取值。"""
    if not jti or not key:
        return
    from app.core.redis import get_redis

    try:
        redis = await get_redis()
        if not redis:
            return
        redis_key = f"{_CONSUMED_KEY_PREFIX}{jti}"
        # 存 JSON 行而不是纯 key 名：脱敏要按 user_id 重新解密，会话 id 用于审计对齐。
        await redis.rpush(
            redis_key,
            json.dumps(
                {
                    "key": key,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "dept_code": dept_code or "",
                },
                ensure_ascii=False,
            ),
        )
        await redis.expire(redis_key, _CONSUMED_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.warning("[SandboxTicket] 记录取用标识失败 jti=%s key=%s", jti, key)


async def peek_consumed_keys(jti: str) -> list[dict[str, str]]:
    """读这张票的记录，**不删除**。

    流式输出期间要反复读（每来一个 chunk 就得知道「到目前为止取过什么」），
    所以读和删必须分开——读一次删一次会让后半个流拿不到前面的值。
    """
    if not jti:
        return []
    from app.core.redis import get_redis

    redis = await get_redis()
    if not redis:
        return []
    raw_items = await redis.lrange(f"{_CONSUMED_KEY_PREFIX}{jti}", 0, -1)
    return _decode_consumed(raw_items)


async def take_consumed_keys(jti: str) -> list[dict[str, str]]:
    """取走这张票的记录并删除。取不到返回空表。"""
    if not jti:
        return []
    from app.core.redis import get_redis

    redis = await get_redis()
    if not redis:
        return []
    redis_key = f"{_CONSUMED_KEY_PREFIX}{jti}"
    raw_items = await redis.lrange(redis_key, 0, -1)
    try:
        await redis.delete(redis_key)
    except Exception:  # noqa: BLE001
        pass
    return _decode_consumed(raw_items)


def _decode_consumed(raw_items: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for raw in raw_items or []:
        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, dict) and payload.get("key"):
            entries.append(
                {
                    "key": str(payload.get("key") or ""),
                    "user_id": str(payload.get("user_id") or ""),
                    "conversation_id": str(payload.get("conversation_id") or ""),
                    "dept_code": str(payload.get("dept_code") or ""),
                }
            )
    return entries
