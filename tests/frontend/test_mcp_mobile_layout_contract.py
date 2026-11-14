"""MCP 工具集 / 我的 MCP 的移动端可用性契约。

窄屏下三处会把可用空间吃光：
1. 页头那句 40 多字的副标题；
2. 「我的 MCP」二级 tab 没有 shrink-0，被 flex 压到只露半截；
3. 审计日志的筛选条件摊成 5 行，把下面 flex-1 的列表区压成 0 高，日志滚不出来。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MCP_VIEW = ROOT / "frontend/src/views/McpManagement.vue"
PERSONAL_MENU = ROOT / "frontend/src/components/mcp/McpPersonalMenu.vue"
CALL_SUMMARY = ROOT / "frontend/src/components/mcp/McpCallSummary.vue"
AUDIT_TAB = ROOT / "frontend/src/components/mcp/McpAuditLogTab.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_subtitle_is_hidden_on_mobile():
    """副标题窄屏隐藏：一行 40 多字换不来任何操作，却会把二级 tab 顶出首屏。"""
    source = _read(MCP_VIEW)
    idx = source.index("mcp-page-desc")
    tag = source[source.rindex("<p", 0, idx) : source.index(">", idx)]
    assert "hidden" in tag, "副标题必须窄屏隐藏"
    assert "sm:block" in tag, "桌面端仍要显示"


def test_personal_menu_variants_are_never_squashed():
    """「我的 MCP」的二级导航在两种变体下都不许被压缩。

    父级是 h-full / min-h-0 的 flex-col，导航行缺 shrink-0 会被压成一条边
    ——看起来就像「被下面的卡片盖住」。管理员走 chips，普通用户走 tabs，
    两条路径都得管住。
    """
    menu = _read(PERSONAL_MENU)

    tabs = menu[menu.index("variant === 'tabs'") :]
    tabs = tabs[: tabs.index("</div>")]
    assert "overflow-x-auto" in tabs
    assert "flex-wrap" not in tabs
    assert "shrink-0" in tabs
    assert "whitespace-nowrap" in tabs

    chips = menu[menu.index("v-else\n    class=\"flex shrink-0") :]
    chips = chips[: chips.index(">")]
    assert "shrink-0" in chips, "chips 导航也必须 shrink-0"

    view = _read(MCP_VIEW)
    # 普通用户那条 tabs
    idx = view.index('variant="tabs"')
    assert "flex-shrink-0" in view[idx : idx + 200]
    # 管理员那条 chips
    chips_idx = view.index('variant="chips"')
    assert "shrink-0" in view[chips_idx : chips_idx + 200]


def test_call_summary_can_be_collapsed():
    """调用概览整卡可折叠，与内部「明细」是两个互不干扰的开关。"""
    source = _read(CALL_SUMMARY)
    assert "const collapsed = ref(false)" in source
    assert "collapsed = !collapsed" in source
    # 折叠时指标、明细按钮、明细块都要一起收起
    assert 'v-if="!collapsed"' in source
    assert 'v-if="hasCalls && !collapsed"' in source
    assert 'v-if="expanded && hasCalls && !collapsed"' in source


def test_audit_filters_collapse_into_one_row_on_mobile():
    """审计日志筛选在窄屏收成一行，否则列表区被压成 0 高、日志滚不出来。

    收起态：搜索 + 筛选开关 + 刷新。
    展开后仍要有真正提交条件的「应用」按钮——上面那颗只管开合。
    """
    source = _read(AUDIT_TAB)

    assert "const mobileFiltersOpen = ref(false)" in source
    assert "const activeFilterCount = computed(" in source

    # 收起态那一行只在小屏出现
    bar = source[source.index("窄屏只留一行") :]
    bar = bar[: bar.index('class="mt-2.5 flex-wrap')]
    assert "sm:hidden" in bar
    assert "mobileFiltersOpen = !mobileFiltersOpen" in bar

    # 完整条件区：窄屏默认 hidden，展开才 flex
    expanded = source[source.index('class="mt-2.5 flex-wrap') :]
    expanded = expanded[: expanded.index(">")]
    assert ":class=\"mobileFiltersOpen ? 'flex' : 'hidden'\"" in source

    # 仔细展开后仍要有真正提交条件的按钮（上面那颗只管开合）
    apply_at = source.index('<span class="sm:hidden">应用</span>')
    btn_tag = source[source.rindex("<button", 0, apply_at) : apply_at]
    assert "@click=\"handleFilterChange\"" in btn_tag
