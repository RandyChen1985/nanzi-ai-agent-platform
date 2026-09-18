"""压缩事件必须带 stage，前端据此只展示"最终"那份压缩。"""
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def test_backend_tags_compaction_events_with_stage():
    context_step = (
        ROOT / "app/services/ai/pipeline/steps/context_step.py"
    ).read_text(encoding="utf-8")
    route_step = (
        ROOT / "app/services/ai/pipeline/steps/route_step.py"
    ).read_text(encoding="utf-8")

    # pre-route 的 SSE 事件必须显式标记阶段（两处发卡点都要）
    assert context_step.count('ctx_event["stage"] = "pre_route"') == 2
    # resolved_model 阶段同理
    assert 'final_context_event["stage"] = "resolved_model"' in route_step


def test_frontend_skips_pre_route_compaction_card():
    source = (
        ROOT / "frontend/src/utils/agentscopeSseHandlers.ts"
    ).read_text(encoding="utf-8")

    assert 'String(data.stage || "") === "pre_route"' in source


def test_chat_input_latest_compaction_uses_the_shared_visibility_rule():
    source = (
        ROOT / "frontend/src/components/embed/ChatInput.vue"
    ).read_text(encoding="utf-8")

    assert "isUserVisibleContextCompaction" in source
    assert (
        'import { isUserVisibleContextCompaction } from "@/composables/useContextCompactions"'
        in source
    )


def test_context_compaction_record_type_exposes_stage():
    source = (ROOT / "frontend/src/api/agent.ts").read_text(encoding="utf-8")

    assert "stage:" in source
    assert "'pre_route' | 'resolved_model'" in source
