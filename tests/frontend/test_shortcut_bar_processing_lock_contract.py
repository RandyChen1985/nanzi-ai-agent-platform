"""Contract: 生成中快捷指令栏只锁「会撞会话锁」的指令。

`/new`（先弹二次确认再切换会话）与 `/history`（只开关历史侧栏）都不会把消息投进
正在运行的会话，所以必须在生成中保持可用；其余指令——尤其是用户自定义指令的
「填充 + 直接发送」——仍要挡住，避免撞上会话运行锁。
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "embed" / "ChatInput.vue"


def _source() -> str:
    return CHAT_INPUT.read_text(encoding="utf-8")


def test_shortcut_bar_is_not_blanket_disabled_while_processing():
    """整条栏不能再用一个 pointer-events-none 全锁：那会连 /new 与 /history 一起锁掉。"""
    source = _source()

    assert "'opacity-50 pointer-events-none select-none': isProcessing" not in source
    # 容器只保留 select-none，禁用下放到具体控件
    assert ":class=\"{ 'select-none': isProcessing }\"" in source


def test_processing_safe_shortcuts_are_new_conversation_and_history():
    """放行名单必须精确到这两个 id，不能写成「系统指令全放行」。"""
    source = _source()

    assert "PROCESSING_SAFE_SHORTCUT_IDS" in source
    assert 'new Set(["sys_clear", "sys_history"])' in source


def test_click_handler_lets_processing_safe_shortcuts_through():
    """模板放行不够：执行层原本无条件拦截，必须一起放行，否则点了没反应。"""
    source = _source()

    assert "if (!cmd) return;" in source
    assert "isInteractionLocked.value && !isShortcutAvailableWhileProcessing(cmd)" in source


def test_user_defined_shortcuts_stay_locked_while_processing():
    """用户自定义指令点一下就发送，生成中必须保持禁用。"""
    source = _source()

    user_row = source.split("visibleRowUserCommands")[-1]
    assert ":class=\"{ 'pointer-events-none opacity-40': isProcessing }\"" in user_row


def test_other_shortcut_bar_controls_stay_locked_while_processing():
    """折叠开关、「更多」抽屉、「新建快捷指令」在生成中仍要挡住。"""
    source = _source()

    # 左侧折叠开关 + 「+ 新建快捷指令」= 两处静态锁定
    assert source.count("'pointer-events-none opacity-50': isProcessing") == 2
    # 「更多」抽屉：与展开态样式互斥，不能两个 opacity 类同时挂
    assert (
        "isProcessing ? 'pointer-events-none opacity-50' : (isDrawerExpanded ? 'opacity-100' : '')"
        in source
    )
