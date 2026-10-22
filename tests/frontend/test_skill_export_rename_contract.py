"""技能导出/下载与重命名的前后端契约。

「进」的通道一直很完整（上传、导入、创建），「出」的通道此前完全不存在：技能既不能
导出备份、也不能改名，想复用只能去服务器磁盘上手工拷目录。这组契约锁住新增通道，
以及重命名必须同步引用这件事（只改目录会静默断链）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT = ROOT / "frontend" / "src" / "views" / "SkillsManagement.vue"
PLATFORM_EP = ROOT / "app" / "api" / "portal" / "endpoints" / "skills.py"
PERSONAL_EP = ROOT / "app" / "api" / "portal" / "endpoints" / "personal_skills.py"
RENAME_SVC = ROOT / "app" / "services" / "skill_rename_service.py"


def test_platform_and_personal_expose_export_and_download():
    for path, validator in ((PLATFORM_EP, "validate_secure_skill_path"), (PERSONAL_EP, "_validate_personal_skill_path")):
        text = path.read_text(encoding="utf-8")
        assert '@router.get("/{skill_id}/export"' in text
        assert '@router.get("/{skill_id}/download"' in text
        # 导出必须经过各自的路径校验，不能直接拼目录
        assert validator in text
        assert "build_skill_archive" in text


def test_archive_builder_skips_runtime_artifacts():
    text = PLATFORM_EP.read_text(encoding="utf-8")
    assert "def build_skill_archive" in text
    # .seed（运行时快照）与 Python 缓存不应跟着技能包走
    assert "__pycache__" in text
    assert "startswith(\".\")" in text
    assert '".pyc"' in text


def test_both_endpoints_expose_rename():
    for path in (PLATFORM_EP, PERSONAL_EP):
        text = path.read_text(encoding="utf-8")
        assert '@router.put("/{skill_id}/rename"' in text
        assert "rename_skill(" in text


def test_rename_service_syncs_all_three_reference_places():
    text = RENAME_SVC.read_text(encoding="utf-8")
    assert "def sync_publication_lineage" in text
    assert "def sync_agent_bindings" in text
    assert "def sync_conversation_mounts" in text
    # 目录改名必须原子，且拒绝覆盖已存在的目标技能
    assert "os.rename" in text
    assert "已被占用" in text


def test_frontend_wires_export_and_rename_with_refresh():
    text = MANAGEMENT.read_text(encoding="utf-8")
    assert "const exportSkill" in text
    assert "const renameSkill" in text
    # 导出走 axios（保留鉴权头），不能用 window.open 丢凭据
    assert "responseType: 'blob'" in text
    # 导出 / 重命名已收进「⋯」菜单，由 runSkillAction 分发
    assert "runSkillAction('export')" in text
    assert "runSkillAction('rename')" in text
    # 重命名后必须刷新列表，否则界面还显示旧 ID
    assert "await Promise.all([fetchSkills(), fetchPersonalSkills()])" in text
    # 未同步的引用必须展示给用户，不能静默
    assert "warnings" in text
