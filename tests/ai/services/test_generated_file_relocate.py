"""产物迁址的契约：生成产物已在工作区里，保存到目录应当移动而不是复制。

背景：画布「保存到目录」过去是「取 blob 再上传」，同一张 5–6MB 的图会在
``generated/`` 和目标目录各存一份。产物本来就在工作区，这里锁住三件事：

1. 移动而不是复制（源文件消失、目标出现、字节一致）；
2. ``ai_artifacts.storage_path`` 同步更新（旧 download_url 仍可用）；
3. 归属与目录边界不能因为「这是产物」就放松。
"""

from pathlib import Path

import pytest

from app.models.artifact import AiArtifact
from app.services.ai.tools import generated_file_service as svc

pytestmark = pytest.mark.no_infrastructure

OWNER_ID = 1
OWNER_KEY = "admin__1"
ARTIFACT_ID = "26399a7a80ed41e58b2431a2ca949a34"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _make_artifact(storage_path: Path, *, owner_user_id: int = OWNER_ID) -> AiArtifact:
    return AiArtifact(
        id=ARTIFACT_ID,
        owner_user_id=owner_user_id,
        artifact_type="image",
        filename="generated-image-20261121-004916-1-7f3ecd.png",
        mime_type="image/png",
        size=len(PNG_BYTES),
        storage_path=str(storage_path),
        token_hash="deadbeef",
    )


def _fake_db(record):
    from unittest.mock import AsyncMock, MagicMock

    db = MagicMock()
    db.get = AsyncMock(return_value=record)
    db.commit = AsyncMock()
    return db


def _patch_root(monkeypatch, root: Path) -> None:
    import app.services.ai.runtime.agentscope.workspace as ws_mod

    async def fake_resolve(ensure_exists: bool = True) -> str:  # noqa: ARG001
        return str(root)

    monkeypatch.setattr(ws_mod, "resolve_workspace_root", fake_resolve)


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    """造出 <root>/admin__1/generated/xxx.png 并返回 (root, 产物路径)。"""
    root = tmp_path / "ws"
    gen = root / OWNER_KEY / "generated"
    gen.mkdir(parents=True)
    source = gen / f"{ARTIFACT_ID}_generated-image-20261121-004916-1-7f3ecd.png"
    source.write_bytes(PNG_BYTES)
    return root, source


async def test_relocate_moves_file_and_updates_storage_path(tmp_path: Path, monkeypatch):
    root, source = _seed(tmp_path)
    _patch_root(monkeypatch, root)
    record = _make_artifact(source)
    db = _fake_db(record)

    saved = await svc.relocate_artifact(
        db,
        artifact_id=ARTIFACT_ID,
        owner_user_id=OWNER_ID,
        user_name="admin",
        target_dir="docs",
        filename="山水.png",
    )

    target = root / OWNER_KEY / "docs" / "山水.png"
    assert saved == str(target)
    assert target.read_bytes() == PNG_BYTES
    assert not source.exists(), "必须是移动，源文件不能继续留在 generated/ 里"
    assert record.storage_path == str(target)
    assert record.filename == "山水.png"
    assert record.size == len(PNG_BYTES)
    db.commit.assert_awaited_once()


async def test_relocate_keeps_default_filename_when_not_specified(tmp_path: Path, monkeypatch):
    root, source = _seed(tmp_path)
    _patch_root(monkeypatch, root)
    record = _make_artifact(source)

    saved = await svc.relocate_artifact(
        _fake_db(record),
        artifact_id=ARTIFACT_ID,
        owner_user_id=OWNER_ID,
        user_name="admin",
        target_dir="docs",
    )
    assert saved.endswith("generated-image-20261121-004916-1-7f3ecd.png")


async def test_relocate_accepts_workspace_relative_target(tmp_path: Path, monkeypatch):
    """前端既可能给 ``docs``，也可能给 ``admin__1/docs``，两种都要落到本人目录。"""
    root, source = _seed(tmp_path)
    _patch_root(monkeypatch, root)
    record = _make_artifact(source)

    saved = await svc.relocate_artifact(
        _fake_db(record),
        artifact_id=ARTIFACT_ID,
        owner_user_id=OWNER_ID,
        user_name="admin",
        target_dir=f"{OWNER_KEY}/docs",
    )
    assert saved == str(root / OWNER_KEY / "docs" / record.filename)


async def test_relocate_rejects_other_users_artifact(tmp_path: Path, monkeypatch):
    root, source = _seed(tmp_path)
    _patch_root(monkeypatch, root)
    record = _make_artifact(source, owner_user_id=999)

    with pytest.raises(svc.ArtifactRelocateError, match="产物不存在"):
        await svc.relocate_artifact(
            _fake_db(record),
            artifact_id=ARTIFACT_ID,
            owner_user_id=OWNER_ID,
            user_name="admin",
            target_dir="docs",
        )
    assert source.exists(), "拒绝时文件必须原地不动"


async def test_relocate_rejects_target_outside_own_workspace(tmp_path: Path, monkeypatch):
    root, source = _seed(tmp_path)
    _patch_root(monkeypatch, root)
    record = _make_artifact(source)

    with pytest.raises(svc.ArtifactRelocateError, match="目标目录必须是本人工作区内"):
        await svc.relocate_artifact(
            _fake_db(record),
            artifact_id=ARTIFACT_ID,
            owner_user_id=OWNER_ID,
            user_name="admin",
            target_dir="../other__2",
        )
    assert source.exists()


async def test_relocate_reports_conflict_instead_of_overwriting(tmp_path: Path, monkeypatch):
    """同名文件交给前端的「覆盖」二次确认，后端不静默替换用户已有文件。"""
    root, source = _seed(tmp_path)
    _patch_root(monkeypatch, root)
    record = _make_artifact(source)
    existing = root / OWNER_KEY / "docs" / "山水.png"
    existing.parent.mkdir(parents=True)
    existing.write_text("用户早就有的文件", encoding="utf-8")

    with pytest.raises(svc.ArtifactRelocateConflict):
        await svc.relocate_artifact(
            _fake_db(record),
            artifact_id=ARTIFACT_ID,
            owner_user_id=OWNER_ID,
            user_name="admin",
            target_dir="docs",
            filename="山水.png",
        )
    assert existing.read_text(encoding="utf-8") == "用户早就有的文件"
    assert source.exists()


async def test_relocate_is_noop_when_already_in_place(tmp_path: Path, monkeypatch):
    """目标就是当前所在位置时不该白搬一次（幂等）。

    注意 ``generated/`` 里的文件名带产物 ID 前缀，而 ``record.filename`` 是显示名；
    要构造真正的「原地」必须让两者一致——否则这是重命名，不是空操作。
    """
    root, source = _seed(tmp_path)
    _patch_root(monkeypatch, root)
    record = _make_artifact(source)
    record.filename = source.name

    saved = await svc.relocate_artifact(
        _fake_db(record),
        artifact_id=ARTIFACT_ID,
        owner_user_id=OWNER_ID,
        user_name="admin",
        target_dir="generated",
    )
    assert saved == str(source)
    assert source.read_bytes() == PNG_BYTES


async def test_relocate_rejects_missing_artifact_file(tmp_path: Path, monkeypatch):
    root, source = _seed(tmp_path)
    _patch_root(monkeypatch, root)
    source.unlink()
    record = _make_artifact(source)

    with pytest.raises(svc.ArtifactRelocateError, match="产物文件已不存在"):
        await svc.relocate_artifact(
            _fake_db(record),
            artifact_id=ARTIFACT_ID,
            owner_user_id=OWNER_ID,
            user_name="admin",
            target_dir="docs",
        )


@pytest.mark.parametrize("bad_id", ["", "xyz", "A" * 32, "0" * 31])
async def test_relocate_rejects_malformed_artifact_id(tmp_path: Path, monkeypatch, bad_id):
    root, _source = _seed(tmp_path)
    _patch_root(monkeypatch, root)
    with pytest.raises(svc.ArtifactRelocateError, match="产物 ID 无效"):
        await svc.relocate_artifact(
            _fake_db(None),
            artifact_id=bad_id,
            owner_user_id=OWNER_ID,
            user_name="admin",
            target_dir="docs",
        )


async def test_relocate_overwrites_only_when_explicitly_asked(tmp_path: Path, monkeypatch):
    """``overwrite=True`` 只该在用户点了「确认覆盖」之后出现；此时旧文件被替换。"""
    root, source = _seed(tmp_path)
    _patch_root(monkeypatch, root)
    record = _make_artifact(source)
    existing = root / OWNER_KEY / "docs" / "山水.png"
    existing.parent.mkdir(parents=True)
    existing.write_text("旧内容", encoding="utf-8")

    saved = await svc.relocate_artifact(
        _fake_db(record),
        artifact_id=ARTIFACT_ID,
        owner_user_id=OWNER_ID,
        user_name="admin",
        target_dir="docs",
        filename="山水.png",
        overwrite=True,
    )
    assert saved == str(existing)
    assert existing.read_bytes() == PNG_BYTES
    assert not source.exists()
