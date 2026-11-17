"""防卡顿与路由 Chunk 失效自愈契约测试。

覆盖五个联防层面，防止回归：
1. `processTimeline.ts` 与 `ChatExecutionTimeline.vue`：computed 内合帧必须开启 `{ clone: true }`，
   交互折叠态收拢进组件内 `uiNodeOverrides`，历史视图和图标分支统一使用 `isSubagentReasoningLog`，
   流式思考增量使用 `reasoningHtmlCache` 复用已渲染片段；同时保持中性灰颜色契约。
2. `EmbedChat.vue` 与 `AgentDebug.vue`：均对 `reasoning_content` 做节流合帧、对 `visibleStreamBody` 与
   `getSkillFlowBadgesForMessage` 做 `WeakMap` 缓存；`AgentDebug.vue` 对齐 `500ms` 思考计时器、
   每 20 条 SSE 让出主线程和 `requestAnimationFrame` 正文缓冲。
3. 管理端路由与视图（`router/index.ts`、`main.ts`、`Dashboard.vue`、`chunkLoadRecovery.ts`）：
   支持动态导入 chunk 失效自动重载恢复（含 15s 冷却守卫）与 `<Suspense>` 错误边界降级。
4. 后端快照器 `process_timeline_snapshot.py`：识别 `_is_subagent_reasoning_log` 并在 `_finalize_log`
   保留 `reasoning=True`。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
MJS_SCRIPT = ROOT / "tests/frontend/timeline_merge_immutability.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行行为测试")
def test_timeline_clone_immutability_and_chunk_recovery_behavior():
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(MJS_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_chat_execution_timeline_uses_immutable_clone_and_ui_overrides():
    view = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")
    util = (ROOT / "frontend/src/utils/processTimeline.ts").read_text(encoding="utf-8")

    assert "options?: { clone?: boolean }" in util
    assert "cloneTimelineItemsForMerge" in util
    assert "cloneTimelineLogItemsForMerge" in util

    assert "mergeTimelineLogs(props.timeline, props.logs, { clone: true })" in view
    assert "uiNodeOverrides" in view
    assert "applyUiOverrides(" in view
    assert "reasoningHtmlCache" in view
    assert "isSubagentReasoningLog(subStep)" in view
    assert "isSubagentReasoningLog(child)" in view
    assert "isSubagentReasoningLog(item)" in view
    assert "markOriginalNodeUserToggled" not in view
    assert "item.userToggled = true" not in view


def test_both_chat_surfaces_throttle_reasoning_and_cache_hot_helpers():
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    badges = (ROOT / "frontend/src/utils/skillFlowBadges.ts").read_text(encoding="utf-8")

    for label, source in (("EmbedChat.vue", embed), ("AgentDebug.vue", debug)):
        assert "REASONING_FLUSH_INTERVAL_MS" in source, f"{label} 缺少思考节流间隔常量"
        assert "flushReasoningBuffer" in source, f"{label} 缺少 flushReasoningBuffer"
        assert "handleBufferedReasoningEvent" in source, f"{label} 缺少 handleBufferedReasoningEvent"
        assert "streamBodyCache = new WeakMap" in source, f"{label} 缺少 visibleStreamBody WeakMap 缓存"
        assert "skillFlowBadgeCache = new WeakMap" in source, f"{label} 缺少技能徽章 WeakMap 缓存"
        assert "logSnapshots" not in source, f"{label} 不应保留每帧全量 logSnapshots 校验"

    assert "for (const log of logs)" in badges
    assert "enabledBadges.push" in badges and "candidateBadges.push" in badges

    assert "ticks % 6 === 0" in debug
    assert "ticks % 20 === 0" in debug
    assert "}, 500);" in debug
    assert "frameProcessed % 20 === 0" in debug
    assert "requestAnimationFrame(flushContentBuffer)" in debug


def test_dashboard_router_and_main_handle_chunk_errors_and_suspense_failures():
    router = (ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")
    main = (ROOT / "frontend/src/main.ts").read_text(encoding="utf-8")
    dashboard = (ROOT / "frontend/src/views/Dashboard.vue").read_text(encoding="utf-8")
    recovery = (ROOT / "frontend/src/utils/chunkLoadRecovery.ts").read_text(encoding="utf-8")

    assert "isDynamicImportChunkError" in recovery
    assert "recoverFromChunkError" in recovery
    assert "onChunkRecoveryFallback" in recovery
    assert "router.onError" in router
    assert "notifyChunkRecoveryFallback" in router
    assert "clearChunkRecoveryFallback" in router
    assert "vite:preloadError" in main
    assert "onErrorCaptured" in dashboard
    assert "onChunkRecoveryFallback" in dashboard
    assert "retryingRouteView" in dashboard
    assert "routeRenderError" in dashboard
    assert "reloadBrowserPage" in dashboard


def test_backend_snapshot_preserves_subagent_reasoning_flag_and_isolates_container():
    snapshot = (
        ROOT / "app/services/ai/runtime/agentscope/process_timeline_snapshot.py"
    ).read_text(encoding="utf-8")

    assert "def _is_subagent_reasoning_log" in snapshot
    assert 'if item.get("reasoning") is True:\n        copied["reasoning"] = True' in snapshot
