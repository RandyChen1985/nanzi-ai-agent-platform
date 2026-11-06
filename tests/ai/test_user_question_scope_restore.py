"""提问回执轮必须继承「提问那一刻」的数据集范围。

本轮手选的数据集只挂在发送那一刻的请求体 / 最后一条 user 消息附件上，而
【用户回答】回执是一条全新的用户消息，两者都没有。不恢复，答完追问这一轮就
退回「用户可访问的全部数据集」，委派出去的子 agent 拿到的也是空范围。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.pipeline.context import PipelineContext
from app.services.ai.pipeline.steps.preflight_step import PreflightStep
from app.services.ai.user_question import build_user_question_receipt
from app.services.ai.user_question_store import UserQuestionStore


pytestmark = pytest.mark.no_infrastructure


class _FakeStore:
    def __init__(self, record: dict):
        self.record = record

    async def submit_answer(self, **_kwargs):
        return dict(self.record)


def _receipt_message() -> str:
    return build_user_question_receipt(
        question_id="uq_scope_restore",
        selected_option_ids=["all_users"],
    )


def _build_context(*, mounted_ids: list[str] | None = None) -> PipelineContext:
    context = PipelineContext(
        messages=[{"role": "user", "content": _receipt_message()}],
        user_info={"user_id": 1, "user_name": "tester"},
        conversation_id="conv_scope_restore",
        metadata_dataset_ids=list(mounted_ids) if mounted_ids is not None else None,
        execution_options={"metadata_dataset_scope": {"source": "user", "request_ids": [], "session": []}},
    )
    context.lane_user_id = 1
    return context


async def _run_preflight(context: PipelineContext, record: dict) -> None:
    step = PreflightStep()
    store = _FakeStore(record)
    with patch.object(step, "_quota_block_message", new_callable=AsyncMock) as quota, patch(
        "app.services.ai.runtime.session_run_lane.conversation_run_lane.is_locked",
        new_callable=AsyncMock,
    ) as locked, patch.object(
        UserQuestionStore,
        "from_runtime",
        new=AsyncMock(return_value=store),
    ):
        quota.return_value = None
        locked.return_value = False
        async for _chunk in step.run(context):
            pass


@pytest.mark.asyncio
async def test_receipt_turn_restores_scope_snapshot_when_request_has_none():
    context = _build_context()
    record = {
        "status": "submitted",
        "purpose": "",
        "selected_option_ids": ["all_users"],
        "options": [{"id": "all_users", "label": "全部用户清单"}],
        "metadata_dataset_ids": ["53"],
    }

    await _run_preflight(context, record)

    assert context.execution_status == "success"
    assert context.metadata_dataset_ids == ["53"]
    assert context.execution_options["metadata_dataset_scope"] == {
        "source": "user_question",
        "request_ids": ["53"],
        "session": [],
    }


@pytest.mark.asyncio
async def test_receipt_turn_keeps_explicit_turn_scope_over_record_snapshot():
    """回执轮自己又选了数据集时以本轮为准，不能被提问时的快照覆盖。"""
    context = _build_context(mounted_ids=["7"])
    record = {
        "status": "submitted",
        "purpose": "",
        "selected_option_ids": ["all_users"],
        "options": [{"id": "all_users", "label": "全部用户清单"}],
        "metadata_dataset_ids": ["53"],
    }

    await _run_preflight(context, record)

    assert context.metadata_dataset_ids == ["7"]
    assert context.execution_options["metadata_dataset_scope"]["source"] == "user"


@pytest.mark.asyncio
async def test_legacy_question_record_does_not_change_scope():
    """上线前提的问没有范围快照，恢复必须保持原状。"""
    context = _build_context()
    record = {
        "status": "submitted",
        "purpose": "",
        "selected_option_ids": ["all_users"],
        "options": [{"id": "all_users", "label": "全部用户清单"}],
    }

    await _run_preflight(context, record)

    assert context.metadata_dataset_ids is None
    assert context.execution_options["metadata_dataset_scope"]["source"] == "user"


@pytest.mark.asyncio
async def test_dataset_selection_card_still_wins_with_selected_option():
    context = _build_context()
    record = {
        "status": "submitted",
        "purpose": "chatbi_dataset_selection",
        "selected_option_ids": ["12"],
        "options": [{"id": "12", "label": "门禁数据"}],
        # 选择卡以选项为准，快照里的旧范围不生效
        "metadata_dataset_ids": ["53"],
    }

    await _run_preflight(context, record)

    assert context.metadata_dataset_ids == ["12"]
    assert context.execution_options["metadata_dataset_scope"]["source"] == "user_question"
