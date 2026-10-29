"""知识库「可见范围」三档语义契约（授权可见 / 部门级 / 个人私有）。

背景：改造前 `visibility` 只是详情页上的展示标记，任何权限判定都不读它——
界面写着「team → 部门用户有权访问」「public → 全平台公开」，实际谁都按授权名单走。

现在三档真正参与判定（用户确认的口径）：

- `public`  授权可见：**需要授权**才能访问，创建人自动可用，可继续分配；
- `team`    部门级：业务归属部门（**含下级部门**）成员自动**只读**，也可继续分配；
- `private` 个人私有：仅创建人可见，**不允许**分配授权，历史授权记录一律不生效。

本文件守住四类约束：

1. 后端判定集中在 `resolve_knowledge_dataset_access`，且 private 分支忽略授权；
2. 授权端点必须拒绝 private 库（否则会出现"加了人却看不到"的假操作）；
3. 前端左栏按三档分 tab，部门级分组、授权可见/个人私有平铺；
4. 存量库通过迁移统一订正为 public，避免切换语义时静默撤权或扩权。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
RAGFLOW_ENDPOINT = ROOT / "app/api/portal/endpoints/ragflow.py"
PERMISSION_SERVICE = ROOT / "app/services/permission_service.py"


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _service() -> str:
    return PERMISSION_SERVICE.read_text(encoding="utf-8")


def _ragflow() -> str:
    return RAGFLOW_ENDPOINT.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 后端判定


def test_visibility_constants_and_normalization():
    service = _service()
    assert 'VISIBILITY_PUBLIC = "public"' in service
    assert 'VISIBILITY_TEAM = "team"' in service
    assert 'VISIBILITY_PRIVATE = "private"' in service
    # 空值/脏值必须兜底成 public（保持改造前"授权即可见"的行为，不误收紧）
    assert "def normalize_knowledge_visibility" in service


def test_access_resolution_goes_through_pure_function():
    service = _service()
    assert "def resolve_knowledge_dataset_access(" in service
    # 判定入口必须复用纯函数，而不是在方法体里另写一套
    assert "access = resolve_knowledge_dataset_access(" in service


def test_private_branch_ignores_grants():
    service = _service()
    # private 命中后必须 continue，跳过后续"授权即可见"的判断
    marker = "if visibility == VISIBILITY_PRIVATE:"
    assert marker in service
    tail = service.split(marker, 1)[1][:200]
    assert "continue" in tail


def test_team_branch_uses_path_prefix_for_sub_departments():
    service = _service()
    assert "def resolve_knowledge_dataset_access(" in service
    # 部门子树判定靠 path 前缀（末尾带 / 的物化路径），天然含下级部门
    assert "path.startswith(str(owner_path))" in service
    assert "owner_dept_paths" in service


def test_management_write_is_still_creator_only():
    """管理级写权限（改元数据/可见范围/删库/授权）恒为创建人。

    部门成员拿到的是**内容级**（content_writable）：能上传/删/解析文档、看切片，
    但不能改这个库归谁看。两者必须分开，否则部门成员能把团队的库改成私有独占。
    """
    service = _service()
    body = service.split("def resolve_knowledge_dataset_access(", 1)[1][:4000]
    # 按整行比对：`content_writable.add(...)` 会把 `writable.add(...)` 误判成子串
    body_lines = [line.strip() for line in body.split("\n")]
    assert body_lines.count("writable.add(dataset_id)") == 1
    # 部门匹配分支只加内容级，不加管理级
    team_branch = body.split("if visibility == VISIBILITY_TEAM", 1)[1][:400]
    team_lines = [line.strip() for line in team_branch.split("\n")]
    assert "content_writable.add(dataset_id)" in team_lines
    assert "writable.add(dataset_id)" not in team_lines


# ---------------------------------------------------------------- 授权端点


def test_permission_endpoints_reject_private_dataset():
    ragflow = _ragflow()
    assert "async def require_dataset_shareable(" in ragflow
    # 只有「新增授权」拦：私有库不允许分配授权
    assert ragflow.count("await require_dataset_shareable(db, dataset_id)") == 1
    # 删除侧刻意放行，让改成私有之前留下的历史授权能被清理，
    # 否则会出现「看得见这条授权、点删除却 400」的死角
    assert "require_dataset_shareable" in ragflow
    assert "个人私有知识库不支持分配授权" in ragflow


# ---------------------------------------------------------------- 前端 tab


def test_left_tree_has_three_visibility_tabs():
    page = _page()
    assert "VISIBILITY_TABS" in page
    assert "label: '授权可见'" in page
    assert "label: '部门级'" in page
    assert "label: '个人私有'" in page
    # tab 切换必须驱动列表过滤，而不是只做视觉高亮
    assert "activeVisibility" in page
    assert "visibilityScopeDatasets" in page
    assert "visibilityTabCount(tab.key)" in page


def test_tab_filters_list_before_search():
    page = _page()
    # 先按 tab 圈定范围，再在该范围内搜索
    assert "datasets.value.filter(dataset => visibilityOf(dataset) === activeVisibility.value)" in page
    assert "if (!query) return visibilityScopeDatasets.value" in page
    assert "return visibilityScopeDatasets.value.filter(ds => {" in page


def test_team_tab_groups_by_department_others_are_flat():
    page = _page()
    assert "if (activeVisibility.value !== 'team')" in page
    assert "isFlat: true" in page
    # 平铺模式不渲染部门分组头
    assert 'v-if="!group.isFlat && !isDeptGroupHidden(group)"' in page


def test_private_dataset_hides_permission_assignment_entry():
    page = _page()
    assert 'v-if="canManagePermissions && !loadingPermissions && !isPrivateDataset"' in page
    assert "个人私有知识库不支持分配授权。" in page


# ---------------------------------------------------------------- 前端文案


def test_detail_page_states_three_scopes_truthfully():
    page = _page()
    # 授权可见：必须说明"需要授权"，不能暗示自动放行
    assert "其他人<b>必须</b>在下方被显式授权才能访问" in page
    # 部门级：必须说明含下级部门 + 只读
    assert "的成员（<b>含下级部门</b>）自动获得只读权限" in page
    # 个人私有：必须说明不可分配
    assert "仅你自己可见，<b>不支持</b>分配授权" in page


def test_create_and_edit_forms_expose_visibility_as_two_line_dropdown():
    page = _page()
    # 两处表单（创建 / 编辑）都用双行下拉：标题 + 场景说明
    assert page.count("<div data-visibility-select") == 2
    assert "VISIBILITY_SCOPE_OPTIONS" in page
    assert "适合：{{ option.scene }}" in page
    # 每档都要有"场景"备注，而不是只有一个名字
    assert "草稿、个人笔记、待整理资料" in page
    assert "部门内部资料、团队对齐" in page
    assert "跨部门共享、需要精确控制名单" in page
    # 每档都要有图标（锁 / 楼宇 / 地球），收起态也显示当前档位图标
    assert page.count("iconPath") >= 5  # 3 个档位 + 兜底项 + 类型声明
    assert page.count("iconClass") >= 5
    assert ':d="option.iconPath"' in page
    assert ':d="visibilityOptionOf(form.visibility).iconPath"' in page
    assert page.count("visibilityOptionOf(form.visibility).iconPath") == 2  # 两处弹窗
    # 左侧三个 tab 复用同一定义，不另写字面量
    assert ':d="visibilityOptionOf(tab.key).iconPath"' in page
    assert ':class="visibilityOptionOf(tab.key).iconClass"' in page


def test_new_dataset_defaults_to_private_with_explicit_warning():
    page = _page()
    # 新建库默认 private（用户确认的口径）
    assert "visibility: 'private'," in page
    assert 'visibility: str = "private"' in _ragflow()
    # 但必须显著提示，避免用户没注意到范围就建出"只有自己能看"的库
    assert "当前为<b>个人私有</b>" in page
    assert "要共享请先改成部门级或授权可见" in page
    # 默认项在下拉里要有徽标
    assert "isDefault: true" in page


def test_visibility_field_comes_before_owner_department():
    """可见范围必须排在「业务归属部门」之前：先决定范围，再决定给哪个部门。"""
    page = _page()
    vis_label = "可见范围</label>"
    dept_label = "业务归属部门/负责人</label>"

    first_vis = page.index(vis_label)
    first_dept = page.index(dept_label)
    assert first_vis < first_dept, "创建弹窗里可见范围应排在归属部门之前"

    second_vis = page.index(vis_label, first_vis + 1)
    second_dept = page.index(dept_label, first_dept + 1)
    assert second_vis < second_dept, "编辑弹窗里可见范围应排在归属部门之前"


def test_owner_department_only_editable_for_team_scope():
    page = _page()
    # 非部门级时归属部门禁用（两处弹窗都要有）
    assert page.count(':disabled="form.visibility !== \'team\'"') == 2
    assert "disabled:bg-gray-50" in page
    assert "disabled:cursor-not-allowed" in page
    # 禁用时文字随之变灰（双行结构里靠 :class 控制，不再用 disabled:text-*）
    assert "form.visibility === 'team' ? 'text-gray-800' : 'text-gray-400'" in page
    # 禁用时必须解释原因，并说明已选值会被保留
    assert "仅「部门级」需要归属部门" in page
    assert "已选值会保留" in page
    # 部门级却没选部门时必须明确告知会退化，否则用户以为已生效
    assert "未选部门会退化为授权可见" in page


def test_switching_to_private_warns_about_revoked_grants():
    page = _page()
    assert "hasExistingPermissions" in page
    assert "个授权对象将失去访问权" in page


def test_old_misleading_copy_is_gone():
    page = _page()
    assert "仅作展示标记" not in page
    assert "该标记<b>不会</b>" not in page
    assert '公开属性 (Visibility)' not in page


# ---------------------------------------------------------------- 存量迁移


def test_migration_adds_owner_user_id_for_handover():
    """归属人改造必须双库同步，且要按用户表回填存量库。

    可见范围归一（原 V189/V89）已从仓库移除，改由运维按发布说明手动执行一次，
    因此这里不再断言那两个文件存在。
    """
    mysql = list((ROOT / "db-prod").glob("V189-add_owner_user_id_*.sql"))
    postgres = list((ROOT / "db-prod-pg").glob("V89-add_owner_user_id_*.sql"))
    assert mysql, "缺少 MySQL 迁移 db-prod/V189-add_owner_user_id_*.sql"
    assert postgres, "缺少 PostgreSQL 迁移 db-prod-pg/V89-add_owner_user_id_*.sql"

    for sql in (mysql[0], postgres[0]):
        text = sql.read_text(encoding="utf-8")
        assert "owner_user_id" in text
        # 回填必须按用户表关联 created_by：否则存量库的归属人全靠人工补
        assert "ai_agent_users" in text and "created_by" in text
        # 只补空值，重跑不能覆盖已经转移过的归属
        assert "IS NULL" in text

def test_only_adding_permissions_is_blocked_for_private_datasets():
    """私有库：新增授权必须拒绝，但删除历史授权要放行（否则脏记录清不掉）。"""
    endpoint = (ROOT / "app/api/portal/endpoints/ragflow.py").read_text(encoding="utf-8")

    add_start = endpoint.index("async def add_dataset_permissions")
    add_end = endpoint.index("async def delete_dataset_permission")
    next_route = endpoint.find("\n@router.", add_end)
    delete_block = endpoint[add_end: next_route if next_route != -1 else len(endpoint)]

    add_block = endpoint[add_start:add_end]

    assert "await require_dataset_shareable(db, dataset_id)" in add_block
    assert "await require_dataset_shareable" not in delete_block
    # 删除侧仍要有写权限校验，不能为了能清理就放开
    assert "await require_dataset_write_access" in delete_block


def test_private_dataset_creation_skips_creator_grant():
    """私有库不落创建人授权行：否则详情页显示"授权用户：创建人"与"不支持分配授权"自相矛盾。"""
    endpoint = (ROOT / "app/api/portal/endpoints/ragflow.py").read_text(encoding="utf-8")
    create_start = endpoint.index("async def create_ragflow_dataset")
    create_end = endpoint.index("@router.put(\"/datasets/{dataset_id}/metadata\")")
    block = endpoint[create_start:create_end]

    assert "_grant_dataset_to_creator" in block
    assert "VISIBILITY_PRIVATE" in block
