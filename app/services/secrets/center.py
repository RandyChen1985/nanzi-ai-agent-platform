"""凭据中心的写操作：创建、轮换、绑定、授权。

与 ``resolver.py`` 的分工：这里负责「把数据写对」，那里负责「把值给对」。
两者都刻意不含任何返回明文的路径——``credential`` 类型建完即不可读。

三个必须在写入侧守住的不变量：

* ``secret_key`` 只允许 ``[a-z0-9._-]``：它会被拼进 ``${secret:<key>}``，
  带空格或冒号会让引用语法无法解析；
* ``credential`` 不写 ``value_preview``：预览就是泄露，哪怕只有 8 个字符；
* 轮换不是覆盖，而是把旧值挪到 ``previous_*`` 并给一个过渡窗口——
  直接覆盖会让正在跑的定时任务在下一分钟突然失败，而且没法回滚。
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import and_, delete, or_, select

from app.models.secret import SecretBinding, SecretGrant, UserSecret
from app.services.secrets.fingerprint import secret_fingerprint
from app.services.secrets.policy import (
    ALL_SCENES,
    CONSUMER_API_TOOL,
    GRANTEE_ALL,
    GRANTEE_DEPT,
    GRANTEE_TYPES,
    GRANTEE_USER,
    SCOPE_SYSTEM,
    SCOPE_USER,
    TYPE_CREDENTIAL,
    TYPE_VARIABLE,
    normalize_host,
)
from app.services.secrets.registry import secret_registry
from app.utils.encryption import get_api_key_manager

logger = logging.getLogger(__name__)

#: ``secret_key`` 的合法字符集。它会被拼进 ``${secret:<key>}`` 引用语法。
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

#: 轮换后的旧值保留天数。够覆盖「按天跑的定时任务」至少一轮。
ROTATION_OVERLAP_DAYS = 7

VALID_SCOPES = frozenset({SCOPE_USER, SCOPE_SYSTEM})
VALID_TYPES = frozenset({TYPE_CREDENTIAL, TYPE_VARIABLE})
VALID_CONSUMER_TYPES = frozenset({"mcp_server", "api_tool", "script_tool", "agent"})


def _to_naive_utc(value: datetime | None) -> datetime | None:
    """库里时间戳一律存 naive UTC；前端若传带时区 ISO 字符串，先折算成 UTC 再去 tzinfo。"""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value

#: 每种消费者**实际会被注入**的槽位前缀。
#
# 注入层只认这些前缀（``injection.py``）：MCP 只看 ``header:``，API 工具看
# ``header:`` / ``query:`` / ``body:``，脚本只看 ``env:``。表外的组合能绑成功，
# 但运行时永远不会被读到——用户以为配好了，调用时才发现没生效，而且没有任何报错。
async def _audit(
    *,
    enabled: bool = True,
    action: str,
    secret,
    actor_id: str | None,
    actor_name: str | None = None,
    detail: dict | None = None,
) -> None:
    """记一条变更审计，**用独立的 session 和事务**。

    为什么不挂在业务事务上：审计表是本次新增的（V209 / V109 第三节），而代码可以先于
    迁移上线。如果共用一个事务，表没建的那一天，用户的建凭据、轮换、授权会
    统统失败——为了一个附属记录把主功能拖垮，不划算。

    代价是极端情况下「操作成功、审计没写」。这是有意的取舍：审计的价值在于
    **事后可查**，不在于强一致；而主功能的可用性是当场就要的。

    任何异常都被吞掉，只记日志。
    """
    if not enabled:
        return

    import json as _json
    import uuid as _uuid

    from app.core.orm import AsyncSessionLocal
    from app.models.secret import SecretAuditLog

    try:
        async with AsyncSessionLocal() as audit_session:
            audit_session.add(
                SecretAuditLog(
                    id=str(_uuid.uuid4()),
                    secret_id=getattr(secret, "id", None),
                    secret_key_snapshot=getattr(secret, "secret_key", None),
                    owner_scope=getattr(secret, "owner_scope", None),
                    actor_id=actor_id,
                    actor_name=actor_name,
                    action=action,
                    detail=_json.dumps(detail, ensure_ascii=False) if detail else None,
                )
            )
            await audit_session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("[SecretAudit] 写审计失败: action=%s", action, exc_info=True)


ALLOWED_SLOT_PREFIXES = {
    # arg: —— 工具方法的入参。MCP 工具声明 credential_params 后，值按
    # ``arg:<短工具名>:<参数名>`` 注入 ``tools/call`` 的 arguments。
    "mcp_server": frozenset({"header", "arg"}),
    # path: —— URL 占位 `{param}`。不能只认 query/body：`POST /refund/{applicant}`
    # 里的 applicant 是 path 参数，绑到 body 上永远不会生效。
    "api_tool": frozenset({"header", "path", "query", "body"}),
    "script_tool": frozenset({"env"}),
    "agent": frozenset({"header", "query", "body"}),
}


class SecretValidationError(ValueError):
    """入参不合法。消息会直接回给前端，所以要说清「哪里不对」。"""


class SecretPermissionError(SecretValidationError):
    """无权操作这条凭据。

    和「参数不合法」分开，是因为端点要对它回 **404** 而不是 400：
    403/400 等于承认「这条凭据存在，只是你碰不了」，那就把 UUID 变成了
    一个可探测的存在性预言机。「看不见就是不存在」要在写路径上也成立。
    """


def _validate_key(secret_key: str) -> str:
    key = (secret_key or "").strip().lower()
    if not key:
        raise SecretValidationError("凭据标识不能为空")
    if not _KEY_PATTERN.match(key):
        raise SecretValidationError(
            "凭据标识只能用小写字母、数字、点、下划线、连字符，且以字母或数字开头"
        )
    return key


def _validate_scenarios(scenarios: Iterable[str] | None) -> list[str]:
    if not scenarios:
        return []
    unknown = set(scenarios) - set(ALL_SCENES)
    if unknown:
        raise SecretValidationError(f"未知的注入场景: {', '.join(sorted(unknown))}")
    # 去重并保持稳定顺序，方便前端比对
    return [scene for scene in ALL_SCENES if scene in set(scenarios)]


def _validate_hosts(hosts: Iterable[str] | None) -> list[str]:
    if not hosts:
        return []
    cleaned: list[str] = []
    for host in hosts:
        normalized = normalize_host(host)
        if not normalized:
            continue
        if len(normalized) > 253:
            raise SecretValidationError(f"域名过长: {host}")
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def _encrypt(value: str) -> str:
    return get_api_key_manager().encrypt_api_key(value)


# ── 结构化值（value_format=json）────────────────────────────────────────
#
# 结构化只是「录入与展示的方式」，不是新的存储：JSON 序列化后仍是同一条字符串，
# 加密、掩码、审计、轮换、注入全部复用原有链路。因此这一节只做三件事：
# 校验（是不是合法对象）、记下模板标识、算一个**只含非敏感字段**的列表摘要。

#: 单值（token / PEM 整段）—— 存量记录全是这一类。
VALUE_FORMAT_TEXT = "text"
#: 结构化对象：JSON 序列化后的字符串。
VALUE_FORMAT_JSON = "json"
VALID_VALUE_FORMATS = frozenset({VALUE_FORMAT_TEXT, VALUE_FORMAT_JSON})

#: 模板标识的合法字符集（``smtp`` / ``api-key`` / ``aliyun-oss``）。
#: 模板定义在前端常量里，后端只存这个标识用于回显，不校验字段结构。
# 首字符必须是小写字母（模板标识要能被当成标识符的一部分）。
# 与 locator._TEMPLATE_ID_RE 保持一致，见那边的说明。
_TEMPLATE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

#: 摘要最多拼几个字段、单段长度、整体长度（DB 列 160，留出分隔符余量）。
SUMMARY_MAX_FIELDS = 3
SUMMARY_FIELD_MAX = 48
SUMMARY_MAX_LEN = 150

#: 明显像密钥的值不进摘要。这是**兜底**，不是主防线——主防线是前端声明的敏感字段名。
#: 兜底的必要性在于：前端把 host 填成域名以外的值、或者用户被模板误导把 token 塞进
#: 用户名里，都不该让那串东西出现在列表上。
_SECRETISH_RE = re.compile(
    r"""^(
        sk-|sk_|ghp_|gho_|ghu_|xox[abps]-|AKIA[0-9A-Z]{8,}|LTAI[0-9A-Za-z]{8,}|
        AKID[0-9A-Za-z]{8,}|-----BEGIN\ |Bearer\ |eyJ[A-Za-z0-9_-]{10,}
    )""",
    re.X,
)


def _looks_like_secret(text: str) -> bool:
    """粗判一个值是不是密钥。宁可错杀（摘要少一段）也不能错放。"""
    if _SECRETISH_RE.match(text):
        return True
    # 长、无空格、字母数字混合的连续串：典型 token 形状（host 名/路径通常带点或斜杠）
    if len(text) >= 40 and " " not in text and re.search(r"[A-Za-z]", text) and re.search(r"\d", text):
        return True
    return False


def _validate_value_format(value_format: str | None) -> str:
    fmt = (value_format or VALUE_FORMAT_TEXT).strip().lower()
    if fmt not in VALID_VALUE_FORMATS:
        raise SecretValidationError(f"未知的值形态: {value_format}（只支持 text / json）")
    return fmt


def _validate_value_template(template: str | None, value_format: str) -> str | None:
    """模板标识只对结构化值有意义；单值形态静默丢弃（而不是报错）。"""
    if value_format != VALUE_FORMAT_JSON:
        return None
    cleaned = (template or "").strip().lower()
    if not cleaned:
        return None
    if not _TEMPLATE_PATTERN.match(cleaned):
        raise SecretValidationError("模板标识只能用小写字母、数字与连字符，且以字母或数字开头")
    return cleaned


def _normalize_secret_fields(fields: Iterable[str] | None) -> list[str]:
    """敏感字段名列表（模板里 ``secret: true`` 的那些键）。

    只接受合法键名，去重保序，上限 50 个。它的唯一用途是**从摘要里排除**，
    不参与加密（整段本来就已经加密了）。
    """
    if not fields:
        return []
    cleaned: list[str] = []
    for raw in fields:
        name = str(raw or "").strip()
        if not name:
            continue
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$", name):
            raise SecretValidationError(f"敏感字段名不合法: {raw}")
        if name not in cleaned:
            cleaned.append(name)
        if len(cleaned) > 50:
            raise SecretValidationError("敏感字段过多（上限 50 个）")
    return cleaned


def parse_structured_value(value: str) -> dict[str, Any]:
    """把结构化值解析成对象；不是合法 JSON 对象一律拒绝。

    只校验「是不是对象」，不校验字段 schema —— 模板升级、字段增删都不该让
    一条已经存好的凭据变得读不出来。
    """
    try:
        data = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise SecretValidationError(f"结构化凭据值不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SecretValidationError("结构化凭据值的顶层必须是 JSON 对象（不支持数组）")
    return data


#: 摘要里跳过的**键名**：它们描述「怎么连」的模式开关，值通常是 ssl / key /
#: preferred 这类枚举，占一格却什么也说明不了，真正有辨识度的用户名反被挤掉。
#:
#: 按键名排除而不是按值猜：按值猜（「短小写就是枚举」）会把 ``h`` / ``db1``
#: 这类短主机名一起误杀，而按名字判断的边界清楚、可枚举、可 review。
#: 服务端兜底的敏感键名词根。前端靠模板的 ``secret`` 标记声明，但「自定义 JSON」
#: 没有模板可依、轮换也可能拿不到声明——摘要绝不能因为「没人告诉我这是密码」
#: 就把密码写进明文列。宁可比前端多排除几个字段，也不能漏一个。
_SENSITIVE_KEY_WORDS = frozenset(
    {
        "password", "passwd", "pwd", "pw", "passphrase",
        "secret", "token", "key", "apikey",
        "privatekey", "credential", "credentials",
        "signature", "salt", "cookie", "session", "dsn",
        # 下面这些按词拆不出来：appsecret 是一个整词、webhook 看着像普通地址、
        # pin/passcode 是短口令。模板里它们都标了 secret，后端得认得出。
        "appsecret", "secretkey", "accesskey", "webhook", "pin", "passcode",
    }
)

#: 明确「只是名字」的键，豁免下面的词根过滤。
#:
#: ``key_name`` 是 Header / 参数的名字（``X-API-Key``），不是密钥本身；把它一起
#: 过滤掉，``api-key`` 这类凭据的摘要就只剩空字符串，列表里没有任何辨识度。
_SENSITIVE_KEY_EXEMPT = frozenset({"key_name", "header_name", "param_name"})

#: 驼峰边界：``apiKey`` → ``api_Key``，用于把键名拆成词。
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
#: 词分隔符：下划线 / 连字符 / 点 / 空格。
_KEY_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _key_words(key_name: str) -> list[str]:
    """``auth_tokens`` → ``['auth','tokens']``；``apiKey`` → ``['api','key']``。"""
    spaced = _CAMEL_BOUNDARY_RE.sub("_", key_name)
    return [word for word in _KEY_WORD_SPLIT_RE.split(spaced.lower()) if word]


def _is_sensitive_key(key_name: str) -> bool:
    """键名里**有一个词**像密码，就整条不进摘要。

    为什么按词而不是按后缀（``endswith``）：后缀匹配放过 ``auth_tokens``、
    ``credentials``、``password_v2``、``api_key_live``——带上复数或版本号就绕过了，
    而这些恰恰是最常见的写法。按词匹配既兜住它们，又不会像裸子串匹配那样把
    ``keyboard`` / ``tokenizer`` 误杀。
    """
    if key_name.strip().lower() in _SENSITIVE_KEY_EXEMPT:
        return False
    for word in _key_words(key_name):
        if word in _SENSITIVE_KEY_WORDS:
            return True
        # 复数：tokens → token / credentials → credential
        if word.endswith("s") and word[:-1] in _SENSITIVE_KEY_WORDS:
            return True
    return False


def _looks_like_credentialed_url(text: str) -> bool:
    """``http://user:pass@host:port`` 这类内嵌账密的地址，整条跳过。

    不用正则：未编码的密码里完全可能有 ``/``、``:``、``@``，任何字符类都会漏掉
    一种写法（``http://user:ab/cd@proxy`` 就能绕过 ``[^/@]+``）。``urlsplit`` 按
    URL 语法拆 netloc，直接问它「有没有 password」。
    """
    if "://" not in text:
        return False
    head = text.split()[0] if text.split() else ""
    try:
        parts = urlsplit(head)
    except ValueError:
        return False
    if parts.password:
        return True
    # 未编码的 ``/`` ``:`` ``@`` 会让 netloc 被提前截断：
    # ``http://user:ab/cd@proxy`` 的 netloc 只有 ``user:ab``，urlsplit 看不到 password。
    # 这时按「:// 之后、第一个 @ 之前同时出现冒号」再兜一层——宁可多跳过一个 URL，
    # 也不要把可能是账密的东西写进摘要列。
    tail = head.split("://", 1)[1]
    return "@" in tail and ":" in tail.split("@")[0]


#: URL 查询参数里出现这些名字，说明这段地址本身就是凭证。
#:
#: 企业微信 / 钉钉 / 飞书的 webhook 把 ``key`` ``access_token`` 直接写在 query 上，
#: 键名却叫 ``webhook_url`` —— 词根看不出敏感，``_looks_like_credentialed_url``
#: 只看 ``user:pass@`` 也看不出来。漏了这条，字段预览会把整段 webhook 明文吐出来。
_CREDENTIAL_QUERY_KEYS = frozenset(
    {
        "key", "token", "access_token", "accesstoken", "secret", "sig", "sign",
        "signature", "api_key", "apikey", "auth", "password", "pwd", "code",
    }
)


def _looks_like_secret_url(text: str) -> bool:
    """地址的 query 里带着 ``key=`` ``access_token=`` 这类参数就当凭证看。"""
    if "://" not in text:
        return False
    head = text.split()[0] if text.split() else ""
    try:
        parts = urlsplit(head)
    except ValueError:
        return False
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=False)
    except ValueError:
        return True  # 解析不了就别冒险预览
    return any(name.strip().lower() in _CREDENTIAL_QUERY_KEYS for name, _ in pairs)


_SUMMARY_SKIP_KEYS = frozenset(
    {
        "security", "security_protocol", "auth_type", "ssl_mode", "sslmode",
        "charset", "encoding", "mode", "type", "protocol", "sasl_mechanism",
        "grant_type", "passive", "verify_tls", "verify", "tls", "use_tls",
        "use_ssl", "internal", "in_query", "path_style", "unread_only",
        "format", "version",
    }
)


def build_value_summary(value: str, secret_fields: Iterable[str] | None = None) -> str | None:
    """生成列表摘要，形如 ``smtp.exmail.qq.com:465 · alert-bot``。

    **安全边界**：候选键名由前端声明（模板常量在前端），但**值**由后端从 JSON 里
    挑出来，前端无法把任意字符串塞进摘要列。它最坏能做的是「少声明一个敏感字段」，
    而那只影响它自己这条凭据，且仍需用户亲手提交。

    另有三道兜底：嵌套结构不进摘要、像密钥的值不进摘要、整体长度硬截断。
    """
    if not value:
        return None
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    blocked = {str(item).strip().lower() for item in (secret_fields or [])}
    parts: list[str] = []
    for key, item in data.items():
        key_name = str(key).strip().lower()
        if key_name in blocked or key_name in _SUMMARY_SKIP_KEYS:
            continue
        # 前端没声明也照样排除：键名像密码的一律不进摘要。
        # 传**原始键名**：先 lower 会把 clientSecret 的驼峰边界抹掉，分词就失效了。
        if _is_sensitive_key(str(key)):
            continue
        # 嵌套结构不进摘要：又长又没辨识度，还可能是另一层凭据。
        if isinstance(item, (dict, list)):
            continue
        if item is None:
            continue
        if isinstance(item, bool):
            text = "是" if item else "否"
        else:
            text = str(item).strip()
        if not text:
            continue
        if _looks_like_secret(text) or _looks_like_credentialed_url(text):
            continue
        parts.append(text[:SUMMARY_FIELD_MAX])
        if len(parts) >= SUMMARY_MAX_FIELDS:
            break

    if not parts:
        return None
    return " · ".join(parts)[:SUMMARY_MAX_LEN]


def _value_meta(
    value: str,
    *,
    secret_type: str,
    value_format: str,
    secret_fields: Iterable[str] | None,
) -> tuple[str | None, str | None]:
    """算出 ``(value_preview, value_summary)``，并把结构化值的合法性校验掉。

    - 单值 + 变量：沿用「前 8 个字符」预览；
    - 单值 + 凭证：两者都不给（预览就是泄露）；
    - 结构化（无论哪种类型）：校验 JSON，摘要只拼非敏感字段，**不给**前 8 个字符
      ——``{"host":`` 这八位没有任何信息量。
    """
    if value_format == VALUE_FORMAT_JSON:
        parse_structured_value(value)  # 校验；不合法直接抛 SecretValidationError
        return None, build_value_summary(value, secret_fields)
    return (value[:8] if secret_type == TYPE_VARIABLE else None), None


async def create_secret(
    session,
    *,
    owner_scope: str = SCOPE_USER,
    owner_id: str,
    secret_key: str,
    name: str,
    value: str,
    secret_type: str = TYPE_CREDENTIAL,
    allowed_scenarios: Iterable[str] | None = None,
    allowed_hosts: Iterable[str] | None = None,
    category: str | None = None,
    remark: str | None = None,
    expires_at: datetime | None = None,
    created_by: str | None = None,
    value_format: str = VALUE_FORMAT_TEXT,
    value_template: str | None = None,
    value_secret_fields: Iterable[str] | None = None,
) -> UserSecret:
    """建一条凭据。值只进密文字段，任何路径都不再返回明文。

    ``value_format=json`` 时 ``value`` 是结构化对象序列化后的字符串，会先校验能否
    解析成对象，再由 ``_value_meta`` 算出只含非敏感字段的摘要。
    """
    if owner_scope not in VALID_SCOPES:
        raise SecretValidationError(f"未知的归属范围: {owner_scope}")
    if secret_type not in VALID_TYPES:
        raise SecretValidationError(f"未知的凭据类型: {secret_type}")
    if not value:
        raise SecretValidationError("凭据值不能为空")
    if owner_scope == SCOPE_USER and not owner_id:
        raise SecretValidationError("用户级凭据必须指定 owner_id")

    fmt = _validate_value_format(value_format)
    template = _validate_value_template(value_template, fmt)
    preview, summary = _value_meta(
        value,
        secret_type=secret_type,
        value_format=fmt,
        secret_fields=_normalize_secret_fields(value_secret_fields),
    )

    key = _validate_key(secret_key)
    # 系统级用空串占位，让 (owner_scope, owner_id, secret_key) 的唯一约束真正生效
    normalized_owner = owner_id if owner_scope == SCOPE_USER else ""

    existing = await session.execute(
        select(UserSecret.id).where(
            UserSecret.owner_scope == owner_scope,
            UserSecret.owner_id == normalized_owner,
            UserSecret.secret_key == key,
        )
    )
    if existing.first() is not None:
        raise SecretValidationError(f"凭据标识已存在: {key}")

    row = UserSecret(
        id=str(uuid.uuid4()),
        owner_scope=owner_scope,
        owner_id=normalized_owner,
        secret_key=key,
        name=(name or key).strip()[:128],
        secret_type=secret_type,
        value_encrypted=_encrypt(value),
        value_fingerprint=secret_fingerprint(value),
        # 预览就是泄露：credential 一律不存，variable 才允许展示前 8 位。
        # 结构化值两者取其一——只给摘要，不给前 8 个字符（见 _value_meta）。
        value_preview=preview,
        value_format=fmt,
        value_template=template,
        value_summary=summary,
        allowed_hosts=_validate_hosts(allowed_hosts),
        allowed_scenarios=_validate_scenarios(allowed_scenarios),
        status="active",
        expires_at=_to_naive_utc(expires_at),
        category=category,
        remark=remark,
        created_by=created_by or owner_id or None,
    )
    session.add(row)
    await _audit(
        action="create",
        secret=row,
        actor_id=created_by or owner_id or None,
        detail={
            "secret_type": secret_type,
            "allowed_scenarios": list(row.allowed_scenarios or []),
            "allowed_hosts": list(row.allowed_hosts or []),
            "expires_at": row.expires_at.isoformat(timespec="seconds") if row.expires_at else None,
        },
    )
    await session.commit()

    # 新值必须立刻进入落库扫描的快照，否则最长 60 秒内它仍会明文进审计。
    secret_registry.invalidate()
    return row


async def rotate_secret(
    session,
    *,
    secret_id: str,
    new_value: str,
    rotated_by: str | None = None,
    overlap_days: int = ROTATION_OVERLAP_DAYS,
    expires_at: datetime | None = None,
    clear_expires_at: bool = False,
    actor_is_admin: bool = False,
    value_format: str | None = None,
    value_template: str | None = None,
    value_secret_fields: Iterable[str] | None = None,
) -> UserSecret:
    """轮换凭据值，旧值保留 ``overlap_days`` 天作为回退窗口。

    ``value_format`` 不传时沿用记录上的形态；传了才允许在换代时把单值换成结构化
    （或反之）。摘要同理重新生成——**不沿用旧摘要**：旧摘要描述的是上一版值，
    留着等于在列表上展示已经不存在的主机名。
    """
    if not new_value:
        raise SecretValidationError("新值不能为空")

    row = await session.get(UserSecret, secret_id)
    if row is None:
        raise SecretValidationError("凭据不存在")
    if row.owner_scope == SCOPE_USER and row.owner_id != rotated_by:
        # 轮换是**写值**，比改元数据更狠：知道 id 就能把别人的凭据换成自己的 token，
        # 之后对方的工具会带着攻击者的身份去调用外部服务。
        raise SecretPermissionError("不能轮换他人的凭据")
    if row.owner_scope == SCOPE_SYSTEM and not actor_is_admin:
        # 系统级凭据是所有绑定方共用的：改一次全体同时变，只能由管理员决定。
        raise SecretPermissionError("系统级凭据由管理员维护")
    if row.secret_type not in VALID_TYPES:
        raise SecretValidationError(f"未知的凭据类型: {row.secret_type}")

    fmt = _validate_value_format(value_format or row.value_format or VALUE_FORMAT_TEXT)
    # 模板：显式传就用新的；没传时沿用旧模板（「同结构换一份值」是最常见的轮换）。
    template = _validate_value_template(
        value_template if value_template is not None else row.value_template, fmt
    )
    preview, summary = _value_meta(
        new_value,
        secret_type=row.secret_type,
        value_format=fmt,
        secret_fields=_normalize_secret_fields(value_secret_fields),
    )

    # 库里一律是 naive UTC（模型 default=datetime.utcnow，序列化时再补 Z）。
    # 用 datetime.now() 在 UTC+8 的机器上会写进本地时间：轮换时间凭空快 8 小时，
    # previous_expires_at 也跟着偏，7 天回退窗口实际只有 6 天 16 小时。
    now = datetime.utcnow()
    row.previous_value_encrypted = row.value_encrypted
    row.previous_expires_at = now + timedelta(days=overlap_days)
    row.value_encrypted = _encrypt(new_value)
    row.value_fingerprint = secret_fingerprint(new_value)
    row.value_preview = preview
    row.value_format = fmt
    row.value_template = template
    row.value_summary = summary
    expires_at = _to_naive_utc(expires_at)
    row.last_rotated_at = now
    if expires_at is not None:
        row.expires_at = expires_at
    elif row.expires_at is not None and row.expires_at <= now:
        # 继承来的过期时间已经过去了。轮换是「换上新凭据」，新值不该一落地就失效——
        # 更糟的是 _pick_ciphertext 会因此判定新值不可用，回退到 previous 里那份
        # 刚被替换掉的旧密文，整个双写窗口用的都是旧值。清掉过期时间，
        # 让它作为一份不过期的新凭据生效；真要限期由调用方显式传。
        row.expires_at = None
    elif clear_expires_at:
        row.expires_at = None
    elif (
        row.expires_at is not None
        and row.previous_expires_at is not None
        and row.expires_at <= row.previous_expires_at
    ):
        # 虽尚未过期，但原截止时间落在本轮过渡窗口结束前：若继续沿用，
        # 新密文会在旧值过渡期结束前先失效、导致 _pick_ciphertext 倒退回旧密文，
        # 直接清掉过期时间避免“换完新值反而用旧值”（如需限期请显式传 expires_at）。
        row.expires_at = None
    await _audit(
        action="rotate",
        secret=row,
        actor_id=rotated_by,
        detail={
            "previous_expires_at": row.previous_expires_at.isoformat(timespec="seconds"),
            "overlap_days": overlap_days,
            "value_format": fmt,
            "value_template": template,
            "expires_at": row.expires_at.isoformat(timespec="seconds") if row.expires_at else None,
        },
    )
    await session.commit()

    secret_registry.invalidate()
    logger.info(
        "[SecretCenter] 轮换凭据 %s（旧值保留至 %s，操作人 %s）",
        row.secret_key,
        row.previous_expires_at.isoformat(timespec="seconds"),
        rotated_by or "-",
    )
    return row


#: 字段预览的长度上限：够辨认（``smtp.exmail.qq.com``），又不至于把整段
#: 配置摊在屏幕上。
_FIELD_PREVIEW_LIMIT = 60


async def describe_value_fields(
    session,
    *,
    secret_id: str,
    user_id: str,
    dept_code: str | None = None,
) -> dict[str, Any]:
    """列出一条凭据「可以取哪几部分」，供绑定时的字段选择器使用。

    这是使用侧的关键一步：结构化凭据能选字段，用户才不必去猜 JSON 长什么样。

    边界：**敏感字段只给名字，不给值**。字段选择器拿到名字就能生成引用，把值一起
    返回等于在「凭据不可读」这条线上开了个口子——尤其 ``credential`` 类型连创建者
    都看不到明文。非敏感字段给一段预览，纯粹是为了让人认出「这个是端口」。
    """
    visible = await list_visible_secrets(
        session, user_id=user_id, dept_code=dept_code, only_active=False
    )
    row = next((item for item in visible if item.id == secret_id), None)
    if row is None:
        raise SecretPermissionError("凭据不存在")

    payload: dict[str, Any] = {
        "id": row.id,
        "secret_key": row.secret_key,
        "name": row.name,
        "format": row.value_format or "text",
        "template": row.value_template or "",
        "kind": row.secret_type,
        "fields": [],
    }
    if payload["format"] != "json":
        # 单值凭据没有字段可选，前端据此直接隐藏字段选择器
        return payload

    try:
        plaintext = get_api_key_manager().decrypt_api_key(row.value_encrypted)
        data = parse_structured_value(plaintext)
    except Exception:  # noqa: BLE001 - 解不开/不是对象就当「没有可选字段」
        logger.warning("[SecretCenter] 凭据 %s 的字段清单读取失败", secret_id)
        return payload

    fields: list[dict[str, Any]] = []

    def walk(node: Any, prefix: str, depth: int) -> None:
        """把 JSON 摊平成「路径 → 可选项」，**对象本身也是一项**。

        对象节点也要列出来：用户可能就是要整段 `auth`（执行期 pick_field_typed
        取到对象会序列化成紧凑 JSON），只列叶子等于替他做了决定。

        上限是必要的：一份几十键的配置展开成三层就是上千项，下拉框会卡住，
        也没人能在里面找到东西。到顶时保留该节点本身，仍可选整段。
        """
        if len(fields) >= _MAX_FIELD_ITEMS:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                # 检查必须放在循环**开头**：子结构递归回来时可能已经满了，
                # 只在 append 前拦的话，外层剩下的兄弟键会继续追加，
                # 200 条的上限照样被穿透。
                if len(fields) >= _MAX_FIELD_ITEMS:
                    return
                path = f"{prefix}.{key}" if prefix else str(key)
                # 敏感性按整条路径判：`db.password` 的 password 要命中，
                # 只看最后一段会漏掉 `auth.apiKey` 这类前缀无关的写法。
                sensitive = _is_sensitive_key(path)
                if isinstance(value, (dict, list)) and depth + 1 < _MAX_FIELD_DEPTH and value:
                    fields.append(
                        {
                            "name": path,
                            "sensitive": sensitive,
                            "preview": "",
                            "kind": "object" if isinstance(value, dict) else "array",
                        }
                    )
                    if len(fields) >= _MAX_FIELD_ITEMS:
                        return
                    if isinstance(value, dict):
                        walk(value, path, depth + 1)
                    else:
                        # 数组按元素展开：只列下标不列内容，元素是对象时继续往下
                        for index, item in enumerate(value):
                            if len(fields) >= _MAX_FIELD_ITEMS:
                                return
                            item_path = f"{path}.{index}"
                            if isinstance(item, (dict, list)):
                                fields.append(
                                    {
                                        "name": item_path,
                                        "sensitive": sensitive,
                                        "preview": "",
                                        "kind": "object" if isinstance(item, dict) else "array",
                                    }
                                )
                                if isinstance(item, dict) and depth + 2 < _MAX_FIELD_DEPTH:
                                    walk(item, item_path, depth + 2)
                            else:
                                fields.append(
                                    _leaf(item_path, item, sensitive)
                                )
                            if len(fields) >= _MAX_FIELD_ITEMS:
                                return
                    continue
                fields.append(_leaf(path, value, sensitive))

    walk(data, "", 0)
    payload["fields"] = fields
    return payload


def _leaf(path: str, value: Any, sensitive: bool) -> dict[str, Any]:
    """叶子字段：非敏感标量给一段预览，其余只给名字。

    键名没命中不等于值安全。这里和 ``build_value_summary`` 用同一套兜底：
    值本身像密钥、像内嵌账密的地址、或地址里带着凭证参数，一律不预览。
    字段选择器只需要名字就能生成引用，给不出预览不损失任何功能。
    """
    preview = ""
    if not sensitive and isinstance(value, (str, int, float, bool)):
        text = str(value)
        blocked = isinstance(value, str) and (
            _looks_like_secret(text)
            or _looks_like_credentialed_url(text)
            or _looks_like_secret_url(text)
        )
        if not blocked:
            if len(text) > _FIELD_PREVIEW_LIMIT:
                text = text[:_FIELD_PREVIEW_LIMIT] + "…"
            preview = text
    return {
        "name": path,
        "sensitive": sensitive,
        "preview": preview,
        "kind": "array" if isinstance(value, list) else ("object" if isinstance(value, dict) else "scalar"),
    }


async def bind_secret(
    session,
    *,
    secret_id: str,
    consumer_type: str,
    consumer_id: str,
    slot: str,
    bound_by_user_id: str | None,
    dept_code: str = "",
    actor_id: str | None = None,
    value_path: str = "",
    allow_whole: bool = False,
    dry_run: bool = False,
    commit: bool = True,
    #: 批量保存时传 False，由调用方在事务提交后统一补写（见下方注释）
    audit: bool = True,
    #: 调用方是不是管理员。只有管理员能把**系统级**凭据设成全局绑定。
    #: 权限判断放在这里而不是接口层：接口层看不到凭据作用域，只能对整批一刀切，
    #: 于是「管理员拿个人凭据绑平台级工具」也会被当成越权拒掉。
    allow_global: bool = True,
) -> SecretBinding | None:
    """把一个槽位绑到一条凭据上。同一槽位重复绑定视为「改绑」。

    ``value_path`` 指定只取凭据的哪一部分（结构化凭据必须给，或者显式
    ``allow_whole=True`` 声明「就要整段 JSON」）。为什么强制选：把一条 SMTP 凭据
    整段注进 ``smtp_password`` 参数，调用方拿到的是 ``{"host":…,"password":…}``，
    必然失败；失败点还离配置处很远，排查要翻运行日志。
    """
    if consumer_type not in VALID_CONSUMER_TYPES:
        raise SecretValidationError(f"未知的消费者类型: {consumer_type}")
    if not consumer_id or not slot:
        raise SecretValidationError("消费者与槽位不能为空")
    prefix, _, slot_name = slot.partition(":")
    if not prefix or not slot_name:
        raise SecretValidationError(
            "槽位格式应为 header:名称 / query:参数名 / body:字段 / env:变量名"
        )
    allowed = ALLOWED_SLOT_PREFIXES.get(consumer_type, frozenset())
    if prefix not in allowed:
        raise SecretValidationError(
            f"{consumer_type} 不支持 {prefix}: 槽位，可用的前缀："
            + " / ".join(sorted(allowed))
        )

    secret = await session.get(UserSecret, secret_id)
    if secret is None:
        raise SecretValidationError("凭据不存在")

    # 全局绑定（bound_by_user_id=None）只对系统级凭据成立：个人凭据是某个人
    # 自己的东西，「给所有人用」这个语义在它身上没有意义。
    # 普通用户绑定系统级凭据时，绑定只对自己生效（bound_by_user_id 落成自己，
    # 解析时按主体过滤），但必须先被授权。
    if bound_by_user_id is None:
        if secret.owner_scope == SCOPE_SYSTEM and not allow_global:
            raise SecretPermissionError("只有管理员可以设置全局绑定")
        if secret.owner_scope != SCOPE_SYSTEM:
            # 个人凭据**不可能**给所有人用，但这里不该直接拒绝：
            # 管理员给平台级工具配自己的凭据是正常操作（先用自己的账号跑通，
            # 再换成平台账号），拒掉等于逼他先建一条系统级凭据再删掉。
            # 降级成本人绑定——语义从「给所有人」变成「只对我生效」，
            # 不越权（绑定仍指向他自己的凭据），也解释得通。
            # 必须落到**非空**的本人标识：空串在这套模型里代表「全局」
            # （见下方的 owner_key），降级成空串等于把个人凭据绑成了全局。
            bound_by_user_id = actor_id or secret.owner_id
            if not bound_by_user_id:
                raise SecretValidationError("只有系统级凭据可以设为全局绑定")
    elif secret.owner_scope == SCOPE_USER and secret.owner_id != bound_by_user_id:
        raise SecretValidationError("不能绑定他人的凭据")
    elif secret.owner_scope == SCOPE_SYSTEM:
        # 普通用户绑系统级凭据：必须先被授权。不校验的话，绑定成功之后
        # `GET /bindings?secret_id=` 会把这个系统的 secret_key / name 回给她，
        # 「未授权的连名字都不出现」就被绕开了。
        visible = await list_visible_secrets(
            session,
            user_id=str(bound_by_user_id or ""),
            dept_code=dept_code,
            only_active=True,
        )
        if secret.id not in {item.id for item in visible}:
            raise SecretPermissionError("凭据不存在")

    # 字段路径与凭据形态必须对得上：单值凭据没有字段可取，结构化凭据整段给出
    # 又几乎必然是错的。两道都在这里拦，避免绑定成功、运行时才失败。
    value_path = normalize_value_path(value_path)
    if secret.value_format == "json":
        # header 位点的值里可以写 ``${secret:key.field}``——字段由引用表达，
        # 绑定层不必重复一遍，否则「同一条凭据给不同槽位取不同字段」这种用法会被堵死。
        # 其它位点（参数 / arg / env）是整值注入，没有引用可写，必须在这里选定。
        need_explicit = prefix != "header"
        if not value_path and need_explicit and not allow_whole:
            raise SecretValidationError(
                "这条凭据是结构化的，请选择要取用的字段；确实要整段 JSON 请显式确认"
            )
        if value_path:
            _ensure_value_path_exists(secret, value_path)
    elif value_path:
        raise SecretValidationError("这是单值凭据，没有可选字段")

    # 「全局绑定」用空串表示，不用 NULL：唯一索引 uk_binding_slot 含这一列，
    # 而 NULL 在唯一索引里互不相等（多行 NULL 允许共存），等于没有约束。
    # 空串让索引真正生效，两个请求同时绑也只能留下一条。
    owner_key = bound_by_user_id or ""

    if dry_run:
        # 只跑校验：批量保存要先确认每一条都能过，再一起落库，否则「选了 5 个参数，
        # 3 个成功 2 个失败」这种半成品最难收拾。
        # 位置必须在这里——前面是纯校验（凭据存在、路径字段合法），后面紧接 `delete`。
        # 放到 delete 之后再返回的话，dry_run 这一轮就已经把旧绑定删掉了，
        # 「校验失败时什么都没写」这句话就不成立。
        return None

    # 同一槽位同一绑定者只保留一条：先删后插，避免唯一索引冲突
    await session.execute(
        delete(SecretBinding).where(
            SecretBinding.consumer_type == consumer_type,
            SecretBinding.consumer_id == consumer_id,
            SecretBinding.slot == slot,
            # 只清「固定绑定」那一行：同一槽位现在还能同时挂一条规则
            # （rule 负责定位、fixed 作 on_miss=fallback_fixed 的回退目标）。
            # 不带这个条件，配一次固定凭据就会把规则静默删掉。
            SecretBinding.strategy == "fixed",
            # 兼容迁移前遗留的 NULL 行。不能写 in_([None, ''])：SQL 里
            # `x IN (NULL, '')` 对 NULL 求值是 NULL，永远匹配不到。
            or_(
                SecretBinding.bound_by_user_id.is_(None),
                SecretBinding.bound_by_user_id == "",
            )
            if not owner_key
            else SecretBinding.bound_by_user_id == owner_key,
        )
    )

    binding = SecretBinding(
        id=str(uuid.uuid4()),
        secret_id=secret_id,
        consumer_type=consumer_type,
        consumer_id=consumer_id,
        slot=slot,
        bound_by_user_id=owner_key,
        value_path=value_path,
    )
    session.add(binding)
    # 全局绑定时 bound_by_user_id 为 None，若不显式透传 actor_id 会丢失操作人。
    audit_actor_id = actor_id if actor_id is not None else bound_by_user_id
    await _audit(
        enabled=audit,
        action="bind",
        secret=secret,
        actor_id=audit_actor_id,
        detail={
            "consumer_type": consumer_type,
            "consumer_id": consumer_id,
            "slot": slot,
            # 为空表示管理员全局绑定；有值就是「只对这个人生效」
            "global": not owner_key,
            # 取了哪一部分。审计里要能回答「这次把密码绑到哪了」
            "value_path": value_path,
        },
    )
    # ``commit=False`` 给批量保存用：逐条提交的话，第 4 条失败时前 3 条已经落库，
    # 「要么全成要么全不成」就成了空话。这种情况下审计也要跟着延后
    # （``audit=False``），由调用方在提交成功之后补写——审计记了没发生的操作，
    # 比漏记更误导。
    if commit:
        await session.commit()
    else:
        await session.flush()
    return binding


#: 字段路径允许的写法：点分隔的段，**后续段允许纯数字**（数组下标）。
#:
#: 首页键不能是数字，但 ``list.0.id`` 这种必须放行——执行期 ``pick_field_typed``
#: 早就支持逐段走数组了，只在配置期拦住等于「运行时能跑，界面配不出来」。
#: 仍然拦住 ``a[0]``、空格、通配符这类「看起来像路径但解析不了」的写法。
#: 字段清单展开的上限：层数与条目数。一份几十键的配置摊到三层就是上千项，
#: 下拉框会卡死，也没人能在里面找到东西。
_MAX_FIELD_DEPTH = 3
_MAX_FIELD_ITEMS = 200

_VALUE_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)*$")


def normalize_value_path(value_path: str | None) -> str:
    """规范化字段路径；空串表示「整段」。格式不对直接拒绝。"""
    path = (value_path or "").strip()
    if not path:
        return ""
    if not _VALUE_PATH_RE.match(path):
        raise SecretValidationError(
            "字段路径只能是用点分隔的名字，如 password / auth.token"
        )
    return path


def _ensure_value_path_exists(secret: UserSecret, value_path: str) -> None:
    """绑定前确认这个字段真的在凭据里。

    要解密一次，但值得：字段写错时，错误应该出现在**点保存的那一刻**，而不是
    几小时后调用方报 401。名字打错时给出最接近的候选，省得自己去翻 JSON。
    """
    try:
        plaintext = get_api_key_manager().decrypt_api_key(secret.value_encrypted)
    except Exception:  # noqa: BLE001 - 解不开就别卡住绑定，运行时还有一道
        logger.warning("[SecretCenter] 绑定时无法解密凭据 %s，跳过字段校验", secret.id)
        return
    try:
        data = parse_structured_value(plaintext)
    except SecretValidationError:
        # 标记成 json 但内容不是对象（历史脏数据）：运行时 pick_field 会拒，
        # 这里不重复报错，否则这类凭据连绑都绑不上。
        return
    # 逐段走完整条路径，而不是只看顶层：`auth.token` 的顶层 `auth` 存在、
    # 但 `token` 拼错时，只查顶层会放行，错误要等到几小时后调用方报错才出现。
    current: Any = data
    walked: list[str] = []
    for segment in value_path.split("."):
        walked.append(segment)
        where = ".".join(walked)
        if isinstance(current, dict):
            if segment not in current:
                candidates = difflib.get_close_matches(
                    segment, [str(k) for k in current], n=3, cutoff=0.5
                )
                hint = f"，你是不是想用：{'、'.join(candidates)}" if candidates else ""
                prefix = ".".join(walked[:-1])
                where = f"{prefix}.{segment}" if prefix else segment
                raise SecretValidationError(f"这条凭据里没有字段 {where}{hint}")
            current = current[segment]
        elif isinstance(current, list):
            if not segment.isdigit():
                raise SecretValidationError(f"{where} 是数组，只能按下标取（如 {where}.0）")
            if int(segment) >= len(current):
                raise SecretValidationError(
                    f"{where} 下标越界：这个数组只有 {len(current)} 项"
                )
            current = current[int(segment)]
        else:
            raise SecretValidationError(f"{where} 不是一个对象，没法继续往下取字段")


async def bind_rule(
    session,
    *,
    consumer_type: str,
    consumer_id: str,
    slot: str,
    rule: dict | str | None,
    bound_by_user_id: str | None,
    actor_id: str | None = None,
    value_path: str = "",
    dry_run: bool = False,
    commit: bool = True,
    audit: bool = True,
    #: 调用方是不是管理员。规则不指向凭据，但「全局规则」照样影响所有人，
    #: 必须同样受权限约束——否则普通用户一句请求就能改掉全平台的取数规则。
    allow_global: bool = True,
) -> SecretBinding | None:
    """把一个槽位绑到「一条定位规则」上（``strategy='rule'``，无 ``secret_id``）。

    与 ``bind_secret`` 分开而不是给它加参数：固定绑定要校验凭据归属与授权，
    规则绑定没有凭据可校验——混在一个函数里会让两条校验路径互相纠缠，也容易在
    「规则行该不该走授权检查」上出错（答案是不该，它不指向任何凭据）。

    规则里**不能出现 secret_id**：``parse_rule`` 拒绝未知字段，所以改一条规则
    永远变不成「读别人凭据」的手段。

    ``value_path``：命中的凭据要取哪个字段。**只校验格式，不校验字段是否存在**——
    命中哪条凭据运行时才知道，此刻没有地方去查。字段缺失会在注入时明确失败，
    而不是把整份 JSON 注进去。
    """
    from app.services.secrets.locator import RuleError, parse_rule

    if consumer_type not in VALID_CONSUMER_TYPES:
        raise SecretValidationError(f"未知的消费者类型: {consumer_type}")
    if not consumer_id or not slot:
        raise SecretValidationError("消费者与槽位不能为空")
    prefix, _, slot_name = slot.partition(":")
    if not prefix or not slot_name:
        raise SecretValidationError(
            "槽位格式应为 header:名称 / query:参数名 / body:字段 / env:变量名"
        )
    allowed = ALLOWED_SLOT_PREFIXES.get(consumer_type, frozenset())
    if prefix not in allowed:
        raise SecretValidationError(
            f"{consumer_type} 不支持 {prefix}: 槽位，可用的前缀："
            + " / ".join(sorted(allowed))
        )

    if bound_by_user_id is None and not allow_global:
        raise SecretPermissionError("只有管理员可以设置全局绑定")

    path = normalize_value_path(value_path)

    try:
        spec = parse_rule(rule)
    except RuleError as exc:
        raise SecretValidationError(str(exc)) from exc

    if dry_run:
        # 与 bind_secret 同口径：批量保存要先确认每一条都合法，再一起写。
        # 规则不指向具体凭据，所以这里没有「凭据是否存在」这类校验。
        return None

    # 只落归一号过的字段，不原样存用户传进来的 dict：避免把未知字段
    # （尤其是 secret_id）持久化下来，将来被读规则的代码误用。
    payload = json.dumps(
        {
            "key_source": spec.key_source,
            "transforms": list(spec.transforms),
            "templates": list(spec.templates),
            "target": spec.target,
            "match": spec.match,
            "on_miss": spec.on_miss,
            # 模板约束属于「结构约定」，是规则的一部分；取用字段写进绑定列
            # （和固定绑定同一列），注入层就不用关心它是哪种策略。
            **({"value_template": spec.value_template} if spec.value_template else {}),
            # target=field 时匹配哪个字段，必须一起落库：漏了它，读回来
            # parse_rule 会判「必须给出 target_field」，这条规则永远不生效，
            # 而保存时一切正常。
            **({"target_field": spec.target_field} if spec.target_field else {}),
        },
        ensure_ascii=False,
    )

    owner_key = bound_by_user_id or ""
    existing = (
        await session.execute(
            select(SecretBinding).where(
                SecretBinding.consumer_type == consumer_type,
                SecretBinding.consumer_id == consumer_id,
                SecretBinding.slot == slot,
                SecretBinding.bound_by_user_id == owner_key,
                SecretBinding.strategy == "rule",
            )
        )
    ).scalars().first()

    audit_detail = {
        "consumer_type": consumer_type,
        "consumer_id": consumer_id,
        "slot": slot,
        # 为空表示管理员配的全局规则；有值就是「只对这个人生效」
        "global": not owner_key,
        # 规则全文进审计：事后要能回答「当时配的是什么」，而不是只知道「配过」
        "rule": json.loads(payload),
        "value_path": path,
    }

    audit_actor_id = actor_id if actor_id is not None else bound_by_user_id
    if existing is not None:
        # 同一槽位重配规则 = 改规则，不新增第二条（唯一索引也会拦住）
        existing.rule_json = payload
        existing.secret_id = None
        existing.value_path = path
        if commit:
            await session.commit()
        else:
            await session.flush()
        await _audit(
            enabled=audit,
            action="bind_rule",
            secret=None,
            actor_id=audit_actor_id,
            detail={"updated": True, **audit_detail},
        )
        return existing

    binding = SecretBinding(
        id=str(uuid.uuid4()),
        secret_id=None,
        consumer_type=consumer_type,
        consumer_id=consumer_id,
        slot=slot,
        bound_by_user_id=owner_key,
        strategy="rule",
        rule_json=payload,
        value_path=path,
    )
    session.add(binding)
    await _audit(
        enabled=audit,
        action="bind_rule",
        secret=None,
        actor_id=audit_actor_id,
        detail=audit_detail,
    )
    if commit:
        await session.commit()
    else:
        # 批量路径：调用方提交成功后再补写审计（见 bind_secret 的说明）
        await session.flush()
    return binding


async def migrate_api_tool_param_slots(session, tool: Any) -> int:
    """URL 或 HTTP 方法改了之后，把参数绑定的槽位重算一遍。返回改动的条数。

    槽位编码的是「这个参数落在哪」：``POST /refund/{applicant}`` 是 ``path:``、
    ``GET /refund?applicant=`` 是 ``query:``、其余方法是 ``body:``。改配置后旧槽位
    就变成孤儿——**界面还显示「已绑定」**（前端按参数名查，三个前缀都试），
    运行时按新槽位查不到绑定，于是报「未绑定」。

    冲突处理：新槽位已有同一绑定者的记录，说明用户已经重绑过，旧的那条是本该被
    删掉的孤儿，直接删除而不是覆盖。
    """
    from app.services.ai.tools.system_params import credential_param_names
    from app.services.secrets.policy import resolve_api_tool_slot

    names = set(credential_param_names(getattr(tool, "parameter_schema", None)))
    if not names:
        return 0

    tool_id = str(getattr(tool, "id", "") or "")
    if not tool_id:
        return 0

    rows = (await session.execute(
        select(SecretBinding).where(
            SecretBinding.consumer_type == CONSUMER_API_TOOL,
            SecretBinding.consumer_id == tool_id,
        )
    )).scalars().all()

    # 带上 strategy：唯一索引是 (slot, bound_by_user_id, strategy)，同一个参数位
    # 同时存在 fixed 和 rule 两条是合法的。漏掉这一维，迁完 fixed 行之后，紧接着
    # 处理同参数的 rule 行会命中「新槽位已经有人绑了」，把合法的规则绑定当残留删掉。
    existing = {(row.slot, row.bound_by_user_id, row.strategy) for row in rows}
    moved = 0
    for row in rows:
        prefix, _, name = str(row.slot).partition(":")
        if prefix not in {"path", "query", "body"} or name not in names:
            continue
        target = resolve_api_tool_slot(
            name=name,
            method=getattr(tool, "method", None),
            url_template=getattr(tool, "url_template", None),
        )
        if not target or target == row.slot:
            continue
        # 先留住旧键：下面要么删行、要么改 row.slot，改完就取不到原值了
        old_key = (row.slot, row.bound_by_user_id, row.strategy)
        if (target, row.bound_by_user_id, row.strategy) in existing:
            # 新槽位已经有人绑了：这条是历史残留，删掉
            await session.delete(row)
            existing.discard(old_key)
        else:
            row.slot = target
            existing.discard(old_key)
            existing.add((target, row.bound_by_user_id, row.strategy))
        moved += 1

    if moved:
        await session.commit()
    return moved


async def unbind_slot(
    session,
    *,
    consumer_type: str,
    consumer_id: str,
    slot: str,
    bound_by_user_id: str | None,
    strategy: str = "fixed",
    actor_id: str | None = None,
    commit: bool = True,
    #: 批量保存时传 False，由调用方在事务提交后统一补写
    audit: bool = True,
) -> int:
    """解绑。返回删除条数（0 表示本来就没绑）。

    ``strategy`` 默认 ``fixed``：同一槽位现在可以同时挂一条规则和一条固定绑定
    （规则当定位、固定当回退目标），不区分就会把两条一起删掉。默认值让所有
    既有调用方的行为保持不变。
    """
    # 全局绑定用空串表示（见 bind_secret 的说明）；这里同时认迁移前的 NULL，
    # 否则升级前建的全局绑定会解不掉。
    owner_key = bound_by_user_id or ""
    owner_clause = (
        SecretBinding.bound_by_user_id == owner_key
        if owner_key
        else or_(
            # 同上：IN 包含 NULL 时匹配不到 NULL 行
            SecretBinding.bound_by_user_id.is_(None),
            SecretBinding.bound_by_user_id == "",
        )
    )

    # 先取 secret_id：删完就查不到了，而审计里「解绑的是哪条凭据」正是要查的
    target = await session.execute(
        select(SecretBinding.secret_id).where(
            SecretBinding.consumer_type == consumer_type,
            SecretBinding.consumer_id == consumer_id,
            SecretBinding.slot == slot,
            SecretBinding.strategy == strategy,
            owner_clause,
        )
    )
    secret_id = target.scalars().first()

    result = await session.execute(
        delete(SecretBinding).where(
            SecretBinding.consumer_type == consumer_type,
            SecretBinding.consumer_id == consumer_id,
            SecretBinding.slot == slot,
            SecretBinding.strategy == strategy,
            owner_clause,
        )
    )
    if secret_id:
        secret = await session.get(UserSecret, secret_id)
        if secret is not None:
            await _audit(
                enabled=audit,
                action="unbind",
                secret=secret,
                actor_id=actor_id if actor_id is not None else bound_by_user_id,
                detail={
                    "consumer_type": consumer_type,
                    "consumer_id": consumer_id,
                    "slot": slot,
                    "removed": result.rowcount or 0,
                },
            )
    if commit:
        await session.commit()
    else:
        await session.flush()
    return result.rowcount or 0


async def grant_secret(
    session,
    *,
    secret_id: str,
    grantee_type: str,
    grantee_id: str,
    granted_by: str | None = None,
    expires_at: datetime | None = None,
) -> SecretGrant:
    """把系统级凭据授权给一个用户、一个部门，或全体用户。

    ``all`` 的 ``grantee_id`` 归一成空串：它不指向任何实体，只是「不限范围」。
    没有这一档时，平台统一采购的账号只能逐部门授权，新增部门必然漏掉；
    而按人授权更糟——新人入职必然漏。
    """
    if grantee_type not in GRANTEE_TYPES:
        raise SecretValidationError(f"未知的授权对象类型: {grantee_type}")
    if grantee_type == GRANTEE_ALL:
        grantee_id = ""
    elif not grantee_id:
        raise SecretValidationError("授权对象不能为空")

    secret = await session.get(UserSecret, secret_id)
    if secret is None:
        raise SecretValidationError("凭据不存在")
    if secret.owner_scope != SCOPE_SYSTEM:
        raise SecretValidationError("只有系统级凭据需要授权；用户级凭据本身就是私有")

    existing = await session.execute(
        select(SecretGrant.id).where(
            SecretGrant.secret_id == secret_id,
            SecretGrant.grantee_type == grantee_type,
            SecretGrant.grantee_id == grantee_id,
        )
    )
    if existing.first() is not None:
        raise SecretValidationError("这条授权已存在")

    grant = SecretGrant(
        id=str(uuid.uuid4()),
        secret_id=secret_id,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        granted_by=granted_by,
        expires_at=expires_at,
    )
    session.add(grant)
    await _audit(
        action="grant",
        secret=secret,
        actor_id=granted_by,
        detail={
            "grantee_type": grantee_type,
            "grantee_id": grantee_id,
            "expires_at": expires_at.isoformat(timespec="seconds") if expires_at else None,
        },
    )
    await session.commit()
    return grant


async def revoke_grant(
    session,
    *,
    secret_id: str,
    grantee_type: str,
    grantee_id: str,
    actor_id: str | None = None,
) -> int:
    """撤销授权。已建立的绑定不随之删除，但下一次调用会被 ``denied_by_grant`` 拦下。"""
    result = await session.execute(
        delete(SecretGrant).where(
            SecretGrant.secret_id == secret_id,
            SecretGrant.grantee_type == grantee_type,
            SecretGrant.grantee_id == grantee_id,
        )
    )
    secret = await session.get(UserSecret, secret_id)
    if secret is not None:
        await _audit(
            action="revoke",
            secret=secret,
            actor_id=actor_id,
            detail={
                "grantee_type": grantee_type,
                "grantee_id": grantee_id,
                "removed": result.rowcount or 0,
            },
        )
    await session.commit()
    return result.rowcount or 0


async def list_visible_secrets(
    session,
    *,
    user_id: str,
    dept_code: str | None = None,
    include_system: bool = True,
    only_active: bool = False,
) -> Sequence[UserSecret]:
    """列出用户能「看见」的凭据：自己的 + 被授权的系统级。

    未授权的系统级凭据**连名称都不返回**——凭据名本身也是情报。

    ``only_active``：只看能用的。**给选择器和绑定校验用**——停用的凭据摆进下拉，
    用户选中、保存成功，等到调用时才发现被拒，等于白配一次。
    凭据管理页要能看见停用的（否则没法重新启用），所以默认不筛。
    """
    conditions = [
        and_(UserSecret.owner_scope == SCOPE_USER, UserSecret.owner_id == user_id)
    ]
    if include_system:
        grant_conditions = [
            and_(SecretGrant.grantee_type == GRANTEE_USER, SecretGrant.grantee_id == user_id),
            # 「授权给全体用户」。少了这条，`_has_grant` 放行、这里却看不见——
            # 表现就是「调用真的能成功，但选择器里找不到这条凭据」。
            SecretGrant.grantee_type == GRANTEE_ALL,
        ]
        if dept_code:
            grant_conditions.append(
                and_(SecretGrant.grantee_type == GRANTEE_DEPT, SecretGrant.grantee_id == dept_code)
            )
        granted_ids = select(SecretGrant.secret_id).where(
            or_(*grant_conditions),
            or_(SecretGrant.expires_at.is_(None), SecretGrant.expires_at > datetime.utcnow()),
        )
        conditions.append(
            and_(UserSecret.owner_scope == SCOPE_SYSTEM, UserSecret.id.in_(granted_ids))
        )

    # 注意：`conditions` 最终要进 `or_()`，往里追加等于「只要 active 就可见」。
    # 附加过滤必须走 AND。
    extra = [UserSecret.status == "active"] if only_active else []

    stmt = (
        select(UserSecret)
        .where(or_(*conditions), *extra)
        .order_by(UserSecret.owner_scope.desc(), UserSecret.secret_key)
    )
    return (await session.execute(stmt)).scalars().all()


async def delete_secret(
    session, *, secret_id: str, actor_id: str, actor_is_admin: bool = False
) -> None:
    """删除凭据。绑定与授权要**显式清理**，使用流水保留。

    这几张表之间没有外键（要能单独导出再导入），所以没有 ``ON DELETE CASCADE``
    兜底。漏了这两条 delete，库里会留下指向已删凭据的授权和绑定：功能上不会
    报错（解析时是 join，孤儿记录匹配不上），但「这条凭据授权给了谁」会变成
    查不清的垃圾数据，而且下次建同名凭据时看不出哪些绑定是陈旧的。
    """
    from sqlalchemy import delete as sa_delete

    from app.models.secret import SecretBinding, SecretGrant

    row = await session.get(UserSecret, secret_id)
    if row is None:
        raise SecretValidationError("凭据不存在")
    if row.owner_scope == SCOPE_USER and row.owner_id != actor_id:
        raise SecretPermissionError("不能删除他人的凭据")
    if row.owner_scope == SCOPE_SYSTEM and not actor_is_admin:
        raise SecretPermissionError("系统级凭据由管理员维护")

    # 审计写在删除**之前**：删完 secret 行就取不到 key 快照了，
    # 而「这条凭据被谁删了」恰恰是事后最需要能查到的一条。
    await _audit(
        action="delete",
        secret=row,
        actor_id=actor_id,
        detail={"by_admin": actor_is_admin},
    )
    await session.execute(sa_delete(SecretGrant).where(SecretGrant.secret_id == secret_id))
    await session.execute(sa_delete(SecretBinding).where(SecretBinding.secret_id == secret_id))
    await session.delete(row)
    await session.commit()
    secret_registry.invalidate()


async def update_secret(
    session,
    *,
    secret_id: str,
    actor_id: str,
    name: str | None = None,
    allowed_scenarios: Iterable[str] | None = None,
    allowed_hosts: Iterable[str] | None = None,
    category: str | None = None,
    remark: str | None = None,
    expires_at: datetime | None = None,
    clear_expires_at: bool = False,
    actor_is_admin: bool = False,
) -> UserSecret:
    """改元数据。**不能**改值——改值必须走 ``rotate_secret``（留轮换痕迹）。

    ``None`` 表示不动；传空列表表示清空（例如 ``allowed_scenarios=[]``
    等于把这条凭据从所有场景下线）。
    """
    row = await session.get(UserSecret, secret_id)
    if row is None:
        raise SecretValidationError("凭据不存在")
    if row.owner_scope == SCOPE_USER and row.owner_id != actor_id:
        raise SecretPermissionError("不能修改他人的凭据")
    if row.owner_scope == SCOPE_SYSTEM and not actor_is_admin:
        raise SecretPermissionError("系统级凭据由管理员维护")

    if name is not None:
        row.name = name.strip()[:128] or row.secret_key
    if allowed_scenarios is not None:
        row.allowed_scenarios = _validate_scenarios(allowed_scenarios)
    if allowed_hosts is not None:
        row.allowed_hosts = _validate_hosts(allowed_hosts)
    if category is not None:
        row.category = category
    if remark is not None:
        row.remark = remark[:500]
    if clear_expires_at:
        row.expires_at = None
    elif expires_at is not None:
        row.expires_at = _to_naive_utc(expires_at)

    await _audit(
        action="update",
        secret=row,
        actor_id=actor_id,
        detail={
            "name": name,
            "category": category,
            "allowed_scenarios": list(row.allowed_scenarios or []),
            "allowed_hosts": list(row.allowed_hosts or []),
            "expires_at": row.expires_at.isoformat(timespec="seconds") if row.expires_at else None,
        },
    )
    await session.commit()
    return row


async def set_secret_status(
    session,
    *,
    secret_id: str,
    status_value: str,
    actor_id: str | None = None,
    actor_is_admin: bool = False,
) -> UserSecret:
    """启用 / 停用。停用是切断所有绑定的唯一手段，且不可撤销地影响全部使用者。"""
    if status_value not in {"active", "disabled"}:
        raise SecretValidationError(f"未知状态: {status_value}")
    row = await session.get(UserSecret, secret_id)
    if row is None:
        raise SecretValidationError("凭据不存在")
    if row.owner_scope == SCOPE_SYSTEM and not actor_is_admin:
        raise SecretPermissionError("系统级凭据由管理员维护，不能在个人视图下调整状态")
    if (
        row.owner_scope == SCOPE_USER
        and not actor_is_admin
        and actor_id is not None
        and row.owner_id != actor_id
    ):
        raise SecretPermissionError("不能操作他人的凭据")

    row.status = status_value
    await _audit(
        action="enable" if status_value == "active" else "disable",
        secret=row,
        actor_id=actor_id,
        detail={"status": status_value},
    )
    await session.commit()
    logger.warning(
        "[SecretCenter] 凭据 %s 被置为 %s（操作人 %s）", row.secret_key, status_value, actor_id or "-"
    )
    return row


async def list_audit(
    session,
    *,
    secret_id: str | None = None,
    actor_id: str | None = None,
    limit: int = 100,
) -> Sequence:
    """变更审计。管理员传 ``actor_id=None`` 且 ``secret_id=None`` 看全局。"""
    from app.models.secret import SecretAuditLog

    stmt = select(SecretAuditLog).order_by(SecretAuditLog.created_at.desc()).limit(limit)
    if secret_id:
        stmt = stmt.where(SecretAuditLog.secret_id == secret_id)
    if actor_id:
        stmt = stmt.where(SecretAuditLog.actor_id == actor_id)
    return (await session.execute(stmt)).scalars().all()


async def list_bindings(session, *, secret_id: str) -> Sequence[SecretBinding]:
    stmt = (
        select(SecretBinding)
        .where(SecretBinding.secret_id == secret_id)
        .order_by(SecretBinding.created_at)
    )
    return (await session.execute(stmt)).scalars().all()


async def match_usage_user_ids(session, keyword: str) -> list[str]:
    """按 ID / 用户名 / 姓名 / 邮箱找人，返回流水表能对上的 ``user_id`` 字符串。

    流水表的 ``user_id`` 是字符串（历史原因，允许存非数字的内部标识），用户表主键是
    整数，所以这里统一转成字符串再比。匹配不到返回空列表——**不能**返回 None 让调用
    方跳过过滤，那等于把别人的流水漏出去。
    """
    from sqlalchemy import or_

    from app.models.user import User

    kw = str(keyword or "").strip()
    if not kw:
        return []
    like = f"%{kw}%"
    stmt = select(User.id).where(
        or_(
            User.user_name.like(like),
            User.real_name.like(like),
            User.email.like(like),
        )
    )
    matched = [int(item) for item in (await session.execute(stmt)).scalars().all()]
    # 纯数字时也当 ID 精确匹配：运维手上往往只有一个裸 ID。
    if kw.isdigit() and int(kw) not in matched:
        matched.append(int(kw))
    return [str(item) for item in matched]


async def resolve_usage_users(session, rows) -> dict[str, dict]:
    """批量补使用记录里的用户展示信息（用户名 / 姓名 / 邮箱）。

    界面原来只显示「用户 7」，排查「某用户说用不了」时还得先去用户表反查是谁。
    一次批量查完，不在循环里逐条查（明细最多 500 条）。
    """
    from app.models.user import User

    raw_ids = {str(row.user_id) for row in rows if getattr(row, "user_id", None)}
    numeric_ids = sorted({int(item) for item in raw_ids if item.isdigit()})
    if not numeric_ids:
        return {}
    stmt = select(User.id, User.user_name, User.real_name, User.email).where(
        User.id.in_(numeric_ids)
    )
    return {
        str(uid): {"user_name": user_name, "real_name": real_name, "email": email}
        for uid, user_name, real_name, email in (await session.execute(stmt)).all()
    }


async def list_usage(
    session,
    *,
    user_id: str | None = None,
    secret_id: str | None = None,
    user_keyword: str | None = None,
    limit: int = 100,
) -> Sequence:
    """使用流水。管理员传 ``user_id=None`` 看全局；普通用户只看自己。

    ``user_keyword`` 只**缩小**范围：与 ``user_id`` 是 AND 关系，所以普通用户带上
    别人的名字也搜不到别人的流水。
    """
    from app.models.secret import SecretUsageLog

    stmt = select(SecretUsageLog).order_by(SecretUsageLog.created_at.desc())
    if user_id:
        stmt = stmt.where(SecretUsageLog.user_id == user_id)
    if secret_id:
        stmt = stmt.where(SecretUsageLog.secret_id == secret_id)
    if user_keyword and str(user_keyword).strip():
        matched_ids = await match_usage_user_ids(session, str(user_keyword))
        if not matched_ids:
            return []
        stmt = stmt.where(SecretUsageLog.user_id.in_(matched_ids))
    return (await session.execute(stmt.limit(max(1, min(limit, 500))))).scalars().all()


async def usage_stats(
    session,
    *,
    user_id: str | None = None,
    secret_id: str | None = None,
    days: int = 7,
) -> dict:
    """用量的聚合视图。明细回答「刚才那次为什么失败」，统计回答「这周正常吗」。

    只做 `COUNT(*) GROUP BY`，不逐条捞——流水表会越积越大，明细有上限、统计没有。
    被拒绝的记录也算进去：`denied_by_*` 的次数本身就是「配了但用不了」的信号，
    只看 success 会让最该发现的问题消失。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import func, select

    from app.models.secret import SecretUsageLog

    window = max(1, min(int(days or 7), 90))
    since = datetime.utcnow() - timedelta(days=window)

    conditions = [SecretUsageLog.created_at >= since]
    if user_id:
        conditions.append(SecretUsageLog.user_id == user_id)
    if secret_id:
        conditions.append(SecretUsageLog.secret_id == secret_id)

    by_result = (
        await session.execute(
            select(SecretUsageLog.result, func.count())
            .where(*conditions)
            .group_by(SecretUsageLog.result)
        )
    ).all()

    by_secret = (
        await session.execute(
            select(SecretUsageLog.secret_key_snapshot, SecretUsageLog.result, func.count())
            .where(*conditions)
            .group_by(SecretUsageLog.secret_key_snapshot, SecretUsageLog.result)
        )
    ).all()

    by_scene = (
        await session.execute(
            select(SecretUsageLog.scene, func.count())
            .where(*conditions)
            .group_by(SecretUsageLog.scene)
        )
    ).all()

    by_day = (
        await session.execute(
            select(func.date(SecretUsageLog.created_at), SecretUsageLog.result, func.count())
            .where(*conditions)
            .group_by(func.date(SecretUsageLog.created_at), SecretUsageLog.result)
        )
    ).all()

    # 把 result 聚到「凭据」这一维：同一凭据的成功与拒绝要放在一起看
    secrets: dict[str, dict] = {}
    for key, result, count in by_secret:
        name = key or '（已删除）'
        item = secrets.setdefault(name, {"secret_key": name, "total": 0, "failed": 0})
        item["total"] += count
        if result != "success":
            item["failed"] += count

    days_map: dict[str, dict] = {}
    for day, result, count in by_day:
        label = day.isoformat() if hasattr(day, "isoformat") else str(day)
        item = days_map.setdefault(label, {"date": label, "total": 0, "failed": 0})
        item["total"] += count
        if result != "success":
            item["failed"] += count

    total = sum(count for _, count in by_result)
    failed = sum(count for result, count in by_result if result != "success")

    return {
        "days": window,
        "total": total,
        "success": total - failed,
        "failed": failed,
        "by_result": [
            {"result": result, "count": count}
            for result, count in sorted(by_result, key=lambda row: -row[1])
        ],
        "by_secret": sorted(secrets.values(), key=lambda item: -item["total"]),
        "by_scene": [
            {"scene": scene, "count": count}
            for scene, count in sorted(by_scene, key=lambda row: -row[1])
        ],
        "by_day": sorted(days_map.values(), key=lambda item: item["date"]),
    }


async def read_variable_value(
    session,
    *,
    secret_id: str,
    actor_id: str,
    dept_code: str = "",
    trace_id: str | None = None,
) -> str:
    """读变量类的明文。

    **只对 ``variable`` 开放**：租户号、接口地址这类东西必须能被复制，否则用户
    没法在别处用。``credential`` 在这里直接拒绝——不是权限不够，是设计上就没有
    这个能力，任何人都没有。

    系统级变量同样受授权约束：它不是「谁都能看的公共常量」，而是「授权给谁谁
    能看」。少了这一步，任何登录用户只要拿到 UUID 就能读出管理员配置的租户号、
    内部接口地址，而列表页还装作看不见它。

    每次成功读取都记一条流水。明文只在这一刻离开平台，没有痕迹的话，
    「这个租户号是怎么流出去的」永远查不清。
    """
    row = await session.get(UserSecret, secret_id)
    if row is None:
        raise SecretValidationError("凭据不存在")
    if row.secret_type != TYPE_VARIABLE:
        raise SecretValidationError("凭证类凭据不可读取；请在配置中引用，由平台注入")
    if row.owner_scope == SCOPE_USER and row.owner_id != actor_id:
        raise SecretPermissionError("不能读取他人的凭据")
    if row.owner_scope == SCOPE_SYSTEM:
        visible = await list_visible_secrets(
            session, user_id=actor_id, dept_code=dept_code, only_active=True
        )
        if row.id not in {item.id for item in visible}:
            # 未授权就当它不存在，和列表页一个口径
            raise SecretPermissionError("不能读取他人的凭据")
    if not row.value_encrypted:
        raise SecretValidationError("凭据没有值")

    plaintext = get_api_key_manager().decrypt_api_key(row.value_encrypted)

    import uuid as _uuid

    from app.models.secret import SecretUsageLog

    session.add(
        SecretUsageLog(
            id=str(_uuid.uuid4()),
            secret_id=row.id,
            secret_key_snapshot=row.secret_key,
            owner_scope=row.owner_scope,
            trace_id=trace_id,
            user_id=actor_id,
            scene="variable_read",
            consumer_type=None,
            consumer_id=None,
            slot=None,
            target_host=None,
            result="success",
        )
    )
    try:
        await session.commit()
    except Exception:  # noqa: BLE001 - 流水写失败不该让用户拿不到值
        logger.warning("[SecretCenter] 读取变量流水写入失败 secret=%s", row.secret_key, exc_info=True)
        await session.rollback()
    return plaintext


async def detach_consumer(session, *, consumer_type: str, consumer_id: str) -> int:
    """删掉挂在某个消费者上的全部绑定，返回清理条数。

    消费者被删除（删工具、删 MCP 服务、删脚本）时必须调用。表间没有外键，
    所以没人会替我们清——留下的孤儿绑定不会报错（解析时按 consumer_id 查，
    消费者没了就永远查不到），但会一直占着唯一索引的位置，而且让「这条凭据
    被绑到哪了」这种反查显示出一个已经不存在的目标。

    解析失败或表不存在时安静返回 0：删工具的接口不该因为凭据表的问题而失败。
    """
    from sqlalchemy import delete as sa_delete

    from app.models.secret import SecretBinding

    if not consumer_type or not consumer_id:
        return 0
    try:
        result = await session.execute(
            sa_delete(SecretBinding).where(
                SecretBinding.consumer_type == consumer_type,
                SecretBinding.consumer_id == consumer_id,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[SecretCenter] 清理消费者绑定失败: %s/%s", consumer_type, consumer_id, exc_info=True
        )
        await session.rollback()
        return 0
