import pytest
from app.api.v1.endpoints.sandbox import (
    DockerWorkspaceEnsureRequest,
    DockerWorkspaceExecRequest,
    ensure_docker_workspace_endpoint,
    ensure_k8s_workspace_endpoint,
    stop_docker_workspace_endpoint,
    restart_docker_workspace_endpoint,
    exec_docker_workspace_endpoint,
    get_docker_workspace_status_endpoint,
)
from app.services.ai.runtime.agentscope.workspace import DockerSandboxUnavailableError

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_stop_docker_workspace_endpoint(monkeypatch):
    captured = {}

    async def fake_stop(**kwargs):
        captured.update(kwargs)
        return {
            "status": "stopped",
            "execution_backend": "docker",
            "workspace_id": "alice__1",
            "container_id": None,
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.stop_docker_workspace_runtime",
        fake_stop,
    )

    response = await stop_docker_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="conv-123"),
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["status"] == "stopped"
    assert response.data["workspace_id"] == "alice__1"
    assert captured["conversation_id"] == "conv-123"
    assert captured["user_id"] == 1
    assert captured["user_name"] == "alice"


@pytest.mark.asyncio
async def test_restart_docker_workspace_endpoint(monkeypatch):
    captured = {}

    async def fake_restart(**kwargs):
        captured.update(kwargs)
        return {
            "status": "running",
            "execution_backend": "docker",
            "workspace_id": "alice__1",
            "container_id": "new-container-456",
            "started_at": "2026-08-24T05:40:00Z",
            "uptime_seconds": 0,
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.restart_docker_workspace_runtime",
        fake_restart,
    )

    response = await restart_docker_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="conv-123"),
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["status"] == "running"
    assert response.data["container_id"] == "new-container-456"
    assert captured["conversation_id"] == "conv-123"


@pytest.mark.asyncio
async def test_exec_docker_workspace_endpoint_success(monkeypatch):
    captured = {}

    async def fake_exec(**kwargs):
        captured.update(kwargs)
        return {
            "stdout": "total 4\n-rw-r--r-- 1 root root 12 Aug 24 05:40 test.txt\n",
            "stderr": "",
            "output": "total 4\n-rw-r--r-- 1 root root 12 Aug 24 05:40 test.txt\n",
            "exit_code": 0,
            "duration_ms": 15,
            "workdir": "/workspace",
            "container_id": "container-123",
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.exec_docker_workspace_command_runtime",
        fake_exec,
    )

    response = await exec_docker_workspace_endpoint(
        body=DockerWorkspaceExecRequest(
            conversation_id="conv-123",
            command="ls -la",
            workdir="/workspace",
        ),
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["exit_code"] == 0
    assert "test.txt" in response.data["stdout"]
    assert response.data["duration_ms"] == 15
    assert captured["command"] == "ls -la"
    assert captured["workdir"] == "/workspace"


@pytest.mark.asyncio
async def test_exec_docker_workspace_endpoint_not_running(monkeypatch):
    async def fake_exec(**kwargs):
        raise DockerSandboxUnavailableError(
            "Docker sandbox container is not running",
            reason_code="docker_container_not_running",
            user_message="Docker 容器未在运行中，请先启动容器后再进入终端。",
        )

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.exec_docker_workspace_command_runtime",
        fake_exec,
    )

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await exec_docker_workspace_endpoint(
            body=DockerWorkspaceExecRequest(
                conversation_id="conv-123",
                command="pwd",
            ),
            user_info={"id": 1, "username": "alice"},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["reason_code"] == "docker_container_not_running"


@pytest.mark.asyncio
async def test_exec_docker_workspace_command_function(monkeypatch):
    from app.services.ai.runtime.agentscope.workspace import exec_docker_workspace_command
    from unittest.mock import AsyncMock, MagicMock

    class FakeMessage:
        def __init__(self, data: bytes, stream: int = 1):
            self.data = data
            self.stream = stream

    class FakeStream:
        def __init__(self):
            self.messages = [
                FakeMessage(b"hello world\n", 1),
                FakeMessage(b"", 1),
            ]
            self.idx = 0

        async def read_out(self):
            if self.idx < len(self.messages):
                msg = self.messages[self.idx]
                self.idx += 1
                if not msg.data:
                    return None
                return msg
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    class FakeExec:
        def start(self, detach=False):
            return FakeStream()

        async def inspect(self):
            return {"ExitCode": 0}

    class FakeContainerWithMarker:
        def __init__(self):
            self.id = "cnt-123"

        async def show(self):
            return {"State": {"Running": True}, "Id": "cnt-123"}

        async def exec(self, cmd=None, **kwargs):
            if cmd and isinstance(cmd, list) and len(cmd) >= 3:
                c = cmd[2]
                if "__NANZI_PWD_" in c:
                    import re
                    m = re.search(r"(__NANZI_PWD_[a-f0-9]+__)", c)
                    if m:
                        marker = m.group(1)
                        class StreamWithDynamicMarker:
                            def __init__(self):
                                self.messages = [
                                    FakeMessage(f"进入目录\n\n{marker}:/workspace/public\n".encode("utf-8"), 1),
                                    FakeMessage(b"", 1),
                                ]
                                self.idx = 0

                            async def read_out(self):
                                if self.idx < len(self.messages):
                                    msg = self.messages[self.idx]
                                    self.idx += 1
                                    if not msg.data:
                                        return None
                                    return msg
                                return None

                            async def __aenter__(self):
                                return self

                            async def __aexit__(self, exc_type, exc_val, exc_tb):
                                return None

                        class ExecWithDynamicMarker:
                            def start(self, detach=False):
                                return StreamWithDynamicMarker()

                            async def inspect(self):
                                return {"ExitCode": 0}

                        return ExecWithDynamicMarker()
            return FakeExec()

    class FakeDocker:
        def __init__(self):
            self.containers = MagicMock()
            self.containers.get = AsyncMock(return_value=FakeContainerWithMarker())

        async def close(self):
            pass

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value="docker"),
    )
    monkeypatch.setattr(
        "aiodocker.Docker",
        FakeDocker,
    )

    res = await exec_docker_workspace_command(
        user_id=1,
        user_name="alice",
        conversation_id="conv-123",
        command="cd public",
        workdir="/workspace",
    )
    assert res["exit_code"] == 0
    assert res["workdir"] == "/workspace/public"
    assert "进入目录" in res["stdout"]
    assert "__NANZI_PWD_" not in res["stdout"]




@pytest.mark.asyncio
async def test_exec_docker_rejects_stale_skills_snapshot(monkeypatch, tmp_path):
    """★ 只读入口（终端）必须核对技能快照：集合过期 → 拒绝执行并逐出那台沙箱。

    终端此前只按标签定位容器就执行命令，管理员禁用/下架技能后仍能 `cat` 甚至执行被禁
    技能的脚本，直到空闲回收——这是唯一连"换技能集合就换沙箱"都拦不住的入口。
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.services.ai.runtime.agentscope import workspace as ws
    from app.services.ai.runtime.agentscope.workspace import exec_docker_workspace_command

    root = str(tmp_path)
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get", AsyncMock(return_value="docker")
    )
    monkeypatch.setattr(ws, "resolve_workspace_root", AsyncMock(return_value=root))
    # 该用户**当前**的技能集合（发现结果）只有 alpha；沙箱当时用的是 alpha+beta
    monkeypatch.setattr(
        ws, "discover_platform_skill_paths", lambda **kwargs: ["/public/skills/alpha"]
    )
    monkeypatch.setattr(
        "app.services.ai.skills.policy.get_forbidden_skill_ids",
        AsyncMock(return_value=set()),
    )

    class FakeContainer:
        id = "cnt-stale"

        async def show(self):
            return {"State": {"Running": True}, "Id": "cnt-stale"}

    class FakeDocker:
        def __init__(self):
            self.containers = MagicMock()
            self.containers.get = AsyncMock(return_value=FakeContainer())

        async def close(self):
            pass

    monkeypatch.setattr("aiodocker.Docker", FakeDocker)

    user_key = ws._resolve_sandbox_user_key(user_id=1, user_name="alice", user_info=None)
    workdir = ws._conversation_workdir(root, user_key, "conv-stale")
    old_layout = ws._skill_paths_cache_token(
        ["/public/skills/alpha", "/public/skills/beta"]
    )
    stale_key = f"{root}::{user_key}::{old_layout}::docker"
    monkeypatch.setitem(
        ws._workspace_sandbox_refs, f"{workdir}::all::docker", stale_key
    )
    evicted: list[str] = []

    async def fake_evict(cache_key, *, reason):
        evicted.append(cache_key)
        return 1

    monkeypatch.setattr(ws, "_evict_sandbox_cache_entry", fake_evict)

    with pytest.raises(DockerSandboxUnavailableError) as exc_info:
        await exec_docker_workspace_command(
            user_id=1,
            user_name="alice",
            conversation_id="conv-stale",
            command="cat /workspace/skills/.seed/beta/SKILL.md",
        )

    assert exc_info.value.reason_code == "docker_sandbox_skills_stale"
    assert "重新" in exc_info.value.user_message or "重置" in exc_info.value.user_message
    assert evicted == [stale_key], "过期的沙箱必须被逐出，而不是留着继续可用"


@pytest.mark.asyncio
async def test_ensure_endpoint_forwards_session_skills_scope(monkeypatch):
    """★ 预热必须按会话所属智能体的技能范围建沙箱，否则预热出来的不是这台会话的容器。

    沙箱身份 = 用户 + 技能集合令牌（缓存键 / 容器名 / 技能目录都由它派生）。预热请求体
    里只有 conversation_id，若按默认全量技能建一台，等构建时用智能体的白名单再算一次
    令牌，就会再建一台——现象是「沙箱已经 warm 起来了，工作区准备还是花了十几秒」，
    预热那台还白占资源到空闲回收。
    """
    captured = {}

    async def fake_scope(conversation_id, user_info):
        captured["scope_conversation"] = conversation_id
        return True, ["skill-alpha", "skill-beta"]

    async def fake_ensure(**kwargs):
        captured.update(kwargs)
        return {"status": "running", "execution_backend": "docker", "workspace_id": "alice__1"}

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox._resolve_session_skills_scope", fake_scope
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.ensure_docker_workspace_runtime", fake_ensure
    )

    await ensure_docker_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="conv-123"),
        user_info={"id": 1, "username": "alice"},
    )

    assert captured["scope_conversation"] == "conv-123"
    assert captured["skills_custom"] is True
    assert captured["allowed_global_skills"] == ["skill-alpha", "skill-beta"]


@pytest.mark.asyncio
async def test_ensure_endpoint_forwards_scope_for_k8s(monkeypatch):
    """K8s 的 Pod 名同样含技能集合令牌，预热口径必须与 docker 侧一致。"""
    captured = {}

    async def fake_scope(conversation_id, user_info):
        return False, []

    async def fake_ensure(**kwargs):
        captured.update(kwargs)
        return {"status": "running", "execution_backend": "k8s", "pod_name": "as-ws-alice"}

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox._resolve_session_skills_scope", fake_scope
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.ensure_k8s_workspace_runtime", fake_ensure
    )

    await ensure_k8s_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="conv-123"),
        user_info={"id": 1, "username": "alice"},
    )

    assert captured["skills_custom"] is False
    assert captured["allowed_global_skills"] == []


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, agent_id):
        self._agent_id = agent_id
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult(self._agent_id)


@pytest.mark.asyncio
async def test_resolve_session_skills_scope_uses_last_executed_agent(monkeypatch):
    """会话 → 智能体按最近一次执行记录取，再取该智能体的 skills_custom / skills。"""
    from types import SimpleNamespace

    from app.api.v1.endpoints.sandbox import _resolve_session_skills_scope

    session = _FakeSession(agent_id="agent-data")
    monkeypatch.setattr("app.core.orm.AsyncSessionLocal", lambda: session)

    seen = {}

    async def fake_config(_session, *, agent_id=None, agent_name=None):
        seen["agent_id"] = agent_id
        return SimpleNamespace(skills_custom=True, skills=["skill-alpha", " "])

    monkeypatch.setattr(
        "app.services.ai.agent_manager.AgentManagerService.get_active_agent_config",
        fake_config,
    )

    assert await _resolve_session_skills_scope("conv-123", {"id": 1}) == (
        True,
        ["skill-alpha"],
    )
    assert seen["agent_id"] == "agent-data"


@pytest.mark.asyncio
async def test_resolve_session_skills_scope_falls_back_to_main_agent(monkeypatch):
    """新会话还没有执行记录时按构建侧的默认主智能体解析，口径与路由兜底一致。"""
    from types import SimpleNamespace

    from app.api.v1.endpoints.sandbox import _resolve_session_skills_scope
    from app.services.ai.context_manager import DEFAULT_MAIN_AGENT_ID

    session = _FakeSession(agent_id=None)
    monkeypatch.setattr("app.core.orm.AsyncSessionLocal", lambda: session)

    seen = {}

    async def fake_config(_session, *, agent_id=None, agent_name=None):
        seen["agent_id"] = agent_id
        return SimpleNamespace(skills_custom=False, skills=["ignored"])

    monkeypatch.setattr(
        "app.services.ai.agent_manager.AgentManagerService.get_active_agent_config",
        fake_config,
    )

    assert await _resolve_session_skills_scope("conv-new", {"id": 1}) == (False, ["ignored"])
    assert seen["agent_id"] == DEFAULT_MAIN_AGENT_ID


@pytest.mark.asyncio
async def test_resolve_session_skills_scope_degrades_to_full_catalog(monkeypatch):
    """解析失败只能回落到「全量技能」，绝不能让预热本身失败。"""
    from app.api.v1.endpoints.sandbox import _resolve_session_skills_scope

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.core.orm.AsyncSessionLocal", boom)

    assert await _resolve_session_skills_scope("conv-123", {"id": 1}) == (False, [])
