"""凭据列表的「最近使用 / 近 7 天调用」统计。

这套统计曾经**整体失效**：``_usage_stats`` 用 ``func.case(...)`` 写条件聚合，
而那根本不是合法的 SQLAlchemy 用法——它连 SQL 都拼不出来，在**构造语句**时就抛
``TypeError: Function.__init__() got an unexpected keyword argument 'else_'``。
外面正好套着 ``except Exception: logger.debug``，于是异常被静默吞掉，统计停留在
初始值。

症状：使用记录弹窗里明明有 5 条 success，列表却显示「最近使用：从未」、详情里
「近 7 天调用 0 次」。用户只能得出「凭据没生效」的结论，而这恰恰是错的。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.api.portal.endpoints.secrets import _usage_stats
from app.core.orm import AsyncSessionLocal
from app.models.secret import SecretUsageLog, UserSecret


pytestmark = pytest.mark.asyncio


USER_ID = "9101"


async def _make_secret(key: str) -> str:
    # 开发库跨用例不清理，主键/唯一键必须每次唯一
    tag = uuid.uuid4().hex[:12]
    secret_id = f"sec-{tag}"
    async with AsyncSessionLocal() as session:
        session.add(
            UserSecret(
                id=secret_id,
                owner_scope="user",
                owner_id=USER_ID,
                secret_key=f"{key}.{tag}",
                name=key,
                secret_type="credential",
                value_encrypted="x",
                allowed_scenarios=["mcp"],
                allowed_hosts=["*"],
                status="active",
            )
        )
        await session.commit()
    return secret_id


async def _add_log(secret_id: str, *, result: str, age_days: float = 0.0) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            SecretUsageLog(
                id=uuid.uuid4().hex,
                secret_id=secret_id,
                secret_key_snapshot="k",
                owner_scope="user",
                scene="mcp",
                consumer_type="mcp_server",
                consumer_id="srv-1",
                slot="header:X",
                target_host="example.com",
                result=result,
                # 库里一律是 naive UTC（模型默认 datetime.utcnow）
                created_at=datetime.utcnow() - timedelta(days=age_days),
            )
        )
        await session.commit()


async def _stats(secret_id: str) -> dict:
    async with AsyncSessionLocal() as session:
        return (await _usage_stats(session, [secret_id]))[secret_id]


class TestUsageStats:
    async def test_counts_calls_within_seven_days(self):
        sid = await _make_secret("usage.recent")
        await _add_log(sid, result="success")
        await _add_log(sid, result="denied_by_scenario")

        stats = await _stats(sid)

        assert stats["usage_7d"] == 2, "两条记录都该计入近 7 天"
        assert stats["failed_7d"] == 1, "只有一条是失败"
        assert stats["last_used_at"], "有记录就必须有「最近一次」，不能显示从未"

    async def test_old_calls_do_not_count_as_recent_but_keep_last_used(self):
        """超过 7 天的记录不计入「近 7 天」，但「最近一次」是全量口径。

        这两件事必须分开：把 last_used_at 也扣在 7 天窗口里，一个两个月没用过的
        凭据会显示成「从未用过」，等于把它「用过」这段历史抹掉了。
        """
        sid = await _make_secret("usage.old")
        await _add_log(sid, result="success", age_days=30)

        stats = await _stats(sid)

        assert stats["usage_7d"] == 0
        assert stats["last_used_at"], "30 天前用过也算用过"

    async def test_mixing_old_and_new_only_counts_new(self):
        sid = await _make_secret("usage.mixed")
        await _add_log(sid, result="success", age_days=30)
        await _add_log(sid, result="success")
        await _add_log(sid, result="success")

        stats = await _stats(sid)

        assert stats["usage_7d"] == 2
        assert stats["failed_7d"] == 0

    async def test_secret_without_logs_reports_zero(self):
        sid = await _make_secret("usage.none")

        stats = await _stats(sid)

        assert stats["usage_7d"] == 0
        assert stats["failed_7d"] == 0
        assert stats["last_used_at"] is None

    async def test_binding_count_is_not_affected(self):
        """绑定数走的是另一张表，别在修统计时把它带歪。"""
        sid = await _make_secret("usage.binding")
        await _add_log(sid, result="success")

        stats = await _stats(sid)

        assert stats["binding_count"] == 0
