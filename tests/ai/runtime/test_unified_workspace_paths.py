"""沙箱与宿主机统一工作区路径（/workspace）契约与回归测试。

覆盖 4 层治理中的后端 3 层：
1. 宿主机系统工具（validate_safe_path）支持 /workspace/... 并严防越界与公共文档写入；
2. 沙箱 Bash 包装层自动改写当前用户宿主前缀，并对未挂载 /app/data/... 报错追加精准提示；
3. 沙箱提示词补齐 uploads/ 映射，且进 LLM 前按 sandbox_policy 规范化 HumanMessage 附件路径。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
# 必须用平台自带的兼容消息类：langchain_core 已随 AgentScope 迁移移除
# （见 app/services/ai/browser/captcha_solver.py），按 requirements.txt 安装的环境里没有它，
# 直接 import langchain_core 会让 `pytest tests/` 在 collection 阶段整批中断。
from app.services.ai.runtime.agentscope.compat import AIMessage, HumanMessage

pytestmark = pytest.mark.no_infrastructure


# ── 第 1 层：validate_safe_path 支持 /workspace/... ────────────────────────


def test_validate_safe_path_maps_logical_workspace_for_current_user(tmp_path, monkeypatch):
    from app.services.ai.tools import system_executive_tools as tools_mod

    user_ws = tmp_path / "data" / "agent_workspaces" / "ddd_15"
    target = user_ws / "uploads" / "测试文档_d1d4.docx"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"docx")

    monkeypatch.setattr(
        tools_mod,
        "_resolve_current_user_workspace_root_sync",
        lambda: str(user_ws),
    )

    import os

    resolved = tools_mod.validate_safe_path("/workspace/uploads/测试文档_d1d4.docx")
    assert os.path.realpath(resolved) == str(target.resolve())


def test_validate_safe_path_rejects_workspace_traversal(tmp_path, monkeypatch):
    from app.services.ai.tools import system_executive_tools as tools_mod

    user_ws = tmp_path / "data" / "agent_workspaces" / "ddd_15"
    user_ws.mkdir(parents=True)

    monkeypatch.setattr(
        tools_mod,
        "_resolve_current_user_workspace_root_sync",
        lambda: str(user_ws),
    )

    with pytest.raises(ValueError, match="路径越界"):
        tools_mod.validate_safe_path("/workspace/../other_99/uploads/secret.txt")


@pytest.mark.asyncio
async def test_validate_safe_path_and_write_file_reject_public_docs_write(tmp_path, monkeypatch):
    from app.services.ai.tools import system_executive_tools as tools_mod

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    monkeypatch.setattr("app.utils.fs_paths.get_data_base_dir", lambda: str(tmp_path))

    assert tools_mod.validate_safe_path("/workspace/public/docs/FAQ.md").endswith(
        "docs/FAQ.md"
    )
    assert tools_mod.validate_safe_path("/workspace//public/./docs/FAQ.md").endswith(
        "docs/FAQ.md"
    )
    for variant in (
        "/workspace/public/docs/FAQ.md",
        "/workspace//public/docs/FAQ.md",
        "/workspace/uploads/../public/docs/FAQ.md",
    ):
        write_res = await tools_mod.write_file.ainvoke({"path": variant, "content": "hack"})
        assert "只读公共文档目录" in write_res


def test_validate_safe_path_rejects_symlink_escape_under_workspace(tmp_path, monkeypatch):
    from app.services.ai.tools import system_executive_tools as tools_mod

    user_ws = tmp_path / "data" / "agent_workspaces" / "ddd_15"
    uploads = user_ws / "uploads"
    uploads.mkdir(parents=True)
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    symlink_file = uploads / "evil.txt"
    symlink_file.symlink_to(outside)

    monkeypatch.setattr(
        tools_mod,
        "_resolve_current_user_workspace_root_sync",
        lambda: str(user_ws),
    )

    with pytest.raises(ValueError, match="路径越界"):
        tools_mod.validate_safe_path("/workspace/uploads/evil.txt")


# ── 第 2 层：沙箱 Bash 宿主工作区前缀自动改写 + 错误自愈 ───────────────────


def test_rewrite_host_workspace_paths_to_sandbox_only_targets_current_user():
    from app.services.ai.runtime.agentscope.workspace import (
        rewrite_host_workspace_paths_to_sandbox,
    )

    host_root = "/app/data/agent_workspaces/ddd_15"
    cmd = (
        "python3 -c \"import docx; "
        "d=docx.Document('/app/data/agent_workspaces/ddd_15/uploads/这个是一片测试文档_d1d4.docx')\""
    )
    rewritten, changed = rewrite_host_workspace_paths_to_sandbox(cmd, host_root=host_root)
    assert changed is True
    assert "/workspace/uploads/这个是一片测试文档_d1d4.docx" in rewritten
    assert "/app/data/agent_workspaces/ddd_15" not in rewritten


def test_rewrite_host_workspace_paths_respects_user_boundary():
    from app.services.ai.runtime.agentscope.workspace import (
        rewrite_host_workspace_paths_to_sandbox,
    )

    # ddd_1 绝不能误改 ddd_15，也绝不能改写公共 /app/data/docs
    cmd = (
        "ls /app/data/agent_workspaces/ddd_15/uploads/a.docx "
        "/app/data/docs/FAQ.md"
    )
    rewritten, changed = rewrite_host_workspace_paths_to_sandbox(
        cmd,
        host_root="/app/data/agent_workspaces/ddd_1",
    )
    assert changed is False
    assert rewritten == cmd


@pytest.mark.asyncio
async def test_sandbox_session_bash_rewrites_command_cwd_and_augments_stream():
    from app.services.ai.runtime.agentscope.workspace import _SandboxSessionBashNativeTool

    captured: dict = {}

    async def _fake_stream(text: str):
        yield SimpleNamespace(content=[{"type": "text", "text": text}])

    class _FakeBash:
        name = "bash"

        async def __call__(self, **kwargs):
            captured.update(kwargs)
            if "/app/data/docs" in kwargs.get("command", ""):
                return _fake_stream("ls: cannot access '/app/data/docs/FAQ.md': No such file or directory")
            return _fake_stream("ok")

    wrapper = _SandboxSessionBashNativeTool(
        _FakeBash(),
        host_root="/app/data/agent_workspaces/ddd_15",
        session_workdir="/app/data/agent_workspaces/ddd_15/sessions/conv-1",
    )

    # 1) 本人宿主路径自动改写（command 与 cwd）
    stream = await wrapper(
        command="cat '/app/data/agent_workspaces/ddd_15/uploads/测试.docx'",
        cwd="/app/data/agent_workspaces/ddd_15/uploads",
    )
    chunks = [chunk async for chunk in stream]
    assert captured["command"] == "cat '/workspace/uploads/测试.docx'"
    assert captured["cwd"] == "uploads"
    assert chunks[0].content[0]["text"] == "ok"

    # 2) 未挂载的 /app/data/docs 报错自动追加精准提示
    err_stream = await wrapper(command="ls /app/data/docs/FAQ.md")
    err_chunks = [chunk async for chunk in err_stream]
    assert "[沙箱路径提示]" in err_chunks[0].content[0]["text"]
    assert "/workspace/uploads/..." in err_chunks[0].content[0]["text"]


# ── 第 3 层：uploads 提示词 + 进 LLM 前规范化 HumanMessage ──────────────────


def test_workspace_prompt_sandbox_includes_uploads_and_unified_path_rule():
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/app/data/agent_workspaces/ddd_15/sessions/conv-1",
        docs_dir="/app/data/agent_workspaces/ddd_15/docs",
        uploads_dir="/app/data/agent_workspaces/ddd_15/uploads",
        file_tool_names=["Bash", "Read", "Write"],
        logical_workspace_root="/workspace",
        logical_session_workdir="/workspace/sessions/conv-1",
        logical_docs_dir="/workspace/docs",
        logical_uploads_dir="/workspace/uploads",
    )
    assert "统一工作区路径优先（沙箱模式）" in prompt
    assert "/workspace/uploads" in prompt
    assert "用户上传附件目录" in prompt


@pytest.mark.asyncio
async def test_normalize_runtime_messages_rewrites_human_attachment_in_docker(tmp_path, monkeypatch):
    """``resolve_user_workspace_root`` 要求用户工作区真实存在，不能写死 /app/data。"""
    from app.services.ai.runtime.agentscope import workspace as ws_mod
    from app.services.config_service import ConfigService

    ws_root = tmp_path / "agent_workspaces"
    user_dir = ws_root / "ddd__15"
    (user_dir / "uploads").mkdir(parents=True)
    attachment_host = user_dir / "uploads" / "这个是一片测试文档_d1d4.docx"
    attachment_host.write_bytes(b"docx")

    async def _get_docker(key, default=None):
        if key == "sandbox_policy":
            return "docker"
        return default

    async def _ws_root():
        return str(ws_root)

    monkeypatch.setattr(ConfigService, "get", _get_docker)
    monkeypatch.setattr(ws_mod, "resolve_workspace_root", _ws_root)

    human = HumanMessage(
        content=(
            "请润色\n\n---\n\n"
            "用户本轮已上传文件附件：这个是一片测试文档.docx，"
            f"其安全托管后的服务器绝对路径是：{attachment_host}。"
        )
    )
    ai = AIMessage(content=f"好的，原文路径 {user_dir}/uploads/x.docx 不改动")

    result = await ws_mod.normalize_runtime_messages_workspace_paths_for_sandbox(
        [human, ai],
        user_id=15,
        user_name="ddd",
    )
    assert "/workspace/uploads/这个是一片测试文档_d1d4.docx" in result[0].content
    assert "工作区统一路径是（Bash 与文件/文档工具通用）" in result[0].content
    assert str(user_dir) not in result[0].content
    # AI 消息原样透传
    assert result[1].content == ai.content


@pytest.mark.asyncio
async def test_normalize_runtime_messages_keeps_host_path_in_local(monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as ws_mod
    from app.services.config_service import ConfigService

    async def _get_local(key, default=None):
        if key == "sandbox_policy":
            return "local"
        return default

    monkeypatch.setattr(ConfigService, "get", _get_local)

    raw = (
        "请润色\n\n---\n\n"
        "用户本轮已上传文件附件：a.docx，其安全托管后的服务器绝对路径是：/app/data/agent_workspaces/ddd__15/uploads/a.docx。"
    )
    human = HumanMessage(content=raw)
    result = await ws_mod.normalize_runtime_messages_workspace_paths_for_sandbox(
        [human],
        user_id=15,
        user_name="ddd",
    )
    assert result[0].content == raw


# ── 第 5 批补齐：目录清单 / session_status / 改图 / 浏览器上传 / 目录树导航 ──


@pytest.mark.asyncio
async def test_list_accessible_directories_aligns_unified_workspace_guideline():
    import json
    from unittest.mock import AsyncMock, patch

    from app.services.ai.tools.resource_catalog_tools import list_accessible_directories

    mock_ctx = SimpleNamespace(
        user_id=15,
        is_admin=False,
        conversation_id="conv-1",
        user_dimensions={"user_name": "ddd"},
    )
    with (
        patch("app.services.ai.tools.resource_catalog_tools.get_current_agent_context", return_value=mock_ctx),
        patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="docker")),
        patch("app.utils.fs_access.get_public_runtime_help_files", return_value=[]),
        patch("app.services.ai.runtime.agentscope.workspace._resolve_docker_public_docs_source", return_value=None),
    ):
        data = json.loads(await list_accessible_directories.ainvoke({}))

    g0 = data["usage_guidelines"][0]
    assert "paths.file_tools" in g0
    assert "已全工具打通" in g0
    assert "不能把 Bash 的容器路径直接当作文件工具路径" not in g0
    uploads_entry = next(
        item for item in data["user_workspace"]["subdirectories"] if item["directory_name"] == "uploads"
    )
    assert "/workspace/uploads/..." in uploads_entry["description"]


@pytest.mark.asyncio
async def test_session_status_workspace_summary_exposes_sandbox_logical_root(tmp_path, monkeypatch):
    from app.services.ai.tools import session_status as ss_mod
    from app.services.config_service import ConfigService

    async def _get_docker(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    async def _ws_root():
        return str(tmp_path)

    monkeypatch.setattr(ConfigService, "get", _get_docker)
    monkeypatch.setattr(ss_mod, "resolve_workspace_root", _ws_root)

    ctx = SimpleNamespace(
        user_id=15,
        conversation_id="conv-1",
        user_dimensions={"user_name": "ddd"},
    )
    summary, _ = await ss_mod._workspace_summary(ctx)
    assert summary["sandbox_policy"] == "docker"
    assert summary["sandbox_logical_root"] == "/workspace"


@pytest.mark.asyncio
async def test_edit_image_source_loader_supports_logical_workspace_path(tmp_path, monkeypatch):
    from app.services.ai.tools import image_generation_tool as img_mod

    ws_root = tmp_path / "agent_workspaces"
    png_path = ws_root / "ddd__15" / "uploads" / "sample.png"
    png_path.parent.mkdir(parents=True)
    # 最小合法 PNG 头（供 sniff_image_mime 识别）
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    png_path.write_bytes(png_bytes)

    other_png = ws_root / "other__99" / "uploads" / "secret.png"
    other_png.parent.mkdir(parents=True)
    other_png.write_bytes(png_bytes)

    async def _ws_root(ensure_exists: bool = False):
        return str(ws_root)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _ws_root,
    )
    ctx = SimpleNamespace(user_id=15, user_dimensions={"user_name": "ddd"})

    mime, data = await img_mod._read_source_image("/workspace/uploads/sample.png", context=ctx)
    assert mime == "image/png"
    assert data == png_bytes

    with pytest.raises(ValueError, match="只能修改当前用户自己工作区里的图片"):
        await img_mod._read_source_image("/workspace/../other__99/uploads/secret.png", context=ctx)


def test_browser_upload_path_supports_logical_workspace_and_rejects_escape(tmp_path, monkeypatch):
    from app.services.ai.tools import browser_tools

    user_ws = tmp_path / "agent_workspaces" / "ddd__15"
    target = user_ws / "uploads" / "doc.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"%PDF-1.4")

    other = tmp_path / "agent_workspaces" / "other__2" / "uploads" / "secret.pdf"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"%PDF-1.4")

    gen_root = tmp_path / "generated_files"
    gen_root.mkdir(parents=True)

    monkeypatch.setattr("app.utils.fs_access.get_user_private_workspace_root", lambda _: str(user_ws))
    monkeypatch.setattr("app.services.ai.tools.generated_file_service.generated_files_root", lambda: gen_root)

    ctx = SimpleNamespace(user_id=15, is_admin=False, user_dimensions={"user_name": "ddd"})
    assert browser_tools._safe_browser_file_path(ctx, "/workspace/uploads/doc.pdf") == target.resolve()
    assert browser_tools._safe_browser_file_path(ctx, "uploads/doc.pdf") == target.resolve()

    with pytest.raises(ValueError, match="浏览器文件目录"):
        browser_tools._safe_browser_file_path(ctx, "/workspace/../other__2/uploads/secret.pdf")


def test_directory_tree_navigator_supports_logical_workspace_path(tmp_path, monkeypatch):
    from app.services.ai.tools import advanced_auxiliary_tools as aux_mod

    ws_root = tmp_path / "agent_workspaces"
    user_ws = ws_root / "ddd__15"
    uploads = user_ws / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "测试文档.docx").write_bytes(b"docx")

    other_ws = ws_root / "other__2" / "uploads"
    other_ws.mkdir(parents=True)
    (other_ws / "secret.docx").write_bytes(b"secret")

    ctx = SimpleNamespace(user_id=15, is_admin=False, user_dimensions={"user_name": "ddd"})
    monkeypatch.setattr(aux_mod, "get_current_agent_context", lambda: ctx)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.default_workspace_root",
        lambda: str(ws_root),
    )
    # 归属判定的 normalize_fs_path 会把 `.../agent_workspaces/...` 重映射到 data 根，
    # tmp 目录不跟着走就会被误判成「别人的工作区」；data 根一并指到 tmp。
    monkeypatch.setattr("app.utils.fs_access.get_data_base_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "app.utils.fs_access.get_user_private_workspace_root",
        lambda _: str(user_ws),
    )

    out = aux_mod.directory_tree_navigator.invoke({"path": "/workspace/uploads"})
    assert "测试文档.docx" in out

    blocked = aux_mod.directory_tree_navigator.invoke({"path": "/workspace/../other__2/uploads"})
    assert "安全拦截" in blocked

