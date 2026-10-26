"""「分配知识库授权成员」弹窗要显示用户所属部门。

动机很具体：给部门级知识库加人的时候，管理员需要先判断「这个人在不在我选的
那个部门」。只给姓名 + 账号时只能靠记忆，加错人不会报错，只会有人一直看不到库。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_candidate_row_renders_departments(page):
    assert "u.departments" in page
    assert "v-for=\"dept in u.departments\"" in page
    # 没有部门的用户也要给个明确说明，否则那一栏是空白，看着像加载失败
    assert "未分配部门" in page


def test_search_matches_department_name(page):
    block = page.split("const availableUsers = computed(", 1)[1].split("return filtered", 1)[0]
    assert "dept?.name" in block
    assert "搜索用户姓名、账号或部门" in page


def test_candidate_api_provides_departments():
    """前后端字段名必须对齐，否则前端永远是空列表。"""
    backend = (ROOT / "app/api/portal/endpoints/metadata.py").read_text(encoding="utf-8")
    assert '"departments": depts_by_user.get(int(u.id), [])' in backend
