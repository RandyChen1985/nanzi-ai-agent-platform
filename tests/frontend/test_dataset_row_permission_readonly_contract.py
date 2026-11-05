"""契约测试：权限控制台里的「数据集自身行级策略」保持只读展示。

需求演进：数据集详情页的权限管理从「成员授权列表 + 行级只读展示」重构为三层权限控制台
（数据集级 / 表级 / 行级，见 prototypes/2026-11-05-dataset-permission-console.html）。
行级信息拆成两部分，本文件锁住其中「只读」的那一半：

- 数据集自身的行级配置（用户例外 / 角色策略 / 默认规则）仍来自数据集详情的
  ``enable_data_perm`` / ``row_filter_config``，在「行级策略」视图里只读展示；
- 授权条目自己的 ``row_policy``（继承 / 不过滤 / 自定义）可以编辑，那是另一半。

只读区不允许长出编辑入口：改了这里会让人以为「改一条授权就改了全数据集」。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = "frontend/src/views/MetadataTables.vue"
CONSOLE = "frontend/src/components/metadata/DatasetPermissionConsole.vue"
SECTION_START = "数据集自身行级策略"
SECTION_END = "三种行级策略"


def _view() -> str:
    return (ROOT / VIEW).read_text(encoding="utf-8")


def _console() -> str:
    return (ROOT / CONSOLE).read_text(encoding="utf-8")


def _readonly_section() -> str:
    source = _console()
    start = source.index(SECTION_START)
    end = source.index(SECTION_END, start)
    return source[start:end]


def test_console_is_wired_into_permissions_tab():
    source = _view()
    assert "DatasetPermissionConsole" in source
    assert ':row-filter-enabled="rowFilterEnabled"' in source
    assert ':row-filter-config="rowFilterConfig"' in source


def test_reads_enable_flag_and_row_filter_config():
    source = _view()
    assert "dataset.value?.enable_data_perm" in source
    assert "dataset.value?.row_filter_config" in source


def test_three_policy_layers_are_rendered():
    section = _readonly_section()
    assert "用户例外" in section and "角色策略" in section and "默认规则" in section
    assert "datasetUserExceptions" in section
    assert "datasetRolePolicies" in section
    assert "datasetDefaultRules" in section


def test_disabled_state_explains_full_visibility():
    section = _readonly_section()
    assert "未启用精细化权限校验" in section
    assert "全部数据行" in section


def test_member_rows_carry_row_level_badges():
    """授权条目上要能看出「这个人/这个角色其实只能看部分行」。"""
    source = _console()
    assert "userExceptionCount(grant.id)" in source
    assert "rolePolicyCount(grant.sub)" in source


def test_badges_only_shown_when_enforcement_enabled():
    """未启用校验时数据集级策略不生效，徽标必须在启用状态下才出现，否则误导。"""
    source = _console()
    assert 'v-if="grant.kind === \'user\' && rowFilterEnabled && userExceptionCount(grant.id)"' in source
    assert 'v-else-if="grant.kind === \'role\' && rowFilterEnabled && rolePolicyCount(grant.sub)"' in source


def test_readonly_section_has_no_edit_action():
    """只读区不能又长出编辑入口：区块内不允许出现任何点击动作或保存按钮。"""
    section = _readonly_section()
    assert "@click" not in section
    assert "submitDrawer" not in section
    assert "removeGrant" not in section
    assert "只读" in section
