"""沙箱自动预热完成提示：不用 toast，改成指向输入框浮标的气泡。

toast 是屏幕顶部居中的一次性提示，用户看完不知道沙箱状态该去哪里看；改成挂在
上下文用量浮标上的气泡后，尖角直接指向「点这里查看运行状态」的入口。
"""
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
COMPOSABLE = ROOT / "frontend/src/composables/chat/useSandboxWorkspace.ts"
CHAT_INPUT = ROOT / "frontend/src/components/embed/ChatInput.vue"


def test_auto_warm_success_raises_hint_instead_of_toast():
    source = COMPOSABLE.read_text(encoding="utf-8")
    auto_warm = source.split("const maybeAutoWarmSandbox", 1)[1].split("const ensureSandboxWorkspace", 1)[0]

    # 自动预热成功不再弹 toast
    assert 'showToast("沙箱环境已预热就绪"' not in auto_warm
    assert "showSandboxWarmHint();" in auto_warm
    # 还在创建时不打扰，轮询到 running 再出气泡
    assert 'showToast("正在预热沙箱运行环境…"' not in auto_warm
    assert "readyToast: false" in auto_warm
    assert "onReady: showSandboxWarmHint" in auto_warm


def test_hint_auto_dismisses_after_three_seconds_and_can_be_closed():
    source = COMPOSABLE.read_text(encoding="utf-8")

    assert "const sandboxWarmHintVisible = ref(false)" in source
    assert "setTimeout(dismissSandboxWarmHint, 3000)" in source
    # 关闭/重新弹出都要清掉旧定时器，否则第二次预热会被上一次的计时器提前收掉
    dismiss_body = source.split("const dismissSandboxWarmHint", 1)[1].split("const showSandboxWarmHint", 1)[0]
    assert "clearTimeout(sandboxWarmHintTimer)" in dismiss_body
    show_body = source.split("const showSandboxWarmHint", 1)[1].split("const maybeAutoWarmSandbox", 1)[0]
    assert "dismissSandboxWarmHint();" in show_body
    # 卸载时清定时器
    assert "if (sandboxWarmHintTimer) {" in source


def test_poll_exposes_on_ready_callback():
    source = COMPOSABLE.read_text(encoding="utf-8")

    assert "onReady?: () => void" in source
    assert 'const { readyToast = true, readyMessage = "沙箱已就绪", onReady, timeoutToast = readyToast } = opts;' in source
    assert "onReady?.();" in source


def test_composable_exports_hint_state():
    source = COMPOSABLE.read_text(encoding="utf-8")

    assert "sandboxWarmHintVisible,\n    sandboxWarmHintTitle,\n    sandboxWarmHintSubtitle,\n    showSandboxWarmHint,\n    dismissSandboxWarmHint," in source


def test_chat_input_renders_hint_pointing_at_usage_badge():
    source = CHAT_INPUT.read_text(encoding="utf-8")

    assert "sandboxWarmHintVisible?: boolean" in source
    assert "(e: 'dismiss-sandbox-warm-hint'): void" in source
    assert 'data-testid="sandbox-warm-hint"' in source
    # 文案参数化（自动预热与 Bash 按需拉起各用一套），默认值仍是预热文案
    assert 'sandboxWarmHintTitle: "沙箱已预热就绪"' in source
    assert 'sandboxWarmHintSubtitle: "点这里查看运行状态"' in source
    assert "{{ sandboxWarmHintTitle }}" in source
    assert "{{ sandboxWarmHintSubtitle }}" in source
    assert '@click.stop="toggleContextUsageDetails()"' in source
    assert '@click.stop="emit(\'dismiss-sandbox-warm-hint\')"' in source
    # 气泡挂在用量浮标上方，尖角朝下指向浮标
    hint_block = source.split('data-testid="sandbox-warm-hint"', 1)[1].split("data-testid=\"context-usage-indicator\"", 1)[0]
    assert "bottom-full right-0" in hint_block
    assert "-bottom-1.5 right-4" in hint_block


def test_both_chat_pages_wire_the_hint():
    for page in ("EmbedChat.vue", "AgentDebug.vue"):
        source = (ROOT / "frontend/src/views" / page).read_text(encoding="utf-8")
        assert "sandboxWarmHintVisible," in source, page
        assert "dismissSandboxWarmHint," in source, page
        assert ':sandbox-warm-hint-visible="sandboxWarmHintVisible"' in source, page
        assert '@dismiss-sandbox-warm-hint="dismissSandboxWarmHint"' in source, page


def test_mobile_falls_back_to_toast():
    """移动端空间有限：气泡会压住输入区，仍用顶部 toast。"""
    source = COMPOSABLE.read_text(encoding="utf-8")
    hint_body = source.split("const showSandboxWarmHint", 1)[1].split("const maybeAutoWarmSandbox", 1)[0]

    assert "if (preferToast?.value)" in hint_body
    assert 'const title = options?.title || "沙箱已预热就绪";' in hint_body
    assert 'showToast(options?.toastMessage || title, "success")' in hint_body
    assert "preferToast?: Ref<boolean>" in source

    for page in ("EmbedChat.vue", "AgentDebug.vue"):
        page_source = (ROOT / "frontend/src/views" / page).read_text(encoding="utf-8")
        # 与 ChatInput 的 isMobileViewport 同阈值
        assert "preferToast: computed(() => windowWidth.value < 640)" in page_source, page


def test_auto_warm_timeout_still_notifies():
    """成功时不打扰，但卡在创建中 60 秒必须让人知道，否则看起来像卡死。"""
    source = COMPOSABLE.read_text(encoding="utf-8")

    assert "timeoutToast?: boolean" in source
    assert "timeoutToast = readyToast" in source
    # 自动预热关掉成功 toast，但显式打开超时 toast
    auto_warm = source.split("const maybeAutoWarmSandbox", 1)[1].split("const ensureSandboxWorkspace", 1)[0]
    assert "timeoutToast: true" in auto_warm
    # 超时文案按后端区分，docker 不说 Pod
    assert '? "Pod 仍在创建中（可能镜像拉取较慢），可稍后点「刷新」查看"' in source
    assert ': "容器仍在启动中（可能镜像拉取较慢），可稍后点「刷新」查看"' in source


def test_bash_lazy_start_ready_uses_bubble_on_desktop():
    """关闭自动预热后执行 Bash：拉起完成在桌面端也走气泡，移动端仍 toast。"""
    page = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    # 原 toast 已替换为气泡调用
    assert 'showToast("沙箱环境已就绪，正在执行命令…", "success")' not in page
    assert 'toastMessage: "沙箱环境已就绪，正在执行命令…"' in page
    assert 'title: "沙箱已就绪"' in page
    assert 'subtitle: "正在执行命令，点这里查看运行状态"' in page
    # 拉起中/失败仍是 toast（过程与错误需要即时反馈）
    assert 'showToast("正在拉起沙箱运行环境…", "info")' in page
    assert 'showToast(data.error_reason || "沙箱环境启动失败", "error")' in page
