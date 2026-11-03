"""实体关系弹窗下拉可搜索化的接线契约。

「新建 / 编辑实体关系」弹窗原先用 4 个原生 <select>，表一多（尤其跨数据集）只能逐条翻。
契约锁住三件事：通用可搜索组件确实存在且具备搜索/分组/键盘/防裁剪能力、弹窗里 4 处都换成了它、
跨数据集分组与字段空选项语义没有被改丢。
"""

from pathlib import Path

import json
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

COMPONENT = ROOT / "frontend/src/components/common/SearchableSelect.vue"
RELATIONSHIP_LIST = ROOT / "frontend/src/components/metadata/RelationshipList.vue"
SSR_SCRIPT = ROOT / "tests/frontend/searchable_select_ssr.cjs"


def test_component_supports_search_groups_keyboard_and_no_clipping():
    source = COMPONENT.read_text(encoding="utf-8")

    # 浮层必须 Teleport 出弹窗，否则会被 overflow-hidden 裁掉
    assert 'Teleport to="body"' in source
    assert "position: 'fixed'" in source
    # 搜索：输入框 + 关键词匹配（中文名/物理名一并参与）
    assert "searchPlaceholder" in source
    assert "keywords" in source
    assert "matchesQuery" in source
    # 无结果 / 无可选项文案
    assert "无匹配项" in source
    assert "暂无可选项" in source
    # 分组标题与空选项
    assert "filteredGroups" in source
    assert "group.label" in source
    assert "showEmptyOption" in source
    assert "emptyValue" in source
    # 键盘与外部点击
    assert "ArrowDown" in source and "ArrowUp" in source and "Escape" in source
    assert "onDocumentPointerDown" in source
    assert "onViewportChange" in source
    # 无障碍
    assert 'role="combobox"' in source
    assert 'role="listbox"' in source
    assert 'role="option"' in source


def test_relationship_modal_uses_searchable_select_for_all_four_pickers():
    source = RELATIONSHIP_LIST.read_text(encoding="utf-8")
    modal_start = source.index('{{ editingId ? "编辑实体关系" : "新建实体关系" }}')
    modal_end = source.index("<!-- Delete Modal -->")
    modal = source[modal_start:modal_end]

    assert 'import SearchableSelect from "../common/SearchableSelect.vue"' in source
    # 源表 / 目标表 / 源关联字段 / 目标关联字段 全部替换
    assert modal.count("<SearchableSelect") == 4
    assert "<select" not in modal
    assert "<optgroup" not in modal
    # 绑定保持原字段名，业务逻辑没被换控件的动作带走
    for binding in (
        ':model-value="form.source_table_id"',
        ':model-value="form.target_table_id"',
        ':model-value="sourceField"',
        ':model-value="targetField"',
    ):
        assert binding in modal
    for handler in (
        "@update:model-value=\"handleSourceTableSelect\"",
        "@update:model-value=\"handleTargetTableSelect\"",
        "@update:model-value=\"handleSourceFieldSelect\"",
        "@update:model-value=\"handleTargetFieldSelect\"",
    ):
        assert handler in modal
    # 关联字段仍允许清空，并且清空值保持 string
    assert ':allow-empty="true"' in modal
    assert 'empty-label="-- 选择字段 --"' in modal
    assert 'empty-value=""' in modal
    # 自动拼关联条件的行为没丢
    assert "applyJoinCondition()" in source
    # 跨数据集分组语义保留
    assert '"当前数据集"' in source
    assert "[跨数据集]" in source
    # 中文业务名参与搜索，否则只能搜英文物理名
    assert "keywords: [t.physical_name, t.term]" in source
    assert "keywords: [col.physical_name, col.term]" in source


def _can_render_sfc() -> bool:
    if shutil.which("node") is None:
        return False
    node_modules = ROOT / "frontend/node_modules"
    return all((node_modules / name).exists() for name in ("vue", "esbuild", "@vue/server-renderer"))


@pytest.mark.skipif(not _can_render_sfc(), reason="前端依赖未安装，跳过真渲染探针")
def test_searchable_select_renders_trigger_labels_and_disabled_state():
    """真编译真渲染：触发器文案、占位、空选项、禁用态都必须是渲染出来的。"""
    proc = subprocess.run(
        ["node", str(SSR_SCRIPT)], cwd=ROOT / "frontend", capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)

    assert data["selected"]["showsSelectedLabel"] is True, "选中后触发器要显示候选文案"
    assert data["selected"]["closedNoListbox"] is True, "未展开时不得渲染下拉浮层"
    assert data["placeholder"]["showsPlaceholder"] is True, "未选择且不允许空值时显示占位文案"
    assert data["emptyOption"]["showsEmptyLabel"] is True, "允许空值时显示「-- 选择字段 --」"
    assert data["emptyOption"]["closedNoListbox"] is True
    assert data["disabled"]["triggerDisabled"] is True
    assert data["disabled"]["keepsAriaLabel"] is True, "禁用态仍要保留无障碍名称"
