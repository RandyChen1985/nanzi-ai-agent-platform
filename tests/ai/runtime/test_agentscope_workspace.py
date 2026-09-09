import os
from unittest.mock import AsyncMock

import pytest

from app.services.ai.runtime.agentscope.workspace import (
    USER_SESSIONS_DIR_NAME,
    WORKSPACE_USER_KEY_SEP,
    USER_DOCS_DIR_NAME,
    clear_workspace_cache,
    delete_workspace_for_session,
    get_local_workspace_offloader,
    resolve_session_workdir,
    resolve_user_docs_dir,
    resolve_user_workspace_root,
    resolve_workspace_user_key,
)

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _local_policy_without_redis(monkeypatch):
    """避免真实 get_local_workspace 依赖 Redis 读取 sandbox_policy。

    这些测试标记了 no_infrastructure（不初始化 DB/Redis），而 get_local_workspace
    内部会 ConfigService.get("sandbox_policy", ...) 走 Redis。这里固定返回 local
    policy，使测试在零 Redis 环境下自足可跑，且不依赖测试执行顺序。
    """
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value="local"),
    )


def test_resolve_workspace_user_key_uses_name_and_id():
    key = resolve_workspace_user_key(user_id=1, user_name="chen.xl")
    assert key == f"chen_xl{WORKSPACE_USER_KEY_SEP}1"


def test_resolve_workspace_user_key_falls_back_to_id_only():
    key = resolve_workspace_user_key(user_id="user/1", user_name=None)
    assert key == "user_1"


@pytest.mark.asyncio
async def test_resolve_session_workdir_isolates_user_and_conversation(tmp_path, monkeypatch):
    async def _root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    path = resolve_session_workdir(
        root=str(tmp_path),
        user_id=1,
        user_name="alice",
        conversation_id="conv:abc",
    )
    assert path.startswith(str(tmp_path))
    assert f"alice{WORKSPACE_USER_KEY_SEP}1" in path
    assert USER_SESSIONS_DIR_NAME in path
    assert "conv_abc" in path


def test_resolve_user_docs_dir_is_per_user_not_per_conversation(tmp_path):
    docs_a = resolve_user_docs_dir(
        root=str(tmp_path),
        user_id=1,
        user_name="alice",
    )
    docs_b = resolve_user_docs_dir(
        root=str(tmp_path),
        user_id=1,
        user_name="alice",
    )
    assert docs_a == docs_b
    assert docs_a.endswith(os.path.join(f"alice{WORKSPACE_USER_KEY_SEP}1", USER_DOCS_DIR_NAME))


@pytest.mark.asyncio
async def test_get_local_workspace_offloader_initializes_workdir(tmp_path, monkeypatch):
    clear_workspace_cache()

    async def _root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )

    workspace = await get_local_workspace_offloader(
        user_id=1,
        user_name="bob",
        conversation_id="c1",
    )
    local_ws = workspace
    assert local_ws is not None
    assert local_ws.is_alive is True
    workdir = resolve_session_workdir(
        root=str(tmp_path),
        user_id=1,
        user_name="bob",
        conversation_id="c1",
    )
    assert os.path.isdir(workdir)
    assert os.path.isdir(os.path.join(workdir, "skills"))


def test_ssh_inline_server_is_valid_and_does_not_put_password_in_argv():
    from app.services.ai.runtime.agentscope.workspace_ssh import _SSH_INLINE_SERVER

    compile(_SSH_INLINE_SERVER, "<ssh-inline-server>", "exec")
    assert "StrictHostKeyChecking=yes" in _SSH_INLINE_SERVER
    assert "StrictHostKeyChecking=no" not in _SSH_INLINE_SERVER
    assert '["sshpass", "-d"' in _SSH_INLINE_SERVER
    assert '["sshpass", "-p"' not in _SSH_INLINE_SERVER


def test_ssh_command_uses_password_file_descriptor_and_known_hosts():
    from app.services.ai.runtime.agentscope.workspace_ssh import SshWorkspace

    workspace = SshWorkspace(
        host="remote.example.com",
        auth_type="password",
        password="do-not-leak",
    )

    args = workspace._ssh_args()
    assert "StrictHostKeyChecking=yes" in args
    assert "StrictHostKeyChecking=no" not in args
    assert "UserKnownHostsFile=/dev/null" not in args

    command = workspace._build_ssh_command(["echo", "ok"], password_fd=9)
    assert command[:3] == ["sshpass", "-d", "9"]
    assert "do-not-leak" not in command
    assert command[:2] != ["sshpass", "-p"]


def test_get_workspace_offloader_extracts_local_workspace_from_pair():
    from app.services.ai.runtime.agentscope.workspace import get_workspace_offloader

    sandbox = object()
    local = object()
    assert get_workspace_offloader((sandbox, local)) is local
    assert get_workspace_offloader(local) is local
    assert get_workspace_offloader(None) is None


@pytest.mark.asyncio
async def test_sandbox_bash_resolves_async_gateway_mcp_and_is_connected(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    class FakeClient:
        name = "sandbox"
        is_connected = True

        def __init__(self):
            self.connect_called = False

        async def connect(self):
            self.connect_called = True
            raise AssertionError("an already-connected gateway MCP must not reconnect")

        async def get_tool(self, name):
            assert name == "bash"
            return "docker-bash-tool"

    client = FakeClient()

    async def list_mcps():
        return [client]

    tool = await workspace_module._sandbox_bash_tool_from_mcps(list_mcps())

    assert tool == "docker-bash-tool"
    assert client.connect_called is False


@pytest.mark.asyncio
async def test_bind_docker_workspace_fails_closed_when_bash_mcp_missing():
    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    class FakeSandbox:
        async def list_mcps(self):
            return []

    class FakeNativeBash:
        name = "Bash"

    async def fake_call(**kwargs):
        return kwargs

    spec = RuntimeToolSpec(
        name="Bash",
        description="bash",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=fake_call,
        native_tool=FakeNativeBash(),
        permission_scope="ask",
    )

    with pytest.raises(
        workspace_module.DockerSandboxUnavailableError,
        match="Docker sandbox Bash MCP is unavailable",
    ):
        await workspace_module.bind_configured_tools_to_workspace(
            (FakeSandbox(), None),
            [spec],
        )


@pytest.mark.asyncio
async def test_bind_k8s_workspace_reports_k8s_bash_mcp_failure():
    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    class FakeSandbox:
        _platform_sandbox_policy = "k8s"

        async def list_mcps(self):
            return []

    class FakeNativeBash:
        name = "Bash"

    async def fake_call(**kwargs):
        return kwargs

    spec = RuntimeToolSpec(
        name="Bash",
        description="bash",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=fake_call,
        native_tool=FakeNativeBash(),
        permission_scope="ask",
    )

    with pytest.raises(K8sSandboxUnavailableError, match="Kubernetes sandbox Bash MCP is unavailable"):
        await workspace_module.bind_configured_tools_to_workspace(
            (FakeSandbox(), None),
            [spec],
        )


@pytest.mark.asyncio
async def test_bind_sandbox_mcp_bash_as_canonical_bash_tool_name():
    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    class FakeMcpBash:
        name = "mcp__sandbox__bash"
        description = "sandbox bash"
        input_schema = {"type": "object", "properties": {"command": {"type": "string"}}}
        is_read_only = False

        async def __call__(self, **kwargs):
            return kwargs["command"]

    class FakeClient:
        name = "sandbox"
        is_connected = True

        async def get_tool(self, name):
            assert name == "bash"
            return FakeMcpBash()

    class FakeSandbox:
        async def list_mcps(self):
            return [FakeClient()]

    async def fake_call(**kwargs):
        return kwargs

    spec = RuntimeToolSpec(
        name="Bash",
        description="bash",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=fake_call,
        native_tool=type("FakeNativeBash", (), {"name": "Bash"})(),
        permission_scope="ask",
    )

    bound = await workspace_module.bind_configured_tools_to_workspace(
        (FakeSandbox(), None),
        [spec],
    )

    assert bound[0].name == "Bash"
    assert bound[0].native_tool.name == "Bash"
    assert await bound[0].callable(command="hostname") == "hostname"

    from app.services.ai.runtime.agentscope.tools import build_toolkit

    toolkit = build_toolkit(bound)
    schemas = await toolkit.get_tool_schemas()
    visible_names = {item["function"]["name"] for item in schemas}
    assert "Bash" in visible_names
    assert "mcp__sandbox__bash" not in visible_names


@pytest.mark.asyncio
async def test_host_grep_falls_back_when_ripgrep_is_unavailable(tmp_path, monkeypatch):
    from agentscope.message import TextBlock, ToolResultState
    from agentscope.tool import ToolChunk

    from app.services.ai.runtime.agentscope.workspace import (
        _WorkspaceFileAccessNativeTool,
    )

    base = tmp_path / "data"
    docs_root = base / "docs"
    own_root = base / "agent_workspaces" / "alice__1"
    other_root = base / "agent_workspaces" / "bob__2"
    docs_root.mkdir(parents=True)
    own_root.mkdir(parents=True)
    other_root.mkdir(parents=True)
    target = own_root / "notes.txt"
    target.write_text("first line\nneedle appears here\n", encoding="utf-8")

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: str(base / "agent_workspaces"),
    )

    class MissingRipgrep:
        name = "Grep"
        description = "Grep"
        input_schema = {"type": "object"}
        is_read_only = True

        async def __call__(self, **kwargs):
            return ToolChunk(
                content=[
                    TextBlock(
                        text="ripgrep error (code 127): [Errno 2] No such file or directory: 'rg'"
                    )
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

    wrapped = _WorkspaceFileAccessNativeTool(
        MissingRipgrep(),
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        workspace_root=str(own_root),
    )

    result = await wrapped(
        pattern="needle",
        path=str(own_root),
        output_mode="content",
    )

    assert result.state == ToolResultState.SUCCESS
    assert "needle appears here" in result.content[0].text

    with pytest.raises(PermissionError, match="文件访问被拒绝"):
        await wrapped(pattern="needle", path=str(other_root), output_mode="content")


@pytest.mark.asyncio
async def test_workspace_path_denial_does_not_raise_from_read_only_fast_path(tmp_path):
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope.tools import AgentScopeNativeApprovalTool
    from app.services.ai.runtime.agentscope.workspace import (
        _WorkspaceFileAccessNativeTool,
    )

    class NativeGrep:
        name = "Grep"
        description = "Grep"
        input_schema = {"type": "object"}
        is_read_only = True

        async def __call__(self, **kwargs):
            raise AssertionError("denied workspace path must not invoke native Grep")

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    wrapped = _WorkspaceFileAccessNativeTool(
        NativeGrep(),
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        workspace_root=str(workspace_root),
    )
    approval_tool = AgentScopeNativeApprovalTool(
        wrapped,
        approval_mode="allow",
        permission_scope="read",
    )
    denied_input = {"pattern": "needle", "path": "/", "output_mode": "content"}

    assert await wrapped.check_read_only(denied_input) is False
    decision = await approval_tool.check_permissions(denied_input, None)

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.decision_reason == "workspace_path_access_denied"
    assert "文件访问被拒绝" in decision.message


@pytest.mark.asyncio
async def test_workspace_missing_file_tool_argument_reaches_tool_validation(tmp_path):
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import AgentScopeNativeApprovalTool

    class NativeRead:
        name = "Read"
        description = "Read"
        input_schema = {"type": "object"}
        is_read_only = True
        called = False

        async def __call__(self, **kwargs):
            self.called = True
            return "unexpected"

    native = NativeRead()
    wrapped = workspace_module._WorkspaceFileAccessNativeTool(
        native,
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        workspace_root=str(tmp_path / "workspace"),
    )
    approval_tool = AgentScopeNativeApprovalTool(
        wrapped,
        approval_mode="allow",
        permission_scope="read",
    )

    wrapped.check_path_access({})
    decision = await approval_tool.check_permissions({}, None)
    result = await approval_tool()

    assert decision.behavior == PermissionBehavior.ALLOW
    assert result.state.name == "ERROR"
    assert "file_path" in result.content[0].text
    assert native.called is False


@pytest.mark.asyncio
async def test_host_file_tools_scan_only_direct_root_help_markdown(tmp_path, monkeypatch):
    from agentscope.message import ToolResultState

    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.workspace import _WorkspaceFileAccessNativeTool

    service_root = tmp_path / "service"
    module_path = service_root / "app" / "utils" / "fs_access.py"
    module_path.parent.mkdir(parents=True)
    help_file = service_root / "README.md"
    nested_secret = service_root / "config" / "secret.md"
    help_file.write_text("platform help needle", encoding="utf-8")
    nested_secret.parent.mkdir()
    nested_secret.write_text("private nested needle", encoding="utf-8")

    base = tmp_path / "data"
    own_root = base / "agent_workspaces" / "alice__1"
    base.mkdir()
    own_root.mkdir(parents=True)
    monkeypatch.setattr("app.utils.fs_access.__file__", str(module_path))
    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: str(base / "agent_workspaces"),
    )

    class NativeFileTool:
        is_read_only = True
        name = "Grep"

        async def __call__(self, **kwargs):
            raise AssertionError(f"native tool must not scan the service root: {kwargs}")

    wrapped = _WorkspaceFileAccessNativeTool(
        NativeFileTool(),
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        workspace_root=str(own_root),
    )

    result = await wrapped(
        pattern="needle",
        path=str(service_root),
        glob="*.md",
        output_mode="content",
    )
    assert result.state == ToolResultState.SUCCESS
    assert str(help_file) in result.content[0].text
    assert str(nested_secret) not in result.content[0].text

    result_without_glob = await wrapped(
        pattern="needle",
        path=str(service_root),
        output_mode="content",
    )
    assert str(help_file) in result_without_glob.content[0].text
    assert str(nested_secret) not in result_without_glob.content[0].text

    with pytest.raises(PermissionError, match="禁止递归扫描服务目录"):
        await wrapped(
            pattern="needle",
            path=str(service_root),
            glob="**/*.md",
            output_mode="content",
        )

    class NativeGlobTool:
        is_read_only = True
        name = "Glob"

        async def __call__(self, **kwargs):
            raise AssertionError(f"native tool must not scan the service root: {kwargs}")

    glob_wrapped = _WorkspaceFileAccessNativeTool(
        NativeGlobTool(),
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        workspace_root=str(own_root),
    )
    glob_result = await glob_wrapped(pattern="*.md", path=str(service_root))
    assert glob_result.state == ToolResultState.SUCCESS
    assert glob_result.content[0].text == str(help_file)

    with pytest.raises(PermissionError, match="禁止递归扫描服务目录"):
        await glob_wrapped(pattern="**/*.md", path=str(service_root))

    class NativeReadTool:
        is_read_only = True
        name = "Read"

        async def __call__(self, **kwargs):
            return help_file.read_text(encoding="utf-8")

    read_wrapped = _WorkspaceFileAccessNativeTool(
        NativeReadTool(),
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        workspace_root=str(own_root),
    )
    read_result = await read_wrapped(file_path=str(help_file))
    assert read_result == "platform help needle"

    class NativeWriteTool:
        is_read_only = False
        name = "Write"

        async def __call__(self, **kwargs):
            raise AssertionError("root help files must remain read-only")

    write_wrapped = _WorkspaceFileAccessNativeTool(
        NativeWriteTool(),
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        workspace_root=str(own_root),
    )
    with pytest.raises(PermissionError, match="文件访问被拒绝"):
        await write_wrapped(file_path=str(help_file), content="overwrite")

    assert workspace_module._is_public_runtime_help_scan(
        "Grep", {"path": str(service_root), "glob": "*.md"}
    )


def test_docker_workspace_path_mapping_uses_one_logical_root_and_rejects_escape():
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    host_root = "/srv/agent_workspaces/alice__1"

    mapped = workspace_module._map_docker_workspace_tool_input(
        "Read",
        {"file_path": "/workspace/sessions/conversation-1/report.md"},
        host_root,
    )
    assert mapped["file_path"] == (
        "/srv/agent_workspaces/alice__1/sessions/conversation-1/report.md"
    )

    relative = workspace_module._map_docker_workspace_tool_input(
        "Write",
        {"file_path": "docs/report.md"},
        host_root,
    )
    assert relative["file_path"] == "/srv/agent_workspaces/alice__1/docs/report.md"

    with pytest.raises(ValueError, match="escapes Docker workspace"):
        workspace_module._map_docker_workspace_tool_input(
            "Read",
            {"file_path": "/workspace/../other-user/secret.txt"},
            host_root,
        )


def test_docker_workspace_path_mapping_prefers_child_mounts():
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    host_root = "/srv/agent_workspaces/alice__1"
    backend_docs = "/app/data/docs"

    mapped = workspace_module._map_docker_workspace_path(
        "/workspace/public/docs/FAQ.md",
        host_root,
        mount_mappings=[
            ("/workspace", host_root, "rw"),
            ("/workspace/public/docs", backend_docs, "ro"),
        ],
    )

    assert mapped == "/app/data/docs/FAQ.md"


def test_docker_workspace_path_mapping_rejects_container_only_mount():
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    with pytest.raises(ValueError, match="container-only"):
        workspace_module._map_docker_workspace_path(
            "/workspace/skills/platform/SKILL.md",
            "/srv/agent_workspaces/alice__1",
            mount_mappings=[
                ("/workspace", "/srv/agent_workspaces/alice__1", "rw"),
                ("/workspace/skills", None, "rw"),
            ],
        )


def test_docker_workspace_path_rejects_daemon_host_physical_path_with_namespace_hint():
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    with pytest.raises(ValueError, match="只接受 /workspace"):
        workspace_module._map_docker_workspace_path(
            "/root/workspace/github/yunshu-ai-agent-platform/data/agent_workspaces/alice__1/docs/report.md",
            "/srv/agent_workspaces/alice__1",
        )


def test_docker_file_tool_mount_mapping_has_public_docs_before_user_root(
    tmp_path,
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    backend_data = tmp_path / "backend-data"
    backend_docs = backend_data / "docs"
    backend_skills = backend_data / "skills"
    backend_docs.mkdir(parents=True)
    backend_skills.mkdir(parents=True)
    monkeypatch.setattr(
        "app.utils.fs_paths.get_data_base_dir",
        lambda: str(backend_data),
    )
    monkeypatch.setattr(
        "app.utils.fs_access.get_platform_skills_root",
        lambda: str(backend_skills),
    )

    mappings = workspace_module._build_docker_file_tool_mount_mappings(
        str(tmp_path / "user")
    )

    assert mappings == [
        ("/workspace/public/docs", str(backend_docs), "ro"),
        ("/workspace/skills", None, "ro"),
        ("/workspace", str(tmp_path / "user"), "rw"),
    ]


def test_docker_file_tool_mount_mapping_marks_unmounted_children_container_only(
    tmp_path,
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    backend_data = tmp_path / "backend-data"
    (backend_data / "docs").mkdir(parents=True)
    monkeypatch.setattr(
        "app.utils.fs_paths.get_data_base_dir",
        lambda: str(backend_data),
    )
    monkeypatch.setattr(
        "app.utils.fs_access.get_platform_skills_root",
        lambda: None,
    )

    mappings = workspace_module._build_docker_file_tool_mount_mappings(
        str(tmp_path / "user"),
        public_docs_mounted=False,
    )

    assert mappings == [
        ("/workspace/public/docs", None, "ro"),
        ("/workspace/skills", None, "ro"),
        ("/workspace", str(tmp_path / "user"), "rw"),
    ]


def test_docker_file_tool_mapping_allows_authorized_public_doc_symlink(tmp_path):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    backend_docs = tmp_path / "data" / "docs"
    service_root = tmp_path / "service"
    backend_docs.mkdir(parents=True)
    service_root.mkdir()
    (service_root / "FAQ.md").write_text("help", encoding="utf-8")
    (backend_docs / "FAQ.md").symlink_to(service_root / "FAQ.md")

    mapped = workspace_module._map_docker_workspace_path(
        "/workspace/public/docs/FAQ.md",
        str(tmp_path / "user"),
        mount_mappings=[
            ("/workspace/public/docs", str(backend_docs), "ro"),
            ("/workspace", str(tmp_path / "user"), "rw"),
        ],
    )

    assert mapped == str(backend_docs / "FAQ.md")


def test_docker_glob_absolute_pattern_maps_to_child_mount(tmp_path):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    mapped = workspace_module._map_docker_workspace_tool_input(
        "Glob",
        {"pattern": "/workspace/public/docs/*.md"},
        str(tmp_path / "user"),
        mount_mappings=[
            ("/workspace/public/docs", str(tmp_path / "data" / "docs"), "ro"),
            ("/workspace", str(tmp_path / "user"), "rw"),
        ],
    )

    assert mapped["path"] == str(tmp_path / "data" / "docs")
    assert mapped["pattern"] == "*.md"


def test_docker_workspace_results_keep_real_user_workspace_paths():
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    host_root = "/srv/agent_workspaces/alice__1"
    result = f"saved: {host_root}/docs/report.md"

    assert workspace_module._logicalize_docker_workspace_result(result, host_root) == result


@pytest.mark.asyncio
async def test_docker_workspace_file_tools_translate_workspace_paths_to_user_root(
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: "/srv")
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: "/srv")
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: "/srv/agent_workspaces",
    )

    calls = []

    class FakeRead:
        name = "Read"
        description = "read"
        input_schema = {"type": "object", "properties": {}}
        is_read_only = True

        async def __call__(self, **kwargs):
            calls.append(kwargs)
            return "read-ok"

    class FakeLocalWorkspace:
        workdir = "/srv/agent_workspaces/alice__1/sessions/conversation-1"
        workspace_user_root = "/srv/agent_workspaces/alice__1"

        async def list_tools(self):
            return [FakeRead()]

    class FakeDockerSandbox:
        _platform_sandbox_policy = "docker"

    async def fake_call(**kwargs):
        return kwargs

    spec = RuntimeToolSpec(
        name="Read",
        description="read",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=fake_call,
        native_tool=type("FakeNativeRead", (), {"name": "Read"})(),
        permission_scope="read",
    )

    bound = await workspace_module.bind_configured_tools_to_workspace(
        (FakeDockerSandbox(), FakeLocalWorkspace()),
        [spec],
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
    )

    assert await bound[0].callable(
        file_path="/workspace/sessions/conversation-1/report.md",
    ) == "read-ok"
    assert calls == [
        {
            "file_path": (
                "/srv/agent_workspaces/alice__1/sessions/conversation-1/report.md"
            ),
        },
    ]


@pytest.mark.asyncio
async def test_bound_docker_file_tool_maps_public_docs_to_backend_path(
    tmp_path,
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    base = tmp_path / "data"
    public_root = base / "docs"
    own_root = base / "agent_workspaces" / "alice__1"
    public_root.mkdir(parents=True)
    own_root.mkdir(parents=True)
    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: str(base / "agent_workspaces"),
    )

    calls = []

    class FakeRead:
        name = "Read"
        description = "read"
        input_schema = {"type": "object", "properties": {}}
        is_read_only = True

        async def __call__(self, **kwargs):
            calls.append(kwargs)
            return "read-ok"

    class FakeLocalWorkspace:
        workdir = str(own_root / "sessions" / "conversation-1")
        workspace_user_root = str(own_root)

        async def list_tools(self):
            return [FakeRead()]

    class FakeDockerSandbox:
        _platform_sandbox_policy = "docker"
        _platform_docker_file_tool_mount_mappings = [
            ("/workspace/public/docs", str(public_root), "ro"),
            ("/workspace", str(own_root), "rw"),
        ]

    spec = RuntimeToolSpec(
        name="Read",
        description="read",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=lambda **kwargs: kwargs,
        permission_scope="read",
        native_tool=FakeRead(),
    )

    bound = await workspace_module.bind_configured_tools_to_workspace(
        (FakeDockerSandbox(), FakeLocalWorkspace()),
        [spec],
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
    )

    assert await bound[0].callable(
        file_path="/workspace/public/docs/FAQ.md",
    ) == "read-ok"
    assert calls == [{"file_path": str(public_root / "FAQ.md")}]


@pytest.mark.asyncio
async def test_host_file_tools_enforce_public_and_private_read_boundary(tmp_path, monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    base = tmp_path / "data"
    public_root = base / "docs"
    own_root = base / "agent_workspaces" / "alice__1"
    other_root = base / "agent_workspaces" / "bob__2"
    private_root = base / "private"
    external_workspace_root = tmp_path / "legacy" / "agent_workspaces" / "alice__1"
    for directory in (
        public_root,
        own_root,
        other_root,
        private_root,
        external_workspace_root,
    ):
        directory.mkdir(parents=True)

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    for name in ("FAQ.md", "README.md"):
        os.symlink(os.path.join(project_root, name), public_root / name)
    os.symlink(
        os.path.join(project_root, "docker", "README.md"),
        public_root / "docker-readme.md",
    )

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: str(base / "agent_workspaces"),
    )

    calls: list[tuple[str, dict[str, str]]] = []

    class FakeFileTool:
        is_read_only = True

        def __init__(self, name: str):
            self.name = name
            self.description = name
            self.input_schema = {"type": "object", "properties": {}}

        async def __call__(self, **kwargs):
            calls.append((self.name, kwargs))
            return "ok"

    class FakeLocalWorkspace:
        workdir = str(own_root / "sessions" / "conversation-1")
        workspace_user_root = str(own_root)

        async def list_tools(self):
            return [FakeFileTool(name) for name in ("Read", "Glob", "Grep")]

    specs = [
        RuntimeToolSpec(
            name=name,
            description=name,
            parameters_schema={"type": "object", "properties": {}},
            source_type="system",
            callable=lambda **kwargs: kwargs,
            permission_scope="read",
            native_tool=FakeFileTool(name),
        )
        for name in ("Read", "Glob", "Grep")
    ]

    bound = await workspace_module.bind_configured_tools_to_workspace(
        (None, FakeLocalWorkspace()),
        specs,
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
    )
    by_name = {spec.name: spec for spec in bound}

    await by_name["Read"].callable(file_path=str(own_root / "own.txt"))
    await by_name["Read"].callable(file_path=str(public_root / "manual.md"))
    await by_name["Read"].callable(file_path=str(public_root / "FAQ.md"))
    await by_name["Glob"].callable(pattern="**/*", path=str(own_root))
    await by_name["Grep"].callable(pattern="secret", path=str(public_root))
    await by_name["Glob"].callable(pattern="**/*")
    await by_name["Grep"].callable(pattern="secret")

    for name, kwargs in (
        ("Read", {"file_path": str(other_root / "secret.txt")}),
        ("Glob", {"pattern": "**/*", "path": str(other_root)}),
        ("Grep", {"pattern": "secret", "path": str(private_root)}),
        ("Grep", {"pattern": "secret", "path": str(external_workspace_root)}),
        ("Read", {"file_path": str(public_root / "docker-readme.md")}),
    ):
        with pytest.raises(PermissionError, match="文件访问被拒绝"):
            await by_name[name].callable(**kwargs)

    assert [name for name, _kwargs in calls] == [
        "Read",
        "Read",
        "Read",
        "Glob",
        "Grep",
        "Glob",
        "Grep",
    ]
    assert calls[-2][1]["path"] == str(own_root)
    assert calls[-1][1]["path"] == str(own_root)


@pytest.mark.asyncio
async def test_host_file_tools_allow_writes_only_in_private_workspace(tmp_path, monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    base = tmp_path / "data"
    public_root = base / "docs"
    own_root = base / "agent_workspaces" / "alice__1"
    other_root = base / "agent_workspaces" / "bob__2"
    for directory in (public_root, own_root, other_root):
        directory.mkdir(parents=True)

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: str(base / "agent_workspaces"),
    )

    calls: list[tuple[str, dict[str, str]]] = []

    class FakeFileTool:
        is_read_only = False

        def __init__(self, name: str):
            self.name = name
            self.description = name
            self.input_schema = {"type": "object", "properties": {}}

        async def __call__(self, **kwargs):
            calls.append((self.name, kwargs))
            return "ok"

    class FakeLocalWorkspace:
        workdir = str(own_root / "sessions" / "conversation-1")
        workspace_user_root = str(own_root)

        async def list_tools(self):
            return [FakeFileTool(name) for name in ("Write", "Edit")]

    specs = [
        RuntimeToolSpec(
            name=name,
            description=name,
            parameters_schema={"type": "object", "properties": {}},
            source_type="system",
            callable=lambda **kwargs: kwargs,
            permission_scope="write",
            native_tool=FakeFileTool(name),
        )
        for name in ("Write", "Edit")
    ]
    user_info = {"user_id": 1, "user_name": "alice", "role": "user"}
    bound = await workspace_module.bind_configured_tools_to_workspace(
        (None, FakeLocalWorkspace()),
        specs,
        user_info=user_info,
    )
    by_name = {spec.name: spec for spec in bound}

    await by_name["Write"].callable(file_path=str(own_root / "new.txt"), content="ok")
    await by_name["Edit"].callable(
        file_path=str(own_root / "existing.txt"),
        old_string="old",
        new_string="new",
    )

    for name, kwargs in (
        (
            "Write",
            {"file_path": str(public_root / "manual.md"), "content": "bad"},
        ),
        (
            "Edit",
            {
                "file_path": str(other_root / "secret.txt"),
                "old_string": "old",
                "new_string": "new",
            },
        ),
    ):
        with pytest.raises(PermissionError, match="文件访问被拒绝"):
            await by_name[name].callable(**kwargs)

    assert [name for name, _kwargs in calls] == ["Write", "Edit"]


@pytest.mark.asyncio
async def test_docker_workspace_file_tools_authorize_after_logical_path_mapping(
    tmp_path,
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    base = tmp_path / "data"
    public_root = base / "docs"
    own_root = base / "agent_workspaces" / "alice__1"
    other_root = base / "agent_workspaces" / "bob__2"
    public_root.mkdir(parents=True)
    own_root.mkdir(parents=True)
    other_root.mkdir(parents=True)

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: str(base / "agent_workspaces"),
    )

    calls: list[dict[str, str]] = []

    class FakeRead:
        name = "Read"
        description = "Read"
        input_schema = {"type": "object", "properties": {}}
        is_read_only = True

        async def __call__(self, **kwargs):
            calls.append(kwargs)
            return "ok"

    class FakeLocalWorkspace:
        workdir = str(own_root / "sessions" / "conversation-1")
        workspace_user_root = str(own_root)

        async def list_tools(self):
            return [FakeRead()]

    class FakeDockerSandbox:
        _platform_sandbox_policy = "docker"

    spec = RuntimeToolSpec(
        name="Read",
        description="Read",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=lambda **kwargs: kwargs,
        permission_scope="read",
        native_tool=FakeRead(),
    )
    bound = await workspace_module.bind_configured_tools_to_workspace(
        (FakeDockerSandbox(), FakeLocalWorkspace()),
        [spec],
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
    )

    await bound[0].callable(file_path="/workspace/sessions/conversation-1/report.md")
    await bound[0].callable(file_path=str(public_root / "manual.md"))
    with pytest.raises(PermissionError, match="文件访问被拒绝"):
        await bound[0].callable(
            file_path=str(other_root / "secret.txt"),
        )

    assert calls == [
        {
            "file_path": str(own_root / "sessions" / "conversation-1" / "report.md"),
        },
        {"file_path": str(public_root / "manual.md")},
    ]


@pytest.mark.asyncio
async def test_bind_file_tools_fails_closed_without_host_workspace():
    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    spec = RuntimeToolSpec(
        name="Read",
        description="Read",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=lambda **kwargs: kwargs,
        permission_scope="read",
        native_tool=type("FakeNativeRead", (), {"name": "Read"})(),
    )

    with pytest.raises(PermissionError, match="文件访问被拒绝"):
        await workspace_module.bind_configured_tools_to_workspace(
            (None, None),
            [spec],
            user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        )


@pytest.mark.asyncio
async def test_native_approval_cannot_override_public_write_denial(tmp_path, monkeypatch):
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import AgentScopeNativeApprovalTool

    base = tmp_path / "data"
    public_root = base / "docs"
    own_root = base / "agent_workspaces" / "alice__1"
    public_root.mkdir(parents=True)
    own_root.mkdir(parents=True)

    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: str(base))
    monkeypatch.setattr("app.utils.fs_access.get_platform_skills_root", lambda: None)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: str(base / "agent_workspaces"),
    )

    class FakeWrite:
        name = "Write"
        description = "Write"
        input_schema = {"type": "object", "properties": {}}
        is_read_only = False

        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            return "written"

    native = FakeWrite()
    wrapped = workspace_module._WorkspaceFileAccessNativeTool(
        native,
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        workspace_root=str(own_root),
    )
    tool = AgentScopeNativeApprovalTool(
        wrapped,
        approval_mode="allow",
        permission_scope="write",
    )
    public_input = {
        "file_path": str(public_root / "manual.md"),
        "content": "must-not-write",
    }

    decision = await tool.check_permissions(public_input, None)
    assert decision.behavior == PermissionBehavior.DENY
    assert decision.bypass_immune is True

    with pytest.raises(PermissionError, match="文件访问被拒绝"):
        await tool(**public_input)
    assert native.calls == 0


@pytest.mark.asyncio
async def test_docker_bash_defaults_to_the_current_session_logical_directory():
    from app.services.ai.runtime.agentscope import workspace as workspace_module
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    calls = []

    class FakeMcpBash:
        name = "mcp__sandbox__bash"
        description = "sandbox bash"
        input_schema = {"type": "object", "properties": {}}
        is_read_only = False

        async def __call__(self, **kwargs):
            calls.append(kwargs)
            return "pwd-ok"

    class FakeClient:
        name = "sandbox"
        is_connected = True

        async def get_tool(self, name):
            assert name == "bash"
            return FakeMcpBash()

    class FakeDockerSandbox:
        _platform_sandbox_policy = "docker"

        async def list_mcps(self):
            return [FakeClient()]

    class FakeLocalWorkspace:
        workdir = "/srv/agent_workspaces/alice__1/sessions/conversation-1"
        workspace_user_root = "/srv/agent_workspaces/alice__1"

        async def list_tools(self):
            return []

    spec = RuntimeToolSpec(
        name="Bash",
        description="bash",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=lambda **kwargs: kwargs,
        native_tool=type("FakeNativeBash", (), {"name": "Bash"})(),
        permission_scope="ask",
    )

    bound = await workspace_module.bind_configured_tools_to_workspace(
        (FakeDockerSandbox(), FakeLocalWorkspace()),
        [spec],
    )

    assert await bound[0].callable(command="pwd") == "pwd-ok"
    assert calls == [{"command": "pwd", "cwd": "sessions/conversation-1"}]


@pytest.mark.asyncio
async def test_docker_workspace_is_reused_per_user_and_isolated_between_users(
    tmp_path,
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    workspace_module.clear_workspace_cache()

    async def fake_root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        fake_root,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.discover_platform_skill_paths",
        lambda **kwargs: [],
    )

    class FakeSandbox:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeLocalWorkspace:
        def __init__(self, **kwargs):
            self.workdir = kwargs["workdir"]

        async def initialize(self):
            return None

        async def close(self):
            return None

    created: list[FakeSandbox] = []

    async def fake_policy(_skill_paths, **kwargs):
        sandbox = FakeSandbox()
        created.append(sandbox)
        return sandbox

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._policy_docker_workspace",
        fake_policy,
    )
    monkeypatch.setattr("agentscope.workspace.LocalWorkspace", FakeLocalWorkspace)

    async def fake_config_get(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_config_get,
    )

    first = await workspace_module.get_local_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="c1",
    )
    second = await workspace_module.get_local_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="c2",
    )
    other_user = await workspace_module.get_local_workspace(
        user_id=2,
        user_name="bob",
        conversation_id="c3",
    )

    assert first is not None and second is not None and other_user is not None
    assert first[0] is second[0]
    assert first[0] is not other_user[0]
    assert len(created) == 2


@pytest.mark.asyncio
async def test_docker_workspace_closes_only_after_last_user_conversation_is_deleted(
    tmp_path,
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    workspace_module.clear_workspace_cache()

    async def fake_root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        fake_root,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.discover_platform_skill_paths",
        lambda **kwargs: [],
    )

    class FakeSandbox:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeLocalWorkspace:
        def __init__(self, **kwargs):
            self.workdir = kwargs["workdir"]

        async def initialize(self):
            return None

        async def close(self):
            return None

    sandbox = FakeSandbox()

    async def fake_policy(_skill_paths, **kwargs):
        return sandbox

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._policy_docker_workspace",
        fake_policy,
    )
    monkeypatch.setattr("agentscope.workspace.LocalWorkspace", FakeLocalWorkspace)

    async def fake_config_get(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_config_get,
    )

    await workspace_module.get_local_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="c1",
    )
    await workspace_module.get_local_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="c2",
    )

    await workspace_module.delete_workspace_for_session(1, "c1", user_name="alice")
    assert sandbox.closed is False

    await workspace_module.delete_workspace_for_session(1, "c2", user_name="alice")
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_policy_docker_uses_stable_user_workspace_id_and_isolated_mount(
    monkeypatch,
    tmp_path,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    class FakeDockerWorkspace:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.__class__.instances.append(self)

        async def initialize(self):
            return None

    async def fake_config_get(key, default=None):
        if key == "sandbox_docker_host_workdir":
            raise AssertionError("Docker must not read the configurable host workdir")
        return default

    mcp_kwargs = {}

    monkeypatch.setattr("agentscope.workspace.DockerWorkspace", FakeDockerWorkspace)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace_container_mcp.build_container_tool_mcp",
        lambda **kwargs: mcp_kwargs.update(kwargs) or object(),
    )
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_config_get,
    )

    result = await workspace_module._policy_docker_workspace(
        [],
        workspace_id="alice__1",
        sandbox_user_key="alice__1",
        workspace_root=str(tmp_path),
    )

    kwargs = result.kwargs
    assert isinstance(result, FakeDockerWorkspace)
    assert kwargs["workspace_id"] == "alice__1"
    assert kwargs["host_workdir"] == str(tmp_path / "alice__1")
    assert result._nanzi_extra_bind_mounts[0] == (
        str(tmp_path / "alice__1" / "sandbox" / "skills"),
        "/workspace/skills",
        "rw",
    )
    assert mcp_kwargs == {}


def test_docker_workspace_mount_layout_keeps_personal_skills_out_of_runtime_mount(
    tmp_path,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    host_workdir = tmp_path / "alice__1"
    public_docs_source = tmp_path / "host-data" / "docs"

    mounts = workspace_module._build_docker_workspace_mounts(
        str(host_workdir),
        str(public_docs_source),
    )

    assert workspace_module._docker_sandbox_skills_dir(str(host_workdir)) == str(
        host_workdir / "sandbox" / "skills"
    )
    assert workspace_module._personal_skills_dir(str(host_workdir)) == str(
        host_workdir / "skills"
    )
    assert mounts == [
        (str(host_workdir), "/workspace", "rw"),
        (
            str(host_workdir / "sandbox" / "skills"),
            "/workspace/skills",
            "rw",
        ),
        (str(public_docs_source), "/workspace/public/docs", "ro"),
    ]


def test_docker_public_docs_source_maps_to_host_data_dir_in_dood(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    monkeypatch.setenv("HOST_DATA_DIR", "/srv/nanzi-data")

    assert workspace_module._resolve_docker_host_data_path(
        "/app/data/docs",
    ) == "/srv/nanzi-data/docs"


@pytest.mark.asyncio
async def test_docker_workspace_adapter_adds_child_bind_mounts(tmp_path):
    from app.services.ai.runtime.agentscope.docker_workspace import (
        build_docker_workspace_with_extra_binds,
    )

    captured: dict[str, object] = {}

    class FakeContainer:
        async def start(self):
            return None

    class FakeContainers:
        async def create_or_replace(self, *, name, config):
            captured["name"] = name
            captured["config"] = config
            return FakeContainer()

    class FakeClient:
        containers = FakeContainers()

    class FakeDockerWorkspace:
        def __init__(self, **kwargs):
            self._client = FakeClient()
            self._image_tag = "python:3.11-slim"
            self.workspace_id = kwargs["workspace_id"]
            self.host_workdir = kwargs["host_workdir"]
            self.env = {}

    source = tmp_path / "sandbox" / "skills"
    source.mkdir(parents=True)
    workspace = build_docker_workspace_with_extra_binds(
        FakeDockerWorkspace,
        workspace_id="alice__1",
        host_workdir=str(tmp_path / "alice__1"),
        extra_bind_mounts=[
            (str(source), "/workspace/skills", "rw"),
            (str(tmp_path / "docs"), "/workspace/public/docs", "ro"),
        ],
    )

    await workspace._create_and_start_container()

    assert captured["name"] == "as_ws_alice__1"
    assert captured["config"]["HostConfig"]["Binds"] == [
        f"{tmp_path / 'alice__1'}:/workspace:rw",
        f"{source}:/workspace/skills:rw",
        f"{tmp_path / 'docs'}:/workspace/public/docs:ro",
    ]


def test_container_tool_mcp_uses_logical_workspace_path():
    from app.services.ai.runtime.agentscope.workspace_container_mcp import (
        K8S_GATEWAY_VENV_PYTHON,
        build_container_tool_mcp,
    )

    spec = build_container_tool_mcp().model_dump(mode="json")

    assert spec["mcp_config"]["cwd"] == "/workspace"
    assert spec["mcp_config"]["env"]["SANDBOX_WORKDIR"] == "/workspace"
    # 默认解释器保持 PATH 解析的 ``python``（Docker/E2B 网关 venv 已在 PATH 首位），
    # 避免回归破坏现有沙箱的 Bash MCP 启动。
    assert spec["mcp_config"]["command"] == "python"

    # Kubernetes 沙箱镜像的系统 python:3.11-slim 不带 mcp，必须固定使用网关
    # 虚拟环境解释器，否则沙箱网关内 Bash MCP 注册失败（HTTP 500）。
    k8s_spec = build_container_tool_mcp(
        interpreter=K8S_GATEWAY_VENV_PYTHON
    ).model_dump(mode="json")
    assert k8s_spec["mcp_config"]["command"] == K8S_GATEWAY_VENV_PYTHON
    assert k8s_spec["mcp_config"]["args"] == spec["mcp_config"]["args"]


@pytest.mark.asyncio
async def test_idle_docker_workspace_is_reaped_and_recreated_on_next_request(
    tmp_path,
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    workspace_module.clear_workspace_cache()

    async def fake_root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        fake_root,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.discover_platform_skill_paths",
        lambda **kwargs: [],
    )

    class FakeSandbox:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeLocalWorkspace:
        def __init__(self, **kwargs):
            self.workdir = kwargs["workdir"]
            self.closed = False

        async def initialize(self):
            return None

        async def close(self):
            self.closed = True

    created: list[FakeSandbox] = []

    async def fake_policy(_skill_paths, **kwargs):
        sandbox = FakeSandbox()
        created.append(sandbox)
        return sandbox

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._policy_docker_workspace",
        fake_policy,
    )
    monkeypatch.setattr("agentscope.workspace.LocalWorkspace", FakeLocalWorkspace)

    async def fake_config_get(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_config_get,
    )

    first = await workspace_module.get_local_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="c1",
    )
    assert first is not None
    cache_key = next(iter(workspace_module._docker_workspace_cache))
    workspace_module._docker_workspace_last_used[cache_key] = 0

    await workspace_module.reap_idle_docker_workspaces(
        idle_seconds=30,
        now=31,
    )

    assert created[0].closed is True
    assert workspace_module._docker_workspace_cache == {}
    assert workspace_module._workspace_cache == {}

    second = await workspace_module.get_local_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="c2",
    )
    assert second is not None
    assert second[0] is not first[0]
    assert len(created) == 2


@pytest.mark.asyncio
async def test_docker_workspace_reaper_can_start_and_stop():
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    workspace_module.clear_workspace_cache()
    task = workspace_module.start_docker_workspace_reaper(
        idle_seconds=30,
        interval_seconds=3600,
    )

    assert task.done() is False
    await workspace_module.stop_docker_workspace_reaper()
    assert task.done() is True


@pytest.mark.asyncio
async def test_ssh_close_closes_connected_mcps_and_removes_key(tmp_path):
    from app.services.ai.runtime.agentscope.workspace_ssh import SshWorkspace

    class FakeMcp:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    workspace = SshWorkspace(host="remote.example.com")
    key_path = tmp_path / "ssh-key.pem"
    key_path.write_text("private-key", encoding="utf-8")
    workspace._local_key_path = str(key_path)
    password_path = tmp_path / "ssh-password"
    password_path.write_text("secret\n", encoding="utf-8")
    workspace._local_password_path = str(password_path)
    mcp = FakeMcp()
    workspace._mcps = [mcp]

    await workspace.close()

    assert mcp.closed is True
    assert workspace._mcps == []
    assert not key_path.exists()
    assert not password_path.exists()


@pytest.mark.asyncio
async def test_ssh_connect_test_executes_remote_probe_in_worker_thread(monkeypatch):
    from subprocess import CompletedProcess

    from app.services.ai.runtime.agentscope.workspace_ssh import SshWorkspace

    workspace = SshWorkspace(host="remote.example.com", auth_type="key")
    calls = []

    def fake_run(remote_args, *, timeout):
        calls.append((remote_args, timeout))
        return CompletedProcess(["ssh"], 0, b"dsh-ssh-ok\n", b"")

    monkeypatch.setattr(workspace, "_run_remote_sync", fake_run)

    assert await workspace._connect_test() is True
    assert calls == [((["echo", "dsh-ssh-ok"]), 45)]


def test_ssh_mcp_config_passes_password_file_path_not_secret(monkeypatch, tmp_path):
    from app.services.ai.runtime.agentscope import workspace_ssh as ssh_module

    captured = {}

    class FakeMcp:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(ssh_module, "MCPClient", FakeMcp)
    password_path = tmp_path / "ssh-password"

    ssh_module.build_ssh_tool_mcp(
        host="remote.example.com",
        auth_type="password",
        password_file_path=str(password_path),
    )

    env = captured["mcp_config"].env
    assert env["SSH_PASSWORD_FILE"] == str(password_path)
    assert "SSH_PASSWORD" not in env


@pytest.mark.asyncio
async def test_docker_workspace_closes_when_initialization_fails(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    class FakeDockerWorkspace:
        def __init__(self, **kwargs):
            self.closed = False

        async def initialize(self):
            raise RuntimeError("docker init failed")

        async def close(self):
            self.closed = True

    monkeypatch.setattr("agentscope.workspace.DockerWorkspace", FakeDockerWorkspace)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace_container_mcp.build_container_tool_mcp",
        lambda: object(),
    )

    async def fake_config_get(key, default=None):
        return default

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_config_get,
    )

    with pytest.raises(RuntimeError, match="docker init failed"):
        await workspace_module._policy_docker_workspace([])


@pytest.mark.asyncio
async def test_docker_workspace_retries_transient_initialize_once(monkeypatch, tmp_path):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    attempts = 0

    class FakeDockerWorkspace:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.is_alive = False
            self._container = type("Container", (), {"id": "container-1"})()
            self.__class__.instances.append(self)

        async def initialize(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("temporary daemon connection reset")
            self.is_alive = True

        async def close(self):
            self.is_alive = False

    monkeypatch.setattr("agentscope.workspace.DockerWorkspace", FakeDockerWorkspace)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace_container_mcp.build_container_tool_mcp",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(workspace_module.asyncio, "sleep", AsyncMock())

    async def fake_config_get(key, default=None):
        return default

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_config_get,
    )

    result = await workspace_module._policy_docker_workspace(
        [],
        workspace_id="alice__1",
        sandbox_user_key="alice__1",
        workspace_root=str(tmp_path),
    )

    assert result._platform_sandbox_policy == "docker"
    assert result._platform_execution_backend == "docker"
    assert result._platform_container_id == "container-1"
    assert attempts == 2


@pytest.mark.asyncio
async def test_docker_workspace_does_not_retry_permission_failure(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    attempts = 0

    class FakeDockerWorkspace:
        def __init__(self, **kwargs):
            self.is_alive = False

        async def initialize(self):
            nonlocal attempts
            attempts += 1
            raise PermissionError("docker socket denied")

        async def close(self):
            pass

    monkeypatch.setattr("agentscope.workspace.DockerWorkspace", FakeDockerWorkspace)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace_container_mcp.build_container_tool_mcp",
        lambda **_kwargs: object(),
    )

    async def fake_config_get(key, default=None):
        return default

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_config_get,
    )

    with pytest.raises(workspace_module.DockerSandboxUnavailableError) as exc_info:
        await workspace_module._policy_docker_workspace([])

    assert attempts == 1
    assert exc_info.value.reason_code == "docker_daemon_unavailable"


@pytest.mark.asyncio
async def test_get_local_workspace_docker_failure_is_not_silently_ignored(
    tmp_path,
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    workspace_module.clear_workspace_cache()

    async def fake_root():
        return str(tmp_path)

    async def fake_config_get(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    async def fail_policy(_skill_paths, **kwargs):
        raise workspace_module.DockerSandboxUnavailableError(
            "docker daemon unavailable",
            reason_code="docker_daemon_unavailable",
            user_message="Docker 沙箱不可用，Bash 未执行。",
        )

    monkeypatch.setattr(workspace_module, "resolve_workspace_root", fake_root)
    monkeypatch.setattr(workspace_module, "discover_platform_skill_paths", lambda **kwargs: [])
    monkeypatch.setattr(workspace_module, "_policy_docker_workspace", fail_policy)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_config_get)

    with pytest.raises(workspace_module.DockerSandboxUnavailableError) as exc_info:
        await workspace_module.get_local_workspace(
            user_id=1,
            user_name="alice",
            conversation_id="conv-docker-failure",
        )

    assert exc_info.value.reason_code == "docker_daemon_unavailable"


@pytest.mark.asyncio
async def test_ensure_docker_workspace_reuses_bound_user_container(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    sandbox = type(
        "FakeSandbox",
        (),
        {"_platform_sandbox_policy": "docker"},
    )()
    get_workspace = AsyncMock(return_value=(sandbox, object()))

    async def fake_config_get(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    monkeypatch.setattr(workspace_module, "get_local_workspace", get_workspace)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_config_get)

    result = await workspace_module.ensure_docker_workspace(
        user_id=1,
        user_name="alice",
        user_info={"user_id": 1, "user_name": "alice"},
        conversation_id="c1",
    )

    assert result is sandbox
    get_workspace.assert_awaited_once()


@pytest.mark.asyncio
async def test_docker_workspace_status_inspects_existing_container_without_initializing(
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    captured = {}

    class FakeContainer:
        id = "container-1"

        async def show(self):
            return {
                "Id": self.id,
                "State": {"Running": True},
            }

    class FakeContainers:
        async def get(self, name):
            captured["name"] = name
            return FakeContainer()

    class FakeDockerClient:
        def __init__(self):
            self.containers = FakeContainers()
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeAioDocker:
        Docker = FakeDockerClient

    async def fake_config_get(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_config_get)
    monkeypatch.setitem(__import__("sys").modules, "aiodocker", FakeAioDocker)
    monkeypatch.setattr(
        workspace_module,
        "get_local_workspace",
        AsyncMock(side_effect=AssertionError("status must not initialize workspace")),
    )

    result = await workspace_module.docker_workspace_status(
        user_id=1,
        user_name="alice",
        user_info={"user_id": 1, "user_name": "alice"},
        conversation_id="c1",
    )

    assert result == {
        "status": "running",
        "execution_backend": "docker",
        "workspace_id": "alice__1",
        "container_id": "container-1",
        "started_at": None,
        "uptime_seconds": None,
    }
    assert captured["name"] == "as_ws_alice__1"


@pytest.mark.asyncio
async def test_ensure_docker_workspace_rejects_effective_local_without_initializing(
    monkeypatch,
):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    get_workspace = AsyncMock()

    async def fake_config_get(key, default=None):
        return "local" if key == "sandbox_policy" else default

    monkeypatch.setattr(workspace_module, "get_local_workspace", get_workspace)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_config_get)

    with pytest.raises(workspace_module.DockerSandboxUnavailableError) as exc_info:
        await workspace_module.ensure_docker_workspace(
            user_id=1,
            user_name="alice",
            conversation_id="c1",
        )

    assert exc_info.value.reason_code == "docker_policy_not_effective"
    get_workspace.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_local_workspace_docker_requires_conversation_id(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    async def fake_config_get(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_config_get)

    with pytest.raises(workspace_module.DockerSandboxUnavailableError) as exc_info:
        await workspace_module.get_local_workspace(
            user_id=1,
            user_name="alice",
            conversation_id=None,
        )

    assert exc_info.value.reason_code == "docker_workspace_start_failed"


@pytest.mark.asyncio
async def test_get_local_workspace_closes_sandbox_when_local_init_fails(tmp_path, monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    workspace_module.clear_workspace_cache()

    async def fake_root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        fake_root,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.discover_platform_skill_paths",
        lambda **kwargs: [],
    )

    class FakeSandbox:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    sandbox = FakeSandbox()

    async def fake_policy(_skill_paths, **kwargs):
        return sandbox

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._policy_docker_workspace",
        fake_policy,
    )

    class FailingLocalWorkspace:
        def __init__(self, **kwargs):
            pass

        async def initialize(self):
            raise RuntimeError("local init failed")

    monkeypatch.setattr("agentscope.workspace.LocalWorkspace", FailingLocalWorkspace)

    async def fake_config_get(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_config_get,
    )

    with pytest.raises(workspace_module.DockerSandboxUnavailableError) as exc_info:
        await workspace_module.get_local_workspace(
            user_id=1,
            user_name="alice",
            conversation_id="conv-cleanup",
        )

    assert exc_info.value.reason_code == "docker_workspace_start_failed"
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_delete_workspace_for_session_closes_cached_sandbox(tmp_path, monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    workspace_module.clear_workspace_cache()

    async def fake_root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        fake_root,
    )

    class FakeSandbox:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    sandbox = FakeSandbox()
    workdir = workspace_module.resolve_session_workdir(
        root=str(tmp_path),
        user_id=2,
        user_name="bob",
        conversation_id="conv-delete",
    )
    os.makedirs(workdir, exist_ok=True)
    workspace_module._workspace_cache[f"{workdir}::all::docker"] = (sandbox, object())

    await workspace_module.delete_workspace_for_session(
        2,
        "conv-delete",
        user_name="bob",
    )

    assert sandbox.closed is True
    assert not os.path.exists(workdir)


@pytest.mark.asyncio
async def test_delete_workspace_for_session_removes_files(tmp_path, monkeypatch):
    clear_workspace_cache()

    async def _root():
        return str(tmp_path)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    workspace = await get_local_workspace_offloader(
        user_id=2,
        user_name="carol",
        conversation_id="c2",
    )
    assert workspace is not None
    workdir = resolve_session_workdir(
        root=str(tmp_path),
        user_id=2,
        user_name="carol",
        conversation_id="c2",
    )
    marker = os.path.join(workdir, "marker.txt")
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("x")

    await delete_workspace_for_session(2, "c2", user_name="carol")
    assert not os.path.exists(workdir)


def test_resolve_user_workspace_root_returns_existing_directory(tmp_path):
    root = str(tmp_path)
    user_root = os.path.join(root, resolve_workspace_user_key(user_id=4, user_name="frank"))
    os.makedirs(user_root, exist_ok=True)

    resolved = resolve_user_workspace_root(root=root, user_id=4, user_name="frank")
    assert resolved is not None
    assert os.path.abspath(resolved) == os.path.abspath(user_root)


def test_resolve_user_workspace_root_missing_directory(tmp_path):
    resolved = resolve_user_workspace_root(
        root=str(tmp_path),
        user_id=4,
        user_name="frank",
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_ssh_private_key_legacy_auth_value_is_normalized(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    config_values = {
        "sandbox_ssh_auth_type": "private_key",
        "sandbox_ssh_password": "",
        "sandbox_ssh_private_key": "PRIVATE KEY",
        "sandbox_ssh_remote_workdir": "/workspace",
        "sandbox_ssh_host": "remote.example.com",
        "sandbox_ssh_port": "22",
        "sandbox_ssh_user": "runner",
    }

    async def fake_config_get(key, default=None):
        return config_values.get(key, default)

    class FakeWorkspace:
        instances = []

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self._local_key_path = None
            self.default_mcps = []
            self.is_alive = False
            self.__class__.instances.append(self)

        def _materialize_key(self):
            self._local_key_path = "/tmp/normalized-key.pem"

        def _materialize_password(self):
            self._local_password_path = None

        async def initialize(self):
            self.is_alive = True

    captured_mcp_kwargs = {}

    def fake_build_ssh_tool_mcp(**kwargs):
        captured_mcp_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_config_get,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace_ssh.SshWorkspace",
        FakeWorkspace,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace_ssh.build_ssh_tool_mcp",
        fake_build_ssh_tool_mcp,
    )

    await workspace_module._policy_ssh_workspace(skill_paths=[])

    assert FakeWorkspace.instances[0].auth_type == "key"
    assert FakeWorkspace.instances[0].private_key == "PRIVATE KEY"
    assert captured_mcp_kwargs["auth_type"] == "key"


@pytest.mark.asyncio
async def test_e2b_workspace_uses_page_overrides_and_falls_back_for_masked_key(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    async def fake_config_get(key, default=None):
        return {"sandbox_e2b_api_key": "saved-e2b-key"}.get(key, default)

    class FakeE2BWorkspace:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.__class__.instances.append(self)

        async def initialize(self):
            return None

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        fake_config_get,
    )
    monkeypatch.setattr("agentscope.workspace.E2BWorkspace", FakeE2BWorkspace)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace_container_mcp.build_container_tool_mcp",
        lambda: object(),
    )

    await workspace_module._policy_e2b_workspace(
        skill_paths=[],
        config_overrides={
            "sandbox_e2b_api_key": "sav****-key",
            "sandbox_e2b_template": "custom-template",
            "sandbox_e2b_timeout_seconds": "45",
        },
    )

    kwargs = FakeE2BWorkspace.instances[0].kwargs
    assert kwargs["api_key"] == "saved-e2b-key"
    assert kwargs["template"] == "custom-template"
    assert kwargs["timeout_seconds"] == 45
