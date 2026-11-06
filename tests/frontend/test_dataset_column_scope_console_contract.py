"""契约测试：权限控制台的字段级（列级）配置入口。

产品背景：原来一条授权 = 「数据集里所有表的所有字段」。表级做完之后仍然存在一个缺口 ——
``ods_crm_customer`` 能进、能查，但 ``id_card`` / ``mobile`` 这些列也一起给了。
字段级就是补这一层。

UI 约束（这三条是这个界面存在的理由）：

1. **表范围先于字段范围**：字段配置必须挂在「这次授权能看到的表」上；
   切到「指定表」后，原来给不在范围的表配的字段规则必须被清掉，否则会存下悬空配置。
2. **默认全部字段**：绝大多数授权不该被字段配置污染，界面上只高亮改过的表。
3. **限定 / 排除两种模式语义不能混**：``exclude`` 模式下勾选表示「不可见」，
   视觉上必须是删除线/警示色，不能被看成「允许」。
"""
from __future__ import annotations

import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[2]
_CONSOLE = (_REPO / "frontend/src/components/metadata/DatasetPermissionConsole.vue").read_text(
    encoding="utf-8"
)


def _block(start: str, end: str) -> str:
    begin = _CONSOLE.index(start)
    return _CONSOLE[_CONSOLE.index(end, begin) :][: 0] or _CONSOLE[begin : _CONSOLE.index(end, begin)]


def _step3() -> str:
    return _block("<!-- Step 3 字段范围 -->", "<!-- Step 4 行级 -->")


def _columns_view() -> str:
    return _block('<!-- 字段范围：行 = 表，列 = 授权主体', 'v-else-if="view === \'rows\'"')


# ---------------------------------------------------------------------------
# 数据形态
# ---------------------------------------------------------------------------


def test_grant_carries_column_rules() -> None:
    """前端类型要能承载 scope.columns，否则保存后刷新就丢。"""
    assert "columns: Record<number, ColumnRule>" in _CONSOLE
    assert "interface ColumnRule" in _CONSOLE
    assert "type ColumnMode = 'include' | 'exclude'" in _CONSOLE


def test_grant_parses_columns_from_response() -> None:
    assert "Object.fromEntries(" in _CONSOLE
    assert "raw?.scope?.columns || {}" in _CONSOLE


def test_submit_sends_columns() -> None:
    """提交必须带上 columns，且空对象不传（后端按「全部字段」处理）。"""
    assert "columns,\n      }," in _CONSOLE or "columns," in _CONSOLE
    block = _CONSOLE[_CONSOLE.index("const aliveTables = new Set(draftActiveTables"):]
    block = block[: block.index("const base = {")]
    assert "aliveTables.has(tableId)" in block
    assert "!rule.names.length" in block


def test_edit_backfills_columns() -> None:
    assert "draft.columns = JSON.parse(JSON.stringify(grant.columns || {}))" in _CONSOLE


# ---------------------------------------------------------------------------
# 抽屉交互
# ---------------------------------------------------------------------------


def test_step3_exists_between_table_and_row() -> None:
    """字段必须排在「表范围」之后、「行级」之前 —— 先决定能看到哪些表，再谈字段。"""
    table_at = _CONSOLE.index("<!-- Step 2 表范围 -->")
    cols_at = _CONSOLE.index("<!-- Step 3 字段范围 -->")
    row_at = _CONSOLE.index("<!-- Step 4 行级 -->")
    assert table_at < cols_at < row_at


def test_step_labels_include_column() -> None:
    assert "['主体', '表范围', '字段', '行级', '有效期']" in _CONSOLE


def test_step3_lists_only_active_tables() -> None:
    """「指定表」模式下只能给勾选的表配字段，否则是悬空配置。"""
    assert "draftActiveTables" in _CONSOLE
    assert "draft.scopeMode === 'all'" in _CONSOLE
    assert "draft.tableIds.includes(t.id)" in _CONSOLE


def test_switching_table_scope_prunes_column_rules() -> None:
    assert "const pruneColRules = ()" in _CONSOLE
    assert "pruneColRules()" in _CONSOLE


def test_column_step_is_optional() -> None:
    """不配字段就是全部字段，这一步永远可以「下一步」。"""
    can_next = _CONSOLE[_CONSOLE.index("const canNext = computed"):]
    can_next = can_next[: can_next.index("const expiryFallback")]
    assert "if (drawerStep.value === 2) return true" in can_next


def test_include_mode_fails_closed_for_new_columns() -> None:
    """限定模式下新增列默认不可见，界面上要说清楚。"""
    step3 = _step3()
    assert "新增的列默认不可见" in step3


# ---------------------------------------------------------------------------
# 视觉语义
# ---------------------------------------------------------------------------


def test_exclude_chips_look_disabled_not_selected() -> None:
    """排除模式勾选 = 不可见，用删除线 + 琥珀色，不能和「允许」的绿色撞色。"""
    step3 = _step3()
    assert "line-through" in step3
    assert "border-amber-300 bg-amber-50 text-amber-700" in step3
    assert "border-emerald-300 bg-emerald-50 font-semibold text-emerald-700" in step3


def test_mode_toggle_is_present() -> None:
    step3 = _step3()
    assert "setColMode('include')" in step3
    assert "setColMode('exclude')" in step3


# ---------------------------------------------------------------------------
# 字段范围视图
# ---------------------------------------------------------------------------


def test_column_view_tab_registered() -> None:
    assert "{ key: 'columns', label: '字段范围' }" in _CONSOLE


def test_column_matrix_shows_visible_over_total() -> None:
    view = _columns_view()
    assert "cell.visible }}/{{ cell.total" in view
    assert "visibleColCount" in _CONSOLE


def test_column_view_marks_invisible_table() -> None:
    """表本身不可见时，字段配置不生效，单元格显示 ·。"""
    assert "grant.mode === 'list' && !grant.tables.includes(table.id)) return null" in _CONSOLE


def test_column_view_filter_by_narrowed() -> None:
    assert "onlyColNarrowed" in _CONSOLE
    assert "colNarrowedTables" in _CONSOLE


def test_column_view_explains_intersection_rule() -> None:
    """界面上要说明字段级取交集（与表级相反），否则没人猜得到。"""
    view = _columns_view()
    assert "字段级取" in view
    assert "交集" in view
