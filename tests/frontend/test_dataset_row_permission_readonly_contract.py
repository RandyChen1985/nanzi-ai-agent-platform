"""契约测试：权限管理 tab 只读展示行级数据权限，并与成员授权区分开。

需求背景：数据集详情页「权限管理」原来只有成员授权（谁能访问这个数据集），
行级隔离配置（用户例外 / 角色策略 / 默认规则）藏在「元数据管理 → 数据权限配置」
弹窗里，管理员在授权页看不到「这个人其实只能看部分行」。现在在授权页只读展示，
并用独立区块 + 行内徽标区分这两个维度。

行级策略直接读数据集详情返回的 `enable_data_perm` / `row_filter_config`，
不新增请求；这里锁住接线和「只读」边界。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = "frontend/src/views/MetadataTables.vue"
SECTION_START = "<!-- 行级数据权限（只读）"
SECTION_END = "<!-- Add Permission Modal -->"


def _source() -> str:
    return (ROOT / VIEW).read_text(encoding="utf-8")


def _row_level_section() -> str:
    source = _source()
    start = source.index(SECTION_START)
    end = source.index(SECTION_END, start)
    return source[start:end]


def test_section_is_placed_after_member_grants():
    source = _source()
    assert source.index("暂无用户授权。") < source.index(SECTION_START)


def test_reads_enable_flag_and_row_filter_config():
    source = _source()
    assert "dataset.value?.enable_data_perm" in source
    assert "dataset.value?.row_filter_config" in source


def test_three_policy_layers_are_rendered():
    section = _row_level_section()
    assert "rowFilterUserGroups" in section
    assert "rowFilterRoleGroups" in section
    assert "rowFilterDefaultRules" in section
    assert "用户例外" in section and "角色策略" in section and "默认规则" in section


def test_disabled_state_explains_full_visibility():
    section = _row_level_section()
    assert "未启用精细化权限校验" in section
    assert "全部数据行" in section


def test_member_rows_carry_row_level_badges():
    source = _source()
    assert "userRowFilterCount(u.id)" in source
    assert "roleRowFilterCount(r.code)" in source


def test_badges_only_shown_when_enforcement_enabled():
    """未启用校验时策略不生效，徽标必须在启用状态下才出现，否则误导。"""
    source = _source()
    assert 'v-if="rowFilterEnabled && userRowFilterCount(u.id)"' in source
    assert 'v-if="rowFilterEnabled && roleRowFilterCount(r.code)"' in source


def test_section_is_read_only():
    """只读区不能又长出编辑入口：区块内不允许出现任何点击动作。"""
    section = _row_level_section()
    assert "@click" not in section
    assert "openAddPermissionModal" not in section
    assert "removePermission" not in section
