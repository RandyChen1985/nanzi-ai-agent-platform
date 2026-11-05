"""契约测试：权限模拟是弹框，不是第四个页签。

模拟是「查一个具体的人」的一次性动作，占一个页签意味着它和「按主体/按表/行级」
三种配置视图平权，页签条一直挤到右上角（实测会把「权限模拟」按钮和页签文字顶出容器）。
改成顶部按钮 + 弹框后：配置视图只有三个，模拟看完即关。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "frontend/src/components/metadata/DatasetPermissionConsole.vue"


def _console() -> str:
    return CONSOLE.read_text(encoding="utf-8")


def test_simulate_is_not_a_view_tab() -> None:
    page = _console()
    assert "{ key: 'simulate'" not in page
    # view 的联合类型里也不能再有 simulate
    assert "| 'simulate'>" not in page


def test_simulate_opens_a_modal() -> None:
    page = _console()
    assert "simModalOpen" in page
    assert "@click=\"openSimulateModal\"" in page
    assert "closeSimModal" in page
    # 弹框要有遮罩点击与 Esc 关闭
    assert '@click.self="closeSimModal"' in page
    assert "Escape" in page


def test_simulate_has_exactly_one_primary_action() -> None:
    page = _console()
    # 之前弹框底部和选择区各有一个「开始模拟」，会让人以为要按两次
    assert page.count("开始模拟") == 1
