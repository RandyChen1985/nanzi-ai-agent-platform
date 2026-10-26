"""知识库三档可见范围（public / team / private）的判定契约。

判定逻辑抽成纯函数 `resolve_knowledge_dataset_access`，本文件穷举其边界；
`PermissionService.get_knowledge_base_access` 只负责把数据查出来喂进去。

三档语义（用户确认的口径）：

- `public`：**需要授权**才能访问，创建人自动可用，可继续分配；
- `team`：业务归属部门（**含下级部门**）成员自动只读，也可继续分配；
- `private`：仅创建人可访问，**不允许**分配授权，历史授权记录一律不生效。
"""

from __future__ import annotations

import pytest

from app.services.permission_service import (
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    VISIBILITY_TEAM,
    normalize_knowledge_visibility,
    resolve_knowledge_dataset_access,
)

pytestmark = pytest.mark.no_infrastructure

# 部门树：1 技术中心 → 7 平台组 → 23 后端小组；3 财务部（兄弟）
OWNER_DEPT_ID = 7
OWNER_DEPT_PATH = "/1/7/"
MY_DEPT_PATH = "/1/7/23/"  # 平台组的子部门，用于验证"含下级"
SIBLING_DEPT_PATH = "/3/"


def _row(dataset_id: str, visibility: str | None, owner_dept_id=None, created_by=None):
    return (dataset_id, visibility, owner_dept_id, created_by)


def _resolve(rows, *, granted=(), my_paths=(), owner_paths=None, defaults=(), user_name="alice"):
    return resolve_knowledge_dataset_access(
        dataset_rows=rows,
        granted_ids=list(granted),
        my_dept_paths=list(my_paths),
        owner_dept_paths=owner_paths if owner_paths is not None else {OWNER_DEPT_ID: OWNER_DEPT_PATH},
        default_ids=list(defaults),
        user_name=user_name,
    )


def test_visibility_normalization_falls_back_to_public():
    assert normalize_knowledge_visibility(VISIBILITY_TEAM) == VISIBILITY_TEAM
    assert normalize_knowledge_visibility(" PRIVATE ") == VISIBILITY_PRIVATE
    # 空值与脏值按 public 兜底：宁可保持改造前的"授权即可见"，也不误收紧
    assert normalize_knowledge_visibility(None) == VISIBILITY_PUBLIC
    assert normalize_knowledge_visibility("") == VISIBILITY_PUBLIC
    assert normalize_knowledge_visibility("shared") == VISIBILITY_PUBLIC


def test_public_requires_grant():
    rows = [_row("ds-public", VISIBILITY_PUBLIC)]
    denied = _resolve(rows)
    assert denied["accessible_ids"] == set()

    allowed = _resolve(rows, granted=["ds-public"])
    assert allowed["accessible_ids"] == {"ds-public"}
    # 授权成员只读
    assert allowed["writable_ids"] == set()


def test_private_only_visible_to_creator_and_ignores_grants():
    rows = [_row("ds-private", VISIBILITY_PRIVATE, created_by="alice")]
    access = _resolve(rows, granted=["ds-private"], user_name="alice")
    assert access["accessible_ids"] == {"ds-private"}
    assert access["writable_ids"] == {"ds-private"}

    # 别人即使历史上被授权过，也不能访问（private 不允许分配授权）
    other = _resolve(rows, granted=["ds-private"], user_name="bob")
    assert other["accessible_ids"] == set()
    assert other["writable_ids"] == set()


def test_team_auto_grants_department_members_as_read_only():
    rows = [_row("ds-team", VISIBILITY_TEAM, owner_dept_id=OWNER_DEPT_ID)]
    access = _resolve(rows, my_paths=["/1/7/"])
    assert access["accessible_ids"] == {"ds-team"}
    assert access["writable_ids"] == set()


def test_team_includes_sub_departments():
    rows = [_row("ds-team", VISIBILITY_TEAM, owner_dept_id=OWNER_DEPT_ID)]
    # 当前用户属于平台组的子部门 → 含下级，应当可见
    access = _resolve(rows, my_paths=[MY_DEPT_PATH])
    assert access["accessible_ids"] == {"ds-team"}


def test_team_excludes_sibling_and_unrelated_departments():
    rows = [_row("ds-team", VISIBILITY_TEAM, owner_dept_id=OWNER_DEPT_ID)]
    assert _resolve(rows, my_paths=[SIBLING_DEPT_PATH])["accessible_ids"] == set()
    # 没有部门的用户也看不到 team 库
    assert _resolve(rows, my_paths=[])["accessible_ids"] == set()


def test_team_without_owner_department_degrades_to_public():
    rows = [_row("ds-team-orphan", VISIBILITY_TEAM, owner_dept_id=None)]
    assert _resolve(rows, my_paths=[MY_DEPT_PATH])["accessible_ids"] == set()
    # 仍可通过显式授权访问
    assert _resolve(rows, my_paths=[MY_DEPT_PATH], granted=["ds-team-orphan"])[
        "accessible_ids"
    ] == {"ds-team-orphan"}


def test_team_allows_extra_grant_outside_the_department():
    rows = [_row("ds-team", VISIBILITY_TEAM, owner_dept_id=OWNER_DEPT_ID)]
    access = _resolve(rows, my_paths=[SIBLING_DEPT_PATH], granted=["ds-team"])
    assert access["accessible_ids"] == {"ds-team"}


def test_creator_wins_over_visibility():
    rows = [
        _row("ds-private", VISIBILITY_PRIVATE, created_by="alice"),
        _row("ds-team", VISIBILITY_TEAM, owner_dept_id=OWNER_DEPT_ID, created_by="alice"),
        _row("ds-public", VISIBILITY_PUBLIC, created_by="alice"),
    ]
    access = _resolve(rows, user_name="alice")
    assert access["accessible_ids"] == {"ds-private", "ds-team", "ds-public"}
    assert access["writable_ids"] == {"ds-private", "ds-team", "ds-public"}


def test_grants_kept_for_datasets_missing_from_metadata():
    """RAGFlow 有、本地元数据尚未同步的库：沿用旧口径（授权即可见）。"""
    access = _resolve([], granted=["ds-unsynced"], user_name="bob")
    assert access["accessible_ids"] == {"ds-unsynced"}


def test_system_default_datasets_are_always_visible():
    access = _resolve([_row("ds-private", VISIBILITY_PRIVATE, created_by="alice")], defaults=["ds-default"])
    assert "ds-default" in access["accessible_ids"]
    # 系统默认库不授予写权限
    assert "ds-default" not in access["writable_ids"]


def test_private_dataset_stays_private_even_if_it_is_a_default():
    """私有库即便被配进系统默认库，也不能对其他人放行。

    早先这里断言的是相反的结论（"管理员显式放行，优先级高于 private"），
    但那等于说「管理员可以把一个"不允许分配权限给其他人"的库公开给全平台」，
    与 private 的定义直接冲突。实际漏洞：``accessible |= defaults`` 在循环外
    无条件合并，visible 与否完全没看 visibility 列。
    """
    access = _resolve(
        [_row("ds-private", VISIBILITY_PRIVATE, created_by="alice")],
        defaults=["ds-private"],
        user_name="bob",
    )
    assert access["accessible_ids"] == set()

    # 创建人自己仍然可读可写（creator 分支在 private 之前）
    own = _resolve(
        [_row("ds-private", VISIBILITY_PRIVATE, created_by="alice")],
        defaults=["ds-private"],
        user_name="alice",
    )
    assert own["accessible_ids"] == {"ds-private"}
    assert own["writable_ids"] == {"ds-private"}



def test_system_default_ids_still_apply_to_unsynced_datasets():
    """回归保护：真正没同步进元数据表的默认库仍要放行（否则默认库配了不生效）。"""
    result = resolve_knowledge_dataset_access(
        dataset_rows=[],
        granted_ids=[],
        my_dept_paths=[],
        owner_dept_paths={},
        default_ids=["ds-not-synced"],
        user_name="someone",
    )
    assert "ds-not-synced" in result["accessible_ids"]


# ---------------------------------------------------------------- 两级写权限
#
# 内容级（content_writable_ids）：上传 / 删除 / 解析文档、查看切片明细。
# 管理级（writable_ids）：改元数据、改可见范围、改归属部门、删库、分配授权、看授权名单。
#
# 部门成员落在内容级：能维护本部门资料，但改不了这个库"归谁看"。


def test_team_member_gets_content_write_but_not_management():
    rows = [_row("ds-team", VISIBILITY_TEAM, OWNER_DEPT_ID, "bob")]
    access = _resolve(rows, my_paths=[MY_DEPT_PATH])

    assert "ds-team" in access["accessible_ids"]
    # 能上传/删/解析文档、看切片
    assert "ds-team" in access["content_writable_ids"]
    # 但不能改元数据/可见范围/删库/分配授权
    assert "ds-team" not in access["writable_ids"]


def test_granted_member_of_public_stays_read_only():
    """被显式授权的非创建人恒为只读 —— 两级都不含。"""
    rows = [_row("ds-public", VISIBILITY_PUBLIC, None, "bob")]
    access = _resolve(rows, granted=["ds-public"], my_paths=[MY_DEPT_PATH])

    assert "ds-public" in access["accessible_ids"]
    assert "ds-public" not in access["content_writable_ids"]
    assert "ds-public" not in access["writable_ids"]


def test_team_member_from_sibling_department_is_read_only():
    rows = [_row("ds-team", VISIBILITY_TEAM, OWNER_DEPT_ID, "bob")]
    access = _resolve(rows, my_paths=[SIBLING_DEPT_PATH])

    assert "ds-team" not in access["accessible_ids"]
    assert "ds-team" not in access["content_writable_ids"]


def test_team_member_who_is_also_granted_keeps_content_write():
    """既是部门成员又是授权成员时，不能被「已授权」提前短路掉写权限。

    这条曾经是真 bug：granted 分支里 `continue` 会跳过后续的 team 判定，
    结果是部门成员反而因为"被额外授权"而丢掉了内容写权限。
    """
    rows = [_row("ds-team", VISIBILITY_TEAM, OWNER_DEPT_ID, "bob")]
    access = _resolve(rows, granted=["ds-team"], my_paths=[MY_DEPT_PATH])

    assert "ds-team" in access["content_writable_ids"]
    assert "ds-team" not in access["writable_ids"]


def test_creator_has_both_levels():
    rows = [_row("ds-mine", VISIBILITY_PRIVATE, None, "alice")]
    access = _resolve(rows)

    assert "ds-mine" in access["accessible_ids"]
    assert "ds-mine" in access["content_writable_ids"]
    assert "ds-mine" in access["writable_ids"]


def test_default_dataset_is_read_only_for_everyone_but_creator():
    """系统默认库对全员可读，但不给写权限 —— 否则任何人都能改公共资料。"""
    rows = [_row("ds-default", VISIBILITY_PUBLIC, None, "bob")]
    access = _resolve(rows, defaults=["ds-default"])

    assert "ds-default" in access["accessible_ids"]
    assert "ds-default" not in access["content_writable_ids"]
    assert "ds-default" not in access["writable_ids"]
