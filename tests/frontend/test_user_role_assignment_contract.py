"""用户弹窗里「业务角色」列表的加载契约。

用户反馈：角色管理里能搜到 `default`，但编辑用户 → 业务角色搜 `d` 搜不到。
角色列表是一条独立的 `/api/portal/roles` 请求，它的加载时机决定了弹窗里能不能看到角色：

- 只在页面 onMounted 拉一次，失败后永不重试；
- 失败只 `console.error`，界面上就是一个空网格，看起来像「系统里没有这个角色」。

这里锁住三件事：打开弹窗时会补拉、失败有可见提示、无权限与「真的没有角色」能区分。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
USERS = ROOT / "frontend/src/views/Users.vue"

pytestmark = pytest.mark.no_infrastructure


def _users_source() -> str:
    return USERS.read_text(encoding="utf-8")


def _function_body(source: str, signature: str, span: int = 1600) -> str:
    start = source.index(signature)
    return source[start : start + span]


def test_opening_edit_dialog_retries_role_list_when_empty():
    """首次加载失败后，打开编辑弹窗必须重试，而不是永远显示空列表。"""
    source = _users_source()

    body = _function_body(source, "const editUser = async")
    assert "ensureBusinessRoles" in body, (
        "editUser 打开时没有补拉角色列表：onMounted 那次失败后，弹窗里永远是空的"
    )


def test_role_list_failure_is_visible_instead_of_silent():
    """拉取失败要能在界面上看见，而不是一个空网格冒充「没有角色」。"""
    source = _users_source()

    body = _function_body(source, "const fetchBusinessRoles = async")
    assert "roleListError" in body, "fetchBusinessRoles 失败后没有标记错误状态"
    assert "showToast" in body, "fetchBusinessRoles 失败后没有提示用户"

    template = source.split("业务角色")[1][:1200]
    assert "roleListError" in template, "模板没有区分「加载失败」与「真的没有角色」"


def test_retry_is_shared_between_create_and_edit():
    """新建与编辑走同一个「确保已加载」入口，避免两条路径各自漂移。"""
    source = _users_source()

    create_body = _function_body(source, "const openCreateDialog = async")
    edit_body = _function_body(source, "const editUser = async")

    assert "ensureBusinessRoles" in create_body
    assert "ensureBusinessRoles" in edit_body
