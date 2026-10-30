"""知识库「业务归属部门」契约。

背景（改造前的事实）：

- 字段在详情页叫「业务归属部门 (Owner)」、在创建弹窗叫「归属部门/人员」、
  在编辑弹窗叫「业务归属人员」，三处不一致；
- 详情页的可见性说明写着「team → 业务归属部门的用户有权访问」「public →
  全平台用户均有权检索」，但后端 `get_knowledge_base_access()` 里既没有
  owner 也没有 visibility 的分支，文案承诺大于实现；
- `owner` 是自由文本，没法按部门浏览。

改造后的约束（本文件守的就是这几条）：

1. 名称统一为「业务归属部门/负责人」，旧文案不得回归；
2. 可见性说明必须显式声明"标记不等于授权"；
3. 归属部门通过只读接口 `/api/portal/departments/options` 获取
   （不能用 `/departments/tree`，那需要部门管理权限，知识库编辑者会 403）；
4. 提交一律走 `owner_dept_id`，且左侧树按部门分组渲染。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
RAGFLOW_ENDPOINT = ROOT / "app/api/portal/endpoints/ragflow.py"
DEPARTMENTS_ENDPOINT = ROOT / "app/api/portal/endpoints/departments.py"
PERMISSION_SERVICE = ROOT / "app/services/permission_service.py"

UNIFIED_LABEL = "业务归属部门/负责人"


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_unified_label_has_no_legacy_variants():
    page = _page()
    assert UNIFIED_LABEL in page
    # 统一之后，这三处旧叫法都必须消失
    assert "业务归属部门 (Owner)" not in page
    assert "归属部门/人员" not in page
    assert "业务归属人员" not in page


def test_visibility_copy_matches_permission_semantics():
    page = _page()
    # 三档可见范围已真正参与判定，文案不能再写成「标记」或「不会自动授权」
    assert "该标记<b>不会</b>按业务归属部门自动授权" not in page
    assert "该标记<b>不会</b>自动向全平台开放" not in page
    assert "仅作展示标记" not in page
    # 旧文案里的错误承诺不得回归
    assert "业务归属部门（{{ selectedDataset.owner || '未指派' }}）的用户有权访问" not in page
    # TEAM 级必须说明「含下级部门」且是只读
    assert "含下级部门" in page
    # 个人私有必须说明「不支持分配授权」
    assert "不支持分配授权" in page


def test_owner_department_uses_read_only_options_endpoint():
    page = _page()
    assert "/api/portal/departments/options" in page
    # 部门管理页的树接口要求部门管理权限，知识库页复用会 403
    assert "/api/portal/departments/tree" not in page


def test_options_endpoint_is_login_only_and_hides_member_counts():
    endpoint = DEPARTMENTS_ENDPOINT.read_text(encoding="utf-8")
    assert '@router.get("/options")' in endpoint
    options_block = endpoint.split('@router.get("/options")', 1)[1].split("@router.post", 1)[0]
    # 只要求登录，不挂部门管理权限
    assert "Depends(get_current_user)" in options_block
    assert "READ_PERMISSION" not in options_block
    # 只读目录不得带出成员数与成员名单
    assert "member_count" not in options_block


def test_page_submits_owner_dept_id_not_free_text_owner():
    page = _page()
    assert "owner_dept_id: form.value.ownerDeptId || null" in page
    assert "owner_dept_id: form.value.ownerDeptId || 0" in page
    # 自由文本输入框已移除
    assert 'v-model="form.owner"' not in page


def test_left_tree_is_grouped_by_department():
    page = _page()
    assert "deptDatasetGroups" in page
    assert 'v-for="group in deptDatasetGroups"' in page
    # 分组必须带"未关联部门"，否则存量自由文本会从树上消失
    assert "未关联部门" in page


def test_backend_endpoint_persists_owner_dept_id_and_validates_department():
    endpoint = RAGFLOW_ENDPOINT.read_text(encoding="utf-8")
    assert "owner_dept_id: Optional[int] = None" in endpoint
    assert "_resolve_owner_department" in endpoint
    assert '"owner_dept_id" in payload.model_fields_set' in endpoint
    assert "业务归属部门不存在或已被删除" in endpoint


def test_owner_text_stays_out_of_permission_model():
    """护栏：`owner`（自由文本快照）不得进入权限判定。

    注意：「TEAM 级」可见范围落地后，`owner_dept_id` **会**作为 team 档的输入
    参与判定（必须与 visibility=team 绑定，见
    `tests/services/test_knowledge_access_visibility.py`）；而自由文本 `owner`
    仍然只是展示兜底，任何档位都不读它。
    """
    service = PERMISSION_SERVICE.read_text(encoding="utf-8")
    access_block = service.split("async def get_knowledge_base_access", 1)[1].split(
        "async def filter_knowledge_dataset_ids", 1
    )[0]
    # 自由文本 owner 不参与判定
    assert "KnowledgeBaseMetadata.owner ==" not in access_block
    assert "KnowledgeBaseMetadata.owner," not in access_block
    # 归属部门只在 team 档生效，必须与 VISIBILITY_TEAM 同现
    assert "KnowledgeBaseMetadata.owner_dept_id" in access_block
    assert "VISIBILITY_TEAM" in access_block
