import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.tools.resource_catalog_tools import list_accessible_directories


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_list_accessible_directories_without_context_rejects():
    with patch("app.services.ai.tools.resource_catalog_tools.get_current_agent_context", return_value=None):
        res = await list_accessible_directories.ainvoke({})
        assert "无法识别当前用户" in res


@pytest.mark.asyncio
async def test_list_accessible_directories_normal_user_local_mode():
    mock_ctx = SimpleNamespace(
        user_id=101,
        is_admin=False,
        conversation_id="test-conv-12345",
        user_dimensions={"username": "zhangsan"},
    )

    with (
        patch("app.services.ai.tools.resource_catalog_tools.get_current_agent_context", return_value=mock_ctx),
        patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="local")),
    ):
        raw = await list_accessible_directories.ainvoke({})
        data = json.loads(raw)

        assert data["sandbox_execution_mode"] == "host_local"
        assert "deployment_environment" in data
        assert data["user_identity"]["user_id"] == 101
        assert data["user_identity"]["user_name"] == "zhangsan"
        assert data["user_identity"]["is_admin"] is False
        assert "admin_notice" not in data

        # 检查用户目录
        user_ws = data["user_workspace"]
        assert user_ws["access"] == "read_write"
        assert "backend_service_root" in user_ws
        assert "host_physical_root" not in user_ws
        subdirs = {d["directory_name"]: d for d in user_ws["subdirectories"]}
        
        assert "docs" in subdirs
        assert subdirs["docs"]["permission"] == "read_write"
        assert "backend_service_path" in subdirs["docs"]
        assert "host_physical_path" not in subdirs["docs"]
        assert "AI 产物落盘" in subdirs["docs"]["recommended_for"]

        assert "sessions/test-conv-12345" in subdirs
        assert subdirs["sessions/test-conv-12345"]["permission"] == "read_write"

        assert "uploads" in subdirs
        assert "skills" in subdirs
        assert ".trash" in subdirs

        # 检查公共目录
        pub = data["public_directories"]
        assert pub["access"] == "read_only"
        pub_dirs = {d["directory_name"]: d for d in pub["directories"]}
        assert "skills" in pub_dirs
        assert pub_dirs["skills"]["permission"] == "read_only"
        assert "branding" in pub_dirs
        assert "docs" in pub_dirs
        assert pub_dirs["docs"]["permission"] == "read_only"
        assert "platform_global_docs" == pub_dirs["docs"]["category"]
        assert data["platform_help_files"]
        assert any(item["path"].endswith("FAQ.md") for item in data["platform_help_files"])
        assert all(item["permission"] == "read_only" for item in data["platform_help_files"])
        assert all(
            item["path_semantics"] == "service_root_host_file_tool_path"
            for item in data["platform_help_files"]
        )
        assert all(item["access_via"] == ["Read", "Glob", "Grep"] for item in data["platform_help_files"])


@pytest.mark.asyncio
async def test_list_accessible_directories_docker_sandbox_mode():
    mock_ctx = SimpleNamespace(
        user_id=202,
        is_admin=False,
        conversation_id="conv:abc",
        user_dimensions={"user_name": "lisi"},
    )

    with (
        patch("app.services.ai.tools.resource_catalog_tools.get_current_agent_context", return_value=mock_ctx),
        patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="docker")),
        patch("app.utils.fs_access.get_public_runtime_help_files", return_value=["/app/FAQ.md"]),
        patch(
            "app.services.ai.runtime.agentscope.workspace._resolve_docker_public_docs_source",
            return_value="/data/host_nanzi/docs",
        ),
        patch.dict("os.environ", {"HOST_DATA_DIR": "/data/host_nanzi"}),
    ):
        raw = await list_accessible_directories.ainvoke({})
        data = json.loads(raw)

        assert data["sandbox_execution_mode"] == "docker_sandbox"
        assert data["user_workspace"]["container_sandbox_root"] == "/workspace"

        subdirs = {d["directory_name"]: d for d in data["user_workspace"]["subdirectories"]}
        assert subdirs["docs"]["container_sandbox_path"] == "/workspace/docs"
        assert subdirs["sessions/conv:abc"]["container_sandbox_path"] == "/workspace/sessions/conv_abc"
        assert subdirs["sessions/conv:abc"]["paths"]["file_tools"] == "sessions/conv_abc"
        assert subdirs["uploads"]["container_sandbox_path"] == "/workspace/uploads"
        assert subdirs["skills"]["container_sandbox_path"] is None
        assert subdirs["skills"]["paths"] == {
            "bash": None,
            "file_tools": "skills",
        }
        assert subdirs["skills"]["path_namespace"] == {
            "bash": None,
            "file_tools": "backend_service",
        }
        assert "host_physical_path" not in subdirs["docs"]
        assert subdirs["docs"]["paths"]["bash"] == "/workspace/docs"
        assert subdirs["docs"]["paths"]["file_tools"] == "docs"
        assert subdirs["docs"]["path_namespace"] == {
            "bash": "docker_sandbox",
            "file_tools": "backend_service",
        }

        public_dirs = {
            item["directory_name"]: item
            for item in data["public_directories"]["directories"]
        }
        assert public_dirs["docs"]["container_sandbox_path"] == "/workspace/public/docs"
        assert public_dirs["docs"]["paths"]["bash"] == "/workspace/public/docs"
        assert public_dirs["docs"]["paths"]["file_tools"] == "/workspace/public/docs"
        assert public_dirs["docs"]["path_namespace"] == {
            "bash": "docker_sandbox",
            "file_tools": "backend_service",
        }
        assert public_dirs["docs"]["access_via"] == ["Bash", "Read", "Glob", "Grep"]
        assert data["platform_help_files"]
        assert all(item["container_sandbox_path"] is None for item in data["platform_help_files"])
        assert all(item["backend_service_path"].startswith("/app/") for item in data["platform_help_files"])
        assert all(
            item["path_semantics"] == "service_root_host_file_tool_path"
            for item in data["platform_help_files"]
        )
        assert all(item["access_via"] == ["Read", "Glob", "Grep"] for item in data["platform_help_files"])
        assert public_dirs["branding"]["container_sandbox_path"] is None
        assert public_dirs["branding"]["path_namespace"]["bash"] is None
        assert public_dirs["branding"]["access_via"] == ["Read", "Glob", "Grep"]
        assert public_dirs["skills"]["container_sandbox_path"] == "/workspace/skills"
        assert public_dirs["skills"]["path_semantics"] == "per_user_seeded_copy"
        assert "paths.file_tools" in data["usage_guidelines"][0]


@pytest.mark.asyncio
async def test_list_accessible_directories_docker_without_public_docs_mount():
    mock_ctx = SimpleNamespace(
        user_id=203,
        is_admin=False,
        conversation_id="conv-no-docs",
        user_dimensions={"user_name": "wangwu"},
    )

    with (
        patch("app.services.ai.tools.resource_catalog_tools.get_current_agent_context", return_value=mock_ctx),
        patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="docker")),
        patch("app.services.ai.runtime.agentscope.workspace._resolve_docker_public_docs_source", return_value=None),
        patch("app.utils.fs_access.get_public_runtime_help_files", return_value=[]),
    ):
        raw = await list_accessible_directories.ainvoke({})
        data = json.loads(raw)

    public_docs = next(
        item
        for item in data["public_directories"]["directories"]
        if item["directory_name"] == "docs"
    )
    assert public_docs["container_sandbox_path"] is None
    assert public_docs["paths"] == {
        "bash": None,
        "file_tools": public_docs["backend_service_path"],
    }
    assert public_docs["access_via"] == ["Read", "Glob", "Grep"]
    assert "未挂载到 Docker Bash" in data["usage_guidelines"][4]


@pytest.mark.asyncio
async def test_list_accessible_directories_admin_user():
    mock_ctx = SimpleNamespace(
        user_id=1,
        is_admin=True,
        conversation_id=None,
        user_dimensions={"username": "admin"},
    )

    with (
        patch("app.services.ai.tools.resource_catalog_tools.get_current_agent_context", return_value=mock_ctx),
        patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="local")),
    ):
        raw = await list_accessible_directories.ainvoke({})
        data = json.loads(raw)

        assert data["user_identity"]["is_admin"] is True
        assert "admin_notice" in data
        assert "全量文件浏览器" in data["admin_notice"]


def test_public_docs_directory_fs_access_read_only(tmp_path):
    from app.utils.fs_access import is_path_allowed, is_path_writable, get_public_data_roots
    import os

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    sample_file = docs_dir / "manual.pdf"
    sample_file.write_text("Public manual content")

    user_info = {
        "user_id": 999,
        "username": "normal_user",
        "role": "user",
    }

    with patch("app.utils.fs_access.get_data_base_dir", return_value=str(tmp_path)):
        public_roots = get_public_data_roots()
        assert str(docs_dir) in public_roots

        # 普通用户可以读
        assert is_path_allowed(str(sample_file), user_info) is True
        # 普通用户不可写
        assert is_path_writable(str(sample_file), user_info) is False


def test_enhance_workspace_error_message():
    from app.services.ai.runtime.agentscope.workspace import enhance_workspace_error_message

    # 1. 找不到文件异常
    err1 = FileNotFoundError("No such file or directory: '/workspace/data/docs/FAQ.md'")
    msg1 = enhance_workspace_error_message(err1)
    assert "list_accessible_directories" in msg1
    assert "目标路径不存在、无法访问或权限受限" in msg1

    # 2. 权限受限异常
    err2 = PermissionError("Permission denied: cannot write to '/data/docs/FAQ.md'")
    msg2 = enhance_workspace_error_message(err2)
    assert "list_accessible_directories" in msg2

    # 3. 越界异常
    err3 = ValueError("path escapes Docker workspace: ../../etc/passwd")
    msg3 = enhance_workspace_error_message(err3)
    assert "list_accessible_directories" in msg3

    # 4. 已经包含 list_accessible_directories 的文本不会重复追加
    msg4 = enhance_workspace_error_message("FileNotFoundError: please call list_accessible_directories")
    assert msg4.count("list_accessible_directories") == 1

    # 5. 普通无关异常不追加
    err5 = ZeroDivisionError("division by zero")
    msg5 = enhance_workspace_error_message(err5)
    assert "list_accessible_directories" not in msg5


def test_agent_prompts_file_anti_guessing_guidelines():
    from app.services.ai.agent_prompts import AgentServicePrompts

    # 包含文件工具与 list_accessible_directories 时的 prompt 构建
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        "你是一个专业助手。",
        runtime_tool_names=[
            "Read",
            "Write",
            "list_accessible_directories",
            "directory_tree_navigator",
            "Bash",
        ],
    )

    # 验证包含防盲猜规则
    assert "文件读写与路径防盲猜规范" in prompt
    assert "严禁盲目臆造不同前缀路径反复试错" in prompt
    assert "list_accessible_directories" in prompt
    assert "平台公共目录（data/docs/、skills/、branding/）为只读（read_only）" in prompt
    assert "查找公共手册/FAQ/文档路径" in prompt
    assert "目录发现与树形导航分工" in prompt
    assert "路径、权限或 Docker/宿主机映射不确定时，**调用 list_accessible_directories**" in prompt
    assert "目标目录已经明确、只需要查看目录树时，调用 directory_tree_navigator" in prompt


def test_agent_prompts_route_platform_docs_through_host_file_tools():
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        "你是一个专业助手。",
        runtime_tool_names=["Read", "Glob", "Grep", "Bash"],
    )

    assert "检索平台公共文档" in prompt
    assert "公共文档须用宿主侧绝对路径读取" in prompt
    assert "优先通过宿主侧" in prompt
    assert "Grep`/`Glob`/`Read`" in prompt
    assert "公共文档仅宿主侧可读" in prompt
    assert "不要因为“是什么意思”等词语改走企业知识库" in prompt
    assert "platform_help_files" in prompt
    assert "不得递归扫描 `/app`" in prompt


def test_agent_prompts_directory_navigator_does_not_require_unbound_catalog_tool():
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        "你是一个专业助手。",
        runtime_tool_names=["directory_tree_navigator"],
    )

    assert "目标目录已经明确、只需要查看目录树时，调用 directory_tree_navigator" in prompt
    assert "list_accessible_directories" not in prompt
