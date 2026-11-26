"""平台技能 / 平台 MCP 的「只读模式」前端契约。

两个页面的平台页签现在对登录用户可见，但无写权限时必须**隐藏**所有写入口
（不是置灰），并且不能再去打 admin-only 接口（否则只读用户每次进页面都会看到
403 弹错）。本文件锁住这几条：

技能页：
- 只读判据集中在 `platformSkillReadonly`；
- 详情抽屉只读：编辑/预览切换、保存、新建文件、上传脚本、编辑面板都隐藏；
- 文件树组件收到 `readonly`，行内删除与右键菜单不再出现；
- 列表里的绑定反查、写按钮按权限隐藏；空态与详情按钮文案切换为「查看」语义。

MCP 页：
- `canSave` 仍是唯一只读开关，写入口全部挂在它上面；
- 选中服务时不再请求 usage / 定义变更（两者都是 admin-only）；
- 变更徽标与「去发布」这类写语义入口对只读用户隐藏；
- 工具参数 chip 用 title 暴露完整 schema（只读看不到测试台）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SKILL_VIEW = ROOT / "frontend/src/views/SkillsManagement.vue"
SKILL_TREE = ROOT / "frontend/src/components/SkillFileTree.vue"
MCP_REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 技能页
# ---------------------------------------------------------------------------


def test_skill_readonly_flag_is_scope_aware() -> None:
    source = _read(SKILL_VIEW)
    assert "const platformSkillReadonly = computed(" in source
    # 个人技能永远可写；只有「非个人中心 + 平台视图 + 无管理权限」才是只读
    readonly_block = source[
        source.index("const platformSkillReadonly = computed(") :
        source.index("const platformSkillReadonly = computed(") + 400
    ]
    assert "!props.personalOnly" in readonly_block
    assert "canManagePlatformSkills" in readonly_block


def test_skill_write_entries_are_hidden_in_readonly_mode() -> None:
    source = _read(SKILL_VIEW)
    # 工具条 / 空态的写入口按 scope + 权限隐藏
    assert "activeScope === 'personal' || canManagePlatformSkills" in source
    # 只读平台空态给出说明而不是「新建/导入」按钮
    assert "当前账号仅可浏览平台技能" in source
    # 详情按钮在只读时变成「查看」
    assert "? '配置' : '查看'" in source
    # 绑定反查入口对无权限者隐藏（后端 /bindings 是 admin-only）
    assert "!personalOnly && skill.scope !== 'personal' && canManagePlatformSkills" in source


def test_skill_detail_drawer_is_readonly() -> None:
    source = _read(SKILL_VIEW)
    # 编辑/预览切换只对可写用户显示
    assert "isMarkdownFile && !platformSkillReadonly" in source
    # 编辑面板只在可写时渲染（只读恒预览）
    assert "editorMode === 'edit' && !platformSkillReadonly" in source
    # 保存 / 新建文件 / 上传脚本三处写入口
    assert source.count('v-if="!platformSkillReadonly"') >= 3
    # 脚本侧守卫：保存与右键菜单入口
    assert source.count("if (platformSkillReadonly.value) return") >= 4


def test_file_tree_component_supports_readonly() -> None:
    tree = _read(SKILL_TREE)
    assert "readonly?: boolean" in tree
    # 只读时不 emit 右键菜单、不渲染删除按钮
    assert "props.readonly ? undefined : emit" in tree or "props.readonly ? undefined" in tree
    view = _read(SKILL_VIEW)
    assert ':readonly="activeSkillScope !== \'personal\' && !canManagePlatformSkills"' in view


# ---------------------------------------------------------------------------
# MCP 页
# ---------------------------------------------------------------------------


def test_mcp_registry_keeps_can_save_as_the_only_readonly_switch() -> None:
    registry = _read(MCP_REGISTRY)
    # 平台（global）服务的写能力仍然只给管理员
    can_save_block = registry[
        registry.index("const canSave = computed(") : registry.index("const canSave = computed(") + 300
    ]
    assert "props.scope === 'personal'" in can_save_block
    assert "role === 'admin'" in can_save_block
    # 写入口都挂在 canSave（或 canSave && …）上：新建、全选、日志、同步、清理、
    # 变更抽屉、批量操作、工具 checkbox、测试/发布、菜单项、启停开关、「⋯」按钮
    assert registry.count('v-if="canSave') >= 16


def test_mcp_registry_skips_admin_only_requests_in_readonly_mode() -> None:
    registry = _read(MCP_REGISTRY)
    select_block = registry[
        registry.index("if (canSave.value) {") :
        registry.index("if (canSave.value) {") + 400
    ]
    # 只读用户不再请求用量与定义变更（两者都是 admin-only）
    assert "fetchServerUsage" in select_block
    assert "fetchDefinitionChanges" in select_block


def test_mcp_change_entry_points_require_can_save() -> None:
    registry = _read(MCP_REGISTRY)
    assert 'v-if="canSave && canSyncMcp && Number(server.pending_change_count || 0) > 0"' in registry
    assert 'v-if="canSave && canSyncMcp && !definitionHintDismissed && totalPendingChanges > 0"' in registry
    assert 'v-if="canSave && canSyncMcp && tool.is_available !== false && hasChangedTool(tool)"' in registry
    assert "canSave && canPublishMcp" in registry


def test_mcp_tool_param_chips_expose_full_schema() -> None:
    registry = _read(MCP_REGISTRY)
    # 只读用户看不到测试台，完整参数契约至少能从 chip 的 title / tooltip 读到
    assert "function toolParameterSchemaTitle(" in registry
    assert ':title="toolParameterSchemaTitle(tool)"' in registry
