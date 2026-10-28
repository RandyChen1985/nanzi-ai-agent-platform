"""会话在线分享的业务逻辑。

设计约束（与设计文档一致）：

- 分享即快照：``content_json`` 存创建那一刻的正文，读分享不查原会话；
- 密码存**密文**而不是哈希：需求要求列表里能反复「复制密码」，哈希不可逆；
- 失效统一 404：不存在 / 已作废 / 已过期对访客是同一句话，避免被枚举探测。

本模块分两层：上半部分是**纯函数**（密码 / 有效期 / 令牌 / 正文清洗），不碰数据库、
不碰 Redis，可以直接单测；下半部分是业务函数（快照装配、增删查、访问计数、解锁限流），
依赖 ``AsyncSession`` 与 Redis。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.conversation_share import ConversationShare

logger = logging.getLogger(__name__)

#: 对外链接 ID 的随机字节数：``token_urlsafe(18)`` 约 24 字符，落在 VARCHAR(32) 内
SHARE_ID_BYTES = 18
SECRET_LENGTH = 8
SECRET_MIN_LENGTH = 6
SECRET_MAX_LENGTH = 32
#: 去掉 0/O/1/l/I：口头转述与手抄密码时最容易出错的字符
SECRET_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

#: 访问令牌最长 12 小时：够访客看完一次分享，短到泄露后窗口有限
TOKEN_MAX_HOURS = 12
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_TURNS = 500
TITLE_MAX_LENGTH = 120

#: 解锁失败限流（Redis 计数，业务函数在 Task 4 使用）
UNLOCK_FAIL_LIMIT = 5
UNLOCK_FAIL_WINDOW_SECONDS = 600

PRESET_TTL = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
CUSTOM_HOUR_RANGE = (1, 24 * 365)
CUSTOM_DAY_RANGE = (1, 365)

#: 快捷按钮语法 ``[标签](quick:命令)`` 的简化匹配（不处理命令里带右括号的极端情况，
#: 与 ``frontend/src/utils/quickButtons.ts`` 的括号深度解析等价度足够：残留的
#: ``(quick:…)`` 由 ``QUICK_RESIDUE_RE`` 兜底）
QUICK_LINK_RE = re.compile(r"(?:\[|【)[^\]】\n]+?(?:\]|】)\s*\(\s*quick:[^)\n]*\)\s*", re.IGNORECASE)
#: AI 偶尔直接吐 HTML 按钮：``<a href="quick:…">标签</a>``
QUICK_HTML_RE = re.compile(
    r"<a\b[^>]*href=([\"'])quick:[^\"']*\1[^>]*>[\s\S]*?</a>", re.IGNORECASE
)
#: 兜底：标签里带括号导致上一条没吃掉时，至少把 ``(quick:...)`` 这段命令清掉
QUICK_RESIDUE_RE = re.compile(r"\(\s*quick:[^)\n]*\)\s*", re.IGNORECASE)
#: 快捷按钮的引导语：链接被摘掉后只剩这句话，分享页里留着很怪
QUICK_INTRO_RE = re.compile(
    r"^(?:#{2,6}\s*)?(?:💬\s*)?(?:确认完成后[，,]?\s*)?"
    r"(?:您?还可以继续|您可能还想了解|您可以这样继续|一键继续|接下来您可以)\s*[:：]?$"
)
#: 摘掉链接后只剩项目符号的行
EMPTY_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*•+]|\d+[.)])\s*$")

#: 内部推理 / SQL 计划 / 生成文件链接 —— 这些都不能出现在对外正文里
THOUGHT_RE = re.compile(r"<thought>[\s\S]*?</thought>", re.IGNORECASE)
ANALYSIS_RE = re.compile(r":::analysis[^\n]*\n[\s\S]*?\n:::", re.IGNORECASE)
SQL_PLAN_RE = re.compile(r"<sql_plan>[\s\S]*?</sql_plan>", re.IGNORECASE)
#: 没闭合的推理块按「后面全是内部推理」处理：宁可丢半段正文，也不能把推理发到公开页
UNCLOSED_THOUGHT_RE = re.compile(r"<thought>[\s\S]*$", re.IGNORECASE)
UNCLOSED_ANALYSIS_RE = re.compile(r":::analysis[\s\S]*$", re.IGNORECASE)
#: 生成文件链接：相对路径（前端拼 API base）与绝对路径都要认，下载需要登录态
GENERATED_LINK_RE = re.compile(
    r"\[([^\]\n]+)\]\(\s*(?:https?://[^\s)]*?)?/api/v1/chat/generated-files/[^\s)]*?\s*\)",
    re.IGNORECASE,
)
BARE_GENERATED_URL_RE = re.compile(
    r"(?:https?://\S*?)?/api/v1/chat/generated-files/[\w\-./%?=&+#~]*",
    re.IGNORECASE,
)
FILE_UNAVAILABLE_HINT = "文件未随分享提供"
#: 段落块被整段摘掉后会留下成串空行，压到最多一个空行
BLANK_LINES_RE = re.compile(r"\n{3,}")
#: 快捷按钮块前面常有一条 ``---`` 分隔线，块摘掉后它就成了结尾的孤立分隔线
TRAILING_SEPARATOR_RE = re.compile(r"(?:\n[ \t]*---[ \t]*)+\s*$")


def _share_fernet() -> Fernet:
    """从平台 ``ENCRYPTION_KEY`` 派生分享专用密钥。

    刻意再 hash 一层：不与 API Key 共用同一把 Fernet key，避免一处泄露波及另一处。
    """
    raw = str(getattr(settings, "ENCRYPTION_KEY", "") or "").encode("utf-8")
    if not raw:
        raise ValueError("ENCRYPTION_KEY is required for conversation sharing")
    derived = hashlib.sha256(b"conversation-share:v1:" + raw).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def generate_secret(length: int = SECRET_LENGTH) -> str:
    """生成访问密码：只用 ``SECRET_ALPHABET`` 里的字符，系统随机源。"""
    return "".join(secrets.choice(SECRET_ALPHABET) for _ in range(length))


def normalize_secret(value: Optional[str]) -> str:
    """校验自定义密码；空白与长度不合法直接拒绝，不静默截断。"""
    secret = str(value or "").strip()
    if not (SECRET_MIN_LENGTH <= len(secret) <= SECRET_MAX_LENGTH):
        raise ValueError(f"访问密码长度需在 {SECRET_MIN_LENGTH}-{SECRET_MAX_LENGTH} 位之间")
    return secret


def resolve_expires_at(expiry: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Optional[datetime]:
    """把前端有效期选项换算成绝对时间；``None`` 表示永久。"""
    base = now or datetime.now()
    options = expiry if isinstance(expiry, dict) else {}
    mode = str(options.get("mode") or "preset").strip().lower()

    if mode == "permanent":
        return None

    if mode == "preset":
        preset = str(options.get("preset") or "7d").strip().lower()
        delta = PRESET_TTL.get(preset)
        if delta is None:
            raise ValueError(f"不支持的有效期预设：{preset}")
        return base + delta

    if mode == "custom":
        try:
            value = int(options.get("value"))
        except (TypeError, ValueError) as exc:
            raise ValueError("自定义有效期需要正整数") from exc
        unit = str(options.get("unit") or "day").strip().lower()
        if unit == "hour":
            low, high = CUSTOM_HOUR_RANGE
            if not (low <= value <= high):
                raise ValueError(f"小时数需在 {low}-{high} 之间")
            return base + timedelta(hours=value)
        if unit == "day":
            low, high = CUSTOM_DAY_RANGE
            if not (low <= value <= high):
                raise ValueError(f"天数需在 {low}-{high} 之间")
            return base + timedelta(days=value)
        raise ValueError(f"不支持的时间单位：{unit}")

    raise ValueError(f"不支持的有效期模式：{mode}")


def encrypt_secret(plaintext: str) -> str:
    """密码密文：Fernet token 再 base64 一层，约 188 字符，稳稳落在 VARCHAR(512) 内。

    刻意不做长度截断——截断的密文解不开，等于用户拿到的密码是错的。
    """
    token = _share_fernet().encrypt(str(plaintext or "").encode("utf-8"))
    return base64.urlsafe_b64encode(token).decode("ascii")


def decrypt_secret(ciphertext: str) -> Optional[str]:
    """解密失败返回 None：密钥轮换后老分享的密码不可用，调用方按「需重建」提示。"""
    try:
        raw = base64.urlsafe_b64decode(str(ciphertext or "").encode("ascii"))
        return _share_fernet().decrypt(raw).decode("utf-8")
    except (InvalidToken, ValueError, TypeError, AttributeError, UnicodeDecodeError):
        logger.warning("Failed to decrypt conversation share secret")
        return None


def verify_secret(ciphertext: str, candidate: str) -> bool:
    """校验访客输入的密码；定时比较，避免按字符试探。

    比较的是 UTF-8 字节：``hmac.compare_digest`` 对含非 ASCII 字符的 ``str`` 会抛
    ``TypeError``，而自定义密码允许中文。
    """
    expected = decrypt_secret(ciphertext)
    if expected is None:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), str(candidate or "").encode("utf-8"))


def issue_access_token(
    share_id: str,
    share_expires_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, int]:
    """签发短期访问令牌：有效期取 ``min(12h, 分享剩余有效期)``。

    返回 ``(token, expires_in_seconds)``，前端按 ``expires_in`` 决定何时重新解锁。
    """
    base = _to_naive(now or datetime.now())
    token_exp = base + timedelta(hours=TOKEN_MAX_HOURS)
    if share_expires_at is not None:
        # 分享自己先过期时，令牌不能比它还长寿
        share_exp = _to_naive(share_expires_at)
        if share_exp < token_exp:
            token_exp = share_exp
    payload = {"share_id": str(share_id), "exp": int(token_exp.timestamp()), "v": 1}
    token = _share_fernet().encrypt(json.dumps(payload).encode("utf-8"))
    encoded = base64.urlsafe_b64encode(token).decode("ascii")
    return encoded, max(0, int((token_exp - base).total_seconds()))


def verify_access_token(token: str, share_id: str, now: Optional[datetime] = None) -> bool:
    """校验令牌：必须解得出、分享 ID 对得上、且未过期（跨分享复用一律失败）。"""
    try:
        raw = base64.urlsafe_b64decode(str(token or "").encode("ascii"))
        payload = json.loads(_share_fernet().decrypt(raw).decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, AttributeError, UnicodeDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if str(payload.get("share_id") or "") != str(share_id):
        return False
    try:
        exp = int(payload.get("exp"))
    except (TypeError, ValueError):
        return False
    return _to_naive(now or datetime.now()).timestamp() < exp


def _to_naive(value: datetime) -> datetime:
    """把可能是 aware 的时间统一成 naive 本地时间，避免与 ``datetime.now()`` 比较报错。"""
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _next_line_had_quick(cleaned_lines: List[str], had_quick: List[bool], start: int) -> bool:
    """往下找第一个非空行，判断它原本是不是快捷按钮行。"""
    for index in range(start, len(cleaned_lines)):
        if (cleaned_lines[index] or "").strip():
            return had_quick[index]
    return False


def _strip_quick_links(text: str) -> str:
    """摘掉快捷按钮，并收掉因此变空的项目符号与引导语。

    与 ``frontend/src/utils/quickButtons.ts`` 的 ``stripQuickButtons`` 同口径，区别是
    不整行丢弃：行里若还有正文（如「先看这个结论：[展开](quick:…)」），正文要留下。
    """
    lines = str(text or "").split("\n")
    cleaned_lines: List[str] = []
    had_quick: List[bool] = []
    for line in lines:
        had = bool(QUICK_LINK_RE.search(line) or QUICK_HTML_RE.search(line))
        current = QUICK_HTML_RE.sub("", QUICK_LINK_RE.sub("", line))
        cleaned_lines.append(current)
        had_quick.append(had)

    kept: List[str] = []
    for index, current in enumerate(cleaned_lines):
        if had_quick[index] and not current.strip():
            # 整行只有一个快捷按钮（多数是「- [🙋 标签](quick:…)」），整行去掉
            continue
        if EMPTY_LIST_MARKER_RE.match(current):
            # 摘掉链接后只剩「- 」这种空项目符号
            continue
        if QUICK_INTRO_RE.match(current.strip().strip("*_").strip()) and _next_line_had_quick(
            cleaned_lines, had_quick, index + 1
        ):
            # 「您还可以继续：」后面只剩被摘掉的按钮，这句话也别留了
            continue
        kept.append(current)
    return "\n".join(kept)


def sanitize_assistant_content(text: str) -> str:
    """清洗助手正文：只留对外的结论，不带内部推理、SQL 与需要登录的下载链接。"""
    value = str(text or "")
    value = THOUGHT_RE.sub("", value)
    value = ANALYSIS_RE.sub("", value)
    value = UNCLOSED_THOUGHT_RE.sub("", value)
    value = UNCLOSED_ANALYSIS_RE.sub("", value)
    value = SQL_PLAN_RE.sub("", value)
    value = _strip_quick_links(value)
    value = QUICK_RESIDUE_RE.sub("", value)
    value = GENERATED_LINK_RE.sub(
        lambda m: f"📎 {m.group(1).strip()}（{FILE_UNAVAILABLE_HINT}）", value
    )
    value = BARE_GENERATED_URL_RE.sub(f"📎 附件（{FILE_UNAVAILABLE_HINT}）", value)
    value = BLANK_LINES_RE.sub("\n\n", value)
    value = TRAILING_SEPARATOR_RE.sub("", value)
    return value.strip()


def sanitize_user_content(text: str) -> str:
    """用户提问只去首尾空白：原话是分享的上下文，不做任何加工。"""
    return str(text or "").strip()


def derive_share_status(
    row: Any, now: Optional[datetime] = None
) -> Literal["active", "expired", "revoked"]:
    """推导对外状态：作废优先于过期；过期不落库，纯按时间算。"""
    if str(getattr(row, "status", "active")) == "revoked":
        return "revoked"
    expires_at = getattr(row, "expires_at", None)
    if expires_at is not None and _to_naive(expires_at) <= _to_naive(now or datetime.now()):
        return "expired"
    return "active"


def is_share_reachable(row: Any, now: Optional[datetime] = None) -> bool:
    """访客能否打开：只有 active 可达。"""
    return derive_share_status(row, now=now) == "active"


# ---------------------------------------------------------------------------
# 业务层：快照装配、增删查、访问计数、解锁限流
# 以下函数都依赖数据库（解锁限流另依赖 Redis），因此不进纯函数测试。
# ---------------------------------------------------------------------------


class ShareNotFoundError(Exception):
    """链接不存在 / 已作废 / 已过期——对外统一 404，不区分原因。"""


class ShareAccessDeniedError(Exception):
    """密钥模式未解锁，或令牌无效。"""


class ShareContentTooLargeError(Exception):
    """会话过长，冻结快照会撑爆单行/单次响应。"""


class ShareRateLimitedError(Exception):
    """同一 IP 在窗口内连续试错太多次，暂时拒绝解锁。"""


@dataclass
class Snapshot:
    """创建分享那一刻的冻结正文：之后源会话被改、被删都与它无关。"""

    title: str
    agent_id: str
    agent_name: str
    messages: List[Dict[str, Any]]
    turn_count: int


async def assemble_snapshot(
    db: AsyncSession, *, user_id: str, conversation_id: str
) -> Optional[Snapshot]:
    """按归属拉全量轮次并组装冻结快照；找不到归属返回 None（端点转 404）。

    三步走，任何一步越界都当场拒绝，避免把「不该分享的内容」写进快照：

    1. 先数轮次：0 轮说明这个会话不属于该用户（或已不存在）；
    2. 超过 ``MAX_SNAPSHOT_TURNS`` 直接抛，不做半截快照；
    3. 正文序列化后超过 ``MAX_SNAPSHOT_BYTES`` 也抛（LONGTEXT 放得下，但响应放不下）。
    """
    from app.models.audit import AgentExecutionHistory as History
    from app.services.ai.history_query import build_history_query

    count_query = select(func.count(History.id)).where(
        History.conversation_id == conversation_id,
        History.user_id == user_id,
    )
    total = int((await db.execute(count_query)).scalar() or 0)
    if total == 0:
        return None
    if total > MAX_SNAPSHOT_TURNS:
        raise ShareContentTooLargeError(f"会话共 {total} 轮，超过 {MAX_SNAPSHOT_TURNS} 轮上限")

    query, _count = build_history_query(
        page=1,
        page_size=MAX_SNAPSHOT_TURNS,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    # build_history_query 内部固定 order_by(id.desc())（历史列表要新的在前），
    # 分享页却必须从第一问往下读：先清掉既有排序再按时间正序重排。
    query = query.order_by(None).order_by(History.created_at.asc(), History.id.asc())
    rows = list((await db.execute(query)).scalars().all())
    if not rows:
        return None

    messages: List[Dict[str, Any]] = []
    for row in rows:
        created_at = row.created_at.isoformat() if row.created_at else None
        question = sanitize_user_content(row.query)
        answer = sanitize_assistant_content(row.summary)
        if question:
            messages.append({"role": "user", "content": question, "created_at": created_at})
        if answer:
            messages.append({"role": "assistant", "content": answer, "created_at": created_at})

    payload_size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
    if payload_size > MAX_SNAPSHOT_BYTES:
        raise ShareContentTooLargeError("会话正文超过 2 MB，暂不支持在线分享")

    title = ""
    for message in messages:
        if message["role"] == "user":
            title = str(message["content"]).replace("\n", " ")[:TITLE_MAX_LENGTH]
            break

    # 智能体按最后一轮的取值冻结：会话中途换过智能体时，展示最近的那个
    agent_id = str(rows[-1].agent_id or "")
    agent_name = await _resolve_agent_name(db, agent_id)
    return Snapshot(
        title=title,
        agent_id=agent_id,
        agent_name=agent_name,
        messages=messages,
        turn_count=len(rows),
    )


async def _resolve_agent_name(db: AsyncSession, agent_id: str) -> str:
    """冻结分享时的智能体显示名：会话存的是 ID，展示要给人看的名字。

    智能体已被删除时回退成 ID——分享页宁可显示 ``agent-xxx`` 也不能空着。
    """
    if not agent_id:
        return ""
    from app.models.agent import AIAgent

    row = (
        await db.execute(select(AIAgent.display_name, AIAgent.name).where(AIAgent.id == agent_id))
    ).first()
    if row is None:
        return agent_id
    return str(row[0] or row[1] or agent_id)


async def create_share(
    db: AsyncSession,
    *,
    owner_user_id: str,
    owner_username: str,
    conversation_id: str,
    access_mode: str,
    secret: Optional[str],
    expires_at: Optional[datetime],
) -> ConversationShare:
    """创建分享：先装配快照（顺带做归属校验），再把冻结正文落库。

    归属校验借道 ``assemble_snapshot``：别人的会话根本装配不出快照，因此这里抛
    ``ShareNotFoundError``，端点转 404——不额外暴露「会话存在但不属于你」。
    """
    snapshot = await assemble_snapshot(db, user_id=owner_user_id, conversation_id=conversation_id)
    if snapshot is None:
        raise ShareNotFoundError("conversation not found")

    mode: Literal["public", "secret"] = "public" if access_mode == "public" else "secret"
    secret_cipher = None
    if mode == "secret":
        # 非法密码（太短/太长）由 normalize_secret 抛 ValueError，端点转 400
        secret_cipher = encrypt_secret(normalize_secret(secret))

    row = ConversationShare(
        share_id=secrets.token_urlsafe(SHARE_ID_BYTES)[:32],
        conversation_id=conversation_id,
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        agent_id=snapshot.agent_id or None,
        agent_name=snapshot.agent_name or None,
        title=snapshot.title or None,
        access_mode=mode,
        secret_cipher=secret_cipher,
        content_json=json.dumps(snapshot.messages, ensure_ascii=False),
        message_count=len(snapshot.messages),
        turn_count=snapshot.turn_count,
        expires_at=_to_naive(expires_at) if expires_at is not None else None,
        status="active",
        view_count=0,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_shares(
    db: AsyncSession,
    *,
    user_id: str,
    page: int,
    page_size: int,
    status: Optional[str] = None,
) -> Tuple[List[ConversationShare], int]:
    """「我分享的」列表：按创建时间倒序，状态过滤在内存里做。

    状态（过期）是按时间现算的，库里没有对应列，因此只能先取当页再筛；
    ``total`` 是未筛选的总数，由端点决定怎么展示。
    """
    page = max(1, int(page))
    page_size = min(max(1, int(page_size)), 100)
    conditions = [
        ConversationShare.owner_user_id == user_id,
        ConversationShare.conversation_id.isnot(None),
    ]
    total = int(
        (await db.execute(select(func.count(ConversationShare.id)).where(*conditions))).scalar() or 0
    )
    rows = (
        (
            await db.execute(
                select(ConversationShare)
                .where(*conditions)
                .order_by(ConversationShare.created_at.desc(), ConversationShare.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    wanted = str(status or "").strip().lower()
    items = [
        row for row in rows if wanted in ("", "all") or derive_share_status(row) == wanted
    ]
    return list(items), total


async def get_share(db: AsyncSession, share_id: str) -> Optional[ConversationShare]:
    """按对外 ID 取一行；不存在返回 None（调用方自己决定 404 还是 False）。"""
    return (
        await db.execute(
            select(ConversationShare).where(ConversationShare.share_id == share_id)
        )
    ).scalar_one_or_none()


async def revoke_share(db: AsyncSession, *, user_id: str, share_id: str) -> bool:
    """作废分享：本人可作废，幂等（重复调用仍然返回 True）；非本人一律 False。"""
    row = await get_share(db, share_id)
    if row is None or str(row.owner_user_id) != str(user_id):
        return False
    if row.status != "revoked":
        row.status = "revoked"
        row.revoked_at = datetime.now()
        await db.commit()
    return True


async def publish_public_meta(db: AsyncSession, share_id: str) -> Dict[str, Any]:
    """访客页首屏元信息：不返回正文，因此**不计数**（计数只留给真正读到内容那次）。"""
    row = await get_share(db, share_id)
    if row is None or not is_share_reachable(row):
        raise ShareNotFoundError(share_id)
    remaining = None
    if row.expires_at is not None:
        remaining = max(0, int((_to_naive(row.expires_at) - datetime.now()).total_seconds()))
    return {
        "title": row.title or "",
        "agent_name": row.agent_name or "",
        "access_mode": row.access_mode,
        "status": derive_share_status(row),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "remaining_seconds": remaining,
        "message_count": int(row.message_count or 0),
        "turn_count": int(row.turn_count or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "owner_display": row.owner_username or "",
    }


async def unlock_share(
    db: AsyncSession, *, share_id: str, secret: str, client_ip: str
) -> Tuple[str, int]:
    """校验访问密码并签发短期令牌：返回 ``(token, expires_in_seconds)``。

    顺序刻意是「先查限流、再比密码、失败计数、成功清零」：失败次数只累积在
    Redis，不落库，窗口过后自动放开。
    """
    row = await get_share(db, share_id)
    if row is None or not is_share_reachable(row):
        raise ShareNotFoundError(share_id)
    if row.access_mode != "secret":
        raise ValueError("public share does not need unlocking")

    await assert_unlock_allowed(share_id, client_ip)
    if not verify_secret(row.secret_cipher or "", secret):
        await register_unlock_failure(share_id, client_ip)
        raise ShareAccessDeniedError("invalid secret")

    await clear_unlock_failures(share_id, client_ip)
    return issue_access_token(row.share_id, row.expires_at)


async def read_share_content(
    db: AsyncSession,
    *,
    share_id: str,
    token: Optional[str],
    viewer_user_id: Optional[str],
    client_ip: str,
) -> Dict[str, Any]:
    """读冻结正文：这是唯一会给 ``view_count`` +1 的入口。

    本人（``viewer_user_id`` 等于分享人）带登录态直接放行，不必再输密码——
    否则分享人自己点开自己的分享还要找密码。
    """
    row = await get_share(db, share_id)
    if row is None or not is_share_reachable(row):
        raise ShareNotFoundError(share_id)

    is_owner = bool(viewer_user_id) and str(viewer_user_id) == str(row.owner_user_id)
    if row.access_mode == "secret" and not is_owner:
        if not token or not verify_access_token(token, row.share_id):
            raise ShareAccessDeniedError("token required")

    # 先把返回值取好再自增：UPDATE + commit 之后 ORM 对象的属性状态不再可靠
    payload = {
        "title": row.title or "",
        "agent_name": row.agent_name or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "messages": json.loads(row.content_json or "[]"),
    }

    now = datetime.now()
    # 原子自增，避免并发打开时读-改-写丢计数
    await db.execute(
        update(ConversationShare)
        .where(ConversationShare.id == row.id)
        .values(view_count=ConversationShare.view_count + 1, last_viewed_at=now)
    )
    await db.commit()
    return payload


def _unlock_fail_key(share_id: str, client_ip: str) -> str:
    """限流计数键：按分享 + IP 独立计数，换 IP 不能白嫖同一份密码尝试预算。"""
    return f"share:unlock:fail:{share_id}:{client_ip or 'unknown'}"


async def assert_unlock_allowed(share_id: str, client_ip: str) -> None:
    """解锁前置检查：窗口内失败次数达到上限就拒绝。

    Redis 不可用时 **fail-open**：宁可少一层限流，也不能让正常访客打不开分享页。
    """
    from app.core.redis import get_redis

    redis = await get_redis()
    if redis is None:
        return
    raw = await redis.get(_unlock_fail_key(share_id, client_ip))
    if raw is not None and int(raw or 0) >= UNLOCK_FAIL_LIMIT:
        raise ShareRateLimitedError("too many attempts")


async def register_unlock_failure(share_id: str, client_ip: str) -> None:
    """记一次解锁失败：首次失败时挂上窗口期，之后只累加不续期。"""
    from app.core.redis import get_redis

    redis = await get_redis()
    if redis is None:
        return
    key = _unlock_fail_key(share_id, client_ip)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, UNLOCK_FAIL_WINDOW_SECONDS)


async def clear_unlock_failures(share_id: str, client_ip: str) -> None:
    """解锁成功后清零：不让历史失败次数继续影响合法访客。"""
    from app.core.redis import get_redis

    redis = await get_redis()
    if redis is None:
        return
    await redis.delete(_unlock_fail_key(share_id, client_ip))
