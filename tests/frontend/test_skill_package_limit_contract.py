"""技能包导入上限 100MB，且只在一处定义（前端契约）。

背景：导入弹窗写着「最大 20MB」，点选一个 30MB 的包直接被 toast 拦下。20MB 对
带脚本与资源的技能包太紧，放宽到 100MB；关键是**别再把数字抄到四个地方**——
弹窗文案、拖拽校验、点选校验、批量上传预检各写一份，改一处就漏三处。

后端同源常量在 `app/core/upload_limits.py`，本文件只锁前端这一侧。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS_PAGE = ROOT / "frontend/src/views/SkillsManagement.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return SKILLS_PAGE.read_text(encoding="utf-8")


def test_limit_is_declared_once_in_mb_and_bytes():
    source = _source()

    assert "const SKILL_PACKAGE_MAX_MB = 100" in source
    assert "const SKILL_PACKAGE_MAX_BYTES = SKILL_PACKAGE_MAX_MB * 1024 * 1024" in source


def test_import_checks_use_the_constant_not_a_literal():
    source = _source()

    assert "file.size > SKILL_PACKAGE_MAX_BYTES" in source
    assert "20 * 1024 * 1024" not in source, "别再写字面量，改上限时一定会漏"
    # 点选与拖拽共用同一个校验函数，否则两条路径的规则会各自漂移
    assert source.count("validateArchiveFile(file)") >= 2


def test_toasts_render_the_limit_from_the_constant():
    source = _source()

    assert "导入技能包不能超过 ${SKILL_PACKAGE_MAX_MB}MB" in source
    assert "'导入技能包不能超过 20MB'" not in source


def test_dialog_hint_shows_the_same_number():
    source = _source()

    assert "最大 {{ SKILL_PACKAGE_MAX_MB }}MB" in source
    assert "最大 20MB" not in source


def test_batch_upload_precheck_uses_the_same_limit():
    source = _source()

    assert "isArchive ? SKILL_PACKAGE_MAX_BYTES : 10 * 1024 * 1024" in source
    assert "超过 ${isArchive ? `${SKILL_PACKAGE_MAX_MB}MB` : '10MB'} 限制" in source
