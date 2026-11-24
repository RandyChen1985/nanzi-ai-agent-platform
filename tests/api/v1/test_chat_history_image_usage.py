"""历史接口回填图片用量：刷新页面后消息上的「图片：…」不能消失。

## 背景

生图/改图的用量写在 `ai_agent_execution_traces.meta_info.image_usage`，本轮通过 SSE
的 `image_usage` 事件推给前端，由 `accumulateImageUsage` 累加成
`msg.image_usage = {calls, images, requested, tokens, size}`。

但 `GET /api/v1/chat/history` 不回传这个字段——刷新页面、或从历史里翻回那一轮，
消息底部的「图片：出图 2 张 · 2 次调用 · 8,762 tok」就没了，用户会以为图片没计量。

## 口径：回传原始单次记录，不在这里聚合

后端返回**每次工具调用的原始用量数组**（字段名与轨迹、与 SSE 事件完全一致），
聚合留给前端那一处 `accumulateImageUsage`。理由：聚合规则（calls+1、images+=成功
张数、requested+=请求张数、tokens+=上游 token、size 取最后一个非空）只该有一份实现，
后端再抄一份迟早会与实时链路算出两个数。

## 只回该回的东西

`meta_info` 里还有 `timeout_seconds`、决策快照等与图片无关的内容，不能整段透传——
既浪费带宽，也可能把内部字段糊到前端消息对象上。
"""
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.main import app
from app.models.audit import AgentExecutionHistory, AgentExecutionTrace
from app.models.user import User
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio

OWN_AGENT_ID = "agent-history-image-usage"


def _usage(*, succeeded=1, requested=1, total_tokens=100, size="1024x1024") -> dict:
    return {
        "tool": "generate_image",
        "requested_count": requested,
        "succeeded_count": succeeded,
        "size": size,
        "model": "gpt-image-1",
        "upstream_tokens": (
            None if total_tokens is None else {"total_tokens": total_tokens}
        ),
        "elapsed_ms": 8206,
    }


class _Fixture:
    def __init__(self) -> None:
        self.user_names: list[str] = []
        self.trace_ids: list[str] = []

    async def create_user(self, db, prefix: str, role: str = "user") -> dict:
        uniq = uuid.uuid4().hex[:10]
        user_name = f"{prefix}_{uniq}"
        api_key = await AuthService.generate_api_key(user_name, role=role, db=db)
        user = (await db.execute(select(User).where(User.user_name == user_name))).scalar_one()
        self.user_names.append(user_name)
        return {
            "headers": {"X-API-Key": api_key},
            "user_id": str(user.id),
            "user_name": user_name,
        }

    async def seed_turn(self, db, *, user: dict, conversation_id: str, steps: list[dict]) -> str:
        """一轮对话 + 若干轨迹步骤；`steps` 里每项是 AgentExecutionTrace 的字段。"""
        trace_id = f"trace-{uuid.uuid4().hex[:16]}"
        db.add(
            AgentExecutionHistory(
                agent_id=OWN_AGENT_ID,
                trace_id=trace_id,
                conversation_id=conversation_id,
                user_id=user["user_id"],
                username=user["user_name"],
                query="画一张数据中台的架构示意图",
                summary="已经画好了",
                status="success",
            )
        )
        for index, step in enumerate(steps, start=1):
            db.add(
                AgentExecutionTrace(
                    trace_id=trace_id,
                    step_number=index,
                    **step,
                )
            )
        await db.commit()
        self.trace_ids.append(trace_id)
        return trace_id

    async def cleanup(self) -> None:
        async with AsyncSessionLocal() as session:
            if self.trace_ids:
                await session.execute(
                    delete(AgentExecutionTrace).where(
                        AgentExecutionTrace.trace_id.in_(self.trace_ids)
                    )
                )
                await session.execute(
                    delete(AgentExecutionHistory).where(
                        AgentExecutionHistory.trace_id.in_(self.trace_ids)
                    )
                )
            for user_name in self.user_names:
                await session.execute(delete(User).where(User.user_name == user_name))
            await session.commit()


@pytest.fixture
async def image_usage_fixture():
    fixture = _Fixture()
    yield fixture
    await fixture.cleanup()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _items(res) -> list:
    assert res.status_code == 200, res.text
    return res.json()["data"]["items"]


async def test_history_returns_image_usage_as_raw_calls(db_session, image_usage_fixture):
    """一轮里两次图像调用 → 返回两条原始记录，字段名与轨迹/SSE 一致。"""
    user = await image_usage_fixture.create_user(db_session, "imgusage")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await image_usage_fixture.seed_turn(
        db_session,
        user=user,
        conversation_id=conversation_id,
        steps=[
            {
                "event_type": "tool_call",
                "tool_name": "generate_image",
                "execution_time_ms": 2000,
                "status": "success",
                "meta_info": {"image_usage": _usage(succeeded=2, requested=2, total_tokens=300)},
            },
            {
                "event_type": "tool_call",
                "tool_name": "edit_image",
                "execution_time_ms": 1500,
                "status": "success",
                "meta_info": {"image_usage": _usage(succeeded=1, requested=2, total_tokens=50, size="768x768")},
            },
        ],
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/history",
            params={"conversation_id": conversation_id},
            headers=user["headers"],
        )
        items = await _items(res)

    assert len(items) == 1
    rows = items[0]["image_usage"]
    assert isinstance(rows, list), "回填的应是原始记录数组，聚合交给前端那一处实现"
    assert len(rows) == 2

    first = rows[0]
    # 字段名必须与 meta_info.image_usage / SSE 事件一致，前端才能复用同一个累加函数
    assert first["succeeded_count"] == 2
    assert first["requested_count"] == 2
    assert first["upstream_tokens"]["total_tokens"] == 300
    assert first["size"] == "1024x1024"
    # 聚合形态（calls/images/requested/tokens）不属于这里
    assert "images" not in first and "calls" not in first

    assert rows[1]["size"] == "768x768", "第二条改图必须原样带着自己的尺寸"
    assert rows[1]["upstream_tokens"]["total_tokens"] == 50


async def test_history_does_not_leak_unrelated_meta_info(db_session, image_usage_fixture):
    """只回图片用量，不把 timeout_seconds / 决策快照等整段 meta_info 透给前端。"""
    user = await image_usage_fixture.create_user(db_session, "imgusage_meta")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await image_usage_fixture.seed_turn(
        db_session,
        user=user,
        conversation_id=conversation_id,
        steps=[
            {
                "event_type": "tool_call",
                "tool_name": "generate_image",
                "execution_time_ms": 2000,
                "status": "success",
                "meta_info": {
                    "timeout_seconds": 180,
                    "timeout_source": "tool_spec",
                    "image_usage": _usage(),
                },
            }
        ],
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/history",
            params={"conversation_id": conversation_id},
            headers=user["headers"],
        )
        items = await _items(res)

    rows = items[0]["image_usage"]
    assert len(rows) == 1
    assert "timeout_seconds" not in rows[0], "无关字段不该跟着回填"
    assert "timeout_source" not in rows[0]


async def test_history_omits_image_usage_for_turns_without_images(db_session, image_usage_fixture):
    """没碰图像工具的轮次不带这个字段——前端据此不渲染图片那一行。"""
    user = await image_usage_fixture.create_user(db_session, "imgusage_none")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await image_usage_fixture.seed_turn(
        db_session,
        user=user,
        conversation_id=conversation_id,
        steps=[
            {
                "event_type": "tool_call",
                "tool_name": "write_file",
                "execution_time_ms": 300,
                "status": "success",
                "meta_info": {"timeout_seconds": 60},
            }
        ],
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/history",
            params={"conversation_id": conversation_id},
            headers=user["headers"],
        )
        items = await _items(res)

    assert items[0].get("image_usage") is None


async def test_history_recovers_usage_from_tool_output_for_legacy_rows(
    db_session, image_usage_fixture
):
    """修复前的历史轮次：`meta_info` 没有用量，但 `tool_output.raw` 里留着完整一份。

    不回退解析的话，那条已经花过钱的老消息刷新后仍然没有「图片：…」这一行。
    """
    user = await image_usage_fixture.create_user(db_session, "imgusage_legacy")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await image_usage_fixture.seed_turn(
        db_session,
        user=user,
        conversation_id=conversation_id,
        steps=[
            {
                "event_type": "tool_call",
                "tool_name": "generate_image",
                "execution_time_ms": 6500,
                "status": "success",
                "meta_info": {"timeout_source": "tool_defined", "timeout_seconds": 300.0},
                "tool_output": {
                    "raw": json.dumps(
                        {
                            "ok": True,
                            "images": [{"filename": "generated-image-1.png"}],
                            "image_usage": _usage(requested=2, succeeded=2, total_tokens=70),
                        },
                        ensure_ascii=False,
                    )
                },
            }
        ],
    )

    async with _client() as client:
        res = await client.get(
            "/api/v1/chat/history",
            params={"conversation_id": conversation_id},
            headers=user["headers"],
        )
        items = await _items(res)

    rows = items[0]["image_usage"]
    assert len(rows) == 1
    assert rows[0]["succeeded_count"] == 2
    assert rows[0]["requested_count"] == 2
    assert rows[0]["upstream_tokens"]["total_tokens"] == 70
