"""知识库「归属人」与「转移归属」的契约。

背景：原先归属靠 `created_by` 用户名文本比较 —— 用户改名、账号重建或离职后，
名字对不上任何人，他名下的 private 库会变成「只有管理员能看见」的无主库，
team 库的管理权也一并消失，而且没有任何交接入口。

本轮把 `owner_user_id` 作为管理权（可读可写可删可授权）的唯一真源，
`created_by` 降级为「创建人」历史快照。这里锁住四件事：

1. 判定是「归属人优先、created_by 仅兜底」，**不是**「或」——否则把库转交给同事后，
   原创建人仍能凭文本快照继续管理，转移等于没做；
2. 转移端点必须同时要求 `element:knowledge:edit` 与**管理级**写权限，并写审计；
3. 建库写入 `owner_user_id`、列表补归属人显示名（否则前端只能显示一串数字）；
4. 前端入口与后端同口径，且弹窗必须说清「private 库原归属人会立刻失权」。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "app/api/portal/endpoints/ragflow.py"
SERVICE = ROOT / "app/services/knowledge_base_service.py"
PERMISSION = ROOT / "app/services/permission_service.py"
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def backend() -> str:
    return BACKEND.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def service() -> str:
    return SERVICE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def permission() -> str:
    return PERMISSION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 判定口径


def test_owner_user_id_wins_and_created_by_is_only_fallback(permission):
    """归属人为空才回落 created_by；两个条件不能写成「或」。"""
    body = permission.split("def resolve_knowledge_dataset_access(", 1)[1][:6000]

    assert "owner_user_id is None" in body
    assert "owner_user_id == current_user_id" in body
    # 「或」的写法会把两段并成一个条件，这里直接排除那种形状
    compact = " ".join(body.split())
    assert "or ( owner_user_id is None" in compact


def test_private_agent_proxy_uses_the_same_owner_semantics(permission):
    """检索侧摘除私有库必须与判定同源，否则转移后原创建人仍被智能体代理放行。"""
    body = permission.split("def find_private_datasets_not_owned(", 1)[1][:4000]

    assert "owner_user_id" in body
    assert "owner_user_id is None" in body


def test_transfer_owner_does_not_overwrite_created_by(service):
    """转移只改归属人：created_by 是真实创建记录，覆盖掉就再也查不清来源。"""
    body = service.split("async def transfer_owner(", 1)[1][:2000]

    assert "record.owner_user_id = int(target_user_id)" in body
    assert "record.created_by" not in body


# ---------------------------------------------------------------- 端点与权限


def test_transfer_endpoint_requires_element_code_and_management_write(backend):
    body = backend.split("async def transfer_dataset_owner(", 1)[1][:3000]

    assert 'require_element_permission(user, db, "element:knowledge:edit")' in body
    # 必须是管理级：部门成员有内容级写权限，但不能把库交出去
    assert "require_dataset_write_access(user, db, [dataset_id])" in body


def test_transfer_endpoint_audits_and_invalidates_both_sides(backend):
    body = backend.split("async def transfer_dataset_owner(", 1)[1][:4000]

    assert '"datasets:transfer_owner"' in body
    assert "invalidate_cached_permissions_for_users" in body
    # 旧归属人与新归属人的缓存都要失效，少一侧就会出现「页面能改、接口 403」
    assert "previous_owner_user_id" in body


def test_transfer_rejects_disabled_accounts(backend):
    body = backend.split("async def transfer_dataset_owner(", 1)[1][:3000]

    assert "目标用户不存在" in body
    assert "目标用户已停用" in body


def test_owner_candidates_uses_knowledge_code_not_metadata_code(backend):
    """候选用户不能复用 metadata/candidates：它要 element:metadata:edit。"""
    body = backend.split("async def list_owner_candidates(", 1)[1][:1500]

    assert 'require_element_permission(user, db, "element:knowledge:edit")' in body
    assert 'require_element_permission(user, db, "element:metadata:edit")' not in body


def test_create_dataset_writes_owner_user_id(backend):
    body = backend.split("async def create_ragflow_dataset(", 1)[1][:4000]

    assert 'owner_user_id=int(user["user_id"])' in body


def test_list_attaches_owner_display_name(backend):
    """列表必须把 owner_user_id 翻译成人名，否则前端只能显示数字。"""
    assert "_attach_owner_user_names" in backend
    body = backend.split("async def _attach_owner_user_names(", 1)[1][:2000]
    assert "real_name" in body and "owner_user_name" in body


# ---------------------------------------------------------------- 前端入口


def test_transfer_entry_is_visible_to_management_only(page):
    assert "const canTransferOwner = (dataset?: KnowledgeBase | null) =>" in page
    block = page.split("const canTransferOwner = (dataset?: KnowledgeBase | null) =>", 1)[1].split("\n\n", 1)[0]
    assert "canEdit.value" in block
    assert "canManageDataset(" in block


def test_transfer_entry_and_modal_are_wired(page):
    assert '@click="openTransferOwner(selectedDataset)"' in page
    assert "转移知识库归属人" in page
    assert "/api/portal/ragflow/owner-candidates" in page
    assert "/owner`," in page


def test_modal_explains_private_library_loses_access(page):
    """最容易踩的坑：以为转移只是换个管理员，结果 private 库原主直接打不开。"""
    footer = page.split("转移知识库归属人", 1)[1][:4000]

    assert "你不再拥有该库的管理权" in footer
    assert "visibilityOf(transferTarget) === 'private'" in footer
    assert "立刻失去访问" in footer


def test_header_shows_owner_with_creator_in_title(page):
    assert "datasetOwnerUserLabel(selectedDataset)" in page
    assert "创建人：" in page


def test_candidates_are_flagged_when_target_cannot_manage(backend):
    """候选要带「能不能管」标记：转移本身不校验功能点，转错人库就成了无主状态。"""
    body = backend.split("async def list_owner_candidates(", 1)[1][:3000]

    assert "get_users_with_element_permission" in body
    assert '"can_manage": int(row.id) in manageable' in body


def test_modal_warns_about_missing_edit_permission(page):
    modal = page.split("转移知识库归属人", 1)[1].split("</Modal>", 1)[0]

    assert "can_manage === false" in modal
    assert "无编辑权限" in modal
    assert "element:knowledge:edit" in modal
    # 提示归提示，提交按钮不能被锁死：交接时角色往往还没配好，硬拦会卡住流程
    assert "selectedTransferCandidate && selectedTransferCandidate.can_manage === false" in modal


def test_owner_label_does_not_fall_back_when_account_deleted(page):
    """有 owner_user_id 却解析不出名字 = 账号已删，不能回落显示创建人。"""
    block = page.split("const datasetOwnerUserLabel", 1)[1][:900]

    assert "owner_user_id" in block
    assert "（已删除）" in block
    # 回落成创建人会让人以为「根本没转移过」，把问题藏起来
    assert "dataset.owner_user_name || dataset.created_by" not in block
