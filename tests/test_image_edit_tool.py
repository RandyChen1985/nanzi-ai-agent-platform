"""edit_image 工具（图生图）的行为测试。

不依赖真实模型服务：配置、HTTP、产物发布、工作区路径全部换成假实现，
请求体组装、路径归属校验、尺寸回落、错误回传这条链本身仍然真跑。

这里刻意不复用 ``test_image_generation_tool.py`` 的 fixture：那边以「文生图」为语境，
断言集中在 ``n`` 与尺寸白名单；改图多出来的是「读本地文件 + base64 进 messages」，
路径安全是重点，单独一套更好读。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services.ai.tools import image_generation_tool as tools

pytestmark = pytest.mark.no_infrastructure

# 120x80 的真 PNG 头 + 填充：嗅探要认出 image/png，体积也要过得了上限检查。
_PNG_HEAD = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAHgAAABQCAYAAADwscKqAAAAAXNSR0IArs4c6QAAAA1JREFU"
    "eNrtwTEBAAAAwqD1T20ND6AAAAAAAAAAAAAAAAAAAAB4N2ZAAAF/8VXVAAAAAElFTkSuQmCC"
)
_PNG_B64 = base64.b64encode(_PNG_HEAD).decode()

BASE_CONFIG = {
    "image_generation_enabled": "1",
    # 改图只在这条协议下可用。
    "image_api_format": "openai_chat",
    "image_api_base_url": "https://api.example.com/v1",
    "image_api_key": "sk-test",
    "image_model": "test-image-model",
    "image_default_size": "1024x1024",
    "image_allowed_sizes": "1024x1024,1664x928,928x1664",
    "image_timeout_seconds": "30",
    "image_max_images_per_call": "1",
    "image_prompt_prefix": "",
}


def _config(overrides: dict | None = None) -> dict:
    merged = dict(BASE_CONFIG)
    merged.update(overrides or {})
    return merged


@pytest.fixture
def patch_config(monkeypatch):
    def _apply(overrides: dict | None = None):
        async def _get_many(defaults):
            merged = _config(overrides)
            return {key: merged.get(key, default) for key, default in defaults.items()}

        monkeypatch.setattr(tools.ConfigService, "get_many", staticmethod(_get_many))

    _apply()
    return _apply


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """把工作区根指到 tmp_path，并造出用户子目录与一张图。"""
    root = tmp_path / "agent_workspaces"
    user_dir = root / "tester__7"
    (user_dir / "uploads").mkdir(parents=True)
    (user_dir / "generated").mkdir(parents=True)

    async def _root(*, ensure_exists: bool = True):
        return str(root)

    def _user_key(*, user_id, user_name=None):
        return "tester__7"

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root", _root
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_user_key",
        _user_key,
    )
    return SimpleNamespace(root=root, user_dir=user_dir)


@pytest.fixture
def patch_context(monkeypatch):
    def _get_context():
        return SimpleNamespace(
            user_id=7,
            conversation_id="conv-1",
            user_dimensions={"user_name": "tester"},
        )

    monkeypatch.setattr(tools, "get_current_agent_context", _get_context)
    monkeypatch.setattr(
        "app.core.context.get_current_agent_context", _get_context, raising=False
    )


@pytest.fixture
def captured_artifacts(monkeypatch):
    recorded: list[dict] = []

    async def _fake_publish(source_path, filename, **kwargs):
        recorded.append({"source_path": source_path, "filename": filename, **kwargs})
        return SimpleNamespace(
            artifact_id="b" * 32,
            token="tok",
            filename=filename,
            mime_type="image/png",
            size=len(_PNG_HEAD),
            download_url=f"https://host/api/v1/chat/generated-files/{'b' * 32}?token=tok",
        )

    monkeypatch.setattr(tools, "publish", _fake_publish)
    return recorded


def _chat_response(*, model: str = "upstream-name") -> dict:
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{_PNG_B64}"},
                        }
                    ],
                }
            }
        ],
    }


def _patch_http(monkeypatch, handler, *, captured: list | None = None):
    """把 httpx 换成 MockTransport，并把请求体记到 captured。"""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(tools.httpx, "AsyncClient", factory)
    return captured


async def _call_edit(**kwargs):
    return await tools.edit_image.ainvoke(kwargs)


def _make_source(workspace, name: str = "coffee.png", data: bytes = _PNG_HEAD) -> Path:
    path = workspace.user_dir / "uploads" / name
    path.write_bytes(data)
    return path


# ══════════════════ 注册与门禁 ══════════════════


def test_edit_image_is_registered_and_permission_auto_approved():
    from app.services.ai.runtime.agentscope.tools import AUTO_APPROVED_TOOL_NAMES
    from app.services.ai.tools.registry import ToolRegistry

    assert "edit_image" in ToolRegistry._registry
    # 改图和生图一样：用户说「把背景换掉」就是这次写入的授权，不再弹审批。
    assert "edit_image" in AUTO_APPROVED_TOOL_NAMES


def test_edit_image_has_tool_policy_metadata():
    from app.services.ai.tool_policy import resolve_tool_metadata

    meta = resolve_tool_metadata("edit_image")
    assert meta.side_effect == "write"
    assert meta.confirmation == "none"


async def test_edit_image_not_gated_by_api_format():
    """格式配置不该再把改图挡在门外。

    早先的做法是「不是 openai_chat 就不下发 edit_image」，但 image_api_format 的
    **默认值就是 openai_images**——等于让改图在默认配置下永远点不亮。协议选择下沉到
    request_image_once（带图即走 chat）之后，这里断言门禁只剩总开关一道。
    """
    from app.services.ai.tools.registry import ToolRegistry

    assert not hasattr(ToolRegistry, "CHAT_ONLY_TOOLS")
    assert ToolRegistry.CONFIG_GATED_TOOLS.get("edit_image") == "image_generation_enabled"


@pytest.mark.parametrize("api_format", ["openai_chat", "openai_images"])
async def test_config_gate_ignores_api_format(patch_config, monkeypatch, api_format):
    """两种格式下都下发：改图自己会切到 chat 端点，不需要管理员先改配置。"""
    from app.services.ai.tools.registry import ToolRegistry

    async def _get(key, default=None):
        return "1" if key == "image_generation_enabled" else api_format

    monkeypatch.setattr(tools.ConfigService, "get", staticmethod(_get))
    assert await ToolRegistry._config_gate_open("edit_image") is True


async def test_config_gate_closed_when_switch_off(monkeypatch):
    """总开关走的是 ConfigService.get（单个键），与批量读取是两条路径。"""
    from app.services.ai.tools.registry import ToolRegistry

    async def _get(key, default=None):
        return "0" if key == "image_generation_enabled" else "openai_chat"

    monkeypatch.setattr(tools.ConfigService, "get", staticmethod(_get))
    assert await ToolRegistry._config_gate_open("edit_image") is False


# ══════════════════ 请求体组装 ══════════════════


async def test_request_carries_image_and_text(
    patch_config, workspace, patch_context, captured_artifacts, monkeypatch
):
    source = _make_source(workspace)
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    result = await _call_edit(
        image=str(source), instruction="把背景换成浅蓝色，杯子别动"
    )

    assert result["ok"] is True
    assert len(bodies) == 1
    body = bodies[0]
    content = body["messages"][0]["content"]
    assert isinstance(content, list), "改图必须是多段内容，而不是一段纯文本"
    assert content[0] == {"type": "text", "text": "把背景换成浅蓝色，杯子别动"}
    assert content[1]["type"] == "image_url"
    data_url = content[1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == _PNG_HEAD


async def test_size_omitted_follows_source(
    patch_config, workspace, patch_context, captured_artifacts, monkeypatch
):
    """不指定尺寸时请求体里不该出现 size —— 传了默认值会把竖图压成方形。"""
    source = _make_source(workspace)
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    result = await _call_edit(image=str(source), instruction="换个背景")

    assert "size" not in bodies[0]
    assert result["ok"] is True


async def test_explicit_size_is_sent(
    patch_config, workspace, patch_context, captured_artifacts, monkeypatch
):
    source = _make_source(workspace)
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    await _call_edit(image=str(source), instruction="换个背景", size="1664x928")

    assert bodies[0]["size"] == "1664x928"


# ══════════════════ 路径安全 ══════════════════


async def test_rejects_path_outside_workspace(
    patch_config, workspace, patch_context, monkeypatch
):
    """工作区外的文件一律拒绝：路径来自用户输入，直接 open 等于开了读任意文件的口子。"""
    outside = workspace.root.parent / "secret.png"
    outside.write_bytes(_PNG_HEAD)

    result = await _call_edit(image=str(outside), instruction="改一下")

    assert result["ok"] is False
    assert "工作区" in result["error"]


async def test_rejects_traversal_escape(
    patch_config, workspace, patch_context, monkeypatch
):
    result = await _call_edit(
        image="../../../etc/passwd", instruction="改一下"
    )
    assert result["ok"] is False
    assert "工作区" in result["error"]


async def test_rejects_other_users_file(
    patch_config, workspace, patch_context, monkeypatch
):
    other = workspace.root / "someone__99" / "uploads"
    other.mkdir(parents=True)
    stolen = other / "x.png"
    stolen.write_bytes(_PNG_HEAD)

    result = await _call_edit(image=str(stolen), instruction="改一下")

    assert result["ok"] is False
    assert "工作区" in result["error"]


async def test_rejects_missing_file(patch_config, workspace, patch_context):
    result = await _call_edit(
        image=str(workspace.user_dir / "uploads" / "nope.png"), instruction="改一下"
    )
    assert result["ok"] is False
    assert "找不到" in result["error"]


async def test_rejects_non_image(patch_config, workspace, patch_context):
    bogus = workspace.user_dir / "uploads" / "note.png"
    bogus.write_bytes(b"this is definitely not an image")

    result = await _call_edit(image=str(bogus), instruction="改一下")

    assert result["ok"] is False
    assert "图片格式" in result["error"]


async def test_rejects_oversized_image(patch_config, workspace, patch_context, monkeypatch):
    monkeypatch.setattr(tools, "MAX_SOURCE_IMAGE_BYTES", 100)
    source = _make_source(workspace, name="big.png", data=_PNG_HEAD * 3)

    result = await _call_edit(image=str(source), instruction="改一下")

    assert result["ok"] is False
    assert "太大" in result["error"]


async def test_accepts_artifact_download_url(
    patch_config, workspace, patch_context, captured_artifacts, monkeypatch
):
    """模型手里通常只有上一张图的 download_url，要能从链接回到本地文件。"""
    generated = workspace.user_dir / "generated" / "prev.png"
    generated.write_bytes(_PNG_HEAD)

    async def _fake_resolve(artifact_id: str, token: str):
        if artifact_id == "c" * 32 and token == "tok":
            return SimpleNamespace(path=generated)
        return None

    monkeypatch.setattr(
        "app.services.ai.tools.generated_file_service.resolve_workspace_artifact",
        _fake_resolve,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    result = await _call_edit(
        image=f"https://host/api/v1/chat/generated-files/{'c' * 32}?token=tok",
        instruction="把这张再改改",
    )

    assert result["ok"] is True


async def test_rejects_bad_artifact_token(patch_config, workspace, patch_context, monkeypatch):
    async def _fake_resolve(artifact_id: str, token: str):
        return None

    monkeypatch.setattr(
        "app.services.ai.tools.generated_file_service.resolve_workspace_artifact",
        _fake_resolve,
    )

    result = await _call_edit(
        image=f"https://host/api/v1/chat/generated-files/{'d' * 32}?token=bad",
        instruction="改一下",
    )

    assert result["ok"] is False
    assert "失效" in result["error"] or "不属于" in result["error"]


# ══════════════════ 错误分支 ══════════════════


async def test_works_under_images_protocol_and_hits_chat_endpoint(
    patch_config, workspace, patch_context, captured_artifacts, monkeypatch
):
    """配置是原生图像格式时，改图照样成功——但请求必须打到 /chat/completions。

    这是「格式只管生图」的核心断言：用户不必为了改图去改生图协议。
    """
    patch_config({"image_api_format": "openai_images"})
    source = _make_source(workspace)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    result = await _call_edit(image=str(source), instruction="把背景换成浅蓝色")

    assert result["ok"] is True
    assert len(seen) == 1
    assert seen[0].endswith("/chat/completions"), f"应走聊天端点，实际 {seen[0]}"
    assert "/images/generations" not in seen[0]


async def test_refuses_when_feature_disabled(patch_config, workspace, patch_context):
    patch_config({"image_generation_enabled": "0"})

    result = await _call_edit(image=str(_make_source(workspace)), instruction="改一下")

    assert result["ok"] is False
    assert "未开启" in result["error"]


async def test_requires_instruction(patch_config, workspace, patch_context):
    result = await _call_edit(image=str(_make_source(workspace)), instruction="   ")

    assert result["ok"] is False
    assert "instruction" in result["error"]


async def test_requires_image_argument(patch_config, workspace, patch_context):
    result = await _call_edit(image="", instruction="改一下")

    assert result["ok"] is False
    assert "image" in result["error"]


# ══════════════════ 产物 ══════════════════


async def test_publishes_as_new_artifact(
    patch_config, workspace, patch_context, captured_artifacts, monkeypatch
):
    source = _make_source(workspace)
    original = source.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    result = await _call_edit(image=str(source), instruction="换个背景")

    assert result["ok"] is True
    assert len(captured_artifacts) == 1
    # 原图不能被覆盖 —— 否则用户改坏了回不去。
    assert source.read_bytes() == original
    assert "download_url" in result["image"]


async def test_preview_edit_pins_chat_endpoint(
    patch_config, patch_context, monkeypatch
):
    """「测试改图」也要无视生图格式——它和 edit_image 是同一条路径。

    这条曾经会抛「请把接口格式改为 openai_chat」，而默认格式正是 openai_images，
    于是设置页那个按钮在默认配置下永远点不亮。
    """
    patch_config({"image_api_format": "openai_images", "image_model": "test-image-model"})
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    result = await tools.preview_image_edit()

    assert result["supports_edit"] is True
    assert result["api_format"] == "openai_chat"
    assert seen and seen[0].endswith("/chat/completions")


async def test_preview_edit_sends_sample_image(patch_config, patch_context, monkeypatch):
    """样图必须真的进了请求体——否则测的是「能不能文生图」，不是「能不能改图」。"""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    await tools.preview_image_edit()

    content = bodies[0]["messages"][0]["content"]
    assert isinstance(content, list) and len(content) == 2
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


# ══════════════════ 参考图（第二张） ══════════════════


async def test_reference_image_joins_the_same_message(
    patch_config, workspace, patch_context, captured_artifacts, monkeypatch
):
    """主图在前、参考图在后，同一条 user 消息里两段 image_url。顺序即语义。"""
    main = _make_source(workspace, name="main.png")
    ref = _make_source(workspace, name="ref.png", data=_PNG_HEAD + b"\x00" * 8)
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    result = await _call_edit(
        image=str(main), reference=str(ref), instruction="参考第二张的配色，改成同样的色调"
    )

    assert result["ok"] is True
    content = bodies[0]["messages"][0]["content"]
    assert len(content) == 3, "应该是 文本 + 主图 + 参考图 三段"
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url" and content[2]["type"] == "image_url"
    # 主图必须是第一张
    first = base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])
    second = base64.b64decode(content[2]["image_url"]["url"].split(",", 1)[1])
    assert first == _PNG_HEAD
    assert second == _PNG_HEAD + b"\x00" * 8


async def test_reference_echoed_in_result(
    patch_config, workspace, patch_context, captured_artifacts, monkeypatch
):
    """返回值里要说清用了几张图，模型转述时才有据可依。"""
    main = _make_source(workspace, name="main.png")
    ref = _make_source(workspace, name="ref.png")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    result = await _call_edit(image=str(main), reference=str(ref), instruction="改一下")

    assert result["reference"] is not None
    assert result["reference"]["bytes"] == len(_PNG_HEAD)


async def test_no_reference_reports_null(
    patch_config, workspace, patch_context, captured_artifacts, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    result = await _call_edit(image=str(_make_source(workspace)), instruction="改一下")

    assert result["reference"] is None
    assert len(result["source"]) == 2


async def test_reference_outside_workspace_is_rejected_and_named(
    patch_config, workspace, patch_context
):
    """参考图越界要指名道姓——说「找不到图片」会让人以为是主图坏了。"""
    outside = workspace.root.parent / "ref-secret.png"
    outside.write_bytes(_PNG_HEAD)

    result = await _call_edit(
        image=str(_make_source(workspace)),
        reference=str(outside),
        instruction="改一下",
    )

    assert result["ok"] is False
    assert "参考图" in result["error"]
    assert "工作区" in result["error"]


async def test_reference_with_multiple_paths_is_rejected(
    patch_config, workspace, patch_context
):
    """模型把两张塞进一个参数时，要明确告诉它只能一张，而不是读不到文件。"""
    result = await _call_edit(
        image=str(_make_source(workspace)),
        reference="a.png,b.png",
        instruction="改一下",
    )

    assert result["ok"] is False
    assert "参考图只能给一张" in result["error"]


async def test_reference_of_non_image_is_rejected(
    patch_config, workspace, patch_context
):
    bogus = workspace.user_dir / "uploads" / "note2.png"
    bogus.write_bytes(b"definitely not an image")

    result = await _call_edit(
        image=str(_make_source(workspace)),
        reference=str(bogus),
        instruction="改一下",
    )

    assert result["ok"] is False
    assert "参考图" in result["error"]


async def test_read_source_image_matches_upload_dir_key(tmp_path, monkeypatch):
    """工作区目录键必须与上传目录一致（{user_name}__{user_id}）。

    回归：AgentContext 没有 user_name 属性，只在 user_dimensions 里。曾经只读属性，
    目录键退化成纯 user_id，用户刚上传的图被判成「不在自己工作区」。
    这里**不** stub resolve_workspace_user_key——stub 过它的测试正是这个问题没被发现的原因。
    """
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_user_key

    root = tmp_path / "agent_workspaces"
    user_key = resolve_workspace_user_key(user_id=7, user_name="tester")
    assert user_key != "7", "前提：带用户名时目录键应为 name__id"
    uploads = root / user_key / "uploads"
    uploads.mkdir(parents=True)
    image = uploads / "pic.png"
    image.write_bytes(_PNG_HEAD)

    async def _root(*, ensure_exists: bool = True):
        return str(root)

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root", _root
    )
    context = SimpleNamespace(user_id=7, user_dimensions={"user_name": "tester"})

    mime, data = await tools._read_source_image(str(image), context=context)
    assert mime == "image/png"
    assert data == _PNG_HEAD


async def test_preview_edit_500_do_request_failed_explains_unsupported_model(
    patch_config, monkeypatch
):
    """回归：kolors 等只支持文生图的模型改图时回 500 do_request_failed（抛 PermissionError），
    必须给出「这个模型看起来不支持改图」的换模型引导，不能被前面的 PermissionError 拦截。
    同时 401 鉴权错误不能被误判为「不支持改图」。
    """
    patch_config({"image_model": "kolors"})
    mode = {"status": 500}

    def handler(request: httpx.Request) -> httpx.Response:
        if mode["status"] == 500:
            return httpx.Response(500, text='{"error":{"code":"do_request_failed"}}')
        return httpx.Response(401, text='{"error":{"message":"invalid api key"}}')

    _patch_http(monkeypatch, handler)
    with pytest.raises(ValueError, match="不支持改图"):
        await tools.preview_image_edit()

    mode["status"] = 401
    with pytest.raises(ValueError, match="连接图像服务失败"):
        await tools.preview_image_edit()


async def test_preview_edit_omits_size_to_follow_sample(patch_config, monkeypatch):
    """「测试改图」应不传 size（跟随样图比例），与 edit_image 的默认行为一致。"""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_response())

    _patch_http(monkeypatch, handler)
    await tools.preview_image_edit()
    assert "size" not in bodies[0]


async def test_read_source_image_rejects_other_users_artifact_url(
    workspace, patch_context, monkeypatch
):
    """按 download_url 反查产物也必须校验位于当前用户 scope 内，禁止跨用户读取。"""
    other_dir = workspace.root / "other__99" / "generated"
    other_dir.mkdir(parents=True)
    other_file = other_dir / "secret.png"
    other_file.write_bytes(_PNG_HEAD)

    async def _fake_resolve(artifact_id: str, token: str):
        return SimpleNamespace(path=other_file)

    monkeypatch.setattr(
        "app.services.ai.tools.generated_file_service.resolve_workspace_artifact",
        _fake_resolve,
    )
    context = SimpleNamespace(user_id=7, user_dimensions={"user_name": "tester"})
    url = f"https://host/api/v1/chat/generated-files/{'a' * 32}?token=validtok"

    with pytest.raises(ValueError, match="不属于当前用户"):
        await tools._read_source_image(url, context=context)


async def test_list_image_models_ignores_masked_api_key(patch_config, monkeypatch):
    """传入脱敏密钥（含 ****）时必须回落到库里保存的真实密钥，避免拿脱敏串去 401。"""
    patch_config({"image_api_key": "real-secret-key"})
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"data": [{"id": "Qwen-Image-2.1-Turbo"}]})

    _patch_http(monkeypatch, handler)
    res = await tools.list_image_models(api_key="real****key")
    assert res["models"] == ["Qwen-Image-2.1-Turbo"]
    assert seen_auth == ["Bearer real-secret-key"]


async def test_chat_protocol_supports_remote_image_url(patch_config, monkeypatch):
    """聊天协议返回 http(s) 图片外链时，应自动下载回来而不是报「没有返回图片」。"""
    patch_config({"image_api_format": "openai_chat"})

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "model": "test-model",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": "https://cdn.example/out.png"},
                                    }
                                ],
                            }
                        }
                    ],
                },
            )
        if str(request.url) == "https://cdn.example/out.png":
            return httpx.Response(200, content=_PNG_HEAD)
        return httpx.Response(404)

    _patch_http(monkeypatch, handler)
    res = await tools.request_image_once(
        config=await tools.load_image_generation_config(),
        prompt="a cat",
        size="1024x1024",
        api_key="k",
    )
    assert res["mime"] == "image/png"
    assert res["data"] == _PNG_HEAD


def test_image_dimensions_parses_jpeg_and_webp():
    """无依赖解析 JPEG SOF0 与 WEBP VP8X 宽高，避免改图返回非 PNG 时宽高丢失。"""
    # 构造最小合法 JPEG（SOI + SOF0: height=480, width=640 + EOI）
    sof0_payload = bytes([8]) + (480).to_bytes(2, "big") + (640).to_bytes(2, "big") + bytes([3, 1, 0x11, 0])
    seg_len = (len(sof0_payload) + 2).to_bytes(2, "big")
    jpeg = b"\xff\xd8\xff\xc0" + seg_len + sof0_payload + b"\xff\xd9"
    assert tools._image_dimensions(jpeg) == (640, 480)

    # 构造最小合法 WEBP VP8X（width=1280, height=720）
    vp8x = (
        b"RIFF"
        + (22).to_bytes(4, "little")
        + b"WEBP"
        + b"VP8X"
        + (10).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + (1280 - 1).to_bytes(3, "little")
        + (720 - 1).to_bytes(3, "little")
    )
    assert tools._image_dimensions(vp8x) == (1280, 720)
