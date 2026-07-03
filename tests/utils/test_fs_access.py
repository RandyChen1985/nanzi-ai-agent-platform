import os

import pytest

from app.utils.fs_access import (
    get_allowed_fs_roots,
    is_fs_virtual_root,
    is_path_allowed,
    is_path_writable,
    normalize_fs_path,
)
from app.utils.fs_paths import get_data_base_dir

pytestmark = pytest.mark.no_infrastructure


def test_regular_user_allowed_roots_include_platform_skills(tmp_path, monkeypatch):
    base = str(tmp_path / "data")
    uploads = os.path.join(base, "uploads")
    skills = str(tmp_path / "home-skills")
    os.makedirs(uploads, exist_ok=True)
    os.makedirs(skills, exist_ok=True)

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: skills)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: os.path.join(base, "agent_workspaces"),
    )

    user_info = {"user_id": 1, "user_name": "alice", "role": "user"}
    roots = get_allowed_fs_roots(user_info)
    norm_roots = [os.path.normpath(r) for r in roots]
    assert os.path.normpath(skills) in norm_roots


def test_regular_user_allowed_roots_include_public_and_private(tmp_path, monkeypatch):
    base = str(tmp_path / "data")
    uploads = os.path.join(base, "uploads")
    private = os.path.join(base, "agent_workspaces", "alice__1", "conv-1")
    os.makedirs(uploads, exist_ok=True)
    os.makedirs(private, exist_ok=True)

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: os.path.join(base, "agent_workspaces"),
    )

    user_info = {"user_id": 1, "user_name": "alice", "role": "user"}
    roots = get_allowed_fs_roots(user_info)
    assert os.path.normpath(uploads) in [os.path.normpath(r) for r in roots]
    assert os.path.normpath(os.path.join(base, "agent_workspaces", "alice__1")) in [
        os.path.normpath(r) for r in roots
    ]


def test_normalize_fs_path_supports_platform_skills_outside_data(tmp_path, monkeypatch):
    base = str(tmp_path / "data")
    skills = str(tmp_path / "home-skills")
    skill_file = os.path.join(skills, "demo-skill", "SKILL.md")
    os.makedirs(os.path.dirname(skill_file), exist_ok=True)
    with open(skill_file, "w", encoding="utf-8") as handle:
        handle.write("demo")

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: skills)

    resolved = normalize_fs_path(skill_file)
    assert resolved == os.path.normpath(skill_file)


def test_regular_user_cannot_access_other_workspace(tmp_path, monkeypatch):
    base = str(tmp_path / "data")
    other = os.path.join(base, "agent_workspaces", "bob__2", "secret.txt")
    os.makedirs(os.path.dirname(other), exist_ok=True)
    with open(other, "w", encoding="utf-8") as handle:
        handle.write("secret")

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: os.path.join(base, "agent_workspaces"),
    )

    user_info = {"user_id": 1, "user_name": "alice", "role": "user"}
    assert not is_path_allowed(other, user_info)


def test_admin_can_access_full_data_tree(tmp_path, monkeypatch):
    base = str(tmp_path / "data")
    os.makedirs(base, exist_ok=True)
    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: base)

    admin = {"user_id": 99, "user_name": "admin", "role": "admin"}
    assert is_fs_virtual_root(None, base)
    assert get_allowed_fs_roots(admin) == [os.path.normpath(base)]
    assert is_path_allowed(os.path.join(base, "agent_workspaces", "bob__2"), admin)


def test_writable_only_within_own_workspace(tmp_path, monkeypatch):
    base = str(tmp_path / "data")
    own_file = os.path.join(base, "agent_workspaces", "alice__1", "conv-1", "note.txt")
    uploads_file = os.path.join(base, "uploads", "note.txt")
    other_file = os.path.join(base, "agent_workspaces", "bob__2", "conv-1", "note.txt")
    os.makedirs(os.path.dirname(own_file), exist_ok=True)
    os.makedirs(os.path.dirname(uploads_file), exist_ok=True)
    os.makedirs(os.path.dirname(other_file), exist_ok=True)

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: os.path.join(base, "agent_workspaces"),
    )

    user_info = {"user_id": 1, "user_name": "alice", "role": "user"}
    assert is_path_allowed(uploads_file, user_info)
    assert not is_path_writable(uploads_file, user_info)
    assert is_path_writable(own_file, user_info)
    assert not is_path_writable(other_file, user_info)
    assert not is_path_writable(os.path.join(base, "agent_workspaces", "alice__1"), user_info)
