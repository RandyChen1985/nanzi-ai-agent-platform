"""契约测试：数据集卡片的「更多」菜单。

两个必须守住的点：

1. 卡片容器不能有 ``overflow-hidden``。菜单是向上弹出的绝对定位浮层，
   祖先一旦裁剪就会把菜单顶部切掉（实测「进入数据集」被切掉半行字）。
   底部 footer 的圆角改由自己 ``rounded-b-xl`` 负责。
2. 菜单里的「权限管理」要直接落到详情页的权限页签，而不是再弹一次旧的授权弹窗
   ——否则三层权限（表级/行级）在弹窗里根本看不全。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "frontend/src/views/MetadataDatasets.vue"
TABLES = ROOT / "frontend/src/views/MetadataTables.vue"
PERM_CONSOLE = ROOT / "frontend/src/components/metadata/DatasetPermissionConsole.vue"


def _datasets() -> str:
    return DATASETS.read_text(encoding="utf-8")


def _tables() -> str:
    return TABLES.read_text(encoding="utf-8")


def test_grid_card_does_not_clip_floating_menu() -> None:
    page = _datasets()
    card_line = next(
        line
        for line in page.splitlines()
        if "cursor-pointer group" in line and "flex flex-col relative" in line
    )
    assert "overflow-hidden" not in card_line, "卡片裁剪会把向上弹出的菜单切掉"
    # 去掉裁剪后 footer 要自己收圆角
    assert "bg-gray-50/60 rounded-b-xl" in page


def test_menu_has_permission_management_entry_jumping_to_tab() -> None:
    page = _datasets()
    assert "权限管理" in page
    assert "openPermissionTab" in page
    assert "tab: 'permissions'" in page


def test_menu_drops_redundant_data_permission_entry() -> None:
    """「数据权限配置」菜单项已按用户要求去掉，只留「权限管理」和「数据行策略」。

    用户原话：「数据权限配置 多余了，去掉」。三者原来两两重叠：
    「数据权限配置」和「数据行策略」打开的是同一个行级策略弹窗（后者只多滚一下），
    「权限管理」则是三层（表/字段/行）的完整视角。去掉后入口不再重复。
    """
    page = _datasets()
    # 菜单里不能再有这一项（弹窗标题仍叫「数据权限配置」，所以只查菜单专属的 title）
    assert 'title="元数据管理 → 数据权限配置"' not in page
    assert "openPermConfigModal(ds)" not in page
    assert "withRowMenu((d) => openPermConfigModal(d))" not in page
    # 另两个入口必须还在，否则行级策略就无处可改
    assert "权限管理" in page
    assert "数据行策略" in page
    assert "openRowPolicyModal(ds)" in page


def test_menu_has_data_row_policy_entry_opening_the_config_modal() -> None:
    """「数据行策略」打开的是配置弹窗，不是跳详情页。

    行级策略的编辑器只有弹窗里有（用户例外 / 角色策略 / 默认策略 + JSON），
    详情页那个「数据行策略」页签只是只读视图，从那里再点过去是绕路。
    """
    page = _datasets()
    assert "数据行策略" in page
    assert '"元数据管理 → 数据行策略"' in page
    assert "openRowPolicyModal(ds)" in page
    assert "withRowMenu((d) => openRowPolicyModal(d))" in page
    # 弹窗实现已抽到共享组件里，列表页只传 show / dataset
    assert "<RowPolicyConfigModal" in page
    assert "focus-row-policy" in page
    # 打开后要自动滚到策略区，否则用户还得自己找
    console = PERM_CONSOLE.parent.joinpath("RowPolicyConfigModal.vue").read_text(encoding="utf-8")
    assert "rowPolicySectionRef" in console
    assert "scrollIntoView" in console


def test_search_box_does_not_reflow_while_typing() -> None:
    """输入时搜索框宽度和右侧留白都不能变。

    原来是 type="search"，浏览器自带的清空 ✕ 一出现就吃掉右侧文字区，
    搜索框看着像被"重新排版"了。改成 text + 自绘按钮，右侧恒定留 pr-8。
    """
    page = _datasets()
    box_start = page.index("relative w-full shrink-0 sm:w-72")
    box = page[box_start : box_start + 1400]
    assert 'type="text"' in box
    assert "pr-8" in box
    assert "shrink-0" in box
    assert "清空搜索" in box


def test_list_view_actions_also_collapsed_into_menu() -> None:
    """列表视图原来横着一排 5 个图标，和卡片不一致，也收进「…」。

    「…」按钮本身在表格里，但菜单必须 Teleport 出去：表格容器是 overflow-x-auto，
    行内绝对定位会被裁掉（卡片那轮已经踩过一次）。
    """
    page = _datasets()
    at = page.index('@click.stop="toggleRowMenu(ds, $event)"')
    row_actions = page[max(0, at - 300) : at + 500]
    assert "更多操作" in row_actions
    # 旧的 5 个横排图标入口不应该再出现
    assert 'title="数据权限"' not in page
    assert 'title="删除"' not in page
    # 菜单浮层走 Teleport + fixed 定位
    assert "rowMenuPos" in page
    assert '@wheel.passive="closeRowMenu"' in page


def test_both_menus_carry_the_same_entries() -> None:
    """卡片菜单和列表菜单的条目必须一致，否则同一操作两个入口两种能力。"""
    page = _datasets()
    labels = ["进入数据集", "Schema 巡检", "同步到 RAGFlow", "权限管理", "数据行策略", "编辑数据集", "删除数据集"]
    for label in labels:
        assert page.count(label) >= 2, f"「{label}」应该同时出现在卡片菜单和列表菜单里"


def test_detail_view_reads_tab_from_query() -> None:
    page = _tables()
    assert "route.query.tab" in page
    # 只接受白名单，避免 ?tab=乱写 把页面渲染成空白
    assert "VALID_TABS" in page
    assert "'permissions'" in page


def test_list_updated_at_shows_seconds() -> None:
    """更新时间要精确到秒。

    排查「谁在什么时候改的」时，日期只到天根本不够用——尤其是同一天里
    有人改过好几轮权限，看起来两行完全一样。
    """
    page = _datasets()
    assert "formatUpdatedAt" in page
    assert "getSeconds()" in page
    # 不能再退回只显示日期
    assert ".toLocaleDateString()" not in page


def test_list_has_no_extra_view_affordance() -> None:
    """「查看 >」去掉：能做的操作都在「…」里，多一个写着「查看」的箭头会让人以为还有别的入口。"""
    page = _datasets()
    at = page.index('@click.stop="toggleRowMenu(ds, $event)"')
    row_actions = page[max(0, at - 300) : at + 1200]
    assert "查看 <svg" not in row_actions
    # 「…」是操作列最后一个元素
    assert row_actions.index("toggleRowMenu") < row_actions.index("</div>")


def test_permission_console_exposes_edit_row_policy_entry() -> None:
    """详情页「数据行策略」那块是只读的，但得给一个能去改的入口。

    用户诉求：「加一个 行级策略的入口」。原来只写了句「编辑入口在元数据管理 →
    数据行策略」，要用户自己回忆路径再一层层点。现在卡片右上角直接给按钮。
    """
    console = PERM_CONSOLE.read_text(encoding="utf-8")
    assert "编辑行级策略" in console
    assert "(e: 'edit-row-policy'): void" in console
    assert "emit('edit-row-policy')" in console
    # 没编辑权限不给入口，避免点了撞 403
    assert 'v-if="canEdit"' in console[console.index("编辑行级策略") - 900 : console.index("编辑行级策略")]


def test_row_policy_entry_opens_the_modal_in_place() -> None:
    """详情页点「编辑行级策略」直接弹窗，不再跳回列表页。

    用户原话：「应该跟这个一样的啊，打开弹框」——菜单里的「数据行策略」是原地弹窗，
    详情页这个按钮也必须一样，跳转属于绕路。
    """
    tables = _tables()
    assert "@edit-row-policy=\"openRowPolicyModal\"" in tables
    start = tables.index("const openRowPolicyModal")
    block = tables[start : start + 300]
    assert "showRowPolicyModal.value = true" in block
    # 不再有任何跨页跳转
    assert "router.push" not in tables
    assert "open_row_policy" not in tables
    # 挂的是共享组件，且打开后滚到策略区
    assert "<RowPolicyConfigModal" in tables
    assert 'focus-row-policy' in tables
    # 保存后要重拉详情：enable_data_perm 变了，上面的「已启用校验」徽标要跟着变
    assert "handleRowPolicySaved" in tables
    saved_block = tables[tables.index("const handleRowPolicySaved"):][:300]
    assert "fetchDatasetInfo()" in saved_block


def test_row_policy_modal_is_shared_by_both_pages() -> None:
    """两个入口共用一个组件，避免两处实现漂移。

    原来弹窗内联在列表页里，详情页想改只能跳转；抽成组件后两边是同一份 UI 与逻辑。
    组件内部必须自己管打开时机（watch show），并把保存结果交回父页面。
    """
    console = PERM_CONSOLE.parent.joinpath("RowPolicyConfigModal.vue").read_text(encoding="utf-8")
    assert "defineProps<{" in console
    assert "show: boolean" in console
    assert "dataset: any | null" in console
    assert "(e: 'saved'): void" in console
    # 打开时复制一份副本，取消不会改脏父页面
    assert "JSON.parse(JSON.stringify(ds))" in console
    # 保存成功后不再自己刷新列表，交给父页面
    assert "emit('saved')" in console
    assert "fetchDatasets" not in console
