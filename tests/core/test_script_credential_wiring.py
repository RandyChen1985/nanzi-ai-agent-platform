"""Bash 与凭据 helper 的接线测试——**用真的 LocalWorkspace 跑**。

为什么单独一个文件：`test_secret_script_channel.py` 测的是纯函数（判定、签票、
落盘），一路绿灯也说明不了「脚本真能取到值」。这一组守的是接线处的四个坑，每一个
都曾经让功能整体不可用，而且**全都不报错**：

1. `await bash_tool(command=...)` 返回的是**尚未执行**的异步生成器，清理票据放进
   `__call__` 的 `finally` 会在命令跑起来之前就删票；
2. 输出 chunk 是 ``ToolChunk(content=[TextBlock])``，正文不在 ``str`` 上；
3. 沙箱里票据要用**命令视角**的路径，宿主绝对路径在容器里不存在；
4. 同一会话并行两条 Bash 时票据文件必须按 jti 分开。

这些点没有一条能靠读代码确认——AgentScope 的返回形态必须实跑才知道。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

VALUE = "sk-wiring-test-abcdef123456"


class _FakePlatform(BaseHTTPRequestHandler):
    """假平台接口：只回一条固定凭据，并记录是否收到票据。"""

    seen_tickets: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server 的接口
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.seen_tickets.append(self.headers.get("X-Nanzi-Ticket") or "")
        raw = json.dumps(
            {"code": 0, "data": {"key": body.get("key"), "value": VALUE}}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def fake_platform():
    _FakePlatform.seen_tickets = []
    server = HTTPServer(("127.0.0.1", 0), _FakePlatform)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _chunk_text(chunks: list[object]) -> str:
    """把 ToolChunk 列表拼成文本——真实结构是 content=[TextBlock]。"""
    parts: list[str] = []
    for chunk in chunks:
        if isinstance(chunk, str):
            parts.append(chunk)
            continue
        content = getattr(chunk, "content", None)
        if isinstance(content, list):
            parts.extend(
                block.text for block in content if isinstance(getattr(block, "text", None), str)
            )
        elif isinstance(content, str):
            parts.append(content)
    return "".join(parts)


def _patch_secret_layer(monkeypatch, platform_url: str, *, consumed: bool = True) -> None:
    """把 helper 依赖的三处外部资源换掉：平台地址、消费记录、凭据解析。"""
    from app.services.secrets.policy import SensitiveValue
    from app.services.secrets.resolver import ResolveResult

    async def fake_url(*, sandboxed: bool) -> str:
        return platform_url

    async def fake_peek(jti: str) -> list[dict[str, str]]:
        return (
            [
                {
                    "key": "weather.api.key",
                    "user_id": "u1",
                    "conversation_id": "c1",
                    "dept_code": "",
                }
            ]
            if consumed
            else []
        )

    async def fake_take(jti: str) -> list[dict[str, str]]:
        return []

    async def fake_resolve(**kwargs: object) -> ResolveResult:
        return ResolveResult(
            ok=True,
            secret_id="1",
            secret_key=str(kwargs.get("secret_key") or ""),
            value=SensitiveValue(VALUE),
        )

    monkeypatch.setattr(
        "app.services.secrets.sandbox_helper.resolve_platform_url", fake_url
    )
    monkeypatch.setattr(
        "app.services.secrets.sandbox_ticket.peek_consumed_keys", fake_peek
    )
    monkeypatch.setattr(
        "app.services.secrets.sandbox_ticket.take_consumed_keys", fake_take
    )
    monkeypatch.setattr(
        "app.services.secrets.resolver.secret_resolver.resolve_by_key", fake_resolve
    )


async def _run_bash(workdir: str, command: str, **overrides: object):
    """用真的 LocalWorkspace.Bash 跑一条命令，返回 (chunks, tool)。"""
    from agentscope.workspace import LocalWorkspace

    from app.services.ai.runtime.agentscope.workspace import (
        _ScriptCredentialBashNativeTool,
    )

    workspace = LocalWorkspace(workdir=workdir)
    bash = next(t for t in await workspace.list_tools() if t.name == "Bash")
    options = {
        "workdir": workdir,
        "host_root": None,
        "sandboxed": False,
        "user_info": {"user_id": "u1", "dept_code": ""},
        "conversation_id": "c1",
    }
    options.update(overrides)
    tool = _ScriptCredentialBashNativeTool(bash, **options)
    result = await tool(command=command)
    assert hasattr(result, "__aiter__"), (
        "Bash 必须返回异步生成器；如果这里变成了别的类型，说明 AgentScope 升级后"
        "返回形态变了，清理票据与脱敏的时机都要跟着改"
    )
    return [chunk async for chunk in result]


@pytest.mark.parametrize("consumed", [True, False])
def test_bash_runs_and_redacts_real_toolchunk(tmp_path, fake_platform, monkeypatch, consumed):
    """真跑一条 bash：命令要被执行到，明文要被掩码。

    这一条同时守两个坑：async_generator（没修的话命令根本不会执行）与
    ToolChunk 结构（没修的话脱敏永远命中不了）。
    """

    def body() -> tuple[str, bool, bool]:
        _patch_secret_layer(monkeypatch, fake_platform, consumed=consumed)
        chunks = asyncio.run(
            _run_bash(str(tmp_path), "nanzi-credential get weather.api.key")
        )
        text = _chunk_text(chunks)
        return text, VALUE in text, "***" in text

    text, has_plaintext, has_mask = body()
    # 命令真的执行了：假平台收到了带票的请求
    assert _FakePlatform.seen_tickets and all(_FakePlatform.seen_tickets)
    if consumed:
        assert not has_plaintext, f"明文进了模型上下文：{text!r}"
        assert has_mask
    else:
        # 没有取用记录 = 脚本没通过 helper 取过值，就没有可掩码的值
        assert VALUE in text


def test_ticket_is_removed_after_execution(tmp_path, fake_platform, monkeypatch):
    """执行结束后票据必须消失——留在工作区等于把凭据的钥匙留在盘上。"""
    _patch_secret_layer(monkeypatch, fake_platform)
    asyncio.run(_run_bash(str(tmp_path), "nanzi-credential get weather.api.key"))
    leftover = os.listdir(tmp_path / ".nanzi")
    assert not [name for name in leftover if name.startswith("ticket.")], leftover


def test_ticket_is_removed_even_when_command_fails(tmp_path, fake_platform, monkeypatch):
    """命令失败（非零退出）也必须清理票据。"""
    _patch_secret_layer(monkeypatch, fake_platform)
    asyncio.run(_run_bash(str(tmp_path), "nanzi-credential get nope; exit 3"))
    leftover = os.listdir(tmp_path / ".nanzi")
    assert not [name for name in leftover if name.startswith("ticket.")], leftover


def test_plain_command_output_is_untouched(tmp_path, fake_platform, monkeypatch):
    """不取凭据的普通命令，输出一个字符都不该变。"""
    _patch_secret_layer(monkeypatch, fake_platform, consumed=False)
    chunks = asyncio.run(_run_bash(str(tmp_path), "echo plain-ok"))
    assert _chunk_text(chunks).strip() == "plain-ok"


def test_helper_dir_is_anchored_on_pwd(tmp_path, fake_platform, monkeypatch):
    """helper 目录必须由命令自己用 ``pwd`` 定位，而不是平台算好的绝对路径。

    平台的 workdir 是「平台侧」路径，在容器里挂到哪一层取决于部署方式（DooD 下
    还要过 HOST_DATA_DIR 映射）。推错一层，整个功能失效而现象只是
    ``command not found``——所以这里认的是「命令自己能算对」，不是「平台算得对」。
    """
    from app.services.ai.runtime.agentscope.workspace import (
        TICKET_FILE_PREFIX,
        _ScriptCredentialBashContext,
        _ScriptCredentialBashNativeTool,
    )

    class _Stub:
        name = "Bash"

    host_root = str(tmp_path / "root")
    session = os.path.join(host_root, "conv-1")
    os.makedirs(session, exist_ok=True)

    for sandboxed in (True, False):
        tool = _ScriptCredentialBashNativeTool(
            _Stub(),
            workdir=session,
            host_root=host_root,
            sandboxed=sandboxed,
            user_info={"user_id": "u1", "dept_code": ""},
            conversation_id="c1",
        )
        helper_dir = os.path.join(session, ".nanzi")
        ctx = _ScriptCredentialBashContext(
            jti="abc123",
            platform_url="http://platform:8001",
            ticket_path=os.path.join(helper_dir, f"{TICKET_FILE_PREFIX}abc123"),
            workdir=session,
        )
        decorated = tool._decorate("echo hi", helper_dir, ctx)
        assert 'NANZI_H="$(pwd)/.nanzi"' in decorated
        assert f'export PATH="$NANZI_H:$PATH"' in decorated
        assert f'"$NANZI_H/{TICKET_FILE_PREFIX}abc123"' in decorated
        # 平台侧绝对路径不该出现在命令里
        assert session not in decorated, "宿主绝对路径不该进容器执行的命令"
        assert decorated.rstrip().endswith("echo hi")


def test_parallel_bash_does_not_share_ticket_file(tmp_path, fake_platform, monkeypatch):
    """同一会话并行两条 Bash：票据文件按 jti 分开，互不覆盖。

    固定文件名时，先结束的那条会删掉后一条正在用的票——后一条取凭据就失败，
    而且失败点离原因很远。
    """
    _patch_secret_layer(monkeypatch, fake_platform)

    async def both():
        return await asyncio.gather(
            _run_bash(str(tmp_path), "sleep 0.6; nanzi-credential get slow.key"),
            _run_bash(str(tmp_path), "nanzi-credential get quick.key"),
        )

    results = asyncio.run(both())
    for chunks in results:
        text = _chunk_text(chunks)
        # 取到了值就会被掩码，所以能看到的形态是 sk-w***3456 而不是明文
        assert "***" in text, f"这条命令没取到值：{text!r}"
        assert VALUE not in text

    leftover = os.listdir(tmp_path / ".nanzi")
    assert not [name for name in leftover if name.startswith("ticket.")], leftover


def test_resolver_script_methods_are_defined_once():
    """脚本通道两个方法在类里只能有一份。

    这条不是形式主义：Python 里后定义的同名方法会静默覆盖前者，多份实现时
    「改了却没用」不会有任何报错。这个文件就是在一个这样的覆盖之后才补上的——
    当时两处修复完整、测试也绿，但真正跑的是后面那份旧代码。
    """
    import inspect

    from app.services.secrets.resolver import SecretResolver

    source = inspect.getsource(SecretResolver)
    assert source.count("async def resolve_by_key") == 1
    assert source.count("async def list_keys_for_script") == 1


def test_conversation_id_falls_back_to_agent_context(tmp_path, fake_platform, monkeypatch):
    """绑定时漏传 conversation_id（例如上游 LocalWorkspace 没挂该属性）时，
    运行时必须能从 AgentContext 兜底拿到，不能因为 no-conversation-id 直接跳过注入。
    """
    from types import SimpleNamespace

    _patch_secret_layer(monkeypatch, fake_platform)
    monkeypatch.setattr(
        "app.core.context.get_current_agent_context",
        lambda: SimpleNamespace(conversation_id="conv-from-ctx"),
    )
    chunks = asyncio.run(
        _run_bash(
            str(tmp_path),
            "nanzi-credential get weather.api.key",
            conversation_id=None,
        )
    )
    text = _chunk_text(chunks)
    assert "***" in text, f"未从 AgentContext 兜底拿到 conversation_id：{text!r}"
    assert VALUE not in text


def test_local_workspace_factories_attach_conversation_id():
    """get_local_workspace 与 build_host_only_workspace 构造 LocalWorkspace 后必须挂上 conversation_id。

    上游 LocalWorkspace 没有 conversation_id 字段，不显式挂上会导致
    bind_configured_tools_to_workspace 永远拿到 None，实机 100% command not found。
    """
    import inspect

    from app.services.ai.runtime.agentscope import workspace as ws_module

    for fn in (ws_module.get_local_workspace, ws_module.build_host_only_workspace):
        src = inspect.getsource(fn)
        assert "local_ws.conversation_id =" in src, f"{fn.__name__} 未给 local_ws 挂载 conversation_id"


def test_bash_in_subdirectory_finds_parent_nanzi_helper(tmp_path, fake_platform, monkeypatch):
    """命令在会话子目录中执行时（例如 cd subdir 或传入子目录 cwd），仍能向上找到 .nanzi/nanzi-credential。"""
    _patch_secret_layer(monkeypatch, fake_platform)
    sub = tmp_path / "skills" / "demo"
    sub.mkdir(parents=True)
    # 先切进子目录再让 prelude 跑（模拟带子目录 cwd 启动）：通过 _decorate 直接验证 shell 逻辑
    from app.services.ai.runtime.agentscope.workspace import (
        TICKET_FILE_PREFIX,
        _ScriptCredentialBashContext,
        _ScriptCredentialBashNativeTool,
    )
    from app.services.secrets.sandbox_helper import write_credential_helper

    write_credential_helper(
        tmp_path,
        ticket="v1.t.s",
        platform_url=fake_platform,
        ticket_name=f"{TICKET_FILE_PREFIX}sub123",
    )

    class _Stub:
        name = "Bash"

    tool = _ScriptCredentialBashNativeTool(
        _Stub(),
        workdir=str(tmp_path),
        host_root=None,
        sandboxed=False,
        user_info={"user_id": "u1", "dept_code": ""},
        conversation_id="c1",
    )
    ctx = _ScriptCredentialBashContext(
        jti="sub123",
        platform_url=fake_platform,
        ticket_path=str(tmp_path / ".nanzi" / f"{TICKET_FILE_PREFIX}sub123"),
        workdir=str(tmp_path),
    )
    decorated = tool._decorate("command -v nanzi-credential", str(tmp_path / ".nanzi"), ctx)
    import subprocess

    proc = subprocess.run(
        ["bash", "-c", decorated],
        cwd=str(sub),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert str(tmp_path / ".nanzi" / "nanzi-credential") in proc.stdout.strip()
