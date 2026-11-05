"""知识库「编辑权限范围」前端契约：入口门槛、列表徽标、弹窗提交。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = (ROOT / "frontend/src/views/KnowledgeBaseManagement.vue").read_text(encoding="utf-8")
API = (ROOT / "app/api/portal/endpoints/ragflow.py").read_text(encoding="utf-8")
DIALOG = (ROOT / "frontend/src/components/knowledge/KnowledgeEditPolicyDialog.vue").read_text(encoding="utf-8")


def test_entry_requires_global_permission_and_manage_right() -> None:
    """用户必须有全局编辑权限，且是管理员/归属人，入口才出现。"""
    assert "canEdit.value && canManageDataset(dataset)" in VIEW
    assert 'v-if="canConfigureEditPolicy(selectedDataset)"' in VIEW
    # 私有库不支持该设置
    assert """v-if="visibilityOf(selectedDataset) !== 'private'\"""" in VIEW


def test_detail_panel_and_list_badge_share_one_dialog() -> None:
    """方案 A（详情页模块）与方案 B（列表徽标）共用同一个弹窗。"""
    assert "KnowledgeEditPolicyDialog" in VIEW
    assert '@click="openEditPolicyDialog(selectedDataset)"' in VIEW
    assert "openEditPolicyDialog(ds)" in VIEW
    assert "editPolicyLabel(ds)" in VIEW


def test_dialog_submits_policy_to_backend() -> None:
    assert "/edit-policy`" in DIALOG
    assert "mode: mode.value" in DIALOG
    # 只认具体用户，不支持角色
    assert 'item.type !== "role"' in DIALOG
    assert "不支持角色" in DIALOG


def test_dialog_modes_follow_visibility() -> None:
    """TEAM 库不提供 granted / all_viewers 两档。"""
    assert 'if (visibility.value === "team")' in DIALOG
    assert "team_members" in DIALOG
    assert "all_viewers" in DIALOG


def test_backend_endpoints_are_registered_with_right_permissions() -> None:
    """接口存在、权限双重校验、策略只替换子键。"""
    assert '@router.put("/datasets/{dataset_id}/edit-policy")' in API
    assert '@router.get("/datasets/{dataset_id}/edit-policy/candidates")' in API
    block = API.split('@router.put("/datasets/{dataset_id}/edit-policy")', 1)[1]
    assert 'require_element_permission(user, db, "element:knowledge:edit")' in block
    assert "require_dataset_write_access(user, db, [dataset_id])" in block
    # 只替换 edit_policy 子键，不能把 folder_structure 之类的键冲掉
    assert 'key="edit_policy"' in block
    # private 库直接拒绝
    assert "个人私有库只有归属人可见" in block


def test_backend_content_write_uses_policy_aware_set() -> None:
    """文档写操作走内容级集合，策略才会真的生效。"""
    assert 'access.get("content_writable_ids")' in API
    assert "async def require_dataset_content_write_access" in API
