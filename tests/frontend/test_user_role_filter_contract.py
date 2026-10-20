"""用户弹窗「业务角色」搜索的接线契约。

用户反馈（admin）：创建用户时搜 `de` 选不到 `default`，而角色管理页能搜到。
根因是过滤只比 `name`，而老库里该角色 name 是本地化文案、只有 code 是 `default`。
行为口径在 `test_business_role_filter_behavior.py`（纯函数），这里只校验接线与空态。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
USERS = ROOT / "frontend/src/views/Users.vue"
FILTER = ROOT / "frontend/src/utils/roleFilter.ts"

pytestmark = pytest.mark.no_infrastructure


def _users() -> str:
    return USERS.read_text(encoding="utf-8")


def test_users_page_delegates_role_filtering_to_the_pure_function():
    """过滤必须走共享纯函数，不能又回到只看 name 的内联实现。"""
    source = _users()

    assert FILTER.exists(), "缺少 frontend/src/utils/roleFilter.ts"
    assert 'from "../utils/roleFilter"' in source
    assert "filterBusinessRoles(" in source
    assert "r.name.toLowerCase().includes" not in source, "只匹配 name 正是本次搜不到 default 的原因"


def test_filter_matches_both_name_and_code():
    """纯函数本身要两列都查（老库 code=default / name=默认用户）。"""
    source = FILTER.read_text(encoding="utf-8")

    assert ".code" in source and ".name" in source
    assert "toLowerCase" in source
    assert "trim()" in source, "前后空格不该让人搜不到"


def test_role_picker_distinguishes_three_empty_states():
    """三种「看不到角色」的原因要能分开：没权限 / 没角色 / 搜索无匹配。"""
    source = _users()

    assert "roleListError && businessRoles.length === 0" in source, "权限或网络失败要单独提示"
    assert "暂无可分配的业务角色" in source, "一个角色都没有时不能留个空框"
    assert "没有匹配" in source, "搜不到时要说明是搜索无匹配，而不是让人以为角色不存在"
    assert "businessRoles.length" in source, "提示里要带上总数量，便于判断是不是搜索条件太窄"


def test_role_search_goes_through_the_backend():
    """关键词必须传给后端 `search`。

    列表按创建时间倒序只回一页，`default` 这类最早创建的角色被截断在最后一页——
    本地过滤再怎么改都搜不到，只有后端全表匹配才行（角色管理页就是这么做的）。
    """
    source = _users()
    fetch = source.split("const fetchBusinessRoles = async", 1)[1].split("/** 输入即搜", 1)[0]

    assert "search: keyword.trim() || undefined" in fetch, "关键词没有传给后端"
    assert "roleFetchSeq" in fetch, "快速输入时旧响应可能后到，必须丢弃过期结果"


def test_search_is_debounced_and_only_runs_while_dialog_open():
    """输入即搜要防抖，且弹窗关闭后不再空打接口。"""
    source = _users()

    assert "setTimeout(" in source
    assert "showCreateDialog.value && !showEditDialog.value" in source


def test_selected_roles_are_summarized_outside_the_current_page():
    """已选角色数要以表单为准并标明「不在当前列表」，否则编辑时会以为角色丢了。"""
    source = _users()

    assert "selectedRoleSummary" in source
    assert "已选" in source
    assert "保存时保留" in source
