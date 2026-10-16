import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
CANCEL_UTIL = ROOT / "frontend/src/utils/cancelConversationRun.ts"
ACTIVE_RUNS_COMPOSABLE = ROOT / "frontend/src/composables/chat/useActiveRuns.ts"
ACTIVE_RUNS_POPOVER = ROOT / "frontend/src/components/chat/ActiveRunsPopover.vue"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
CHAT_ENDPOINT = ROOT / "app/api/v1/endpoints/chat.py"


def test_cancel_util_exposes_detailed_result():
    """终止结果必须能区分「真取消」与「仅强释放」，否则文案只能含糊其辞。"""
    source = CANCEL_UTIL.read_text(encoding="utf-8")

    assert "cancelConversationRunDetailed" in source
    assert "runCancelled" in source
    assert "run_cancelled" in source
    # 既有调用方继续拿到 boolean，签名不能变
    assert "Promise<boolean>" in source


def test_active_runs_poll_interval_switches_with_panel_state():
    """面板打开时快轮询、关闭时慢轮询，隐藏页面由调用方停表。"""
    source = ACTIVE_RUNS_COMPOSABLE.read_text(encoding="utf-8")

    assert "OPEN_POLL_INTERVAL_MS = 3000" in source
    assert "IDLE_POLL_INTERVAL_MS = 15000" in source
    assert "setOpen" in source


def test_active_runs_refresh_failure_keeps_previous_items():
    """刷新失败必须保留上一次的数据：清零角标会让用户彻底失去「还有会话在跑」的感知。"""
    source = ACTIVE_RUNS_COMPOSABLE.read_text(encoding="utf-8")

    assert "failed" in source
    catch_block = re.search(r"catch\s*(?:\([^)]*\))?\s*\{([\s\S]*?)\}", source)
    assert catch_block, "composable 里必须有 catch 分支处理刷新失败"
    assert "items.value = []" not in catch_block.group(1)


def test_active_runs_popover_is_pure_renderer_with_expected_events():
    """浮层只负责渲染与抛事件，网络请求留在 EmbedChat，便于独立验证交互。"""
    source = ACTIVE_RUNS_POPOVER.read_text(encoding="utf-8")

    assert "defineEmits" in source
    for event in ("'select'", "'terminate'", "'refresh'", "'close'"):
        assert event in source, f"浮层缺少 {event} 事件"
    # 浮层自己不发请求
    assert "axios" not in source
    assert "/api/v1/chat/active-runs" not in source


def test_active_runs_popover_separates_stale_group_and_labels():
    """疑似僵死的条目要单独成组，且按钮文案为「强制释放」。"""
    source = ACTIVE_RUNS_POPOVER.read_text(encoding="utf-8")

    assert "疑似僵死" in source
    assert "强制释放" in source
    assert "当前没有进行中的会话" in source


def test_embed_chat_wires_active_runs_entry_and_termination():
    """入口、标题映射与终止分支都必须接上，且终止当前会话走 stopGeneration。"""
    source = EMBED_CHAT.read_text(encoding="utf-8")

    assert "ActiveRunsPopover" in source
    assert "/api/v1/chat/active-runs" in source
    assert "createActiveRunsController" in source
    assert "handleActiveRunTerminate" in source
    assert "cancelConversationRunDetailed" in source
    assert "stopGeneration({ notifySuccess: true });\n    return;" in source
    # 移动端入口不能漏
    assert "runHeaderMoreActiveRuns" in source


def test_switching_conversation_resets_remote_run_status():
    """切会话必须先清掉上一轮的「输出已完成」标记。

    outputCompleted 只在轮询到 active=false 时复位；若它从上一个会话残留下来，
    remoteRunBusy 会被压制，切进一个仍在运行的会话时既没有「正在处理」也没有
    停止按钮。换身份/清空会话路径已经修过同一个坑，普通切换路径此前漏了。
    """
    source = EMBED_CHAT.read_text(encoding="utf-8")
    match = re.search(r"const handleHistoryClick = async \(item: any\) => \{([\s\S]*?)\n\};", source)
    assert match, "未找到 handleHistoryClick 定义"
    body = match.group(1)

    assert "resetRunStatus();" in body, "handleHistoryClick 未重置远程运行态"
    assert "conversationId.value = target;" in body


def test_backend_exposes_active_runs_route_without_shadowing_conversation_route():
    """接口必须是 /chat/active-runs：/conversation/active-runs 会被 {conversation_id} 抢先匹配。"""
    source = CHAT_ENDPOINT.read_text(encoding="utf-8")

    assert '"/active-runs"' in source
    assert '"/conversation/active-runs"' not in source
    assert "def list_active_conversation_runs" in source
    assert "class ActiveConversationRunsResponse" in source


def test_lane_list_active_is_read_only():
    """列表实现必须是纯只读：不能顺手删锁或改锁。"""
    lane_source = (ROOT / "app/services/ai/runtime/session_run_lane.py").read_text(encoding="utf-8")
    match = re.search(r"async def list_active\(([\s\S]*?)\n    async def ", lane_source)
    assert match, "未找到 list_active 实现"
    body = match.group(1)

    assert "scan_iter" in body
    assert "force_release" not in body
    assert "await redis.set(" not in body
    assert "delete" not in body


def test_active_runs_stop_actually_halts_polling():
    """stop() 必须能真正停住轮询。

    只 clearTimeout 不够：若此刻正好有请求在飞，它的 finally 会重新 schedule，
    于是页面进入 hidden 后仍会继续每 15s 请求一次，与「hidden 暂停」的设计不符。
    """
    source = ACTIVE_RUNS_COMPOSABLE.read_text(encoding="utf-8")

    assert "stopped" in source, "缺少 stopped 标志，inflight 请求完成后会复活轮询"
    schedule_body = re.search(r"const schedule = \(\) => \{([\s\S]*?)\n  \}", source)
    assert schedule_body, "未找到 schedule 实现"
    assert "stopped" in schedule_body.group(1), "schedule 未检查 stopped"
    stop_body = re.search(r"const stop = \(\) => \{([\s\S]*?)\n  \}", source)
    assert stop_body, "未找到 stop 实现"
    assert "stopped = true" in stop_body.group(1), "stop() 未置 stopped"


def test_active_runs_resumes_polling_when_panel_reopens():
    """用户重新打开面板时必须恢复轮询，否则停表一次就再也不刷新了。"""
    source = ACTIVE_RUNS_COMPOSABLE.read_text(encoding="utf-8")
    set_open_body = re.search(r"const setOpen = \(value: boolean\) => \{([\s\S]*?)\n  \}", source)
    assert set_open_body, "未找到 setOpen 实现"

    assert "stopped = false" in set_open_body.group(1)


def test_cancel_util_marks_whether_backend_responded():
    """请求异常与「后端明确答复无锁可释放」必须区分。

    会话恰好自然结束时后端返回 success=false（无 handle、无锁可删），这不是失败；
    若把它当成失败，停止按钮会弹出「服务端未确认释放结果」的假警告。
    """
    source = CANCEL_UTIL.read_text(encoding="utf-8")

    assert "responded" in source
    # 兼容入口：拿到后端响应即视为已确认（无锁可释放=锁本就不被占用，目标已达成）
    assert "result.responded" in source


def test_embed_chat_binds_mobile_popover_and_visibility_pause():
    """移动端必须真的有浮层实例（只有入口按钮会出现「点了没反应」）；
    hidden 必须启动/暂停角标轮询。"""
    source = EMBED_CHAT.read_text(encoding="utf-8")

    assert 'variant="modal"' in source, "移动端缺少 modal 变体的浮层实例"
    assert re.search(r'<ActiveRunsPopover[\s\S]{0,600}?variant="modal"', source), (
        "variant=\"modal\" 没有用在 ActiveRunsPopover 上"
    )
    assert "stopActiveRunsWatch()" in source, "hidden 时未暂停角标轮询"
    assert "startActiveRunsWatch()" in source


def test_desktop_active_runs_entry_sits_next_to_subtitle():
    """入口放在副标题「准备就绪」后面（用户指定位置），且比头部其他按钮更小。"""
    source = EMBED_CHAT.read_text(encoding="utf-8")
    subtitle_at = source.index('class="text-[11px] text-gray-400')
    fullscreen_at = source.index("<!-- Fullscreen Button (desktop only) -->")
    assert subtitle_at < fullscreen_at
    block = source[subtitle_at:fullscreen_at]

    assert 'ref="activeRunsRef"' in block, "桌面入口不在副标题行内"
    assert "h-3.5 w-3.5" in block, "入口图标未缩小"
    assert "rounded-lg p-2" not in block, "入口仍用头部按钮的大尺寸"


def test_active_runs_popover_supports_left_alignment():
    """锚定在标题区左侧时浮层要向左对齐，否则会伸到视口外。"""
    source = ACTIVE_RUNS_POPOVER.read_text(encoding="utf-8")

    assert "align" in source
    assert "left-0" in source


def test_header_text_container_does_not_clip_the_popover():
    """入口挂进副标题行后，祖先的 overflow-hidden 会把浮层整块裁掉（点了像没反应）。"""
    source = EMBED_CHAT.read_text(encoding="utf-8")
    header_at = source.index("<!-- Dynamic Header Status (New) -->")
    subtitle_at = source.index('class="text-[11px] text-gray-400')
    assert header_at < subtitle_at

    # 只看 class 属性（注释里可能出现这个词，不该算数）
    classes = re.findall(r'class="([^"]*)"', source[header_at:subtitle_at])
    assert not any("overflow-hidden" in value for value in classes), (
        "标题区祖先带 overflow-hidden，会把锚定浮层裁掉"
    )
