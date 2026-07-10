from io import BytesIO
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from unittest.mock import patch

from app.api.portal.endpoints import skills
from app.api.portal.endpoints.skills import FileEditRequest, SkillCreateRequest

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def mock_skills_dir(tmp_path):
    test_settings = SimpleNamespace(SKILLS_DIR=str(tmp_path))
    with patch("app.api.portal.endpoints.skills.settings", test_settings):
        yield tmp_path


def run(coro):
    return asyncio.run(coro)


def test_list_skills_without_menu_permission(mock_skills_dir):
    """Embed 等场景：无技能管理菜单权限的用户仍可拉取全量技能列表。"""
    skill_dir = mock_skills_dir / "public-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: 公开技能\ndescription: 任意登录用户可见\n---\n\n# Body",
        encoding="utf-8",
    )
    user = {"user_id": 99, "user_name": "embed_user", "role": "user"}

    response = run(skills.list_skills(user=user))
    assert response["status"] == "success"
    assert len(response["data"]) == 1
    assert response["data"][0]["id"] == "public-skill"
    assert response["data"][0]["name"] == "公开技能"

    preview = run(skills.preview_skill_md(skill_id="public-skill", user=user))
    assert preview["status"] == "success"
    assert preview["data"]["skill_md_content"].startswith("---")
    assert preview["data"]["name"] == "公开技能"


def test_skills_lifecycle_flow(mock_skills_dir):
    user = {"user_name": "admin", "role": "admin"}

    response = run(skills.list_skills(user=user))
    assert response["status"] == "success"
    assert response["data"] == []

    skill_req = SkillCreateRequest(
        id="test-cli-helper",
        name="测试CLI辅助技能",
        description="提供CLI脚本操作的测试工具技能",
    )
    response = run(skills.create_skill(skill_req, user=user))
    assert response["status"] == "success"

    response = run(skills.list_skills(user=user))
    data = response["data"]
    assert len(data) == 1
    assert data[0]["id"] == "test-cli-helper"
    assert data[0]["name"] == "测试CLI辅助技能"
    assert data[0]["description"] == "提供CLI脚本操作的测试工具技能"

    with pytest.raises(HTTPException) as duplicate_error:
        run(skills.create_skill(skill_req, user=user))
    assert duplicate_error.value.status_code == 400
    assert "已存在" in duplicate_error.value.detail

    response = run(skills.get_skill_detail("test-cli-helper", user=user))
    detail = response["data"]
    assert detail["id"] == "test-cli-helper"
    assert detail["name"] == "测试CLI辅助技能"
    assert "在此处编写该技能的具体战术规范" in detail["skill_md_content"]
    assert len(detail["file_tree"]) == 1
    assert detail["file_tree"][0]["name"] == "SKILL.md"
    assert detail["file_tree"][0]["is_dir"] is False

    updated_md = (
        "---\n"
        "name: 更新后的名称\n"
        "description: 更新后的描述\n"
        "---\n\n"
        "# 战术守则\n"
        "1. 绝不越权\n"
    )
    edit_req = FileEditRequest(path="SKILL.md", content=updated_md)
    response = run(skills.edit_skill_file("test-cli-helper", edit_req, user=user))
    assert response["status"] == "success"

    response = run(skills.list_skills(user=user))
    data = response["data"]
    assert data[0]["name"] == "更新后的名称"
    assert data[0]["description"] == "更新后的描述"

    upload = UploadFile(
        filename="helper.py",
        file=BytesIO(b"def say_hello():\n    print('hello')"),
    )
    response = run(
        skills.upload_skill_file(
            "test-cli-helper",
            folder="scripts",
            file=upload,
            user=user,
        )
    )
    assert "上传成功" in response["message"]

    response = run(skills.get_skill_detail("test-cli-helper", user=user))
    detail = response["data"]
    assert len(detail["file_tree"]) == 2
    scripts_dir = next(node for node in detail["file_tree"] if node["name"] == "scripts")
    assert scripts_dir["is_dir"] is True
    assert len(scripts_dir["children"]) == 1
    assert scripts_dir["children"][0]["name"] == "helper.py"

    response = run(
        skills.get_skill_file_content(
            "test-cli-helper",
            path="scripts/helper.py",
            user=user,
        )
    )
    assert "say_hello()" in response["content"]

    bad_edit = FileEditRequest(path="scripts/helper.exe", content="some text")
    with pytest.raises(HTTPException) as bad_edit_error:
        run(skills.edit_skill_file("test-cli-helper", bad_edit, user=user))
    assert bad_edit_error.value.status_code == 400
    assert "只允许在线编辑保存文本" in bad_edit_error.value.detail

    huge_edit = FileEditRequest(path="SKILL.md", content="x" * (2 * 1024 * 1024 + 10))
    with pytest.raises(HTTPException) as huge_edit_error:
        run(skills.edit_skill_file("test-cli-helper", huge_edit, user=user))
    assert huge_edit_error.value.status_code == 400
    assert "大小超出 2MB" in huge_edit_error.value.detail

    with pytest.raises(HTTPException) as delete_core_error:
        run(skills.delete_skill_file("test-cli-helper", path="SKILL.md", user=user))
    assert delete_core_error.value.status_code == 400
    assert "禁止直接删除核心规范文件" in delete_core_error.value.detail

    response = run(
        skills.delete_skill_file(
            "test-cli-helper",
            path="scripts/helper.py",
            user=user,
        )
    )
    assert "删除成功" in response["message"]

    response = run(skills.get_skill_detail("test-cli-helper", user=user))
    detail = response["data"]
    scripts_dir = next(node for node in detail["file_tree"] if node["name"] == "scripts")
    assert len(scripts_dir["children"]) == 0

    response = run(skills.delete_entire_skill("test-cli-helper", user=user))
    assert "物理彻底移除" in response["message"]

    response = run(skills.list_skills(user=user))
    assert response["data"] == []


def test_path_traversal_protection(mock_skills_dir):
    user = {"user_name": "admin", "role": "admin"}

    with pytest.raises(HTTPException) as bad_id_error:
        run(skills.get_skill_detail("../../etc", user=user))
    assert bad_id_error.value.status_code == 400
    assert "非法智能体技能ID格式" in bad_id_error.value.detail

    edit_req = FileEditRequest(
        path="../../../etc/passwd",
        content="root:x:0:0:root:/root:/bin/bash",
    )
    with pytest.raises(HTTPException) as bad_edit_error:
        run(skills.edit_skill_file("test-skill", edit_req, user=user))
    assert bad_edit_error.value.status_code == 403
    assert "安全拦截" in bad_edit_error.value.detail

    with pytest.raises(HTTPException) as bad_read_error:
        run(
            skills.get_skill_file_content(
                "test-skill",
                path="../../../etc/passwd",
                user=user,
            )
        )
    assert bad_read_error.value.status_code == 403
    assert "安全拦截" in bad_read_error.value.detail


def test_skills_stats_service_recording(mock_skills_dir):
    from app.services.ai.skills_stats_service import skills_stats_service

    # 模拟写入 Redis 并读取
    run(skills_stats_service.record_activations(["test-skill-1", "test-skill-2"]))

    stats = run(skills_stats_service.get_stats(days=7))
    assert "total" in stats
    assert "trend" in stats


def test_get_skills_stats_endpoint(mock_skills_dir):
    user = {"user_name": "admin", "role": "admin"}

    response = run(skills.get_skills_stats(user_info=user))
    assert "total" in response
    assert "trend" in response


def test_enforce_command_blacklist(mock_skills_dir):
    from unittest.mock import patch, AsyncMock
    from app.services.ai.runtime.agentscope.tools import _enforce_command_blacklist
    from app.core.context import AgentContext
    from agentscope.permission import PermissionBehavior

    # 模拟 Context
    mock_ctx = AgentContext(
        agent_id="test-agent",
        agent_name="test-name",
        user_id=123,
        is_admin=False
    )

    mock_perms = SimpleNamespace(
        roles=["user"],
        permissions=SimpleNamespace(
            forbidden_commands=["rm", "shutdown"]
        )
    )

    with patch("app.core.context.get_current_agent_context", return_value=mock_ctx):
        with patch("app.services.permission_service.PermissionService.get_user_permissions", new_callable=AsyncMock) as mock_get_perms:
            mock_get_perms.return_value = mock_perms

            # 测试包含 rm 的命令被拦截
            res = run(_enforce_command_blacklist("exec_command", {"command": "rm -rf /"}))
            assert res is not None
            assert res.behavior == PermissionBehavior.DENY
            assert "安全策略拦截" in res.message

            # 测试不包含敏感词的命令放行
            res2 = run(_enforce_command_blacklist("exec_command", {"command": "git status"}))
            assert res2 is None

            # 测试非 exec_command 工具直接放行
            res3 = run(_enforce_command_blacklist("read_file", {"path": "/app/data"}))
            assert res3 is None
