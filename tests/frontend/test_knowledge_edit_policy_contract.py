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


def test_list_grant_column_opens_references_drawer() -> None:
    """列表「授权」列可直接打开抽屉的「引用与授权」页，省掉先进详情再切 Tab。"""
    assert "const openDatasetReferencesFor" in VIEW
    assert "openLibDrawer('references')" in VIEW
    assert '@click.stop="openDatasetReferencesFor(ds)"' in VIEW
    # 没有管理权限时保持纯文本，避免「能点但被后端 403」
    assert "canManageDataset(ds) ? 'cursor-pointer" in VIEW


def test_detail_header_shows_my_edit_permission_badge() -> None:
    """详情页头部显示「可编辑 / 只读」，并区分只读的两种原因。"""
    assert "selectedDatasetContentWritable" in VIEW
    assert "{{ selectedDatasetContentWritable ? '可编辑' : '只读' }}" in VIEW
    # 只读原因：功能权限缺失 vs 策略没覆盖
    assert "当前角色没有知识库的上传/删除/解析功能权限" in VIEW
    assert "本库的编辑权限范围没有覆盖到你" in VIEW
