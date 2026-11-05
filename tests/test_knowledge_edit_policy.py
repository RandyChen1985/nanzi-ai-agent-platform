"""知识库「编辑权限范围」判定单测（纯函数，无 DB）。"""

from app.services.permission_service import (
    ALLOWED_EDIT_POLICY_MODES,
    EDIT_POLICY_ALL_VIEWERS,
    EDIT_POLICY_GRANTED,
    EDIT_POLICY_READONLY,
    EDIT_POLICY_SELECTED,
    EDIT_POLICY_TEAM_MEMBERS,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    VISIBILITY_TEAM,
    normalize_edit_policy_mode,
    resolve_edit_policy_from_config,
    resolve_knowledge_dataset_access,
)


def _resolve(rows, *, granted=(), policies=None, defaults=(), dept_paths=None, owner_paths=None, user_id=2, user_name="bob"):
    return resolve_knowledge_dataset_access(
        dataset_rows=rows,
        granted_ids=list(granted),
        my_dept_paths=dept_paths or [],
        owner_dept_paths=owner_paths or {},
        default_ids=list(defaults),
        user_name=user_name,
        user_id=user_id,
        edit_policies=policies or {},
    )


PUBLIC_ROW = ("d1", VISIBILITY_PUBLIC, None, "alice", None)
TEAM_ROW = ("d2", VISIBILITY_TEAM, 7, "alice", None)
PRIVATE_ROW = ("d3", VISIBILITY_PRIVATE, None, "alice", None)


def test_public_default_is_readonly():
    """默认（无策略）与改造前一致：被授权成员只读。"""
    out = _resolve([PUBLIC_ROW], granted=["d1"])
    assert out["accessible_ids"] == {"d1"}
    assert out["content_writable_ids"] == set()


def test_public_granted_allows_granted_members_only():
    out = _resolve([PUBLIC_ROW], granted=["d1"], policies={"d1": {"mode": EDIT_POLICY_GRANTED}})
    assert out["content_writable_ids"] == {"d1"}

    # 不是授权成员（只因为默认库能看见）时，granted 不放行
    out = _resolve([PUBLIC_ROW], defaults=["d1"], policies={"d1": {"mode": EDIT_POLICY_GRANTED}})
    assert out["accessible_ids"] == {"d1"}
    assert out["content_writable_ids"] == set()


def test_public_all_viewers_covers_default_visible_members():
    """all_viewers 与 granted 的唯一差别：默认库带来的可见者也放行。"""
    out = _resolve([PUBLIC_ROW], defaults=["d1"], policies={"d1": {"mode": EDIT_POLICY_ALL_VIEWERS}})
    assert out["content_writable_ids"] == {"d1"}

    out = _resolve([PUBLIC_ROW], granted=["d1"], policies={"d1": {"mode": EDIT_POLICY_ALL_VIEWERS}})
    assert out["content_writable_ids"] == {"d1"}


def test_public_selected_only_named_users():
    policy = {"mode": EDIT_POLICY_SELECTED, "editors": [{"type": "user", "user_id": 2}]}
    out = _resolve([PUBLIC_ROW], granted=["d1"], policies={"d1": policy})
    assert out["content_writable_ids"] == {"d1"}

    # 没被点名的人仍然只读
    out = _resolve(
        [PUBLIC_ROW],
        granted=["d1"],
        policies={"d1": {"mode": EDIT_POLICY_SELECTED, "editors": [{"type": "user", "user_id": 99}]}},
    )
    assert out["content_writable_ids"] == set()


def test_selected_cannot_grant_write_without_visibility():
    """编辑权不新增可见性：看不见这个库，被点名也不该拿到写权限。"""
    out = _resolve(
        [PUBLIC_ROW],
        granted=[],
        policies={"d1": {"mode": EDIT_POLICY_SELECTED, "editors": [{"type": "user", "user_id": 2}]}},
    )
    assert out["accessible_ids"] == set()
    assert out["content_writable_ids"] == set()


def test_team_default_keeps_department_member_writable():
    """TEAM 库默认 = 现状：归属部门（含下级）成员可编辑内容。"""
    out = _resolve(
        [TEAM_ROW],
        dept_paths=["/1/7/9/"],
        owner_paths={7: "/1/7/"},
    )
    assert out["accessible_ids"] == {"d2"}
    assert out["content_writable_ids"] == {"d2"}


def test_team_readonly_takes_content_right_back():
    out = _resolve(
        [TEAM_ROW],
        dept_paths=["/1/7/9/"],
        owner_paths={7: "/1/7/"},
        policies={"d2": {"mode": EDIT_POLICY_READONLY}},
    )
    assert out["accessible_ids"] == {"d2"}
    assert out["content_writable_ids"] == set()


def test_team_members_mode_is_not_allowed_for_public():
    """team_members 只属于 TEAM 库；public 库传它会被归一成默认值。"""
    assert normalize_edit_policy_mode(EDIT_POLICY_TEAM_MEMBERS, VISIBILITY_PUBLIC) == EDIT_POLICY_READONLY
    assert normalize_edit_policy_mode("nonsense", VISIBILITY_TEAM) == EDIT_POLICY_TEAM_MEMBERS


def test_private_dataset_never_accepts_edit_policy():
    policy = {"mode": EDIT_POLICY_SELECTED, "editors": [{"type": "user", "user_id": 2}]}
    out = _resolve([PRIVATE_ROW], policies={"d3": policy})
    assert out["accessible_ids"] == set()
    assert out["content_writable_ids"] == set()


def test_owner_still_writable_regardless_of_policy():
    out = _resolve(
        [PUBLIC_ROW],
        user_id=1,
        user_name="alice",
        policies={"d1": {"mode": EDIT_POLICY_READONLY}},
    )
    assert out["content_writable_ids"] == {"d1"}


def test_allowed_modes_are_scoped_by_visibility():
    assert set(ALLOWED_EDIT_POLICY_MODES[VISIBILITY_PUBLIC]) == {
        EDIT_POLICY_READONLY,
        EDIT_POLICY_GRANTED,
        EDIT_POLICY_ALL_VIEWERS,
        EDIT_POLICY_SELECTED,
    }
    assert set(ALLOWED_EDIT_POLICY_MODES[VISIBILITY_TEAM]) == {
        EDIT_POLICY_READONLY,
        EDIT_POLICY_TEAM_MEMBERS,
        EDIT_POLICY_SELECTED,
    }


def test_resolve_edit_policy_from_config_drops_roles_and_bad_ids():
    config = {
        "edit_policy": {
            "mode": EDIT_POLICY_SELECTED,
            "editors": [
                {"type": "user", "user_id": 178, "user_name": "chenxiaolong"},
                {"type": "role", "id": 3},
                {"type": "user", "user_id": "oops"},
            ],
            "updated_by": "chenxiaolong",
        }
    }
    policy = resolve_edit_policy_from_config(config, VISIBILITY_PUBLIC)
    assert policy["mode"] == EDIT_POLICY_SELECTED
    assert [item["user_id"] for item in policy["editors"]] == [178]


def test_resolve_edit_policy_from_config_defaults_on_missing():
    assert resolve_edit_policy_from_config(None, VISIBILITY_PUBLIC)["mode"] == EDIT_POLICY_READONLY
    assert resolve_edit_policy_from_config({}, VISIBILITY_TEAM)["mode"] == EDIT_POLICY_TEAM_MEMBERS
    assert resolve_edit_policy_from_config({"edit_policy": {"mode": "x"}}, VISIBILITY_TEAM)["mode"] == EDIT_POLICY_TEAM_MEMBERS
