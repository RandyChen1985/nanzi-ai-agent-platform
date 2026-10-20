"""第三方用户同步的部门归属配置：可选开关、单列映射、默认只增不减（前端契约）。

同步抽屉原本只有账号字段映射，部门归属完全靠事后手工分。这里加的是**可选**能力：
不打开时行为与今天完全一致（新账号无部门），打开后才按来源表的部门编码挂到本平台
已有部门上。前端要守住的三点：

1. 开关默认关闭，且总开关没开时不可操作（与定时同步的禁用逻辑一致）；
2. 只暴露「来源部门列 + 归属方式」，没有「自动建部门」——平台部门树是本地维护的；
3. 保存/回填走同一份 config，旧配置缺 `dept_sync` 时归一化成关闭状态。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRAWER = ROOT / "frontend/src/components/ThirdPartyUserSyncDrawer.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return DRAWER.read_text(encoding="utf-8")


def test_config_type_and_defaults_include_dept_sync():
    source = _source()

    assert "interface DeptSync" in source
    assert "dept_sync: DeptSync" in source
    assert "dept_sync: { enabled: false, source_column: null, mode: 'add' }" in source


def test_dept_sync_block_is_behind_the_master_switch():
    source = _source()
    section = source.split("<!-- 部门同步（可选）", 1)[1].split("<!-- 定时同步 -->", 1)[0]

    assert "同步部门归属" in section
    assert 'v-model="config.dept_sync.enabled"' in section
    assert ':disabled="!config.enabled"' in section, "总开关关闭时部门同步不可操作"


def test_dept_sync_maps_a_single_source_column():
    source = _source()
    section = source.split("<!-- 部门同步（可选）", 1)[1].split("<!-- 定时同步 -->", 1)[0]

    assert 'v-model="config.dept_sync.source_column"' in section
    assert "Department.code" in section, "要说明来源编码与平台部门编码对齐"
    # 未匹配时的行为写在界面上，避免管理员以为是静默失败
    assert "找不到的部门会跳过" in section


def test_dept_sync_mode_is_additive_by_default():
    source = _source()
    section = source.split("<!-- 部门同步（可选）", 1)[1].split("<!-- 定时同步 -->", 1)[0]

    assert "deptModeOptions" in section
    # 文案在选项常量里（模板只渲染 label），单独断言，避免文案与选项脱节
    assert "只增不减" in source
    assert "以来源为准" in source


def test_saved_config_is_normalized_on_load():
    """旧配置没有 dept_sync 键：回填时必须补默认值，否则模板访问 undefined 报错。"""
    source = _source()
    loader = source.split("config.value = {", 1)[1][:2000]

    assert "dept_sync:" in loader
    assert "data?.dept_sync?.enabled" in source
    assert "data?.dept_sync?.mode === 'replace'" in source
