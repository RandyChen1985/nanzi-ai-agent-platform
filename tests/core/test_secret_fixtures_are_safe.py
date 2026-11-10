"""测试 fixture 不得清空凭据表。

这组契约来自一次真实事故：``test_secret_*.py`` 的 fixture 用
``DELETE FROM sys_secrets`` 清表，而这些测试连的是**开发库**。跑一次
``pytest tests/core`` 就把开发者自己建的凭据全删了——包括正在联调的那条。

事故没有报错：DELETE 成功了，测试全绿。所以这件事必须靠静态契约守住，
不能靠"下次注意"。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 会连数据库、需要自己清理数据的测试文件。
#: test_secret_param_binding.py 是纯逻辑（不碰库），不在其列。
SECRET_TEST_FILES = [
    ROOT / "tests/core/test_secret_center.py",
    ROOT / "tests/core/test_secret_injection.py",
    ROOT / "tests/core/test_secret_resolver.py",
    ROOT / "tests/api/portal/test_secret_endpoints.py",
]

#: 整表删除的特征写法
TABLE_WIPE_PATTERNS = [
    r"delete\(\s*table\s*\)",
    r"delete\(\s*[A-Za-z_]+\.__table__\s*\)",
    r"DELETE\s+FROM\s+sys_secret",
    r"TRUNCATE\s+",
]


def _code_without_comments(path: Path) -> str:
    """去掉注释和 docstring，只看真正会执行的代码。

    注释里会提到「不能清表」，那是说明，不是行为。
    """
    source = path.read_text(encoding="utf-8")
    source = re.sub(r'"""[\s\S]*?"""', "", source)
    source = re.sub(r"'''[\s\S]*?'''", "", source)
    lines = [line.split("#", 1)[0] for line in source.splitlines()]
    return "\n".join(lines)


@pytest.mark.parametrize("path", SECRET_TEST_FILES)
def test_no_table_wipe(path):
    code = _code_without_comments(path)
    for pattern in TABLE_WIPE_PATTERNS:
        assert not re.search(pattern, code), (
            f"{path.name} 里还有整表删除（{pattern}）。"
            "测试没有权利假设整张表都是它建的——只清理自己建的那几条。"
        )


@pytest.mark.parametrize("path", SECRET_TEST_FILES)
def test_uses_snapshot_guard(path):
    """清理必须走「快照 + 只删新增」。"""
    source = path.read_text(encoding="utf-8")
    assert "secret_test_guard" in source, f"{path.name} 应使用快照式清理"


def test_guard_only_deletes_new_rows():
    """守卫本身只删快照之后出现的行。"""
    from tests import secret_test_guard

    source = Path(secret_test_guard.__file__).read_text(encoding="utf-8")
    assert "not in keep" in source
    assert "keep_secret_ids" in source


def test_tests_do_not_share_user_id_with_real_data():
    """测试用的 owner_id 必须带前缀。

    原来 ``USER_ID = "7"`` 与开发库里的真实用户 id 完全相同，于是
    ``list_visible_secrets(user_id="7")`` 既能看见测试数据，也能看见真实数据，
    「断言列表为空」就成了「真实用户没有凭据」的同义词。
    """
    for path in SECRET_TEST_FILES[:3]:
        source = path.read_text(encoding="utf-8")
        match = re.search(r'USER_ID\s*=\s*"([^"]+)"', source)
        if match:
            assert match.group(1).startswith("pytest-"), (
                f"{path.name} 的 USER_ID 是 {match.group(1)!r}，应与真实用户隔离"
            )
