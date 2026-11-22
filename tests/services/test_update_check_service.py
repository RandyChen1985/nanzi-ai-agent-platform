"""版本检测服务：比较口径、缓存、节流与失败降级。

这里是「有没有新版本」的唯一判定实现，所以比较规则必须被钉死——
平台版本四段（1.0.23.0）、tag 三段（1.0.23），漏掉补零就会把「已最新」误报成
「有新版本」，或者把本地更新的补丁构建判成落后。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pytest

from app.services import update_check_service as svc

pytestmark = pytest.mark.no_infrastructure


class FakeRedis:
    """够用的内存替身：只实现 get / set(ex, nx)。"""

    def __init__(self) -> None:
        self.strings: Dict[str, Any] = {}

    async def get(self, key: str) -> Optional[str]:
        return self.strings.get(key)

    async def set(self, key: str, value: Any, ex: Optional[int] = None, nx: bool = False):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True


@pytest.fixture
def memory_redis(monkeypatch):
    """把 Redis 换成内存替身，并清掉模块级降级缓存。"""

    fake = FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(svc, "get_redis", _get_redis)
    svc._memory_cache.clear()
    svc._memory_cache_at = 0.0
    svc._memory_failure = None
    svc._memory_failure_at = 0.0
    return fake


@pytest.fixture
def upstream_calls(monkeypatch):
    """替换上游抓取：既能计数，也能按需抛错。"""

    state = {"count": 0, "error": None, "latest": "1.0.24"}

    async def _fetch() -> Dict[str, Any]:
        state["count"] += 1
        if state["error"]:
            raise state["error"]
        return {
            "latest": state["latest"],
            "latest_normalized": svc.normalize_version(state["latest"]),
            "published_at": "2026-11-22T13:38:28+08:00",
            "release_url": f"https://gitee.com/x/y/releases/tag/{state['latest']}",
            "notes": ["凭据中心", "图像生成"],
            "has_migrations": True,
        }

    monkeypatch.setattr(svc, "fetch_upstream_info", _fetch)
    return state


# ── 版本比较：12 条边界用例（与原型、与前端展示口径一致）─────────────────
@pytest.mark.parametrize(
    ("current", "latest", "expect"),
    [
        ("1.0.24.0", "1.0.24", "none"),      # 四段 vs 三段：补零后相等，不能误报
        ("1.0.23.0", "1.0.24", "update"),    # 主流程
        ("1.0.9.0", "1.0.10", "update"),     # 逐段数值比较，字符串比会判反
        ("1.0.24.1", "1.0.24", "none"),      # 本地补丁构建更新
        ("v1.0.24.0", "v1.0.25", "update"),  # 带 v 前缀
        ("1.0.24.0", "1.0.24-rc1", "skip"),  # 预发布不催升级
        ("dev", "1.0.24", "skip"),           # 开发构建
        ("1.0.24.0", "1.0.24.0", "none"),    # 完全相同
        ("2.0.0.0", "1.9.9", "none"),        # 大版本落后于本地
        ("1.0.24.0", "", "skip"),            # 上游为空
        ("1.0.24.0", "release-1", "skip"),   # 不规范 tag
        ("1.0.24.0", "1.1", "update"),       # 缺段补零
    ],
)
def test_compare_versions(current: str, latest: str, expect: str) -> None:
    assert svc.compare_versions(current, latest).verdict == expect


def test_equals_is_not_an_update() -> None:
    """核心回归点：三段 tag 与四段版本同版本时不能提示。"""

    result = svc.compare_versions("1.0.24.0", "1.0.24")
    assert result.has_update is False
    assert "相等" in result.reason


def test_local_ahead_is_not_an_update() -> None:
    assert svc.compare_versions("1.0.24.1", "1.0.24").has_update is False


@pytest.mark.parametrize(
    ("raw", "expect"),
    [
        ("1.0.24.0", "1.0.24"),
        ("v1.0.25", "1.0.25"),
        ("1.0.26.3", "1.0.26"),
        ("dev", "dev"),
        ("", ""),
    ],
)
def test_normalize_version_strips_build_segment(raw: str, expect: str) -> None:
    assert svc.normalize_version(raw) == expect


# ── 上游解析 ─────────────────────────────────────────────────────────────
def test_pick_latest_tag_prefers_version_over_date() -> None:
    """补发老版本时 commit 日期会更新，但「最新版本」应按版本号判断。"""

    tags = [
        {"name": "1.0.9", "commit": {"date": "2026-11-20T00:00:00+08:00"}},
        {"name": "1.0.10", "commit": {"date": "2026-11-10T00:00:00+08:00"}},
    ]
    assert svc._pick_latest_tag(tags)["name"] == "1.0.10"


def test_pick_latest_tag_skips_prerelease() -> None:
    tags = [
        {"name": "1.0.25-rc1", "commit": {"date": "2026-11-25T00:00:00+08:00"}},
        {"name": "1.0.24", "commit": {"date": "2026-11-22T00:00:00+08:00"}},
    ]
    assert svc._pick_latest_tag(tags)["name"] == "1.0.24"


def test_extract_notes_uses_feature_headings() -> None:
    body = "\n".join(
        [
            "# v1.0.24.0",
            "一些前言",
            "### 1. 结构化凭据（Structured Credentials）",
            "细节",
            "### 2. 🎨 图像生成",
            "细节",
        ]
    )
    notes = svc._extract_notes(body)
    assert notes[0] == "结构化凭据"
    assert notes[1] == "图像生成"


def test_extract_notes_falls_back_to_plain_lines() -> None:
    notes = svc._extract_notes("# 标题\n- 第一条\n- 第二条")
    assert notes == ["第一条", "第二条"]


def test_truncate_body_keeps_normal_release_notes() -> None:
    body = "## 更新内容\n" + "\n".join(f"- 第 {i} 条" for i in range(20))
    assert svc._truncate_body(body) == body.strip()


def test_truncate_body_marks_oversized_notes() -> None:
    """截断要留一句话说明，静默截半句比不给看更糟。"""

    body = "x" * (svc.MAX_BODY_CHARS + 500)
    truncated = svc._truncate_body(body)
    assert len(truncated) > svc.MAX_BODY_CHARS
    assert "正文过长已截断" in truncated


def test_payload_carries_body_for_the_dialog() -> None:
    """弹层要显示全文，所以正文必须随检查结果一起缓存。"""

    upstream = {
        "latest": "1.0.24",
        "latest_normalized": "1.0.24",
        "published_at": "2026-11-22T13:38:28+08:00",
        "release_url": "https://gitee.com/x/y/releases/tag/1.0.24",
        "notes": ["凭据中心"],
        "body": "## 更新内容\n- 结构化凭据",
        "has_migrations": True,
    }
    payload = svc._build_payload("1.0.23.0", upstream, source="live")
    assert payload["body"].startswith("## 更新内容")


def test_has_migrations_matches_chinese_and_english() -> None:
    assert svc._has_migrations("## 数据库迁移清单") is True
    assert svc._has_migrations("Migrations: V218~V222") is True
    assert svc._has_migrations("只有功能变更") is False


@pytest.mark.parametrize(
    ("message", "expect_keyword"),
    [
        ("httpx.ConnectTimeout: timed out", "超时"),
        ("Client error '429 Too Many Requests'", "过于频繁"),
        ("getaddrinfo failed", "无法连接"),
        ("上游 tags 为空", "没有任何 tag"),
    ],
)
def test_friendly_error_is_human_readable(message: str, expect_keyword: str) -> None:
    assert expect_keyword in svc._friendly_error(RuntimeError(message))


# ── 缓存 / 节流 / 失败降级 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_first_check_hits_upstream_then_serves_cache(memory_redis, upstream_calls) -> None:
    first = await svc.check_for_update("1.0.23.0")
    assert upstream_calls["count"] == 1
    assert first["has_update"] is True
    assert first["source"] == "live"

    second = await svc.check_for_update("1.0.23.0")
    assert upstream_calls["count"] == 1, "24h 缓存内不该再打上游"
    assert second["source"] == "cache"
    assert second["has_update"] is True


@pytest.mark.asyncio
async def test_current_version_change_recomputes_without_refetch(memory_redis, upstream_calls) -> None:
    """就地升级后：缓存不重取，但 has_update 必须按新版本重算。"""

    await svc.check_for_update("1.0.23.0")
    after_upgrade = await svc.check_for_update("1.0.24.0")
    assert upstream_calls["count"] == 1
    assert after_upgrade["has_update"] is False


@pytest.mark.asyncio
async def test_manual_check_bypasses_cache(memory_redis, upstream_calls) -> None:
    await svc.check_for_update("1.0.23.0")
    forced = await svc.check_for_update("1.0.23.0", force=True)
    assert upstream_calls["count"] == 2
    assert forced["source"] == "live"


@pytest.mark.asyncio
async def test_manual_check_is_throttled(memory_redis, upstream_calls) -> None:
    await svc.check_for_update("1.0.23.0", force=True)
    throttled = await svc.check_for_update("1.0.23.0", force=True)
    assert upstream_calls["count"] == 1, "60 秒内不该再打上游"
    # 节流不是失败：结论仍是那份缓存结果，只标记「这次没刷新」。
    # 用 error 表达会被前端渲染成红色「检查失败」，用户以为接口坏了。
    assert throttled["throttled"] is True
    assert throttled["error"] is None
    assert throttled["latest"] == "1.0.24"


@pytest.mark.asyncio
async def test_upstream_failure_becomes_friendly_error(memory_redis, upstream_calls) -> None:
    upstream_calls["error"] = RuntimeError("httpx.ConnectTimeout: timed out")
    payload = await svc.check_for_update("1.0.23.0", force=True)
    assert payload["has_update"] is False
    assert payload["error"] and "超时" in payload["error"]


@pytest.mark.asyncio
async def test_failure_is_not_retried_within_five_minutes(memory_redis, upstream_calls) -> None:
    """上游抖一下不该变成每次打开菜单都去重试。"""

    upstream_calls["error"] = RuntimeError("boom")
    await svc.check_for_update("1.0.23.0")
    assert upstream_calls["count"] == 1

    upstream_calls["error"] = None
    second = await svc.check_for_update("1.0.23.0")
    assert upstream_calls["count"] == 1, "5 分钟失败标记内不该再打上游"
    assert second["error"]


@pytest.mark.asyncio
async def test_cache_payload_survives_roundtrip(memory_redis, upstream_calls) -> None:
    await svc.check_for_update("1.0.23.0")
    stored = json.loads(memory_redis.strings[svc.CACHE_KEY])
    assert stored["latest"] == "1.0.24"
    assert stored["release_url"].endswith("1.0.24")
    assert stored["has_migrations"] is True
