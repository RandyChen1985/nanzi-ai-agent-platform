import os

import pytest

from app.utils.fs_access import (
    get_allowed_fs_roots,
    is_fs_virtual_root,
    is_path_allowed,
)
from app.utils.fs_paths import get_data_base_dir

pytestmark = pytest.mark.no_infrastructure


def test_regular_user_allowed_roots_include_public_and_private(tmp_path, monkeypatch):
    base = str(tmp_path / "data")
    uploads = os.path.join(base, "uploads")
    private = os.path.join(base, "agent_workspaces", "alice__1", "conv-1")
    os.makedirs(uploads, exist_ok=True)
    os.makedirs(private, exist_ok=True)

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: base)
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


def test_regular_user_cannot_access_other_workspace(tmp_path, monkeypatch):
    base = str(tmp_path / "data")
    other = os.path.join(base, "agent_workspaces", "bob__2", "secret.txt")
    os.makedirs(os.path.dirname(other), exist_ok=True)
    with open(other, "w", encoding="utf-8") as handle:
        handle.write("secret")

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: base)
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: base)
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
