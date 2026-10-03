from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


ROOT = Path(__file__).resolve().parents[2]


def test_browser_panel_contains_same_origin_viewer_and_manual_input_contract():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    assert "WebSocket" in source
    assert "mouse_click" in source
    assert "mouse_down" in source
    assert "mouse_move" in source
    assert "mouse_up" in source
    assert "@pointerdown" in source
    assert "@pointermove" in source
    assert "@pointerup" in source
    assert "screenshot_ref" in source
    assert "autopilot" in source
    assert "defineModel<boolean>('pinned'" in source
    assert "panelWidth" in source
    assert "startResize" in source
    assert "左右拖拽调整浏览器宽度" in source


def test_browser_panel_exposes_clear_guarded_status_and_dismissible_notice():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    assert "showSafetyNotice" in source
    assert "安全确认已开启" in source
    assert "关闭安全提示" in source
    assert "approvalMode === 'guarded'" in source
    assert "watch(() => props.approvalMode" in source
    assert "approvalStatusLabel" not in source
    assert "approvalStatusClass" not in source


def test_browser_panel_explains_screenshot_surface_and_hides_internal_targets():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    assert "远程页面截图" in source
    assert "不是网页本体" in source
    assert "点击、滚轮、键盘会转发到远程浏览器" in source
    assert "每 5 秒自动刷新" in source
    # 人工接管 / 验证码 / 手动暂停等"刷新暂停"状态下必须保留手动拉取一次画面的入口
    assert "⟳ 刷新画面" in source
    assert "controlOwner === 'human' || captchaDetected || autoRefreshPaused" in source
    assert 'v-for="element in snapshot.elements"' not in source
    assert 'ref="viewportRef"' in source
    assert "viewportRef.value?.focus" in source


def test_browser_panel_shows_captcha_solving_hud_on_screenshot():
    """AI 解算验证码时必须在截图画面上有可见效果，而不只是顶部一行小字。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "isSolvingCaptcha" in source
    assert "captcha-solve-scanline" in source
    assert "captcha-solve-ring" in source
    assert "AI 正在识别验证码" in source
    assert "@keyframes captcha-scan" in source
    # 浮层不得拦截用户操作，用户要能随时人工接管
    assert "pointer-events-none absolute inset-0 z-20" in source


def test_browser_panel_shows_local_drag_trail_for_manual_drag():
    """人工拖拽必须具备本地轨迹与位移反馈：截图是静态的，否则用户只能凭感觉估位置。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "dragTrailPath" in source
    assert "dragDelta" in source
    assert "Δx" in source
    assert "clearDragTrail" in source
    # 轨迹层同样不得拦截操作
    assert "pointer-events-none absolute inset-0 z-20 h-full w-full" in source


def test_browser_panel_shows_red_notice_on_screenshot_surface():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    assert "当前为远程静态截图，非实时网页（操作存在延迟）· 严禁用于任何违法违规行为" in source
    assert "pointer-events-none" in source
    assert "text-slate-400" in source
    notice_class = 'class="pointer-events-none absolute bottom-3 left-3 z-10"'
    image_wrapper = '<div v-if="screenshotUrl" class="relative">'
    assert notice_class in source
    assert source.index(notice_class) < source.index(image_wrapper)
    assert 'class="pointer-events-none absolute inset-x-0 top-2 z-10 flex justify-center px-3"' not in source


def test_browser_panel_exposes_remote_focus_feedback_plain_manual_input_and_session_close():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    assert "remoteFocusMessage" in source
    assert "已聚焦远程页面" in source
    assert "lastClickStyle" not in source
    assert "naturalWidth" in source
    assert "naturalHeight" in source
    assert 'type="text"' in source
    assert 'type="password"' not in source
    assert "showManualInput" in source
    assert 'v-if="showManualInput"' in source
    assert "focused_input" in source
    assert "人工输入" in source
    assert "close-session" in source


def test_browser_panel_cache_busts_screenshot_when_snapshot_changes():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    assert "const screenshotUrl = computed" in source
    assert "snapshot_id=" in source
    assert ':src="screenshotUrl"' in source


def test_browser_panel_refresh_pause_is_manual_not_pointer_triggered():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    assert '@click="autoRefreshPaused ? resumeAutoRefresh() : pauseAutoRefresh()"' in source
    assert '@mouseenter="pauseAutoRefresh"' not in source
    assert '@focus="pauseAutoRefresh"' not in source


def test_browser_panel_exposes_human_handoff_refresh_and_captcha_state():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    assert "controlOwner" in source
    assert "当前由人工操作" in source
    assert "交还 AI" in source
    assert "interactionInProgress" in source
    assert "captchaDetected" in source
    assert "release_control" in source
    assert "controlOwner.value === 'human'" in source


def test_embed_chat_contains_browser_panel_toggle_and_session_binding():
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    assert "BrowserPanel" in source
    assert "/api/v1/chat/browser/sessions/open" in source
    assert "browser_session_id" in source
    assert "browserViewerToken" in source
    assert "browser_session" in source
    assert "v-model:pinned=\"browserPinned\"" in source
    assert "v-model:panel-width=\"browserPanelWidthReactive\"" in source
    assert 'const browserApprovalMode = ref<BrowserApprovalMode>("autopilot")' in source
    assert '@close-session="closeBrowserSession"' in source
    assert "const closeBrowserSession = async" in source
    assert "axios.delete" in source


def test_browser_panel_opens_immediately_with_loading_stages_and_reuses_viewer_session():
    panel = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "loading" in panel
    assert "正在准备服务端浏览器" in panel
    assert "正在连接实时画面" in panel
    assert "snapshotRequestInFlight" in panel
    assert "if (snapshotRequestInFlight.value) return" in panel
    assert "browserPanelOpening" in embed
    assert "browserPanelVisible.value = true" in embed
    assert "browserSessionId.value && browserViewerToken.value" in embed


def test_browser_panel_keeps_open_and_exposes_environment_retry_after_open_failure():
    panel = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "environmentError?: string | null" in panel
    assert "environmentError" in panel
    assert "envInfo.value?.status === 'missing_driver'" in panel
    assert "(event: 'retry'): void;" in panel
    assert "@click=\"emit('retry')\"" in panel
    assert "installBrowserEnvironment" in panel
    assert "/api/v1/chat/browser/environment/install/stream" in panel
    assert "installLogs" in panel
    assert "text/event-stream" in panel
    assert "管理员一键安装" in panel
    assert "authToken?: string" in panel
    assert ':auth-token="config.token"' in embed
    # 凭据改由父级 authToken（EmbedChat 的 config.token）或同源 HttpOnly Cookie 承载，
    # 不再从 localStorage 兜底读取。
    assert "const token = props.authToken;" in panel
    assert "localStorage.getItem('yovole_token')" not in panel
    assert "browserEnvironmentError" in embed
    assert ":environment-error=\"browserEnvironmentError\"" in embed
    assert '@retry="openBrowserPanel"' in embed
    # 环境未就绪由后端的 X-Browser-Error 标记判定，不再仅凭 503 就展示安装引导
    assert "environment_not_ready" in embed
    assert "x-browser-error" in embed
    assert "browserPanelVisible.value = true;" in embed.split("} catch", 1)[1].split("} finally", 1)[0]


def test_browser_panel_does_not_request_duplicate_initial_snapshot_and_reports_disconnect():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    on_open = source.split("client.onopen", 1)[1].split("client.onmessage", 1)[0]

    assert "client.send(JSON.stringify({ type: 'snapshot' }));" not in on_open
    assert "if (socket.value !== client) return;" in on_open
    assert "BROWSER_PANEL_REFRESH_INTERVAL_MS = 5000" in source
    # 轮询间隔改为自适应：空闲 5s，AI 有动作时临时提频。间隔统一由 currentPollIntervalMs() 决定，
    # 因此不再直接以固定常量建定时器。
    assert "setInterval(requestSnapshot, currentPollIntervalMs())" in source
    assert "浏览器连接已断开" in source


def test_browser_panel_ignores_stale_socket_events_and_token_attachment_respects_open_generation():
    panel = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "if (socket.value !== client) return" in panel
    assert "openingGeneration?: number" in embed
    assert "openingGeneration !== undefined && openingGeneration !== browserOpenGeneration" in embed
    assert "const openingGeneration = browserOpenGeneration;" in embed
    # session_id 现在在传入 attachBrowserSession 前先 trim 归一化。
    assert "String(data.session_id || \"\").trim()" in embed
    assert "data.approval_mode," in embed
    assert "openingGeneration," in embed


def test_browser_panel_refreshes_after_ai_browser_action_without_short_polling():
    panel = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "refreshSignal?: number" in panel
    assert "watch(() => props.refreshSignal" in panel
    assert ":refresh-signal=\"browserRefreshSignal\"" in embed
    assert "data.type === \"browser_refresh\"" in embed


def test_browser_refresh_event_auto_opens_panel_if_closed():
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    assert "data.type === \"browser_refresh\"" in embed
    assert "!browserPanelVisible.value || browserSessionId.value !== targetSessionId" in embed
    assert "attachBrowserSession(" in embed


def test_browser_panel_normalizes_protocol_less_navigation_addresses():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "const normalizeNavigationUrl" in source
    assert "https://${value}" in source
    assert "if (/^https?:\\/\\//i.test(value)) return value;" in source
    assert "const value = normalizeNavigationUrl(address.value);" in source
    assert "address.value = value;" in source


def test_browser_panel_interactive_enhancements_contract():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    # 1. 点击波纹动效
    assert "ripples" in source
    assert "addRipple" in source
    assert "animate-ping" in source

    # 2. 实时鼠标坐标展示
    assert "cursorCoords" in source
    assert "handleImagePointerLeave" in source
    assert "cursorCoords.x" in source
    assert "cursorCoords.y" in source

    # 3. 操作同步中状态指示
    assert "isSyncing" in source
    assert "triggerSyncing" in source
    assert "同步操作中…" in source

    # 4. 适合宽度 / 1:1 原图模式切换
    assert "viewMode" in source
    assert "适合窗口" in source
    assert "1:1 原图" in source
    assert "w-[1280px]" in source

    # 5. 标准导航按钮：后退、前进、刷新及可用状态智能判断
    assert "goBack" in source
    assert "goForward" in source
    assert "reloadPage" in source
    assert "can_go_back" in source
    assert "can_go_forward" in source
    assert "title=\"后退\"" in source
    assert "title=\"前进\"" in source
    assert "title=\"刷新页面\"" in source
    assert "go_back" in source
    assert "go_forward" in source
    assert "reload" in source

    # 6. 底部常驻快捷键工具栏
    assert "quickKeys" in source
    assert "sendQuickKey" in source
    assert "快捷键" in source


def test_browser_panel_crop_and_visual_analysis_contract():
    panel_source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    embed_source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    # 1. 框选模式开关与状态
    assert "cropMode" in panel_source
    assert "toggleCropMode" in panel_source
    assert "activeCropRect" in panel_source
    assert "区域分析" in panel_source

    # 2. Canvas 裁剪与操作卡片
    assert "cropDataUrl" in panel_source
    assert "showCropCard" in panel_source
    assert "copyCropImage" in panel_source
    assert "downloadCropImage" in panel_source
    assert "askAiWithCrop" in panel_source
    assert "ask-ai-crop" in panel_source

    # 3. EmbedChat 联动
    assert "@ask-ai-crop=\"handleBrowserCropAskAi\"" in embed_source
    assert "handleBrowserCropAskAi" in embed_source


def test_browser_panel_multi_tabs_contract():
    panel_source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    server_source = (ROOT / "app/api/v1/endpoints/browser.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "app/services/ai/browser/browser_runtime.py").read_text(encoding="utf-8")

    # 1. 前端多标签页栏 (Tab Bar) 状态与交互
    assert "tabs" in panel_source
    assert "BrowserTab" in panel_source
    assert "switchTab" in panel_source
    assert "closeTab" in panel_source
    assert "newTab" in panel_source
    assert "switch_tab" in panel_source
    assert "close_tab" in panel_source
    assert "new_tab" in panel_source

    # 3. 标签页右键菜单与批量管理 (关闭其他/关闭右侧/关闭所有)
    assert "tabContextMenu" in panel_source
    assert "openTabContextMenu" in panel_source
    assert "closeOtherTabs" in panel_source
    assert "closeTabsToRight" in panel_source
    assert "closeAllTabs" in panel_source
    assert "关闭其他标签页" in panel_source
    assert "关闭右侧标签页" in panel_source
    assert "关闭所有标签页" in panel_source
    assert "browser_runtime.close_other_tabs" in server_source
    assert "browser_runtime.close_tabs_to_right" in server_source
    assert "browser_runtime.close_all_tabs" in server_source
    assert "async def close_other_tabs" in runtime_source
    assert "async def close_tabs_to_right" in runtime_source
    assert "async def close_all_tabs" in runtime_source


def test_browser_panel_ai_action_status_contract():
    panel_source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    server_source = (ROOT / "app/api/v1/endpoints/browser.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "app/services/ai/browser/browser_runtime.py").read_text(encoding="utf-8")

    # 1. 前端 AI 细化动作状态与配置
    assert "currentAiAction" in panel_source
    assert "AI_ACTION_CONFIG" in panel_source
    assert "aiActionInfo" in panel_source
    assert "ai_action" in panel_source
    assert "AI 正在点击" in panel_source
    assert "AI 正在输入内容" in panel_source
    assert "AI 正在读取屏幕" in panel_source
    # 2. 服务端 AI 动作广播与订阅
    assert "subscribe_events" in runtime_source
    assert "set_ai_action" in runtime_source
    assert "clear_ai_action" in runtime_source
    assert "broadcast_event" in runtime_source
    assert "_forward_runtime_events" in server_source

    # 3. 前端人工操作细化动作状态与配置
    assert "currentHumanAction" in panel_source
    assert "HUMAN_ACTION_CONFIG" in panel_source
    assert "humanActionInfo" in panel_source
    assert "setHumanAction" in panel_source
    assert "人工点击" in panel_source
    assert "人工滚动" in panel_source
    assert "人工按键" in panel_source
    assert "人工输入" in panel_source
    assert "人工导航" in panel_source
    assert "人工切换标签" in panel_source
    assert "人工拖拽" in panel_source


def test_browser_panel_element_hover_inspector_contract():
    panel_source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    worker_source = (ROOT / "app/services/ai/browser/browser_worker.py").read_text(encoding="utf-8")
    schema_source = (ROOT / "app/schemas/browser.py").read_text(encoding="utf-8")

    # 1. 后端 schema 与 worker 坐标边界提取
    assert "bbox: Optional[dict[str, Any]] = None" in schema_source
    assert "tag: Optional[str] = None" in schema_source
    assert "bbox" in worker_source
    assert "tag: tagName" in worker_source

    # 2. 前端元素悬停命中测试与高亮徽标
    assert "hoveredElement" in panel_source
    assert "hoveredElementStyle" in panel_source
    assert "cursorCoords" in panel_source
    assert "Element Hover Inspector" in panel_source
    assert "hoveredElement.role || hoveredElement.tag" in panel_source


def test_browser_panel_install_logs_auto_scroll():
    panel_source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert 'ref="installLogsRef"' in panel_source
    assert "const installLogsRef = ref<HTMLPreElement | null>(null);" in panel_source
    assert "scrollInstallLogsToBottom" in panel_source
    assert "installLogsRef.value.scrollTop = installLogsRef.value.scrollHeight" in panel_source
    assert "watch(" in panel_source


def test_browser_panel_auth_credentials_contract():
    chat_source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "const hasValidAuthCredentials = (): boolean =>" in chat_source
    assert "Boolean(config.token || hasPermission.value || accountInfo.value || currentUser.value)" in chat_source
    assert "!hasValidAuthCredentials()" in chat_source
    assert "browserEnvironmentError.value = detail ||" in chat_source



def test_browser_panel_renders_captcha_plan_replay_and_failure_notice():
    """AI 解算过程必须可视化：识别到的目标/距离/轨迹，以及失败后的人工接手提示。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    # AI 的"解算计划"：缺口目标点 + 滑块起点 + 需拖动距离
    assert "captchaTargetPct" in source
    assert "captchaSliderPct" in source
    assert "需拖动" in source
    # AI 实际轨迹回放
    assert "captchaTrailReveal" in source
    assert "captchaTrailPath" in source
    # 解算计时：避免"卡着不动"时无从判断
    assert "已用时" in source
    # 失败后的人工接手提示：卡片 + HUD 转红
    assert "captchaFailed" in source
    assert "AI 无法完成验证码" in source
    assert "captcha_human_required" in source
    # 解算进度不能被普通快照刷新清掉
    assert "isCaptchaAction" in source


def test_browser_panel_puts_end_session_into_close_menu():
    """结束会话入口收进 ✕ 下拉菜单，并讲清"关闭面板"与"销毁会话"的区别。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "closeMenuOpen" in source
    assert "closeSessionIntent" in source
    assert "openCloseSessionConfirm" in source
    assert "结束会话（保留登录）" in source
    assert "结束会话并清除登录数据" in source
    # 单击 ✕ 依然只是关闭面板
    assert "emit('close')" in source
    # 必须明确提示"关闭 ≠ 结束会话"
    assert "会话继续运行" in source


def test_browser_panel_releases_captcha_failure_notice_after_takeover():
    """红色「AI 无法完成验证码」提示不能在人工接管后一直挂着。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "clearResolvedCaptchaState" in source
    # 用户一旦开始人工操作即收起警示卡片
    assert "captchaFailureDismissed.value = true" in source


def test_browser_panel_clears_captcha_state_when_page_recovers():
    """页面恢复正常（page_state 不再是 captcha）时必须清掉验证码状态与红色提示。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "else if (captchaDetected.value || isCaptchaAction.value)" in source
    assert "clearResolvedCaptchaState();" in source


def test_browser_panel_downgrades_captcha_hud_after_human_takeover():
    """人工接手后 HUD 不应继续是红色警示，而应退化为中性的"请人工完成验证"。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "captchaAwaitingHuman" in source
    assert "请人工完成验证" in source
    assert "ring-amber-400/80" in source


def test_browser_panel_probes_captcha_result_after_human_action():
    """人工拖完滑块后应自动追几帧结果，而不是逼用户手动刷新。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "startCaptchaResultProbe" in source
    assert "stopCaptchaResultProbe" in source
    assert "CAPTCHA_RESULT_PROBE_MAX_ATTEMPTS" in source


def test_browser_panel_hides_solving_hud_when_human_takes_over():
    """用户一旦接管，界面必须立刻收起「AI 正在识别验证码」，不等后端往返。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "用户已接管：立刻收起" in source
    assert "const pauseForInteraction" in source


def test_browser_panel_env_notice_requires_backend_flag():
    """只有后端明确标记"环境未就绪"时才展示安装引导；普通 503 必须按真实错误提示。"""
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "environment_not_ready" in source
    assert "x-browser-error" in source
    # 不能再仅凭 503 就把任何错误当成"运行环境未就绪"
    assert "status === 503 ||" not in source


def test_browser_panel_allows_dismissing_captcha_hud():
    """解算/失败提示会盖在验证码上：必须支持收起，并在用户开始操作时自动收起。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "captchaHudDismissed" in source
    assert 'aria-label="收起验证码提示"' in source
    assert '@click.stop="captchaHudDismissed = true"' in source
    # 人工接管阶段不再重复长文案（原因已由失败卡片给过），避免底部条遮挡
    assert 'v-if="!captchaAwaitingHuman"' in source


def test_browser_panel_renders_operation_trace_timeline():
    """过程时间线必须在面板里可见：随阶段变化的 HUD 文案 + 可展开的操作记录。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "actionTrace" in source
    assert "'captcha_trace'" in source
    assert "操作过程" in source
    assert "captchaStageLabel" in source


def test_browser_panel_records_ai_actions_into_operation_trace():
    """AI 的每个浏览器动作都要进入操作时间线。

    回归背景：后端一直在广播 ai_action（navigating / clicking / filling / scrolling …），
    但前端只把它当「当前状态」覆盖式赋值，历史动作全部丢失，
    时间线里因此只有验证码解算步骤。
    """
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    branch = source.split("payload.type === 'ai_action'", 1)[1][:1200]
    assert "pushActionTrace" in branch, "ai_action 事件必须追加到时间线，而不是只覆盖当前状态"
    assert "source: 'ai'" in branch


def test_browser_panel_records_human_actions_into_operation_trace():
    """人工操作也要进入同一条时间线，便于对照「AI 做了什么、人做了什么」。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    body = source.split("const setHumanAction", 1)[1][:1200]
    assert "pushActionTrace" in body, "人工动作必须追加到时间线"
    assert "source: 'human'" in body


def test_browser_panel_operation_trace_marks_all_three_sources():
    """时间线要能区分来源：AI 动作、人工操作、验证码解算。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "source: 'captcha'" in source
    assert "source: 'ai'" in source
    assert "source: 'human'" in source
    assert "ACTION_SOURCE_META" in source, "三种来源需要有各自的图标与配色，否则时间线读不出谁做的"


def test_browser_panel_draws_horizontal_drag_line_not_diagonal_to_target():
    """拖动示线必须是水平的：斜着连到缺口坐标会让人误以为 AI 在斜着拖。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    assert "captchaDragEndPct" in source
    assert ':x2="captchaDragEndPct.x"' in source
    assert ':y2="captchaDragEndPct.y"' in source
    assert ':y2="captchaTargetPct.y"' not in source, "不得再把拖动示线直接连到缺口坐标"


def test_browser_panel_hides_pointless_attempt_counter_when_single_attempt():
    """收手策略下只自动尝试一次：不能给用户显示「1/1」这种没有信息量的计数。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    assert "Number(extra.max_attempts) > 1" in source


def test_browser_open_conflict_message_is_not_duplicated():
    """409 文案不得重复「请稍等几秒」：异常消息本身已是完整的用户引导。"""
    source = (ROOT / "app/api/v1/endpoints/browser.py").read_text(encoding="utf-8")
    assert "验证码识别完成后会自动恢复，请稍等几秒再试" not in source
    assert "detail=str(exc)" in source


def test_embed_chat_auto_retries_browser_open_on_conflict():
    """409 是「几秒后自愈」的忙碌冲突：前端应自动重开面板，而不是让用户手动再点一次。"""
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    assert "browserOpenConflictRetries" in source
    assert "status === 409" in source


def test_browser_panel_resumes_auto_refresh_after_human_interaction():
    """人工操作结束后必须恢复自动刷新。

    回归背景（用户反馈：双击链接跳转后画面不更新，要手动点刷新）：
    ``pauseForInteraction`` 会 ``stopPolling``，而 ``finishInteraction`` 从不重启它；
    即便有人调用 ``startPolling``，它又被 ``controlOwner === 'human'`` 拦着；
    再叠加 ``control_state`` 事件分支里的 ``stopPolling``，形成双重封锁——
    人工双击跳转后画面永远停在旧截图。
    """
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    start_polling = source.split("const startPolling = () => {", 1)[1][:400]
    assert "controlOwner.value === 'human'" not in start_polling, (
        "人工接管空闲时也应刷新；真正该暂停的只是正在操作的那几秒（interactionInProgress）"
    )

    finish = source.split("const finishInteraction = () => {", 1)[1][:700]
    assert "startPolling()" in finish, "操作结束后必须恢复自动刷新，否则只能手动点刷新"


def test_browser_panel_refreshes_once_after_non_captcha_interaction():
    """跳转、提交表单这类非验证码操作，结束后要立刻补一帧，不能干等下一个轮询周期。"""
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "scheduleInteractionSnapshot" in source, "非验证码操作需要一个延迟补帧的调度函数"
    finish = source.split("const finishInteraction = () => {", 1)[1][:700]
    assert "scheduleInteractionSnapshot()" in finish


def test_browser_panel_honours_external_refresh_signal_during_human_control():
    """人工接管期间也要响应外部刷新信号。

    refreshSignal 代表「远程页面已经变了」（AI 工具调用完成等），
    与轮询同理，人工空闲时应照常取帧，只有正在操作的那几秒需要跳过。
    """
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    block = source.split("watch(() => props.refreshSignal", 1)[1][:320]
    assert "controlOwner.value === 'human'" not in block, (
        "外部刷新信号不应因人工接管而被丢弃"
    )
    assert "requestSnapshot()" in block


def test_browser_panel_pauses_polling_while_tab_hidden():
    """标签页切到后台时必须停轮询。

    面板此前无论可见性都每 5 秒跑一次完整快照 + 截图：没人看画面，服务端却在
    反复做全量 DOM 抓取并往磁盘写帧。切回前台要立刻补一帧，否则用户看到的是
    切走之前那张旧图，还得再等满一个周期。
    """
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "const handleVisibilityChange = () =>" in source
    assert "document.addEventListener('visibilitychange', handleVisibilityChange)" in source
    assert "document.removeEventListener('visibilitychange', handleVisibilityChange)" in source, (
        "卸载时必须摘掉监听，否则组件反复挂载会累积回调"
    )
    assert "pageHidden = typeof document !== 'undefined' && document.hidden;" in source

    hidden_block = source.split("const handleVisibilityChange = () => {", 1)[1].split("\n};", 1)[0]
    assert "stopPolling();" in hidden_block, "隐藏时必须停轮询"
    assert "requestSnapshot();" in hidden_block, "回到前台必须补一帧"
    # startPolling 自身也要挡住后台取帧：其他路径（control_state / captcha 恢复等）都会调它
    start_polling = source.split("const startPolling = () => {", 1)[1].split("\n};", 1)[0]
    assert "if (pageHidden) return;" in start_polling


def test_browser_panel_uses_active_burst_interval_after_ai_actions():
    """AI 一动就把画面节奏提上来，但必须是有界窗口，且不得反复重建定时器。

    固定 5 秒最尴尬的地方是"最该看的那一刻反而最慢"：AI 刚点击/跳转，用户正盯着
    面板，却要等满一整个周期。这里在 ai_action 后开一个 10 秒活跃窗口、间隔降到
    1.2 秒，窗口结束自动降回 5 秒。
    """
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")

    assert "const BROWSER_PANEL_ACTIVE_INTERVAL_MS = 1200;" in source
    assert "const BROWSER_PANEL_ACTIVE_BURST_MS = 10000;" in source
    assert "const currentPollIntervalMs = () =>" in source
    assert "const enterActiveBurst = () =>" in source
    assert "const exitActiveBurst = () =>" in source
    # ai_action 是"AI 正在操作"的信号，必须由它触发活跃窗口
    ai_action_block = source.split("} else if (payload.type === 'ai_action') {", 1)[1][:400]
    assert "enterActiveBurst();" in ai_action_block
    # 活跃窗口内不重建定时器：否则密集的 ai_action 会把间隔无限重置，一帧都不刷新
    enter_block = source.split("const enterActiveBurst = () => {", 1)[1].split("\n};", 1)[0]
    assert "const wasActive = Date.now() < activeBurstUntil;" in enter_block
    assert "if (!wasActive && pollTimer) startPolling();" in enter_block
    # 窗口结束降回空闲节奏
    exit_block = source.split("const exitActiveBurst = () => {", 1)[1].split("\n};", 1)[0]
    assert "activeBurstUntil = 0;" in exit_block
    assert "if (pollTimer) startPolling();" in exit_block
    # 断开与卸载都要清掉活跃定时器，避免泄漏
    assert "clearTimeout(activeBurstTimer);" in source
    assert source.count("clearTimeout(activeBurstTimer);") >= 2, (
        "stopPolling 路径与 closeSocket/卸载路径都应清理活跃窗口定时器"
    )
