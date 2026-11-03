"""原图生命周期契约：鉴权收口 + 删库/删文档清理。

图片转 md 之后，原图留在平台本地目录里。这两件事以前是空的：

1. ``data/uploads/knowledge_images`` 整个处在 ``/static/uploads`` 静态托管之下，
   知道路径就能拿到原图——合同、证件这类扫描件等于裸奔；
2. 删库、删文档只删 RAGFlow 侧的东西，本地文件永远留着，越攒越多还没人清。

这里的用例守住三条边界：目录不许回到上传目录、清理要按库/按文档删干净、
下载只认纯文件名（``../`` 穿不出去）。
"""
import pathlib

import pytest

from app.services import knowledge_image_service as image_service

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPG = b"\xff\xd8\xff" + b"1" * 32


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    """把新老两个目录都指到 tmp，避免读到仓库里真实文件。"""
    new = tmp_path / "knowledge_images"
    legacy = tmp_path / "uploads" / "knowledge_images"
    new.mkdir(parents=True, exist_ok=True)
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(image_service, "_SOURCE_DIR", str(new))
    monkeypatch.setattr(image_service, "_LEGACY_SOURCE_DIR", str(legacy))
    return new, legacy


def test_source_dir_is_outside_static_upload_mount():
    """原图目录不能落在 data/uploads 下——那个目录整个挂在 /static/uploads。"""
    assert pathlib.Path(image_service._SOURCE_DIR).parts[:2] == ("data", "knowledge_images")
    assert not str(image_service._SOURCE_DIR).startswith("data/uploads")
    # 老位置仍被记录着，迁移才有的可搬
    assert image_service._LEGACY_SOURCE_DIR == "data/uploads/knowledge_images"


def test_absolute_path_rejects_traversal(dirs):
    new, _ = dirs
    (new / "ds-1").mkdir()
    (new / "ds-1" / "a-1234abcd.png").write_bytes(PNG)

    assert image_service.absolute_source_path("ds-1", "a-1234abcd.png")
    # 只认纯文件名，任何带目录成分的一律拒绝
    assert image_service.absolute_source_path("ds-1", "../ds-1/a-1234abcd.png") is None
    assert image_service.absolute_source_path("ds-1", "../../etc/passwd") is None
    assert image_service.absolute_source_path("ds-1", "/etc/passwd") is None


def test_absolute_path_rejects_non_image_and_missing(dirs):
    new, _ = dirs
    (new / "ds-1").mkdir()
    (new / "ds-1" / "a-1234abcd.png").write_bytes(PNG)
    (new / "ds-1" / "secret.txt").write_text("x", encoding="utf-8")

    assert image_service.absolute_source_path("ds-1", "secret.txt") is None
    assert image_service.absolute_source_path("ds-1", "nope.png") is None
    # 库目录都不存在时也不许抛
    assert image_service.absolute_source_path("ds-missing", "a-1234abcd.png") is None


def test_absolute_path_falls_back_to_legacy_dir(dirs):
    """升级过程中还没搬走的图，仍然要能打开。"""
    _, legacy = dirs
    (legacy / "ds-1").mkdir()
    (legacy / "ds-1" / "b-5678ef90.webp").write_bytes(PNG)

    assert image_service.absolute_source_path("ds-1", "b-5678ef90.webp")


def test_delete_dataset_images_removes_new_and_legacy(dirs):
    new, legacy = dirs
    for root in (new, legacy):
        (root / "ds-1").mkdir()
        (root / "ds-1" / "a-1234abcd.png").write_bytes(PNG)
        (root / "ds-1" / "a-1234abcd.md").write_text("# md", encoding="utf-8")
        (root / "ds-2").mkdir()
        (root / "ds-2" / "keep-9999aaaa.png").write_bytes(PNG)

    removed = image_service.delete_dataset_images("ds-1")

    assert removed == 4                      # 两个目录 × (png + md)
    assert not (new / "ds-1").exists()       # 目录本身也清掉
    assert not (legacy / "ds-1").exists()
    assert (new / "ds-2" / "keep-9999aaaa.png").exists()   # 别的库不动


def test_delete_dataset_images_is_safe_when_absent(dirs):
    assert image_service.delete_dataset_images("never-existed") == 0


def test_delete_source_for_markdown_removes_image_and_snapshot(dirs):
    new, _ = dirs
    (new / "ds-1").mkdir()
    (new / "ds-1" / "合同-第3页-a3f9c2d1.png").write_bytes(PNG)
    (new / "ds-1" / "合同-第3页-a3f9c2d1.md").write_text("# md", encoding="utf-8")
    (new / "ds-1" / "别的文档-bbbb1111.md").write_text("# other", encoding="utf-8")

    removed = image_service.delete_source_for_markdown("ds-1", "合同-第3页-a3f9c2d1.md")

    assert removed == 2
    assert not (new / "ds-1" / "合同-第3页-a3f9c2d1.png").exists()
    assert not (new / "ds-1" / "合同-第3页-a3f9c2d1.md").exists()
    assert (new / "ds-1" / "别的文档-bbbb1111.md").exists()


def test_delete_source_for_markdown_ignores_plain_documents(dirs):
    """普通文档（不是图片转来的）反查不到原图，返回 0 且不抛。"""
    new, _ = dirs
    (new / "ds-1").mkdir()
    assert image_service.delete_source_for_markdown("ds-1", "员工手册.pdf") == 0
    assert image_service.delete_source_for_markdown("ds-1", "") == 0


def test_source_path_for_markdown_matches_hash_tail(dirs):
    new, _ = dirs
    (new / "ds-1").mkdir()
    (new / "ds-1" / "截图-5-77aa88bb.jpg").write_bytes(JPG)

    found = image_service.source_path_for_markdown("ds-1", "截图-5-77aa88bb.md")

    assert found and found.endswith("截图-5-77aa88bb.jpg")


def test_migrate_legacy_dir_moves_files(dirs):
    new, legacy = dirs
    (legacy / "ds-1").mkdir()
    (legacy / "ds-1" / "a-1234abcd.png").write_bytes(PNG)
    (legacy / "ds-1" / "a-1234abcd.md").write_text("# md", encoding="utf-8")

    moved = image_service.migrate_legacy_dir()

    assert moved == 2
    assert (new / "ds-1" / "a-1234abcd.png").exists()
    assert (new / "ds-1" / "a-1234abcd.md").exists()
    assert not (legacy / "ds-1").exists()
    # 再跑一次是空操作
    assert image_service.migrate_legacy_dir() == 0


def test_migrate_legacy_dir_keeps_existing_target(dirs):
    """新目录已有同内容文件时不覆盖，旧文件留着下次再说。"""
    new, legacy = dirs
    (new / "ds-1").mkdir()
    (new / "ds-1" / "a-1234abcd.png").write_bytes(PNG)
    (legacy / "ds-1").mkdir()
    (legacy / "ds-1" / "a-1234abcd.png").write_bytes(b"different")

    assert image_service.migrate_legacy_dir() == 0
    assert (new / "ds-1" / "a-1234abcd.png").read_bytes() == PNG
    assert (legacy / "ds-1" / "a-1234abcd.png").exists()


def test_mime_for_extension():
    assert image_service.mime_for_extension("a.PNG") == "image/png"
    assert image_service.mime_for_extension("a.webp") == "image/webp"
    assert image_service.mime_for_extension("a.exe") == "application/octet-stream"


# ---------- 端点接线：清理必须真的挂在删除路径上 ----------


def _endpoint_source(name: str) -> str:
    import inspect

    import app.api.portal.endpoints.ragflow as ragflow

    return inspect.getsource(getattr(ragflow, name))


def test_dataset_delete_endpoint_cleans_image_dir():
    src = _endpoint_source("delete_ragflow_datasets")
    assert "image_service.delete_dataset_images(dataset_id)" in src
    # 清理必须在 mark_deleted 之后：库还在的时候不能先把图删了
    assert src.index("mark_deleted") < src.index("delete_dataset_images")


def test_document_delete_endpoint_cleans_by_document_name():
    src = _endpoint_source("delete_dataset_documents")
    # 删除接口只吃 id，必须先查名字才能反查原图
    assert "list_documents" in src
    assert "image_service.delete_source_for_markdown(dataset_id, name)" in src
    # 取名字失败只跳过清理，不能挡住删除
    assert "names_by_id = {}" in src


def test_image_source_endpoint_requires_dataset_access():
    src = _endpoint_source("get_dataset_image_source")
    assert "require_dataset_access" in src
    assert "absolute_source_path" in src
    assert "FileResponse" in src
    # 目录一层就够：原图不该再从静态托管出去（目录位置由
    # test_source_dir_is_outside_static_upload_mount 守）
    assert "image_service.absolute_source_path(dataset_id, file_name)" in src


def test_image_source_endpoint_404_when_missing():
    """权限通过但文件不在时必须 404，而不是 500 或返回空文件。"""
    src = _endpoint_source("get_dataset_image_source")
    assert "if not path:" in src
    assert "status_code=404" in src
    # 鉴权在取文件之前：否则等于先读盘再判断能不能看
    assert src.index("require_dataset_access") < src.index("absolute_source_path")


def test_image_response_is_private_cached():
    """原图是私有资源，只允许浏览器私有缓存，不能被 CDN / 代理缓存。"""
    src = _endpoint_source("get_dataset_image_source")
    assert "private, max-age=" in src


# ---------- 原图探测端点：文档详情里「查看原图」的前置判断 ----------


@pytest.mark.asyncio
async def test_source_info_endpoint_returns_url_and_metadata(dirs, monkeypatch):
    """探测入参是 md 文件名，返回的是原图名 + 可直接给 src 的 url。"""
    from app.api.portal.endpoints import ragflow as ragflow_ep

    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow_ep, "require_dataset_access", _allow)
    new, _ = dirs
    (new / "ds-1").mkdir()
    (new / "ds-1" / "白板纪要-a3f9c2d1.png").write_bytes(PNG)

    # 权限校验放行：用 admin 角色，require_dataset_access 直接 return
    result = await ragflow_ep.get_dataset_image_source_info(
        dataset_id="ds-1",
        md_filename="白板纪要-a3f9c2d1.md",
        user={"user_id": 1, "user_name": "tester", "role": "admin"},
        db=None,
    )
    data = result["data"]

    assert data["exists"] is True
    assert data["name"] == "白板纪要-a3f9c2d1.png"
    assert data["size"] == len(PNG)
    assert data["content_type"] == "image/png"
    assert data["url"] == "/api/portal/ragflow/datasets/ds-1/images/source/白板纪要-a3f9c2d1.png"


@pytest.mark.asyncio
async def test_source_info_endpoint_reports_missing_without_throwing(dirs, monkeypatch):
    """普通文档 / 原图已删：返回 exists=false，前端据此不渲染入口。"""
    from app.api.portal.endpoints import ragflow as ragflow_ep

    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow_ep, "require_dataset_access", _allow)

    async def run(name):
        return await ragflow_ep.get_dataset_image_source_info(
            dataset_id="ds-1",
            md_filename=name,
            user={"user_id": 1, "user_name": "tester", "role": "admin"},
            db=None,
        )

    assert (await run("员工手册.pdf"))["data"] == {"exists": False}
    assert (await run("不存在-a3f9c2d1.md"))["data"] == {"exists": False}


def test_frontend_probes_before_showing_source_entry():
    """前端只在探测通过后渲染入口，且用哈希尾巴先筛一遍，避免每个文档都打一次接口。"""
    page = pathlib.Path("frontend/src/views/KnowledgeBaseManagement.vue").read_text(encoding="utf-8")

    assert "sourceImage" in page
    assert "looksLikeImageDerivedDoc" in page
    # 名字先本地筛：`-8位hex.md` 才算图片转来的文档
    assert r"/-[0-9a-f]{8}\.md$/i" in page
    # 探测在前，有结果才渲染入口
    assert 'v-if="sourceImage"' in page
    assert "getImageSourceInfo" in page
    # 图片地址来自探测接口返回的 url（鉴权端点），不是拼静态路径
    assert ':src="sourceImage.url"' in page
