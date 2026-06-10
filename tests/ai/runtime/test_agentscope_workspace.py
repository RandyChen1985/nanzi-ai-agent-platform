import os

import pytest

from app.services.ai.runtime.agentscope.workspace import (
    clear_workspace_cache,
    delete_workspace_for_session,
    get_local_workspace_offloader,
    resolve_session_workdir,
    resolve_workspace_root,
)

pytestmark = pytest.mark.no_infrastructure


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
        user_id="user/1",
        conversation_id="conv:abc",
    )
    assert path.startswith(str(tmp_path))
    assert "user_1" in path
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
        user_id="u1",
        conversation_id="c1",
    )
    assert workspace is not None
    assert workspace.is_alive is True
    workdir = resolve_session_workdir(
        root=str(tmp_path),
        user_id="u1",
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
    workspace = await get_local_workspace_offloader(user_id="u2", conversation_id="c2")
    assert workspace is not None
    workdir = resolve_session_workdir(
        root=str(tmp_path),
        user_id="u2",
        conversation_id="c2",
    )
    marker = os.path.join(workdir, "sessions", "marker.txt")
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("x")

    await delete_workspace_for_session("u2", "c2")
    assert not os.path.exists(workdir)
