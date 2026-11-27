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


@pytest.mark.asyncio
async def test_trash_subtree_blocks_mutations_and_restores_to_original_dir(tmp_path, monkeypatch):
    env = _setup_fake_fs(tmp_path, monkeypatch)
    alice = env["alice_info"]
    nested_dir = env["alice_root"] / "my-proj" / "docs"
    note_file = nested_dir / "note.md"
    assert note_file.is_file()

    # 删除后记录原目录元数据
    deleted = await fs_module.delete_fs_entry(
        fs_module.FileDeleteRequest(path=str(note_file)),
        user_info=alice,
    )
    trashed_path = deleted.data.trashed_path
    assert trashed_path and os.path.isfile(trashed_path)

    # 回收站子树禁止再次删除、重命名、移动、新建、复制到回收站
    with pytest.raises(HTTPException) as exc:
        await fs_module.delete_fs_entry(fs_module.FileDeleteRequest(path=trashed_path), user_info=alice)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await fs_module.rename_fs_entry(
            fs_module.FileRenameRequest(path=trashed_path, new_name="x.md"),
            user_info=alice,
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await fs_module.create_fs_entry(
            fs_module.FileEntryCreateRequest(parent_path=str(env["alice_root"] / ".trash"), name="bad.txt", kind="file"),
            user_info=alice,
        )
    assert exc.value.status_code == 403

    # 恢复应优先回到原目录 my-proj/docs
    restored = await fs_module.restore_fs_entry(
        fs_module.FileDeleteRequest(path=trashed_path),
        user_info=alice,
    )
    assert restored.data.restored_to_original_dir is True
    assert os.path.normpath(restored.data.path) == os.path.normpath(str(note_file))
    assert note_file.is_file()


@pytest.mark.asyncio
async def test_admin_delete_other_user_file_goes_to_owner_trash_and_restores_to_owner(tmp_path, monkeypatch):
    env = _setup_fake_fs(tmp_path, monkeypatch)
    admin = env["admin_info"]
    bob_file = env["bob_root"] / "bob-report.md"
    bob_file.write_text("# bob", encoding="utf-8")

    deleted = await fs_module.delete_fs_entry(
        fs_module.FileDeleteRequest(path=str(bob_file)),
        user_info=admin,
    )
    # 必须落在 bob 自己的 .trash，绝不能落进管理员的回收站
    assert str(env["bob_root"] / ".trash") in deleted.data.trashed_path
    assert "u99_admin" not in deleted.data.trashed_path

    restored = await fs_module.restore_fs_entry(
        fs_module.FileDeleteRequest(path=deleted.data.trashed_path),
        user_info=admin,
    )
    assert os.path.normpath(restored.data.path) == os.path.normpath(str(bob_file))
    assert bob_file.is_file()


@pytest.mark.asyncio
async def test_move_batch_download_usage_search_and_recent_cleanup(tmp_path, monkeypatch):
    import zipfile

    env = _setup_fake_fs(tmp_path, monkeypatch)
    alice = env["alice_info"]
    src_file = env["alice_root"] / "my-proj" / "docs" / "note.md"
    target_dir = env["alice_root"] / "docs"

    # 1. 移动文件
    moved = await fs_module.move_fs_entry(
        fs_module.FileMoveRequest(path=str(src_file), target_dir=str(target_dir)),
        user_info=alice,
    )
    moved_path = moved.data.path
    assert os.path.isfile(moved_path)
    assert not src_file.exists()

    # 禁止将目录移入自身子目录
    with pytest.raises(HTTPException) as exc:
        await fs_module.move_fs_entry(
            fs_module.FileMoveRequest(
                path=str(env["alice_root"] / "my-proj"),
                target_dir=str(env["alice_root"] / "my-proj" / "docs"),
            ),
            user_info=alice,
        )
    assert exc.value.status_code == 400

    # 2. 打包下载（含文件与目录）
    resp = await fs_module.batch_download_files(
        fs_module.BatchDownloadRequest(paths=[moved_path], archive_name="pack"),
        user_info=alice,
    )
    zip_path = resp.path
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            assert "note.md" in zf.namelist()
    finally:
        fs_module._cleanup_temp_file(zip_path)

    # 3. 工作区容量与上传上限统计
    usage = await fs_module.get_workspace_usage(refresh=True, user_info=alice)
    assert usage.data.file_count >= 1
    assert usage.data.total_bytes >= 2
    assert usage.data.upload_max_bytes > 0

    # 4. 搜索自动跳过 node_modules 等噪音目录
    nm_dir = env["alice_root"] / "my-proj" / "node_modules" / "pkg"
    nm_dir.mkdir(parents=True, exist_ok=True)
    (nm_dir / "noise_target.js").write_text("console.log(1)", encoding="utf-8")
    search_res = await fs_module.search_files(q="noise_target", path=str(env["alice_root"]), max_results=50, user_info=alice)
    assert search_res.data.items == []

    # 5. 最近文件自动剔除已不存在的僵尸条目
    cleaned = fs_module._sanitize_workspace_recent_files(
        [
            fs_module.WorkspaceRecentFileItem(path=moved_path, name="note.md", mtime=10.0),
            fs_module.WorkspaceRecentFileItem(path=str(env["alice_root"] / "deleted_ghost.md"), name="deleted_ghost.md", mtime=11.0),
        ],
        alice,
    )
    assert [item.name for item in cleaned] == ["note.md"]

    # 6. 公共技能目录里的 .py 脚本允许复制到本人工作区
    skill_py = env["skills_root"] / "alpha-skill" / "helper.py"
    skill_py.write_text("print('ok')", encoding="utf-8")
    copied = await fs_module.copy_to_workspace(
        fs_module.CopyToWorkspaceRequest(source_path=str(skill_py)),
        user_info=alice,
    )
    assert os.path.isfile(copied.data.path)
    assert copied.data.name.endswith(".py")

    # 7. 悬空符号链接可正常删除，不报 404
    dangling = env["alice_root"] / "dangling_link.txt"
    os.symlink(str(env["alice_root"] / "nonexistent_target.txt"), str(dangling))
    del_link = await fs_module.delete_fs_entry(
        fs_module.FileDeleteRequest(path=str(dangling)),
        user_info=alice,
    )
    assert del_link.data.trashed_path
    assert not os.path.lexists(str(dangling))


@pytest.mark.asyncio
async def test_symlink_dir_target_cannot_escape_workspace_and_trash_meta_is_thread_safe(tmp_path, monkeypatch):
    import asyncio
    from io import BytesIO
    from fastapi import UploadFile

    env = _setup_fake_fs(tmp_path, monkeypatch)
    alice = env["alice_info"]
    outside_dir = tmp_path / "outside_secret"
    outside_dir.mkdir(parents=True, exist_ok=True)

    # 1. 工作区内的符号链接指向外部目录：upload / move target_dir 必须一律 403 拦截
    evil_link_dir = env["alice_root"] / "evil_dir"
    os.symlink(str(outside_dir), str(evil_link_dir))

    upload_file = UploadFile(filename="pwn.txt", file=BytesIO(b"nope"))
    with pytest.raises(HTTPException) as exc:
        await fs_module.upload_to_workspace(
            parent_path=str(evil_link_dir),
            file=upload_file,
            user_info=alice,
        )
    assert exc.value.status_code == 403
    assert not (outside_dir / "pwn.txt").exists()

    legit_file = env["alice_root"] / "docs" / "safe.txt"
    legit_file.write_text("safe", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        await fs_module.move_fs_entry(
            fs_module.FileMoveRequest(path=str(legit_file), target_dir=str(evil_link_dir)),
            user_info=alice,
        )
    assert exc.value.status_code == 403
    assert legit_file.is_file()

    # 2. 并发删除多个文件，.trash_meta.json 加锁后所有原目录元数据必须完整不丢失
    sub_dir = env["alice_root"] / "docs" / "concurrent_batch"
    sub_dir.mkdir(parents=True, exist_ok=True)
    batch_files = []
    for idx in range(8):
        fp = sub_dir / f"item_{idx}.txt"
        fp.write_text(f"body-{idx}", encoding="utf-8")
        batch_files.append(fp)

    results = await asyncio.gather(
        *[
            fs_module.delete_fs_entry(fs_module.FileDeleteRequest(path=str(fp)), user_info=alice)
            for fp in batch_files
        ]
    )
    trashed_paths = [r.data.trashed_path for r in results]
    restored_list = await asyncio.gather(
        *[
            fs_module.restore_fs_entry(fs_module.FileDeleteRequest(path=tp), user_info=alice)
            for tp in trashed_paths
        ]
    )
    assert all(r.data.restored_to_original_dir for r in restored_list)
    assert all(fp.is_file() for fp in batch_files)


@pytest.mark.asyncio
async def test_batch_download_blocks_symlink_escape_but_allows_normal_files(tmp_path, monkeypatch):
    """工作区内的软链不得把打包下载引到 data 之外（沙箱外逃）。"""
    import zipfile

    env = _setup_fake_fs(tmp_path, monkeypatch)
    alice = env["alice_info"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    (outside / "od").mkdir()
    (outside / "od" / "p.txt").write_text("PWD", encoding="utf-8")

    link_file = env["alice_root"] / "innocent.txt"
    os.symlink(str(outside / "secret.txt"), str(link_file))
    link_dir = env["alice_root"] / "proj"
    os.symlink(str(outside / "od"), str(link_dir))

    for target in (link_file, link_dir):
        with pytest.raises(HTTPException) as exc:
            await fs_module.batch_download_files(
                fs_module.BatchDownloadRequest(paths=[str(target)]),
                user_info=alice,
            )
        assert exc.value.status_code == 403

    ok_file = env["alice_root"] / "my-proj" / "docs" / "note.md"
    resp = await fs_module.batch_download_files(
        fs_module.BatchDownloadRequest(paths=[str(ok_file)]),
        user_info=alice,
    )
    try:
        with zipfile.ZipFile(resp.path, "r") as zf:
            assert zf.namelist() == ["note.md"]
    finally:
        fs_module._cleanup_temp_file(resp.path)


def test_batch_download_cleans_temp_zip_even_on_unsatisfiable_range():
    """416 / 客户端断连不会执行 background 任务，临时 ZIP 仍必须被清理。"""
    import tempfile

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    holder: dict[str, str] = {}

    @app.get("/dl")
    def _download():  # pragma: no cover - 仅用于驱动响应对象
        fd, path = tempfile.mkstemp(prefix="workspace_dl_probe_", suffix=".zip")
        os.write(fd, b"x" * 128)
        os.close(fd)
        holder["path"] = path
        return fs_module._CleanupFileResponse(path, media_type="application/zip", filename="probe.zip")

    client = TestClient(app)
    ranged = client.get("/dl", headers={"Range": "bytes=999999-1000000"})
    assert ranged.status_code == 416
    assert not os.path.exists(holder["path"]), "416 后临时 ZIP 残留"

    normal = client.get("/dl")
    assert normal.status_code == 200
    assert not os.path.exists(holder["path"]), "正常下载后临时 ZIP 残留"


@pytest.mark.asyncio
async def test_restore_keeps_trash_meta_when_move_fails(tmp_path, monkeypatch):
    """恢复失败时不得丢掉原目录记录，否则该条目永久无法回到原目录。"""
    env = _setup_fake_fs(tmp_path, monkeypatch)
    alice = env["alice_info"]
    note = env["alice_root"] / "my-proj" / "docs" / "note.md"

    deleted = await fs_module.delete_fs_entry(
        fs_module.FileDeleteRequest(path=str(note)), user_info=alice
    )
    trashed_path = deleted.data.trashed_path
    trash_root = str(env["alice_root"] / ".trash")
    trashed_name = os.path.basename(trashed_path)
    assert fs_module._peek_trash_meta(trash_root, trashed_name)

    real_move = fs_module.shutil.move

    def _boom(*args, **kwargs):
        raise OSError("simulated move failure")

    monkeypatch.setattr(fs_module.shutil, "move", _boom)
    with pytest.raises(HTTPException) as exc:
        await fs_module.restore_fs_entry(fs_module.FileDeleteRequest(path=trashed_path), user_info=alice)
    assert exc.value.status_code == 500
    monkeypatch.setattr(fs_module.shutil, "move", real_move)

    assert fs_module._peek_trash_meta(trash_root, trashed_name), "恢复失败后元数据被误删"

    restored = await fs_module.restore_fs_entry(
        fs_module.FileDeleteRequest(path=trashed_path), user_info=alice
    )
    assert restored.data.restored_to_original_dir is True
    assert os.path.normpath(restored.data.path) == os.path.normpath(str(note))
    assert not fs_module._peek_trash_meta(trash_root, trashed_name)


@pytest.mark.asyncio
async def test_move_entry_never_overwrites_and_blocks_public_source(tmp_path, monkeypatch):
    env = _setup_fake_fs(tmp_path, monkeypatch)
    alice = env["alice_info"]
    admin = env["admin_info"]
    dst_dir = env["alice_root"] / "uploads"
    src = env["alice_root"] / "docs" / "same.txt"
    src.write_text("SRC", encoding="utf-8")
    existing = dst_dir / "same.txt"
    existing.write_text("EXISTING", encoding="utf-8")

    # 底层同步移动：同名即 FileExistsError，绝不静默替换
    with pytest.raises(FileExistsError):
        fs_module._move_entry_exclusive_sync(str(src), str(dst_dir), "same.txt")
    assert existing.read_text(encoding="utf-8") == "EXISTING"
    assert src.is_file()

    with pytest.raises(HTTPException) as exc:
        await fs_module.move_fs_entry(
            fs_module.FileMoveRequest(path=str(src), target_dir=str(dst_dir)), user_info=alice
        )
    assert exc.value.status_code == 409
    assert existing.read_text(encoding="utf-8") == "EXISTING"

    # 公共目录是只读资源：admin 也不能把它移进私有工作区
    admin_root = env["workspaces_root"] / "u99_admin"
    admin_root.mkdir(parents=True, exist_ok=True)
    public_file = env["public_docs"] / "guide.md"
    with pytest.raises(HTTPException) as exc:
        await fs_module.move_fs_entry(
            fs_module.FileMoveRequest(path=str(public_file), target_dir=str(admin_root)),
            user_info=admin,
        )
    assert exc.value.status_code == 403
    assert public_file.is_file()


@pytest.mark.asyncio
async def test_owner_resolution_and_empty_trash_survive_symlinked_data_base(tmp_path, monkeypatch):
    """data 基础目录是软链时，归属推断 / 回收站保护 / 按归属清空仍必须正确。"""
    env = _setup_fake_fs(tmp_path, monkeypatch)
    alias = tmp_path / "alias"
    os.symlink(str(tmp_path), str(alias))
    monkeypatch.setattr(fs_module, "get_base_dir", lambda: str(alias / "data"))
    monkeypatch.setattr(fs_access, "get_data_base_dir", lambda: str(alias / "data"))

    user_keys = {1: "u1_alice", 2: "u2_bob", 99: "u99_admin"}

    def alias_private_root(user_info):
        return str(alias / "data" / "agent_workspaces" / user_keys[user_info["user_id"]])

    monkeypatch.setattr(fs_access, "get_user_private_workspace_root", alias_private_root)
    monkeypatch.setattr(fs_module, "get_user_private_workspace_root", alias_private_root)

    admin = env["admin_info"]
    alice = env["alice_info"]
    bob_root = env["bob_root"]
    bob_file = bob_root / "bob-report.md"
    bob_file.write_text("# bob", encoding="utf-8")

    deleted = await fs_module.delete_fs_entry(
        fs_module.FileDeleteRequest(path=str(bob_file)), user_info=admin
    )
    real_trashed = os.path.realpath(deleted.data.trashed_path)
    assert real_trashed.startswith(os.path.realpath(str(bob_root / ".trash")) + os.sep)
    assert "u99_admin" not in real_trashed
    assert fs_module._is_any_trash_path(deleted.data.trashed_path, admin) is True

    # 按归属清空：admin 清他人回收站不会误清自己的
    admin_root = env["workspaces_root"] / "u99_admin"
    admin_root.mkdir(parents=True, exist_ok=True)
    emptied = await fs_module.empty_trash(path=str(bob_root / ".trash"), user_info=admin)
    assert emptied.data.deleted_count == 1
    assert not os.path.exists(real_trashed)

    (bob_root / ".trash").mkdir(parents=True, exist_ok=True)
    (bob_root / ".trash" / "y.txt").write_text("y", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        await fs_module.empty_trash(path=str(bob_root / ".trash"), user_info=alice)
    assert exc.value.status_code == 403
