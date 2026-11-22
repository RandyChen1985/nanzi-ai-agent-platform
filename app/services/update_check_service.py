"""平台版本检测：当前版本与上游最新 tag 的比对。

数据源固定为 Gitee OpenAPI v5（本项目的国内主仓），只读两个公开端点，
无需 token：

- ``/tags``     取全部 tag，用于判断「最新版本是多少」
- ``/releases`` 取发行说明，用于说明「更新了什么」

为什么由服务端代理，而不是让浏览器直连 Gitee：
1. 浏览器直连会吃 CORS，且把第三方域名写进前端；
2. 限流按 IP 算，N 个用户在线就是 N 倍请求量；
3. 结果要跨实例共享——多副本各自算一遍没有任何收益。

存储约定（全部走 Redis，不建表）：
- ``agent:update_check:latest``   检查结果 JSON，TTL 24h（派生数据，过期即重查）
- ``agent:update_check:failure``  失败标记，TTL 5 分钟（防止上游抖动时每分钟重试）
- ``agent:update_check:throttle`` 手动检查节流，SET NX EX 60
用户级的「忽略此版本」不在这里，它属于账号偏好，落在 ``agent:portal_prefs:{user_id}``。

版本号口径（本文件唯一权威实现，前端只做展示）：
- 平台版本是四段（``1.0.23.0``），tag 是三段（``1.0.23``），**右补 0 到四段**再比；
- 逐段按数字比，不按字符串比（``1.0.9`` < ``1.0.10``）；
- 只认「最新 tag 严格大于当前版本」，相等、本地更新、预发布 tag、``dev`` 构建一律不提示。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis

from app.core import redis as redis_module
from app.core.http_client import GlobalHttpClient
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# ── 上游仓库（固定 Gitee，不跟随 branding 配置）────────────────────────────
GITEE_OWNER = "randychen1985"
GITEE_REPO = "nanzi-ai-agent-platform"
GITEE_WEB_BASE = f"https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}"
GITEE_API_BASE = f"https://gitee.com/api/v5/repos/{GITEE_OWNER}/{GITEE_REPO}"
# 同一个仓库在 GitHub 上也有一份（origin），两边发版说明内容一致。
# 弹层里两个地址都给：有人习惯 GitHub，有人在内网只能开其中一个。
GITHUB_OWNER = "RandyChen1985"
GITHUB_REPO = "nanzi-ai-agent-platform"
GITHUB_WEB_BASE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"

TAGS_URL = f"{GITEE_API_BASE}/tags"
RELEASES_URL = f"{GITEE_API_BASE}/releases"

# ── Redis 键与时效 ────────────────────────────────────────────────────────
CACHE_KEY = "agent:update_check:latest"
FAILURE_KEY = "agent:update_check:failure"
THROTTLE_KEY = "agent:update_check:throttle"
DAILY_LOCK_KEY_PREFIX = "lock:system_update_check"

CACHE_TTL_SECONDS = 24 * 3600
FAILURE_TTL_SECONDS = 5 * 60
THROTTLE_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 8.0
MAX_NOTES = 6
# 发行说明正文上限：弹层里要能滚动通读，但没必要为一次检查塞进几十 KB。
# 超长时保留前 2 万字符（约 300 行 markdown），末行提示去仓库看全文。
MAX_BODY_CHARS = 20000

VERSION_SEGMENTS = 4


# ── 进程内降级缓存（Redis 不可用时使用）────────────────────────────────────
_memory_cache: Dict[str, Any] = {}
_memory_cache_at: float = 0.0
_memory_failure: Optional[Dict[str, Any]] = None
_memory_failure_at: float = 0.0


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def current_platform_version() -> str:
    """当前部署版本。前端展示的是构建期注入的版本，这里用同源的 APP_VERSION。"""

    return (os.environ.get("APP_VERSION") or "dev").strip()


# ── 版本解析与比较 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedVersion:
    segments: tuple
    prerelease: bool

    @property
    def normalized(self) -> str:
        """归一化后的展示形态：去掉构建段的三段，与 tag 对齐（1.0.24.0 → 1.0.24）。"""

        return ".".join(str(part) for part in self.segments[:3])


def parse_version(raw: Any) -> Optional[ParsedVersion]:
    """解析版本号；不可比较时返回 None（空、dev、非数字、段数过多）。

    预发布后缀（``-rc1`` / ``-beta.2`` / ``+build``）先剥离再判断：
    否则 ``1.0.24-rc1`` 会被当成 ``1.0.24.rc1`` 这种非法段。
    """

    text = str(raw or "").strip().lstrip("vV")
    if not text or re.match(r"^dev", text, re.IGNORECASE):
        return None
    core = re.split(r"[-+]", text, maxsplit=1)
    head = core[0]
    prerelease = len(core) > 1
    parts = head.split(".")
    if not parts or len(parts) > VERSION_SEGMENTS:
        return None
    if not all(re.fullmatch(r"\d+", part) for part in parts):
        return None
    segments = [int(part) for part in parts]
    while len(segments) < VERSION_SEGMENTS:
        segments.append(0)
    return ParsedVersion(segments=tuple(segments), prerelease=prerelease)


@dataclass(frozen=True)
class CompareResult:
    verdict: str  # update / none / skip
    reason: str

    @property
    def has_update(self) -> bool:
        return self.verdict == "update"


def compare_versions(current: Any, latest: Any) -> CompareResult:
    """判断「最新 tag 是否严格大于当前版本」。

    这是本需求的判定核心，前端不再重复实现，避免两处口径漂移。
    """

    parsed_current = parse_version(current)
    if parsed_current is None:
        return CompareResult("skip", "当前版本不可比较")
    parsed_latest = parse_version(latest)
    if parsed_latest is None:
        return CompareResult("skip", "最新 tag 不可比较")
    if parsed_latest.prerelease and not parsed_current.prerelease:
        # 预发布不该催着普通人升级：rc/beta 本身就是给测试用的
        return CompareResult("skip", "最新 tag 是预发布版")
    for index, (newer, older) in enumerate(
        zip(parsed_latest.segments, parsed_current.segments), start=1
    ):
        if newer > older:
            return CompareResult("update", f"第 {index} 段 {newer} > {older}")
        if newer < older:
            return CompareResult("none", f"本地更新（第 {index} 段更大）")
    return CompareResult("none", "归一化后相等")


def normalize_version(raw: Any) -> str:
    """把任意版本号压成与 tag 对齐的三段形态；解析不了就原样返回去空白。"""

    parsed = parse_version(raw)
    if parsed is None:
        return str(raw or "").strip()
    return parsed.normalized


# ── 上游数据抓取 ──────────────────────────────────────────────────────────


def _pick_latest_tag(tags: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从 tag 列表里挑「版本号最大」的稳定 tag。

    不按 commit 日期挑：补发一个老版本的分支时日期会更新，那不是「最新版本」。
    全部 tag 都不可解析时退回列表首项（Gitee 按名称倒序返回）。
    """

    parsed: List[tuple] = []
    for item in tags:
        name = str(item.get("name") or "").strip()
        version = parse_version(name)
        if version is None or version.prerelease:
            continue
        parsed.append((version.segments, name, item))
    if parsed:
        parsed.sort(key=lambda row: row[0], reverse=True)
        return parsed[0][2]
    return tags[0] if tags else None


def _release_body_for_tag(releases: List[Dict[str, Any]], tag: str) -> Dict[str, Any]:
    for item in releases:
        if str(item.get("tag_name") or "").strip() == tag:
            return item
    return {}


def _extract_notes(body: str) -> List[str]:
    """从发行说明正文里抽「更新了什么」的高层要点。

    优先取 Key Features 的小节标题（那是给人看的目录），没有就退回正文前几行。
    这里只做展示用的摘要，正文详情让用户点「查看更新内容」去仓库看。
    """

    if not body:
        return []
    notes: List[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("### "):
            continue
        title = stripped[4:].strip()
        title = re.sub(r"^\d+[.、]\s*", "", title)  # 去掉「1. 」
        # 去掉图标：含变体选择符（U+FE0F）与零宽连接符（U+200D），否则「🕒️ 」会残留一个不可见字符
        title = re.sub(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+\s*", "", title)
        # 去掉结尾的英文别名，半角与全角括号都要处理（文档里两种都出现过）
        title = re.sub(r"[(（][^)）]*[)）]\s*$", "", title).strip()
        if title:
            notes.append(title[:80])
        if len(notes) >= MAX_NOTES:
            break
    if notes:
        return notes
    plain = [
        re.sub(r"^[>*\-\s]+", "", line).strip()
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return [line[:80] for line in plain[:MAX_NOTES] if line]


def _truncate_body(body: str) -> str:
    """正文截断：弹层要能读全文，但缓存里不该塞进任意大的东西。

    超长时保留前缀并补一句说明，而不是静默截半句——用户读到一半发现断了
    却没有任何提示，比直接告诉「去仓库看全文」更糟。
    """

    text = (body or "").strip()
    if len(text) <= MAX_BODY_CHARS:
        return text
    return text[:MAX_BODY_CHARS] + "\n\n> 正文过长已截断，完整内容请到仓库发行版页查看。"


def _has_migrations(body: str) -> bool:
    """正文里提到迁移就提示「升级前先跑 SQL」。

    刻意不做编号区间解析：格式一变就会漏报，而「含迁移」这个布尔值已经够用——
    具体跑哪几个脚本，部署时看发行说明正文即可。
    """

    return bool(body) and bool(
        re.search(r"数据库迁移|迁移清单|Migrations?", body, re.IGNORECASE)
    )


async def fetch_upstream_info() -> Dict[str, Any]:
    """真正打一次 Gitee。任何一步失败都抛异常，由调用方统一归类。"""

    client = await GlobalHttpClient.get_client()
    tags_resp = await client.get(
        TAGS_URL, params={"per_page": 100}, timeout=REQUEST_TIMEOUT_SECONDS
    )
    tags_resp.raise_for_status()
    tags = tags_resp.json()
    if not isinstance(tags, list) or not tags:
        raise ValueError("上游 tags 为空")

    latest_item = _pick_latest_tag(tags)
    if not latest_item:
        raise ValueError("上游无可用 tag")
    tag_name = str(latest_item.get("name") or "").strip()
    commit = latest_item.get("commit") or {}

    # 发行说明是「锦上添花」：拿不到不影响「有没有新版本」的主结论
    body = ""
    release_url = f"{GITEE_WEB_BASE}/releases/tag/{tag_name}"
    github_release_url = f"{GITHUB_WEB_BASE}/releases/tag/{tag_name}"
    try:
        releases_resp = await client.get(
            RELEASES_URL, params={"per_page": 20}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        if releases_resp.status_code == 200:
            release = _release_body_for_tag(releases_resp.json() or [], tag_name)
            body = str(release.get("body") or "")
            if not body:
                logger.debug("Gitee release for tag %s has empty body", tag_name)
    except Exception as exc:  # noqa: BLE001 - 发行说明失败不阻断主流程
        logger.warning("Failed to fetch Gitee releases: %s", exc)

    return {
        "latest": tag_name,
        "latest_normalized": normalize_version(tag_name),
        "published_at": commit.get("date") or "",
        "release_url": release_url,
        "github_release_url": github_release_url,
        "notes": _extract_notes(body),
        "body": _truncate_body(body),
        "has_migrations": _has_migrations(body),
    }


# ── 缓存读写 ─────────────────────────────────────────────────────────────


async def _read_cache() -> Optional[Dict[str, Any]]:
    redis = await get_redis()
    if redis is not None:
        try:
            raw = await redis.get(CACHE_KEY)
            if raw:
                import json

                decoded = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                parsed = json.loads(decoded)
                if isinstance(parsed, dict):
                    return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read update-check cache from Redis: %s", exc)
        return None
    if _memory_cache and _now_ts() - _memory_cache_at < CACHE_TTL_SECONDS:
        return dict(_memory_cache)
    return None


async def _write_cache(payload: Dict[str, Any]) -> None:
    global _memory_cache, _memory_cache_at
    redis = await get_redis()
    if redis is not None:
        try:
            import json

            await redis.set(CACHE_KEY, json.dumps(payload, ensure_ascii=False), ex=CACHE_TTL_SECONDS)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write update-check cache to Redis: %s", exc)
    _memory_cache = dict(payload)
    _memory_cache_at = _now_ts()


async def _read_failure() -> Optional[Dict[str, Any]]:
    global _memory_failure
    redis = await get_redis()
    if redis is not None:
        try:
            raw = await redis.get(FAILURE_KEY)
            if raw:
                import json

                decoded = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                parsed = json.loads(decoded)
                if isinstance(parsed, dict):
                    return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read update-check failure flag: %s", exc)
        return None
    if _memory_failure and _now_ts() - _memory_failure_at < FAILURE_TTL_SECONDS:
        return dict(_memory_failure)
    return None


async def _write_failure(message: str) -> None:
    global _memory_failure, _memory_failure_at
    payload = {"error": message, "checked_at": _now_iso()}
    redis = await get_redis()
    if redis is not None:
        try:
            import json

            await redis.set(FAILURE_KEY, json.dumps(payload, ensure_ascii=False), ex=FAILURE_TTL_SECONDS)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write update-check failure flag: %s", exc)
    _memory_failure = payload
    _memory_failure_at = _now_ts()


async def _acquire_throttle() -> bool:
    """手动检查的 60 秒节流；Redis 不可用时放行（宁可多打一次，也不要让按钮失灵）。"""

    redis = await get_redis()
    if redis is None:
        return True
    try:
        return bool(await redis.set(THROTTLE_KEY, "1", ex=THROTTLE_SECONDS, nx=True))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to acquire update-check throttle: %s", exc)
        return True


def _friendly_error(exc: Exception) -> str:
    """把上游异常翻成一句人话，前端直接展示。"""

    text = str(exc)
    if "Timeout" in type(exc).__name__ or "timed out" in text.lower():
        return "连接 Gitee 超时，请稍后再试"
    if re.search(r"\b429\b|rate limit", text, re.IGNORECASE):
        return "Gitee 请求过于频繁，请稍后再试"
    if re.search(r"\b404\b", text):
        return "仓库或 tag 接口不存在（可能仓库已改名）"
    if re.search(r"\b5\d\d\b", text):
        return "Gitee 服务暂时不可用，请稍后再试"
    if "tags 为空" in text or "无可用 tag" in text:
        return "上游仓库还没有任何 tag"
    if re.search(r"getaddrinfo|Name or service not known|ConnectError|Connection", text):
        return "无法连接 Gitee（内网/离线环境可忽略此项）"
    return "检查失败，请稍后重试"


# ── 对外入口 ─────────────────────────────────────────────────────────────


def _build_payload(
    current: str,
    upstream: Dict[str, Any],
    *,
    source: str,
) -> Dict[str, Any]:
    result = compare_versions(current, upstream.get("latest"))
    return {
        "current": current,
        "latest": upstream.get("latest") or "",
        "latest_normalized": upstream.get("latest_normalized") or "",
        "has_update": result.has_update,
        "reason": result.reason,
        "published_at": upstream.get("published_at") or "",
        "release_url": upstream.get("release_url") or "",
        "github_release_url": upstream.get("github_release_url") or "",
        "notes": upstream.get("notes") or [],
        "body": upstream.get("body") or "",
        "has_migrations": bool(upstream.get("has_migrations")),
        "checked_at": upstream.get("checked_at") or _now_iso(),
        "source": source,
        "throttled": False,
        "error": None,
    }


async def check_for_update(current: Optional[str] = None, *, force: bool = False) -> Dict[str, Any]:
    """返回一次检查结果。

    ``force=False``（打开菜单/进页面）：优先读 24h 缓存，缓存缺失才回源一次；
    ``force=True``（用户点「检查更新」）：一定回源，但受 60 秒节流约束。

    无论上游是否成功都返回结构化结果，失败原因放在 ``error`` 里——
    让前端显示一句人话，而不是把一个 500 抛给用户。
    """

    current_version = (current or "").strip() or current_platform_version()

    if not force:
        cached = await _read_cache()
        if cached:
            payload = dict(cached)
            payload["current"] = current_version
            # 兼容升级前写入的老缓存（当时还没存正文），别让前端拿到 undefined
            payload.setdefault("body", "")
            payload.setdefault("github_release_url", "")
            # 缓存里存的是上次比较结果；当前版本可能变了（就地升级），重算一次
            result = compare_versions(current_version, payload.get("latest"))
            payload["has_update"] = result.has_update
            payload["reason"] = result.reason
            payload["source"] = "cache"
            return payload

    if force and not await _acquire_throttle():
        cached = await _read_cache()
        if cached:
            payload = dict(cached)
            payload["current"] = current_version
            # 兼容升级前写入的老缓存（当时还没存正文），别让前端拿到 undefined
            payload.setdefault("body", "")
            payload.setdefault("github_release_url", "")
            result = compare_versions(current_version, payload.get("latest"))
            payload["has_update"] = result.has_update
            payload["reason"] = result.reason
            payload["source"] = "throttled"
            # 节流不是失败：结果仍然是上面那份缓存结论，只是这次没能回源刷新。
            # 用 error 表达会让前端渲染成红色「检查失败」，用户以为接口坏了。
            payload["throttled"] = True
            payload["error"] = None
            return payload
        return {
            "current": current_version,
            "latest": "",
            "latest_normalized": "",
            "has_update": False,
            "reason": "throttled",
            "published_at": "",
            "release_url": "",
            "github_release_url": "",
            "notes": [],
            "body": "",
            "has_migrations": False,
            "checked_at": _now_iso(),
            "source": "throttled",
            "throttled": True,
            "error": None,
        }

    # 5 分钟内失败过的，直接用上次的失败结论，避免上游抖动时反复重试
    if not force:
        failure = await _read_failure()
        if failure:
            cached = await _read_cache()
            return {
                "current": current_version,
                "latest": (cached or {}).get("latest") or "",
                "latest_normalized": (cached or {}).get("latest_normalized") or "",
                "has_update": False,
                "reason": "error",
                "published_at": "",
                "release_url": "",
                "github_release_url": "",
                "notes": [],
                "body": "",
                "has_migrations": False,
                "checked_at": failure.get("checked_at") or _now_iso(),
                "source": "cache",
                "error": failure.get("error") or "检查失败，请稍后重试",
            }

    try:
        upstream = await fetch_upstream_info()
    except Exception as exc:  # noqa: BLE001 - 失败要变成一句人话，不是 500
        message = _friendly_error(exc)
        await _write_failure(message)
        logger.warning("Update check failed: %s", exc)
        cached = await _read_cache()
        return {
            "current": current_version,
            "latest": (cached or {}).get("latest") or "",
            "latest_normalized": (cached or {}).get("latest_normalized") or "",
            "has_update": False,
            "reason": "error",
            "published_at": "",
            "release_url": "",
            "github_release_url": "",
            "notes": [],
            "body": "",
            "has_migrations": False,
            "checked_at": _now_iso(),
            "source": "live",
            "error": message,
        }

    upstream["checked_at"] = _now_iso()
    payload = _build_payload(current_version, upstream, source="live")
    try:
        await _write_cache({**payload, "current": current_version})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist update-check result: %s", exc)
    return payload


async def refresh_daily() -> Dict[str, Any]:
    """每日自动检查（调度器调用）。

    多副本部署时用「当天 + 分钟」的 Redis 锁保证只有一台真正回源；
    拿不到锁直接返回缓存结论，不抛异常——调度任务失败不该刷错误堆栈。
    """

    lock_key = f"{DAILY_LOCK_KEY_PREFIX}:{datetime.now().strftime('%Y%m%d')}"
    client: Optional[aioredis.Redis] = redis_module.redis_client
    if client is not None:
        try:
            if not await client.set(lock_key, "locked", ex=6 * 3600, nx=True):
                logger.info("⏩ Update check skipped: another node already ran it today.")
                cached = await _read_cache()
                if cached:
                    return cached
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to acquire update-check daily lock: %s", exc)

    logger.info("⏰ Running daily platform update check...")
    payload = await check_for_update(force=True)
    if payload.get("error"):
        logger.warning("Daily update check finished with error: %s", payload["error"])
    else:
        logger.info(
            "✅ Update check done: current=%s latest=%s has_update=%s",
            payload.get("current"),
            payload.get("latest"),
            payload.get("has_update"),
        )
    return payload
