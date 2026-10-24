import os
import zipfile
import tarfile
import asyncio
import threading
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from unittest.mock import patch

from app.api.portal.endpoints import skills

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def mock_skills_dir(tmp_path):
    test_settings = SimpleNamespace(SKILLS_DIR=str(tmp_path))
    with patch("app.api.portal.endpoints.skills.settings", test_settings):
        yield tmp_path


def run(coro):
    return asyncio.run(coro)


def create_in_memory_zip(files: dict) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for filepath, content in files.items():
            content_bytes = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(filepath, content_bytes)
    return buf.getvalue()


def create_in_memory_tar(files: dict) -> bytes:
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for filepath, content in files.items():
            content_bytes = content.encode("utf-8") if isinstance(content, str) else content
            tarinfo = tarfile.TarInfo(name=filepath)
            tarinfo.size = len(content_bytes)
            tf.addfile(tarinfo, BytesIO(content_bytes))
    return buf.getvalue()


def test_upload_archive_success(mock_skills_dir):
    """测试将 zip 压缩包正常上传并解压到已有技能的 scripts 子目录下"""
    user = {"user_name": "admin", "role": "admin"}
    
    # 1. 创建目标技能
    skill_req = skills.SkillCreateRequest(
        id="test-archive-skill",
        name="测试压缩包技能",
        description="用于测试上传 zip 和 tar 包",
    )
    response = run(skills.create_skill(skill_req, user=user))
    assert response["status"] == "success"
    
    # 2. 构建 ZIP 压缩文件
    zip_bytes = create_in_memory_zip({
        "helpers.py": "def do_helper():\n    pass",
        "config.json": '{"version": "1.0"}'
    })
    
    upload = UploadFile(
        filename="scripts.zip",
        file=BytesIO(zip_bytes),
    )
    
    # 3. 触发上传解压 API
    response = run(
        skills.upload_skill_archive(
            "test-archive-skill",
            folder="scripts",
            file=upload,
            user=user,
        )
    )
    assert response["status"] == "success"
    assert "上传解压成功" in response["message"]
    
    # 4. 验证物理文件是否存在
    skill_dir = mock_skills_dir / "test-archive-skill"
    assert (skill_dir / "scripts" / "helpers.py").is_file()
    assert (skill_dir / "scripts" / "config.json").is_file()
    assert (skill_dir / "scripts" / "helpers.py").read_text(encoding="utf-8") == "def do_helper():\n    pass"


def test_upload_archive_tar_success(mock_skills_dir):
    """测试将 tar.gz 压缩包正常上传并解压到已有技能的 scripts 子目录下"""
    user = {"user_name": "admin", "role": "admin"}
    
    # 1. 创建目标技能
    skill_dir = mock_skills_dir / "test-tar-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test", encoding="utf-8")
    
    # 2. 构建 tar.gz 压缩包
    tar_bytes = create_in_memory_tar({
        "utils.py": "def calc():\n    return 42",
        "data.txt": "hello tar"
    })
    
    upload = UploadFile(
        filename="utils.tar.gz",
        file=BytesIO(tar_bytes),
    )
    
    # 3. 触发上传
    response = run(
        skills.upload_skill_archive(
            "test-tar-skill",
            folder="scripts",
            file=upload,
            user=user,
        )
    )
    assert response["status"] == "success"
    assert (skill_dir / "scripts" / "utils.py").is_file()
    assert (skill_dir / "scripts" / "data.txt").is_file()
    assert (skill_dir / "scripts" / "data.txt").read_text(encoding="utf-8") == "hello tar"


def test_skill_package_limit_is_100mb():
    """技能包上限从 20MB 放宽到 100MB。

    带脚本、模板与少量资源的技能包很轻松就几十 MB；20MB 的老限制逼着作者把
    资源拆出去，反而更乱。上限只在常量里定义一次，两个端点共用。
    """
    assert skills.SKILL_PACKAGE_MAX_MB == 100
    assert skills.SKILL_PACKAGE_MAX_BYTES == 100 * 1024 * 1024


def test_personal_skill_endpoints_share_the_same_limit():
    """个人技能与平台技能必须同一个上限，否则同一份包一边能传一边不能。"""
    from app.api.portal.endpoints import personal_skills

    assert personal_skills.SKILL_PACKAGE_MAX_BYTES == skills.SKILL_PACKAGE_MAX_BYTES


def test_upload_archive_size_limit(mock_skills_dir, monkeypatch):
    """测试文件大小超出上限时直接拦截。

    真造 100MB 数据只会拖慢测试，这里把常量缩小到 1KB 测边界；文案按
    `SKILL_PACKAGE_MAX_MB` 渲染，所以断言里带的是 100MB。
    """
    monkeypatch.setattr(skills, "SKILL_PACKAGE_MAX_BYTES", 1024)
    user = {"user_name": "admin", "role": "admin"}
    skill_dir = mock_skills_dir / "test-limit-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test", encoding="utf-8")

    # 大文件内容：超过被缩小的上限 1 字节
    large_data = b"x" * 1025
    upload = UploadFile(
        filename="large.zip",
        file=BytesIO(large_data),
    )

    with pytest.raises(HTTPException) as exc_info:
        run(
            skills.upload_skill_archive(
                "test-limit-skill",
                folder="scripts",
                file=upload,
                user=user,
            )
        )
    assert exc_info.value.status_code == 400
    assert "超出 100MB 限制" in exc_info.value.detail


def test_zip_slip_path_traversal_protection(mock_skills_dir):
    """测试防范路径穿越 (Zip Slip)，恶意文件包含 ../ 越界写入时被拦截"""
    user = {"user_name": "admin", "role": "admin"}
    skill_dir = mock_skills_dir / "test-slip-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test", encoding="utf-8")
    
    # 构造带有路径穿越的 ZIP 文件
    zip_bytes = create_in_memory_zip({
        "../../escape.txt": "dangerous content"
    })
    
    upload = UploadFile(
        filename="malicious.zip",
        file=BytesIO(zip_bytes),
    )
    
    with pytest.raises(HTTPException) as exc_info:
        run(
            skills.upload_skill_archive(
                "test-slip-skill",
                folder="scripts",
                file=upload,
                user=user,
            )
        )
    
    assert exc_info.value.status_code == 400
    assert "非法越权路径" in exc_info.value.detail
    # 确认物理文件没有被成功解压到外层
    assert not (mock_skills_dir / "escape.txt").exists()


def test_tar_slip_path_traversal_protection(mock_skills_dir):
    """测试 Tar 解包时的防范路径穿越攻击"""
    user = {"user_name": "admin", "role": "admin"}
    skill_dir = mock_skills_dir / "test-slip-skill-tar"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test", encoding="utf-8")
    
    # 构造带有路径穿越的 Tar 包
    tar_bytes = create_in_memory_tar({
        "../../escape_tar.txt": "dangerous content tar"
    })
    
    upload = UploadFile(
        filename="malicious.tar.gz",
        file=BytesIO(tar_bytes),
    )
    
    with pytest.raises(HTTPException) as exc_info:
        run(
            skills.upload_skill_archive(
                "test-slip-skill-tar",
                folder="scripts",
                file=upload,
                user=user,
            )
        )
    
    assert exc_info.value.status_code == 400
    assert "非法越权路径" in exc_info.value.detail
    assert not (mock_skills_dir / "escape_tar.txt").exists()


def test_import_skill_package_success(mock_skills_dir):
    """测试将一个完整的压缩技能包导入为新技能"""
    user = {"user_name": "admin", "role": "admin"}
    
    # 技能压缩包中直接包含 SKILL.md 
    zip_bytes = create_in_memory_zip({
        "SKILL.md": "---\nname: 导入的技能\ndescription: 成功导入\n---\n\n# Body",
        "scripts/main.py": "print('hello')"
    })
    
    upload = UploadFile(
        filename="imported-skill.zip",
        file=BytesIO(zip_bytes),
    )
    
    # 触发导入 API
    response = run(
        skills.import_skill_package(
            file=upload,
            overwrite=False,
            user=user
        )
    )
    assert response["status"] == "success"
    assert "导入成功" in response["message"]
    
    # 校验导入的技能结构
    skill_dir = mock_skills_dir / "imported-skill"
    assert skill_dir.is_dir()
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "scripts" / "main.py").is_file()
    
    # 校验元数据解析正常
    meta = skills.parse_skill_metadata("imported-skill", str(skill_dir / "SKILL.md"))
    assert meta["name"] == "导入的技能"
    assert meta["description"] == "成功导入"


def test_import_skill_package_overwrite_protection(mock_skills_dir):
    """测试导入已存在的技能时触发覆盖保护"""
    user = {"user_name": "admin", "role": "admin"}
    
    # 创建同名技能目录
    existing_dir = mock_skills_dir / "dup-skill"
    existing_dir.mkdir()
    (existing_dir / "SKILL.md").write_text("# Existing", encoding="utf-8")
    
    zip_bytes = create_in_memory_zip({
        "SKILL.md": "---\nname: 新技能\n---\n# New",
    })
    
    upload = UploadFile(
        filename="dup-skill.zip",
        file=BytesIO(zip_bytes),
    )
    
    # 不开启 overwrite 应当被拒绝
    with pytest.raises(HTTPException) as exc_info:
        run(
            skills.import_skill_package(
                file=upload,
                overwrite=False,
                user=user
            )
        )
    assert exc_info.value.status_code == 400
    assert "已存在，请开启 overwrite" in exc_info.value.detail
    
    # 开启 overwrite 应该导入成功，且原内容被覆盖
    response = run(
        skills.import_skill_package(
            file=upload,
            overwrite=True,
            user=user
        )
    )
    assert response["status"] == "success"
    assert (existing_dir / "SKILL.md").read_text(encoding="utf-8") == "---\nname: 新技能\n---\n# New"


def test_import_skill_package_missing_skill_md(mock_skills_dir):
    """测试上传的技能包若缺少核心规范文件 SKILL.md，将报错且自动物理清除已解压文件"""
    user = {"user_name": "admin", "role": "admin"}
    
    zip_bytes = create_in_memory_zip({
        "readme.txt": "this is not a skill",
        "scripts/main.py": "print('hello')"
    })
    
    upload = UploadFile(
        filename="invalid-skill.zip",
        file=BytesIO(zip_bytes),
    )
    
    with pytest.raises(HTTPException) as exc_info:
        run(
            skills.import_skill_package(
                file=upload,
                overwrite=False,
                user=user
            )
        )
    
    assert exc_info.value.status_code == 400
    assert "必须包含核心规范文件 SKILL.md" in exc_info.value.detail
    
    # 物理清除机制：检验目标文件夹是否不存在
    assert not (mock_skills_dir / "invalid-skill").exists()


def test_import_skill_package_auto_unwrap(mock_skills_dir):
    """测试将一个整个文件夹直接打包的压缩包导入，能够自动平铺外层子目录"""
    user = {"user_name": "admin", "role": "admin"}
    
    # 文件夹整体打包：顶层是一个 my-source/ 目录，里面有 SKILL.md 和 scripts/
    zip_bytes = create_in_memory_zip({
        "my-source/SKILL.md": "---\nname: 平铺技能\n---\n# Test",
        "my-source/scripts/plugin.py": "print('plugin')",
        "my-source/.hidden_junk": "should be ignored"
    })
    
    upload = UploadFile(
        filename="unwrap-skill.zip",
        file=BytesIO(zip_bytes),
    )
    
    response = run(
        skills.import_skill_package(
            file=upload,
            overwrite=False,
            user=user
        )
    )
    assert response["status"] == "success"
    
    skill_dir = mock_skills_dir / "unwrap-skill"
    # 平铺后直接存在于 unwrap-skill 根目录下，而不是 my-source/
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "scripts" / "plugin.py").is_file()
    assert not (skill_dir / "my-source").exists()


def test_zip_extract_budget_blocks_zip_bomb(mock_skills_dir, monkeypatch):
    """解压后的总量必须受限：上传只限 20MB，高压缩比归档能膨胀成数 GB。

    个人技能上传是普通用户可用的入口，若不卡解压侧，一次上传即可撑爆磁盘。
    """
    monkeypatch.setattr(skills, "MAX_EXTRACTED_BYTES", 1024)
    user = {"user_name": "admin", "role": "admin"}
    skill_dir = mock_skills_dir / "test-budget-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test", encoding="utf-8")

    zip_bytes = create_in_memory_zip({"big.txt": "x" * 8192})
    upload = UploadFile(filename="bomb.zip", file=BytesIO(zip_bytes))

    with pytest.raises(HTTPException) as exc_info:
        run(
            skills.upload_skill_archive(
                "test-budget-skill", folder="scripts", file=upload, user=user
            )
        )
    assert exc_info.value.status_code == 400
    assert "解压后体积超过上限" in exc_info.value.detail
    # 在写盘前就拦下，不应留下内容
    assert not (skill_dir / "scripts" / "big.txt").exists()


def test_tar_extract_budget_blocks_zip_bomb(mock_skills_dir, monkeypatch):
    """TAR 侧同样受限（tar.gz 的压缩比往往比 zip 更高）。"""
    monkeypatch.setattr(skills, "MAX_EXTRACTED_BYTES", 1024)
    user = {"user_name": "admin", "role": "admin"}
    skill_dir = mock_skills_dir / "test-budget-skill-tar"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test", encoding="utf-8")

    tar_bytes = create_in_memory_tar({"big.txt": b"x" * 8192})
    upload = UploadFile(filename="bomb.tar.gz", file=BytesIO(tar_bytes))

    with pytest.raises(HTTPException) as exc_info:
        run(
            skills.upload_skill_archive(
                "test-budget-skill-tar", folder="scripts", file=upload, user=user
            )
        )
    assert exc_info.value.status_code == 400
    assert "解压后体积超过上限" in exc_info.value.detail
    # 同样要在写盘前/写盘中止住，不留半份内容
    assert not (skill_dir / "scripts" / "big.txt").exists()


def test_extract_budget_limits_file_count(mock_skills_dir, monkeypatch):
    """文件数也要受限：大量小文件同样会拖垮 inode 与元数据。"""
    monkeypatch.setattr(skills, "MAX_EXTRACTED_FILES", 2)
    user = {"user_name": "admin", "role": "admin"}
    skill_dir = mock_skills_dir / "test-budget-count"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test", encoding="utf-8")

    zip_bytes = create_in_memory_zip({f"f{i}.txt": "x" for i in range(5)})
    upload = UploadFile(filename="many.zip", file=BytesIO(zip_bytes))

    with pytest.raises(HTTPException) as exc_info:
        run(
            skills.upload_skill_archive(
                "test-budget-count", folder="scripts", file=upload, user=user
            )
        )
    assert exc_info.value.status_code == 400
    assert "文件数超过上限" in exc_info.value.detail


def test_zero_byte_files_are_caught_only_by_file_count(mock_skills_dir, monkeypatch):
    """0 字节文件让体积始终为 0，只有文件数上限能拦住这类洪水。

    这是文件数校验不能删的原因：体积上限量的是「占多少空间」，量不了「要创建多少个
    inode、跑多少次 write」。实测 45KB 的 zip 即可解出 500 个空文件（每条约 90 字节
    归档开销），按 100MB 体积上限放行时条目数可达百万级。
    """
    monkeypatch.setattr(skills, "MAX_EXTRACTED_FILES", 3)
    # 体积上限设到 1GB，确保这道拦截只可能来自文件数
    monkeypatch.setattr(skills, "MAX_EXTRACTED_BYTES", 1024 * 1024 * 1024)
    user = {"user_name": "admin", "role": "admin"}
    skill_dir = mock_skills_dir / "test-zero-byte-flood"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test", encoding="utf-8")

    zip_bytes = create_in_memory_zip({f"empty{i}.txt": "" for i in range(6)})
    upload = UploadFile(filename="empty.zip", file=BytesIO(zip_bytes))

    with pytest.raises(HTTPException) as exc_info:
        run(
            skills.upload_skill_archive(
                "test-zero-byte-flood", folder="scripts", file=upload, user=user
            )
        )
    assert exc_info.value.status_code == 400
    assert "文件数超过上限" in exc_info.value.detail


def test_archive_extraction_runs_off_the_event_loop(mock_skills_dir, monkeypatch):
    """解压必须交给线程池：同步解压会把整个 worker 的事件循环卡住。

    文件数上限放开后，一个包可能要写几万个文件，几秒到几十秒的同步 IO 会让期间
    所有请求一起排队。这里锁住「不在事件循环线程上执行解压」这个行为。
    """
    thread_names = []
    original = skills.safe_extract_archive

    def spy(*args, **kwargs):
        thread_names.append(threading.current_thread().name)
        return original(*args, **kwargs)

    monkeypatch.setattr(skills, "safe_extract_archive", spy)
    user = {"user_name": "admin", "role": "admin"}
    skill_dir = mock_skills_dir / "test-threadpool"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test", encoding="utf-8")

    zip_bytes = create_in_memory_zip({"a.txt": "hello"})
    upload = UploadFile(filename="ok.zip", file=BytesIO(zip_bytes))

    run(
        skills.upload_skill_archive(
            "test-threadpool", folder="scripts", file=upload, user=user
        )
    )

    assert thread_names, "解压没有被调用"
    assert thread_names[0] != threading.main_thread().name
