import os

import pytest

from app.services.ai.runtime.agentscope.workspace import (
    WORKSPACE_USER_KEY_SEP,
    clear_workspace_cache,
    delete_workspace_for_session,
    get_local_workspace_offloader,
    resolve_session_workdir,
    resolve_user_workspace_root,
    resolve_workspace_user_key,
)

pytestmark = pytest.mark.no_infrastructure


def test_resolve_workspace_user_key_uses_name_and_id():
    key = resolve_workspace_user_key(user_id=1, user_name="chen.xl")
    assert key == f"chen_xl{WORKSPACE_USER_KEY_SEP}1"


def test_resolve_workspace_user_key_falls_back_to_id_only():
    key = resolve_workspace_user_key(user_id="user/1", user_name=None)
    assert key == "user_1"


@pytest.mark.asyncio
async def test_resolve_session_workdir_isolates_user_and_conversation(tmp_path, monkeypatch):
    async def _root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    path = resolve_session_workdir(
        root=str(tmp_path),
        user_id=1,
        user_name="alice",
        conversation_id="conv:abc",
    )
    assert path.startswith(str(tmp_path))
    assert f"alice{WORKSPACE_USER_KEY_SEP}1" in path
    assert "conv_abc" in path


@pytest.mark.asyncio
async def test_get_local_workspace_offloader_initializes_workdir(tmp_path, monkeypatch):
    clear_workspace_cache()

    async def _root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )

    workspace = await get_local_workspace_offloader(
        user_id=1,
        user_name="bob",
        conversation_id="c1",
    )
    assert workspace is not None
    assert workspace.is_alive is True
    workdir = resolve_session_workdir(
        root=str(tmp_path),
        user_id=1,
        user_name="bob",
        conversation_id="c1",
    )
    assert os.path.isdir(workdir)
    assert os.path.isdir(os.path.join(workdir, "skills"))


@pytest.mark.asyncio
async def test_delete_workspace_for_session_removes_files(tmp_path, monkeypatch):
    clear_workspace_cache()

    async def _root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    workspace = await get_local_workspace_offloader(
        user_id=2,
        user_name="carol",
        conversation_id="c2",
    )
    assert workspace is not None
    workdir = resolve_session_workdir(
        root=str(tmp_path),
        user_id=2,
        user_name="carol",
        conversation_id="c2",
    )
    marker = os.path.join(workdir, "sessions", "marker.txt")
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("x")

    await delete_workspace_for_session(2, "c2", user_name="carol")
    assert not os.path.exists(workdir)


def test_resolve_user_workspace_root_returns_existing_directory(tmp_path):
    root = str(tmp_path)
    user_root = os.path.join(root, resolve_workspace_user_key(user_id=4, user_name="frank"))
    os.makedirs(user_root, exist_ok=True)

    resolved = resolve_user_workspace_root(root=root, user_id=4, user_name="frank")
    assert resolved is not None
    assert os.path.abspath(resolved) == os.path.abspath(user_root)


def test_resolve_user_workspace_root_missing_directory(tmp_path):
    resolved = resolve_user_workspace_root(
        root=str(tmp_path),
        user_id=4,
        user_name="frank",
    )
    assert resolved is None
