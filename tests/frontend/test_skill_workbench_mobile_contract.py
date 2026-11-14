"""技能工作台的移动端可用性契约。

窄屏（<768px）既没有 hover，也没有足够横向空间：顶部四个 tab 会被压成逐字换行，
卡片上的「导出 / 重命名 / 删除」因为 ``opacity-0 + group-hover`` 在触屏上永远不可见。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS_VIEW = ROOT / "frontend/src/views/SkillsManagement.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workspace_tabs_scroll_horizontally_on_mobile():
    source = _read(SKILLS_VIEW)
    tab_block = source.split("<!-- Scope Tab 切换", 1)[1].split("<!-- 已发布 / 下架管理面板", 1)[0]
    assert "overflow-x-auto" in tab_block, "tab 容器必须可横向滚动"
    assert "whitespace-nowrap" in tab_block, "tab 文字不允许逐字换行"
    # 四个 tab 按钮都要 shrink-0，否则仍会被压缩换行
    assert tab_block.count("shrink-0") >= 4


def test_card_actions_are_visible_without_hover_on_touch():
    source = _read(SKILLS_VIEW)
    # 四个动作已收进「⋯」：⋯ 必须在触屏常显，桌面才靠 hover 显现
    assert (
        "opacity-100 transition-all hover:bg-gray-100 hover:text-gray-600 "
        "md:opacity-0 md:group-hover:opacity-100"
    ) in source
    # 菜单项本身不能接 opacity-0 / group-hover，否则触屏点了也看不见
    menu_block = source.split('<!-- 卡片「⋯」操作菜单 -->', 1)[1].split('<SkillShareDialog', 1)[0]
    assert "opacity-0" not in menu_block
    assert "group-hover" not in menu_block


def test_create_button_follows_the_stats_button():
    """新建放在工具栏、统计按钮之后，且不在可滚动容器里。

    两条约束同时成立才算对：
    - 位置：排在统计后面，不再占页头右侧；
    - 结构：它必须待在那个 overflow-x-auto 容器之外——
      新建的下拉菜单是 absolute 定位，被滚动容器包住就会被裁掉，
      点开看起来毫无反应（智能体中心踩过一次同样的坑）。
    """
    source = _read(SKILLS_VIEW)

    header = source.split("<!-- Header：只剩标题与说明入口", 1)[1]
    header = header.split("<!-- 技能工作台全生命周期指引横幅", 1)[0]
    assert "openCreateModal" not in header, "新建不该再占页头"

    toolbar = source.split("<!-- 排序 / 视图 / 统计 留在可滑区", 1)[1]
    toolbar = toolbar.split("<!-- 业务分组页签", 1)[0]
    stats_at = toolbar.index("openStatsModal")
    create_at = toolbar.index("openCreateModal")
    assert create_at > stats_at, "新建必须在统计按钮之后"

    # 可滑区必须在新建之前就闭合：统计按钮之后第一个 </div> 就是它的闭合
    strip_close = toolbar.index("</div>", toolbar.index("openStatsModal"))
    assert strip_close < create_at, (
        "可滑区容器应该在新建之前闭合，否则新建的下拉菜单会被 overflow 裁掉"
    )
    # 只认主按钮（下拉里的「空白新建」带括号，不算第二个入口）
    assert toolbar.count('@click="openCreateModal"') == 1


def test_create_button_label_shrinks_on_mobile():
    """窄屏下主操作只留「新建」，桌面仍是「新建技能」。"""
    source = _read(SKILLS_VIEW)
    assert '新建<span class="hidden sm:inline">技能</span>' in source
