"""工作区行操作 ⋮ 菜单的契约。

锁定三件事：
1. 行内不再平铺「画布打开 | 下载 | 复制路径 | 挂载文件」四个文字按钮，只留一个 ⋮；
2. ⋮ 与右键共用同一份菜单项构造器（`buildWorkspaceActionEntries`）与同一个浮层组件；
3. 浮层的可访问性/触摸目标/关闭时机（Teleport、role=menu、Esc、点外、40px）不被回退。

行为验证（菜单项矩阵）见 tests/frontend/workspace_action_entries.mjs。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DRAWER = "frontend/src/components/embed/WorkspaceBrowserDrawer.vue"
MENU = "frontend/src/components/embed/WorkspaceActionMenu.vue"

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _run_node(script: str) -> str:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_action_entries_matrix_behaviour_via_node():
    output = _run_node("tests/frontend/workspace_action_entries.mjs")
    assert "工作区行操作菜单项测试全部通过" in output


def test_action_menu_component_is_body_teleported_with_menu_semantics():
    source = _source(MENU)

    # 抽屉祖先链上的 transform 会改写 fixed 的包含块，必须挂到 body 才铺得准
    assert '<Teleport to="body">' in source
    assert 'role="menu"' in source
    assert 'role="menuitem"' in source
    assert 'aria-disabled' in source
    # 视口夹取：定位要测量真实尺寸，不是写死
    assert "getBoundingClientRect" in source
    assert "maxHeight" in source


def test_action_menu_closes_on_escape_and_outside_click():
    source = _source(MENU)

    assert "@keydown.esc" in source
    assert "document.addEventListener('mousedown'" in source
    assert "document.removeEventListener('mousedown'" in source
    # 键盘可达：上下键在菜单项之间移动
    assert "keydown.down" in source
    assert "keydown.up" in source


def test_action_menu_renders_danger_separator_and_touch_target():
    source = _source(MENU)

    assert "separatorBefore" in source
    assert "border-t" in source
    assert "text-red-600" in source
    # 菜单项至少 40px 高：触屏可点
    assert "min-h-[40px]" in source
    assert "entry.disabledHint" in source


def test_row_actions_collapse_into_single_overflow_trigger():
    source = _source(DRAWER)

    # 四个平铺文案必须全部收进纯函数，模板里不再手写
    for label in ('复制路径', '挂载文件到会话', '画布打开预览', '下载文件'):
        assert label not in source, f'工作区模板仍手写菜单项：{label}'
    assert 'EllipsisVerticalIcon' in source
    assert 'aria-haspopup="menu"' in source
    assert 'aria-label="更多操作"' in source
    # 批量选择模式不显示；桌面端每行都露一个淡按钮（悬停变清晰），移动端只给选中行
    assert '!multiSelectMode && (shouldShowRowActions(item) || !isMobile)' in source
    # 必须像个按钮：有边框有底色（裸图标太淡，用户不知道能点）
    assert 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900' in source
    assert 'group-hover:opacity-100' in source
    # 触屏没有 hover：非选中行保持半透明可见，不能做成 opacity-0（透明但可点）
    assert 'opacity-40' in source
    assert 'opacity-0 group-hover:opacity-100' not in source


def test_row_and_context_menu_share_single_entries_source():
    source = _source(DRAWER)

    assert "from '@/utils/workspaceActionEntries'" in source
    assert "import WorkspaceActionMenu from '@/components/embed/WorkspaceActionMenu.vue'" in source
    assert 'buildWorkspaceActionEntries' in source
    # 同一个实例服务两个入口：一次只开一个菜单
    # （用正则而不是 count：类型名 WorkspaceActionMenuState 也含同一前缀）
    assert len(re.findall(r'<WorkspaceActionMenu(?:\s|/)', source)) == 1
    assert ':entries="actionMenuEntries"' in source
    assert '@select="runWorkspaceAction"' in source
    assert '@close="closeActionMenu"' in source


def test_row_menu_trigger_stops_mousedown_so_second_click_can_close():
    """菜单挂在 body 上并在 document 的 mousedown 里点外关闭。

    如果 ⋮ 不拦 mousedown，第二次点击会先被 document 关闭、再被 click 重新打开，
    表现为「怎么点都收不起来」。这里锁住 @mousedown.stop 与 click 的分工。
    """
    source = _source(DRAWER)

    assert re.search(
        r'@mousedown\.stop\s+@click\.stop="toggleRowActionMenu\(\$event, item\)"',
        source,
    )


def test_action_menu_closes_when_list_scrolls_or_selection_changes():
    source = _source(DRAWER)

    assert '@scroll.passive="closeActionMenu"' in source
    # 选中其它行 / 切换目录 / 执行动作前都要关，避免浮层指向已消失的行
    assert source.count('closeActionMenu()') >= 4
