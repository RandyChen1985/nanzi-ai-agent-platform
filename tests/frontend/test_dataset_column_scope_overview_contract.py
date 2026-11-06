"""契约测试：字段级权限在概览卡片与数据集卡片上的可见性。

用户诉求（两张截图）：
- 权限管理页顶部只有「数据集级 / 表级 / 行级」三张概览卡，中间空了一块，缺字段级；
- 元数据集的卡片上也要能看出这个数据集有没有字段级限制。

守住的点：
1. 概览里有第四张「字段级 · 能看哪些字段」卡，且和另外三张一样带「?」说明；
2. 字段级统计口径是「实际被收窄的表」，不是「配了规则的授权数」——后者会把
   「白名单 = 全表」这种没实际收窄的情况也算进来；
3. 后端权限摘要接口返回字段级字段，否则卡片徽标没有数据来源；
4. 卡片徽标只在真有字段规则时出现，没配的数据集不占位置。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "frontend/src/components/metadata/DatasetPermissionConsole.vue"
DATASETS = ROOT / "frontend/src/views/MetadataDatasets.vue"
ENDPOINT = ROOT / "app/api/portal/endpoints/metadata.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_overview_has_fourth_column_scope_card() -> None:
    console = _read(CONSOLE)
    # 卡片标题出现两次：一次在 SCOPE_HELP 里、一次在模板上，两处都要有
    assert console.count("字段级 · 能看哪些字段") == 2
    # 和另外三张一样，带自己那一层的「?」说明
    assert "@click.stop=\"scopeHelpKey = 'column'\"" in console
    # 四张卡片要能并排，小屏两列
    assert "md:grid-cols-2 xl:grid-cols-4" in console


def test_column_scope_card_counts_actually_narrowed_tables() -> None:
    console = _read(CONSOLE)
    start = console.index("const columnScopeStats = computed(")
    block = console[start : start + 900]
    # 口径：被收窄的表优先取 colNarrowedTables（真的少了列），不是「配了规则的表」
    assert "narrowed: colNarrowedTables.value.length" in block
    # 过期授权不参与统计
    assert "if (grant.status === 'expired') continue" in block
    # 白名单与排除要分开数，两者语义相反
    assert "mode === 'include'" in block and "includeTables" in block
    assert "excludeTables" in block


def test_column_scope_has_help_entry() -> None:
    console = _read(CONSOLE)
    assert "type ScopeHelpKey = 'dataset' | 'table' | 'column' | 'row'" in console
    help_at = console.index("  column: {")
    help_block = console[help_at : help_at + 900]
    assert "title: '字段级 · 能看哪些字段'" in help_block
    # 必须讲清「取交集」这个和表级相反的方向，否则配错会以为放宽了
    assert "取交集" in help_block
    assert "pitfall" in help_block


def test_permission_summary_api_returns_column_stats() -> None:
    endpoint = _read(ENDPOINT)
    start = endpoint.index('"/datasets/permission-summary"')
    block = endpoint[start : start + 4200]
    assert '"column_rules": 0' in block
    assert '"column_tables": 0' in block
    assert "item[\"column_rules\"] += 1" in block
    assert "item[\"column_tables\"] += len(col_rules)" in block


def test_dataset_card_shows_column_badge_only_when_configured() -> None:
    page = _read(DATASETS)
    at = page.index("字段 <b>")
    badge = page[max(0, at - 900) : at + 200]
    assert "permissionItem(ds)?.column_rules" in badge, "没配字段规则就别占卡片位置"
    assert "canSeePermissionSummary" in badge, "没有编辑权限的人看不到授权摘要"
    # 徽标要能解释清楚数字含义，卡片放不下明细
    assert "columnScopeTooltip(ds)" in badge
    tooltip_at = page.index("const columnScopeTooltip")
    assert "字段范围 看明细" in page[tooltip_at : tooltip_at + 400]
