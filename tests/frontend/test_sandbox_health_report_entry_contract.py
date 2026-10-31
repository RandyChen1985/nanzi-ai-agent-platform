"""沙箱「体检报告」入口：只在沙箱运行时出现，点击即发送预设任务。

需求：沙箱启动后，在状态区给一个入口，点了就让 AI 对沙箱做一次系统体检并产出
HTML 报告；沙箱没启动时不该出现——一堆点不动的入口只会误导用户。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT_INPUT = ROOT / "frontend/src/components/embed/ChatInput.vue"
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"


def _chat_input() -> str:
    return CHAT_INPUT.read_text(encoding="utf-8")


def _embed() -> str:
    return EMBED.read_text(encoding="utf-8")


def test_health_report_entry_lives_in_running_only_menu():
    """入口必须挂在「运行中才渲染」的沙箱操作菜单里。"""

    source = _chat_input()

    assert "体检报告" in source
    assert "emit('sandbox-health-report')" in source

    running_guard = "(sandboxWorkspaceStatus || 'idle') === 'running'"
    menu_index = source.index("体检报告")
    # 入口之前在最近的一个「运行中」判断，说明它处在该条件块内
    preceding_guard = source.rindex(running_guard, 0, menu_index)
    assert menu_index > preceding_guard


def test_health_report_emits_declared():
    """事件要在 emits 里声明，避免静默失效。"""

    source = _chat_input()

    assert "(e: 'sandbox-health-report'): void;" in source


def test_health_report_sends_preset_prompt():
    """页面要把事件接到发送链路，并带上预设任务。"""

    source = _embed()

    assert '@sandbox-health-report="sendSandboxHealthReport"' in source
    assert "const SANDBOX_HEALTH_REPORT_PROMPT" in source
    # 任务里要明确「HTML 报告」与「告诉我路径」，否则用户拿到一堆终端输出
    assert "HTML 报告" in source
    assert "告诉我路径" in source
    assert "void sendMessage({ content: SANDBOX_HEALTH_REPORT_PROMPT });" in source


def test_health_report_blocked_while_processing():
    """正在跑的时候不重复发送，避免连点堆叠多轮任务。"""

    source = _embed()

    assert "const sendSandboxHealthReport = () => {" in source
    assert "if (isProcessing.value || remoteRunBusy.value) return;" in source
