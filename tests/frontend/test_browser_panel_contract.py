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
    assert 'v-for="element in snapshot.elements"' not in source
    assert 'ref="viewportRef"' in source
    assert "viewportRef.value?.focus" in source


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
    assert "props.authToken || (typeof localStorage" in panel
    assert "browserEnvironmentError" in embed
    assert ":environment-error=\"browserEnvironmentError\"" in embed
    assert '@retry="openBrowserPanel"' in embed
    assert "error?.response?.status === 503" in embed
    assert "browserPanelVisible.value = true;" in embed.split("} catch", 1)[1].split("} finally", 1)[0]


def test_browser_panel_does_not_request_duplicate_initial_snapshot_and_reports_disconnect():
    source = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    on_open = source.split("client.onopen", 1)[1].split("client.onmessage", 1)[0]

    assert "client.send(JSON.stringify({ type: 'snapshot' }));" not in on_open
    assert "if (socket.value !== client) return;" in on_open
    assert "BROWSER_PANEL_REFRESH_INTERVAL_MS = 5000" in source
    assert "setInterval(requestSnapshot, BROWSER_PANEL_REFRESH_INTERVAL_MS)" in source
    assert "浏览器连接已断开" in source


def test_browser_panel_ignores_stale_socket_events_and_token_attachment_respects_open_generation():
    panel = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "if (socket.value !== client) return" in panel
    assert "openingGeneration?: number" in embed
    assert "openingGeneration !== undefined && openingGeneration !== browserOpenGeneration" in embed
    assert "const openingGeneration = browserOpenGeneration;" in embed
    assert "String(data.session_id || \"\")," in embed
    assert "data.approval_mode," in embed
    assert "openingGeneration," in embed


def test_browser_panel_refreshes_after_ai_browser_action_without_short_polling():
    panel = (ROOT / "frontend/src/components/embed/BrowserPanel.vue").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "refreshSignal?: number" in panel
    assert "watch(() => props.refreshSignal" in panel
    assert ":refresh-signal=\"browserRefreshSignal\"" in embed
    assert "data.type === \"browser_refresh\"" in embed


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

