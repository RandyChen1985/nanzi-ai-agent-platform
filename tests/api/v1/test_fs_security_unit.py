"""无外部基础设施依赖的 fs 端点单元测试：验证保留目录保护、被禁技能过滤与线程池路径。"""

from __future__ import annotations

import os
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import fs as fs_module
from app.utils import fs_access


pytestmark = pytest.mark.no_infrastructure


def _setup_fake_fs(tmp_path, monkeypatch):
    data_base = tmp_path / "data"
    public_docs = data_base / "docs"
    public_branding = data_base / "branding"
    platform_uploads = data_base / "uploads"
    skills_root = tmp_path / "platform_skills"
    workspaces_root = data_base / "agent_workspaces"
    alice_root = workspaces_root / "u1_alice"
    bob_root = workspaces_root / "u2_bob"

    for d in (
        public_docs,
        public_branding,
        platform_uploads,
        skills_root / "alpha-skill",
        skills_root / "secret-skill",
        alice_root / "docs",
        alice_root / "uploads",
        alice_root / ".trash",
        alice_root / "my-proj" / "docs",
        bob_root,
    ):
        d.mkdir(parents=True, exist_ok=True)

    (skills_root / "alpha-skill" / "SKILL.md").write_text("# alpha", encoding="utf-8")
    (skills_root / "secret-skill" / "SKILL.md").write_text("# secret", encoding="utf-8")
    (public_docs / "guide.md").write_text("# guide", encoding="utf-8")
    (alice_root / "my-proj" / "docs" / "note.md").write_text("hi", encoding="utf-8")

    alice_info = {"user_id": 1, "user_name": "alice", "role": "user"}
    admin_info = {"user_id": 99, "user_name": "admin", "role": "admin"}

    def fake_private_root(user_info):
        if not user_info:
            return None
        uid = user_info.get("user_id")
        if uid == 1:
            return str(alice_root)
        if uid == 99:
            return str(workspaces_root / "u99_admin")
        return None

    monkeypatch.setattr(fs_module, "get_base_dir", lambda: str(data_base))
    monkeypatch.setattr(fs_access, "get_data_base_dir", lambda: str(data_base))
    monkeypatch.setattr(fs_access, "get_platform_skills_root", lambda: str(skills_root))
    monkeypatch.setattr(fs_access, "get_user_private_workspace_root", fake_private_root)
    monkeypatch.setattr(fs_module, "get_user_private_workspace_root", fake_private_root)

    def fake_forbidden(path, user_info):
        return (not fs_access.is_fs_admin(user_info)) and ("secret-skill" in str(path))

    monkeypatch.setattr(fs_access, "is_forbidden_platform_skill_path", fake_forbidden)
    monkeypatch.setattr(fs_module, "is_forbidden_platform_skill_path", fake_forbidden)

    return {
        "data_base": data_base,
        "public_docs": public_docs,
        "skills_root": skills_root,
        "workspaces_root": workspaces_root,
        "alice_root": alice_root,
        "bob_root": bob_root,
        "alice_info": alice_info,
        "admin_info": admin_info,
    }


@pytest.mark.asyncio
async def test_delete_and_rename_block_roots_and_reserved_dirs_but_allow_nested(tmp_path, monkeypatch):
    env = _setup_fake_fs(tmp_path, monkeypatch)
    alice = env["alice_info"]
    admin = env["admin_info"]

    # 普通用户：工作区根目录与根级 docs / uploads / .trash 均被拦截
    for protected in (
        env["alice_root"],
        env["alice_root"] / "docs",
        env["alice_root"] / "uploads",
        env["alice_root"] / ".trash",
    ):
        with pytest.raises(HTTPException) as exc:
            await fs_module.delete_fs_entry(fs_module.FileDeleteRequest(path=str(protected)), user_info=alice)
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            await fs_module.rename_fs_entry(
                fs_module.FileRenameRequest(path=str(protected), new_name="renamed_x"),
                user_info=alice,
            )
        assert exc.value.status_code == 400

    # 管理员：公共根目录、agent_workspaces、任意用户根目录均被拦截
    for protected in (
        env["data_base"],
        # data 根下的平台级 uploads 与前端 WORKSPACE_ROOT_PROTECTED_NAMES 同口径保护
        env["data_base"] / "uploads",
        env["public_docs"],
        env["workspaces_root"],
        env["alice_root"],
        env["bob_root"],
        env["alice_root"] / "docs",
    ):
        with pytest.raises(HTTPException) as exc:
            await fs_module.delete_fs_entry(fs_module.FileDeleteRequest(path=str(protected)), user_info=admin)
        assert exc.value.status_code == 400

    # 子项目下的同名 docs 目录允许正常删除（不误伤）
    nested_docs = env["alice_root"] / "my-proj" / "docs"
    res = await fs_module.delete_fs_entry(fs_module.FileDeleteRequest(path=str(nested_docs)), user_info=alice)
    assert res.data.trashed_path
    assert not nested_docs.exists()
    assert os.path.isdir(res.data.trashed_path)

    # 清空回收站正常通过线程池执行
    emptied = await fs_module.empty_trash(user_info=alice)
    assert emptied.data.deleted_count >= 1
    assert not os.path.exists(res.data.trashed_path)


@pytest.mark.asyncio
async def test_list_and_search_hide_forbidden_platform_skills_for_regular_user(tmp_path, monkeypatch):
    env = _setup_fake_fs(tmp_path, monkeypatch)
    alice = env["alice_info"]
    admin = env["admin_info"]
    skills_root = str(env["skills_root"])

    alice_items = fs_module._list_directory_entries(skills_root, alice)
    alice_names = [i.name for i in alice_items]
    assert "alpha-skill" in alice_names
    assert "secret-skill" not in alice_names

    admin_items = fs_module._list_directory_entries(skills_root, admin)
    admin_names = [i.name for i in admin_items]
    assert "alpha-skill" in admin_names
    assert "secret-skill" in admin_names

    alice_search = await fs_module.search_files(q="secret", path=skills_root, max_results=50, user_info=alice)
    assert alice_search.data.items == []

    alice_search_alpha = await fs_module.search_files(q="alpha", path=skills_root, max_results=50, user_info=alice)
    assert any(i.name == "alpha-skill" for i in alice_search_alpha.data.items)

    admin_search = await fs_module.search_files(q="secret", path=skills_root, max_results=50, user_info=admin)
    assert any(i.name == "secret-skill" for i in admin_search.data.items)


@pytest.mark.asyncio
async def test_copy_to_workspace_runs_via_threadpool_and_preserves_content(tmp_path, monkeypatch):
    env = _setup_fake_fs(tmp_path, monkeypatch)
    alice = env["alice_info"]
    src = env["public_docs"] / "guide.md"

    res = await fs_module.copy_to_workspace(
        fs_module.CopyToWorkspaceRequest(source_path=str(src)),
        user_info=alice,
    )
    assert res.data.name == "guide.md"
    assert os.path.isfile(res.data.path)
    with open(res.data.path, "r", encoding="utf-8") as fh:
        assert fh.read() == "# guide"
