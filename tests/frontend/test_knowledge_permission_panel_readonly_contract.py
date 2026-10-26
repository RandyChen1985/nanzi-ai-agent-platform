"""只读成员在详情页看到的「系统授权分配详情」。

后端 `GET /datasets/{id}/permissions` 用 `require_dataset_write_access` 校验，
所以非创建人的部门成员必然 403 —— 这是既定权限，不是故障。但前端原先把
这个 403 渲染成红字「授权信息读取失败」+ 重试按钮，还照样显示
「分配授权成员」按钮（点了同样 403），看起来像系统坏了。

这里锁住三点：按钮受数据写权限约束、只读成员不发该请求、面板降级为说明文案。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

PAGE = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/views/KnowledgeBaseManagement.vue"
)


@pytest.fixture(scope="module")
def page() -> str:
    assert PAGE.exists()
    return PAGE.read_text(encoding="utf-8")


def test_grant_button_requires_dataset_write_permission(page):
    """只有菜单权限不够：后端 POST 还要求对该库有写权限。"""
    assert "const canManagePermissions = computed(() => {" in page
    block = page.split("const canManagePermissions = computed(() => {", 1)[1].split("})", 1)[0]
    assert "hasPermission('element:user:edit')" in block
    assert "canManageDataset(" in block


def test_readonly_member_skips_the_request_and_reports_restricted(page):
    assert "const permissionsRestricted = ref(false)" in page
    # 早退：不再打一个注定 403 的请求
    fetch_block = page.split("const fetchDatasetPermissions = async", 1)[1].split(
        "} catch (err)", 1
    )[0]
    assert "canManageDataset(target)" in fetch_block
    assert "permissionsRestricted.value = true" in fetch_block


def test_panel_explains_restricted_instead_of_erroring(page):
    assert "仅创建人与管理员可查看授权名单" in page
    # 顺序：无权查看优先于「读取失败」，否则只读成员永远看到红字。
    # 授权面板已从「文档与设置」并入「引用与授权」，它是那里独立区块的第一分支，
    # 所以是 v-if 而不是 v-else-if —— 优先顺序的意图不变。
    restricted_at = page.index('v-if="permissionsRestricted"')
    error_at = page.index('v-else-if="permissionsError"')
    assert restricted_at < error_at


def test_team_hint_hides_grant_instruction_for_readonly_members(page):
    """「也可在下方额外授权」对只读成员是误导，他根本没有这个入口。"""
    assert '<template v-if="selectedDatasetWritable">；也可在下方额外授权给其他用户/角色</template>' in page
    assert "const selectedDatasetWritable = computed(" in page
