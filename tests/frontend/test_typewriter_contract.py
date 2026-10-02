"""重置密码页顶部循环文案（打字机效果）的契约。

这行字是纯装饰，但它踩坑的方式都很安静：动画把布局顶得上下跳、对屏幕阅读器
反复播报半截句子、用户正好错过那一轮就再也没看到关键约束、页面跳转后定时器
还在跑。这些都不会在肉眼验收里暴露，所以逐条钉住。

文案内容本身也钉住：它与"找回密码"的业务措辞刻意无关（用户明确要求"契合 AIGC
智能体、可以文艺，不一定跟忘记密码有关"），若哪天有人把它改回业务提示，
这里会提醒他关键约束不该只存在于循环动画里。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESET = (ROOT / "frontend/src/views/ResetPassword.vue").read_text(encoding="utf-8")
COMPOSABLE_PATH = ROOT / "frontend/src/composables/useTypewriter.ts"
COMPOSABLE = COMPOSABLE_PATH.read_text(encoding="utf-8")

AMBIENT_BLOCK_START = "const AMBIENT_LINES"
AMBIENT_BLOCK = RESET[
    RESET.index(AMBIENT_BLOCK_START) : RESET.index("]", RESET.index(AMBIENT_BLOCK_START))
]


def test_composable_exists_and_is_wired_into_the_page():
    assert COMPOSABLE_PATH.exists(), "打字机逻辑应抽成 composable，而不是写进页面"
    assert "export function useTypewriter" in COMPOSABLE
    assert "useTypewriter(AMBIENT_LINES)" in RESET


def test_ambient_lines_are_the_agreed_set():
    for line in (
        "记忆只是暂时离线，重新对齐就好。",
        "上下文被截断的地方，正是重写的开始。",
        "每一次遗忘，都是一次重新训练。",
        "权重会被重置，意图始终清晰。",
    ):
        assert line in AMBIENT_BLOCK, f"缺少约定文案：{line}"


def test_ambient_line_sits_above_the_heading():
    """放在卡片顶部、大标题之上——用户指的就是那块留白。"""
    ambient = RESET.index("{{ ambientText }}")
    heading = RESET.index("{{ heading }}")
    assert ambient < heading, "顶部文案必须在标题之上"


def test_ambient_line_renders_once_for_both_modes():
    """两种模式共用同一处渲染：文案与模式无关，不该各写一份。"""
    assert RESET.count("{{ ambientText }}") == 1


def test_key_constraints_never_move_into_the_loop():
    """「15 分钟有效、只能用一次」必须留在静态说明里。

    循环动画每一轮都会清空重打，把关键约束放进去，用户正好错过那一轮就等于没提示过。
    """
    assert "重置链接有效期 15 分钟，且只能使用一次。" in RESET
    for leaked in ("15 分钟", "有效期", "邮箱", "密码"):
        assert leaked not in AMBIENT_BLOCK, f"关键信息「{leaked}」不该进循环文案"


def test_reduced_motion_falls_back_to_static_text():
    """前庭敏感用户不该被迫看一段永不停歇的逐字动画。"""
    assert "prefers-reduced-motion: reduce" in COMPOSABLE
    assert "matchMedia" in COMPOSABLE
    # 静止态要给完整一句，而不是空白
    assert "displayed.value = texts[0] ?? ''" in COMPOSABLE


def test_line_height_is_locked_against_jitter():
    """锁高：逐字增减会不断改变行宽，不锁高会把标题和输入框顶得上下跳。"""
    idx = RESET.index("{{ ambientText }}")
    window = RESET[max(0, idx - 400) : idx]
    assert "min-h-" in window, "文案容器必须锁定最小高度"


def test_cursor_and_accessibility_attributes():
    idx = RESET.index("{{ ambientText }}")
    window = RESET[max(0, idx - 500) : idx + 300]
    assert "animate-pulse" in window, "光标缺失就只剩一行文字在变，缺少打字机观感"
    assert 'aria-hidden="true"' in window, "装饰性循环动画不该被屏幕阅读器反复播报"


def test_timers_and_listeners_are_cleaned_up():
    assert "onUnmounted" in COMPOSABLE, "重置成功页 1.5 秒后跳转，不清理会留下悬挂回调"
    assert "clearTimeout" in COMPOSABLE
    assert "removeEventListener" in COMPOSABLE


def test_background_tab_pauses_the_animation():
    assert "visibilitychange" in COMPOSABLE
    assert "document.hidden" in COMPOSABLE
