from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def test_context_compactions_composable_has_request_safe_shared_state_contract():
    source = (ROOT / "frontend/src/composables/useContextCompactions.ts").read_text(
        encoding="utf-8",
    )

    assert "useContextCompactions" in source
    assert "ContextCompactionRecord" in source
    assert "contextCompactions" in source
    assert "contextCompactionCount" in source
    assert "refreshContextCompactions" in source
    assert "latestRequestId" in source
    assert "requestId !== latestRequestId" in source
    assert "conversationId" in source
    assert "headers" in source
    assert "getContextCompactions" in source
    assert "contextCompactionsError" in source
    assert "Array.isArray(records)" in source


def test_pre_route_compaction_is_not_counted_as_a_user_visible_compaction():
    """pre-route 的压缩是会被重算的中间态，不得计入"压缩次数"。

    路由前的压缩只为让路由拿到不超窗的上下文，其窗口随后会被路由后（resolved_model）
    阶段按目标模型的真实窗口重算——窗口按"从最新往回取"，阶段②的窗口必然包含阶段①的
    窗口，所以阶段①的结果永远不会是最终状态。计入它会让同一轮数出两次压缩，而真实只
    压缩了一次（卡片同理多一张）。
    """
    source = (ROOT / "frontend/src/composables/useContextCompactions.ts").read_text(
        encoding="utf-8",
    )

    assert "isUserVisibleContextCompaction" in source
    assert 'record.stage === "pre_route"' in source
    # 计数必须走这个判定，而不是直接按 event_type 过滤
    assert "contextCompactions.value.filter(isUserVisibleContextCompaction)" in source
