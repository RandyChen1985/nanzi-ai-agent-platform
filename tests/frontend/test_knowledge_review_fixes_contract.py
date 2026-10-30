"""外部 Review 反馈的 7 处缺陷 + 1 处同类漏网，逐条锁定。

这些问题有一个共同点：**都不会报错，只会让界面说的和实际做的不一致**。
比如编辑弹窗显示「个人私有」而详情页显示「授权可见」，用户改个名字保存，
库就静默换了可见范围 —— 没有异常、没有提示，只有别人突然打不开了。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
USERS = ROOT / "frontend/src/views/Users.vue"
PERM = ROOT / "app/services/permission_service.py"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def users() -> str:
    return USERS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def perm() -> str:
    return PERM.read_text(encoding="utf-8")


# ---------- 1. 编辑弹窗的可见范围兜底必须与详情页同口径 ----------

def test_edit_form_falls_back_to_same_visibility_as_detail_page(page):
    """`|| 'private'` 会让 visibility 为空的库在弹窗里显示成私有，保存即静默改范围。"""
    assert "dataset.visibility || 'private'" not in page
    # 现在只剩编辑弹窗这一处需要 visibility：目录写入已改成独立端点，
    # 不再整份 PUT 元数据（之前那个 silent save 是「挪个文档就改范围」的源头）
    assert page.count("visibility: visibilityOf(dataset),") == 1
    assert "saveDatasetExtraConfig" not in page


def test_folder_save_does_not_touch_visibility(page):
    """目录结构只能写 folder_structure，绝不能顺带回写可见范围。"""
    block = page.split("const saveFolderStructure", 1)[1].split(
        "虚拟文件夹层级管理结束", 1
    )[0]
    assert "visibilityOf(dataset)" not in block
    assert "/folder-structure" in block


# ---------- 2. 从左侧树直接编辑时，授权列表要跟着换 ----------

def test_open_edit_refreshes_permissions_for_the_target_dataset(page):
    block = page.split("const openEdit = (dataset: KnowledgeBase)", 1)[1].split(
        "customQuestions.value = []", 1
    )[0]
    # 未选中该库时走 selectDatasetNode（内部会 fetchDatasetPermissions）
    assert "selectDatasetNode(dataset)" in block
    assert "editingDsId !== selectedId.value" in block


# ---------- 3. 运营分析深链要联动左侧 Tab ----------

def test_deeplink_switches_visibility_tab(page):
    block = page.split("const applyDeepLinkFromQuery", 1)[1].split("onMounted", 1)[0]
    assert "activeVisibility.value = visibilityOf(dataset)" in block


# ---------- 4. 首屏落在概览，不再默认选中任何库 ----------

def test_entry_lands_on_overview_instead_of_auto_selecting(page):
    """这条曾经锁的是「首屏默认选中要落在当前 Tab 内」。

    原先 `fetchDatasets` 会在没有选中时自动选中第一个库，并顺手把 activeVisibility
    改成那个库的分类——否则左侧「授权可见 0 个」而右侧显示着一个 TEAM 级库。补偿
    逻辑和默认选中是一体的。

    现在进页面落在概览（`selectedType === null`），选中完全交给用户点击，那条对齐
    Tab 的补偿也就没有再存在的理由。所以这里反转断言：默认选中必须消失。
    """
    block = page.split("const fetchDatasets", 1)[1].split("只读部门目录", 1)[0]
    assert "visibilityScopeDatasets.value[0] ?? datasets.value[0]" not in block
    assert "selectDatasetNode(firstDataset)" not in block
    # 概览态靠 selectedType === null 表达
    assert "const showOverview = () => {" in page
    assert "selectedType.value = null" in page


# ---------- 5. 用户页全选不能带上私有库 ----------

def test_users_select_all_excludes_private_datasets(users):
    block = users.split("const selectableResources = computed(", 1)[1].split(");", 1)[0]
    assert "isPrivateKnowledgeBase(r)" in block


# ---------- 6. 停用部门不再授予 team 库可见性 ----------

def test_team_visibility_ignores_disabled_departments(perm):
    assert "DEPARTMENT_STATUS_ENABLED" in perm
    # 归属部门与成员部门两条查询都要带上状态过滤
    assert perm.count("Department.status == DEPARTMENT_STATUS_ENABLED") == 2


# ---------- 7. 私有库判定忽略软删记录 ----------

def test_private_lookups_skip_soft_deleted_metadata(perm):
    for fn in ("_exclude_private_datasets", "find_private_datasets_not_owned"):
        block = perm.split(f"async def {fn}", 1)[1].split("async def ", 1)[0]
        assert 'KnowledgeBaseMetadata.status != "deleted"' in block, fn


# ---------- 8. 注释不能再说「该字段不参与权限」 ----------

@pytest.mark.parametrize(
    "path",
    [
        "app/models/knowledge.py",
        "app/api/portal/endpoints/ragflow.py",
        "db-prod/V188-add_owner_dept_to_knowledge_metadata.sql",
        "db-prod-pg/V88-add_owner_dept_to_knowledge_metadata.sql",
    ],
)
def test_comments_no_longer_claim_owner_dept_is_not_a_permission_input(path):
    """team 级下 owner_dept_id 就是自动只读的判定依据，注释必须说清，否则后人会随手改。"""
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "不参与任何权限" not in text
    assert "team" in text.lower()
