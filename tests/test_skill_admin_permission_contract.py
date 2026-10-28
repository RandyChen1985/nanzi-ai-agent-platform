from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_skill_platform_endpoints_use_dedicated_element_permission():
    source = (ROOT / "app/api/portal/endpoints/skills.py").read_text(encoding="utf-8")

    assert 'element", "element:skills:admin"' in source
    assert "skill_platform_admin" in source
    assert "skill_publication_reviewer = skill_platform_admin" in source


def test_skill_workbench_exposes_dedicated_element_permission():
    """权限码的注册地是后端菜单种子（前端常量已只剩类型与纯函数）。"""
    from app.services.menu_seed import builtin_element_permission_codes

    view = (ROOT / "frontend/src/views/SkillsManagement.vue").read_text(encoding="utf-8")

    assert "element:skills:admin" in builtin_element_permission_codes()
    assert "element:skills:admin" in view


def test_skill_admin_permission_is_seeded_for_both_database_dialects():
    mysql = (ROOT / "db-prod/V113-register_skill_admin_permission.sql").read_text(encoding="utf-8")
    postgres = (ROOT / "db-prod-pg/V12-register_skill_admin_permission.sql").read_text(encoding="utf-8")

    assert "element" in mysql and "element:skills:admin" in mysql
    assert "element" in postgres and "element:skills:admin" in postgres


def test_publication_notifications_target_the_dedicated_element_permission():
    source = (ROOT / "app/services/skill_publication_service.py").read_text(encoding="utf-8")

    assert source.count('ResourcePermission.resource_type == "element"') == 2
    assert source.count('ResourcePermission.resource_id == "element:skills:admin"') == 2


def test_skill_publication_service_parses_with_python311():
    python311 = shutil.which("python3.11")
    if not python311:
        pytest.skip("python3.11 is not installed in this environment")

    service_path = ROOT / "app/services/skill_publication_service.py"
    result = subprocess.run(
        [
            python311,
            "-c",
            "from pathlib import Path; import sys; compile(Path(sys.argv[1]).read_text(), sys.argv[1], 'exec')",
            str(service_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_session_start_primes_platform_skill_admin_cache():
    """会话启动必须预热同步缓存，否则工具侧（同步函数）永远判定为无权限。

    ``create_skills`` / ``import_skill_from_sandbox`` 是同步工具，只能读进程内缓存。
    预热点放在 ``stream_chat`` 里与「禁用公共技能」缓存并列——这两个缓存都依赖同一个
    请求上下文里的真实用户身份。
    """
    source = (ROOT / "app/services/ai/agent_service.py").read_text(encoding="utf-8")

    assert "prime_platform_skill_admin" in source
    # 预热必须在身份确定之后，不能拿匿名上下文去查
    prime_at = source.index("prime_platform_skill_admin")
    required_user_id_at = source.index("required_user_id = require_user_id(user_info)")
    assert required_user_id_at < prime_at


def test_skill_tools_use_the_shared_platform_admin_gate():
    """两个写技能的工具都走同一道闸门，不再各自判 role 字符串。"""
    source = (ROOT / "app/services/ai/tools/system_executive_tools.py").read_text(
        encoding="utf-8"
    )

    assert source.count("can_manage_platform_skills(user_info)") == 2
    # 旧口径（role 里含 admin）不该再出现在写平台技能的判定里
    assert 'is_admin = (role == "admin"' not in source
