"""generate_image 工具的行为测试。

不依赖真实模型服务：``ConfigService``、HTTP 客户端、产物登记全部换成假实现，
请求体组装、尺寸校验、错误回传、落盘与发布这条链本身仍然真跑。
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

# 1x1 透明 PNG：足够让落盘与发布逻辑跑通，不需要真图片。
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_PNG_B64 = base64.b64encode(_PNG_1PX).decode()

BASE_CONFIG = {
    "image_generation_enabled": "1",
    # 以聊天协议为基线：下面这批用例断言的是 chat 通道的解析行为（choices[0]...image_url）。
    # 原生图像协议的用例单独一组，见文件末尾。
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


def _apply_config(monkeypatch, overrides: dict | None = None):
    """不依赖 fixture 的版本：直接调用端点函数的用例要让配置生效得自己 patch。"""

    async def _get_many(defaults):
        merged = _config(overrides)
        return {key: merged.get(key, default) for key, default in defaults.items()}

    monkeypatch.setattr(tools.ConfigService, "get_many", staticmethod(_get_many))


@pytest.fixture
def patch_config(monkeypatch):
    """按需返回配置；每次调用都重新读，便于单个用例内改值。"""

    def _apply(overrides: dict | None = None):
        async def _get_many(defaults):
            merged = _config(overrides)
            return {key: merged.get(key, default) for key, default in defaults.items()}

        monkeypatch.setattr(tools.ConfigService, "get_many", staticmethod(_get_many))

    _apply()
    return _apply


@pytest.fixture
def captured_artifacts(monkeypatch):
    """拦下产物发布，记录登记参数。"""
    recorded: list[dict] = []

    async def _fake_publish(source_path, filename, **kwargs):
        recorded.append({"source_path": source_path, "filename": filename, **kwargs})
        # 按扩展名推断 mime，跟真实 register_artifact 的行为一致：写死 image/png 会
        # 让「网关回 jpg」这类用例测不出问题。
        suffix = Path(filename).suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "application/octet-stream")
        return SimpleNamespace(
            artifact_id="a" * 32,
            token="tok",
            filename=filename,
            mime_type=mime,
            size=len(_PNG_1PX),
            download_url=f"https://host/api/v1/chat/generated-files/{'a' * 32}?token=tok",
        )

    monkeypatch.setattr(tools, "publish", _fake_publish)
    return recorded


@pytest.fixture
def patch_context(monkeypatch):
    # 必须写成同步：真实实现就是同步的（读 contextvar）。stub 成 async 会把
    # 「实现里多写了一个 await」这类错配一起掩盖掉。
    def _get_context():
        return SimpleNamespace(
            user_id=7,
            conversation_id="conv-1",
            user_dimensions={"user_name": "tester"},
        )

    monkeypatch.setattr(tools, "get_current_agent_context", _get_context)
    # 有些实现直接 import 到模块命名空间，两处都打上以防漏
    monkeypatch.setattr(
        "app.core.context.get_current_agent_context", _get_context, raising=False
    )


async def _call_generate(**kwargs):
    """@tool 把函数包成 FunctionTool，得用 ainvoke 调。"""
    return await tools.generate_image.ainvoke(kwargs)


def _patch_http(monkeypatch, handler, *, calls: list | None = None):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(tools.httpx, "AsyncClient", factory)
    return calls


def _image_api_response(*, model: str = "upstream-name", count: int = 1) -> dict:
    """原生 OpenAI 图像接口的响应：图片在 data[].b64_json。"""
    return {
        "created": 1795073851,
        "model": model,
        "size": "1024x1024",
        "output_format": "png",
        "data": [{"b64_json": _PNG_B64, "url": None, "revised_prompt": None}] * count,
    }


def _image_response(*, model: str = "upstream-name") -> dict:
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [
            {
                "message": {
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{_PNG_B64}"},
                            "stage_durations": {"stage_0_gen_ms": 1200},
                        }
                    ]
                }
            }
        ],
    }


# ── 配置与开关 ────────────────────────────────────────────────


async def test_switch_off_refuses_with_actionable_message(patch_config, patch_context):
    patch_config({"image_generation_enabled": "0"})
    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is False
    # 报错要指向"找管理员开开关"，而不是让模型猜
    assert "未开启" in result["error"]


@pytest.mark.parametrize(
    "missing, expect",
    [
        ("image_api_base_url", "API 地址"),
        ("image_api_key", "密钥"),
        ("image_model", "模型"),
    ],
)
async def test_missing_required_config_names_the_field(
    patch_config, patch_context, missing, expect
):
    patch_config({missing: ""})
    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is False
    assert expect in result["error"]


# ── 尺寸校验 ──────────────────────────────────────────────────


async def test_size_outside_whitelist_is_rejected_with_allowed_values(
    patch_config, patch_context
):
    result = await _call_generate(prompt="一只猫", size="999x999")

    assert result["ok"] is False
    # 必须把可用值告诉模型，否则它只会原样重试同一个错误尺寸
    assert "999x999" in result["error"]
    assert "1664x928" in result["error"]


async def test_asterisk_size_format_is_normalized_or_rejected(patch_config, patch_context, monkeypatch):
    """星号格式会被网关静默忽略，必须在平台侧拦下或纠正。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫", size="1328*1328")

    # 1328x1328 不在白名单里：要么报错并提示，要么归一后仍判非法
    assert result["ok"] is False


# ── 用户在输入框里选定的尺寸 ──────────────────────────────────
# 独立于「模型传什么」的一条链路：面板上勾了 928x1664，就必须出 928x1664。


def _patch_user_size(monkeypatch, size: str | None):
    """模拟 execution_options.image_size：有值代表用户在「图像参数」里点过具体尺寸。"""

    def _get(key, default=None):
        if key == "image_size":
            return size
        return default

    monkeypatch.setattr(tools, "get_execution_option", _get)


async def test_user_selected_size_wins_over_model_choice(
    patch_config, patch_context, monkeypatch
):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    _patch_user_size(monkeypatch, "928x1664")

    # 模型自己传了 1024x1024，用户的明确选择必须覆盖它
    result = await _call_generate(prompt="一张竖版海报", size="1024x1024")

    assert result["ok"] is True, result
    assert seen[0]["size"] == "928x1664"


async def test_user_selected_size_ignores_invalid_model_size(
    patch_config, patch_context, monkeypatch
):
    """模型传了白名单外的尺寸时不该整单失败——用户已经指定了合法尺寸。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    _patch_user_size(monkeypatch, "1664x928")

    result = await _call_generate(prompt="一张横版海报", size="999x999")

    assert result["ok"] is True, result
    assert seen[0]["size"] == "1664x928"


async def test_user_selected_size_outside_whitelist_falls_back_silently(
    patch_config, patch_context, monkeypatch
):
    """白名单外的尺寸（后台刚删、前端缓存还没刷）回落默认，而不是让用户看到一次失败。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    _patch_user_size(monkeypatch, "777x777")

    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is True, result
    assert seen[0]["size"] == "1024x1024"


async def test_no_user_size_keeps_model_choice(
    patch_config, patch_context, monkeypatch
):
    """没在面板里选尺寸时行为不变：模型传的合法尺寸照用。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    _patch_user_size(monkeypatch, None)

    result = await _call_generate(prompt="一只猫", size="1664x928")

    assert result["ok"] is True, result
    assert seen[0]["size"] == "1664x928"


# ── 正常路径 ──────────────────────────────────────────────────


async def test_success_returns_published_image(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只戴眼镜的橘猫", size="1664x928")

    assert result["ok"] is True, result
    image = result["images"][0]
    assert image["mime_type"] == "image/png"
    # 宽高以图片真实解析结果为准（假图是 1x1），不是回显请求参数
    assert image["width"] == 1 and image["height"] == 1
    assert image["actual_size"] == "1x1"
    assert result["size_mismatch"] is True
    assert "1664x928" in result["note"]
    assert image["download_url"].startswith("https://host/api/v1/chat/generated-files/")
    assert result["model"]

    body = seen[0]
    assert body["model"] == "test-image-model"
    assert body["size"] == "1664x928"
    assert body["stream"] is False
    assert body["messages"][0]["content"] == "一只戴眼镜的橘猫"

    assert captured_artifacts, "产物必须被发布，否则前端拿不到下载链接"


async def test_default_size_used_when_not_specified(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is True
    assert seen[0]["size"] == "1024x1024"


async def test_prompt_prefix_is_applied(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    patch_config({"image_prompt_prefix": "企业级扁平插画，蓝金配色。"})
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    await _call_generate(prompt="年会主视觉")

    sent = seen[0]["messages"][0]["content"]
    assert "企业级扁平插画" in sent
    assert "年会主视觉" in sent


async def test_requested_count_is_clamped_to_configured_max(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    patch_config({"image_max_images_per_call": "1"})
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫", n=5)

    assert result["ok"] is True
    assert len(seen) == 1, "超过上限的请求不应真的多打模型"
    assert result.get("requested_n_clamped") is True


# ── 失败路径 ──────────────────────────────────────────────────


async def test_api_error_is_passed_back_verbatim(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is False
    # 模型看到原文才能告诉用户去改密钥
    assert "401" in result["error"] or "Invalid API key" in result["error"]


async def test_network_error_retries_once_then_fails(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("boom", request=request)

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is False
    assert len(attempts) == 2, "网络错误应重试一次"


async def test_response_without_image_explains_why(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": "抱歉，我无法生成这张图片"}}],
            },
        )

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is False
    assert result["error"]


async def test_published_payload_has_width_and_height(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    """前端按 width/height 决定占位比例，缺了会跳版。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫", size="928x1664")

    image = result["images"][0]
    # 关键：这两个字段必须存在且是真实尺寸，前端靠它算占位比例
    assert image["width"] == 1
    assert image["height"] == 1
    assert image["size"] == len(_PNG_1PX)
    assert image["filename"].endswith(".png")


# ── 注册表门禁：总开关关闭时工具不该出现在任何智能体的清单里 ──────────


async def _registry_with_switch(monkeypatch, value):
    from app.services.ai.tools.registry import ToolRegistry
    from app.services.config_service import ConfigService

    async def _get(key, default=None):
        if key == "image_generation_enabled":
            if isinstance(value, Exception):
                raise value
            return value
        return default

    monkeypatch.setattr(ConfigService, "get", staticmethod(_get))
    # 运行时描述增强要读整组配置，这里给一份可用的
    patch = _config()
    async def _get_many(defaults):
        return {k: patch.get(k, d) for k, d in defaults.items()}

    monkeypatch.setattr(ConfigService, "get_many", staticmethod(_get_many))
    return ToolRegistry


async def test_registry_hides_tool_when_switch_off(monkeypatch):
    Registry = await _registry_with_switch(monkeypatch, "0")
    assert await Registry.get_tool("generate_image") is None


async def test_registry_exposes_tool_when_switch_on(monkeypatch):
    Registry = await _registry_with_switch(monkeypatch, "1")
    tool_obj = await Registry.get_tool("generate_image")
    assert tool_obj is not None
    assert tool_obj.name == "generate_image"


async def test_registry_hides_tool_when_config_unreadable(monkeypatch):
    """读不到开关时按关闭处理：宁可少一个工具，也不要意外开始计费。"""
    Registry = await _registry_with_switch(monkeypatch, RuntimeError("redis down"))
    assert await Registry.get_tool("generate_image") is None


async def test_runtime_tool_description_lists_allowed_sizes(monkeypatch):
    Registry = await _registry_with_switch(monkeypatch, "1")
    spec = await Registry.get_runtime_tool("generate_image")
    assert spec is not None
    # 模型看不到配置，只能从描述里知道能选哪些尺寸
    assert "1664x928" in spec.description
    assert "1024x1024" in spec.description


async def test_unrelated_tools_are_not_gated(monkeypatch):
    Registry = await _registry_with_switch(monkeypatch, "0")
    assert await Registry.get_tool("read_image") is not None


# ── 状态接口：对话页入口靠它判断两道闸 ──────────────────────────────


async def test_status_endpoint_reports_system_gate_only(monkeypatch):
    """只回系统层判定。

    早先还会按 agent_id 回「这个智能体勾没勾工具」，但「智能委派」这类入口智能体
    只负责分发、本身不勾业务工具，而真正执行的是被委派的子智能体——那个判定必然
    误伤。现在一律不回，让运行时去报错。
    """
    from app.api.portal.endpoints import system as system_endpoint

    _apply_config(monkeypatch, {"image_generation_enabled": "0"})
    payload = await system_endpoint.get_image_generation_status(
        agent_id="agent-1", user={"user_id": 1}
    )
    assert payload["system_enabled"] is False
    assert "agent_enabled" not in payload
    assert "agent_edit_enabled" not in payload
    # 尺寸白名单要一起回，前端面板不需要再猜
    assert payload["allowed_sizes"] == ["1024x1024", "1664x928", "928x1664"]
    assert payload["default_size"] == "1024x1024"
    assert payload["max_images"] == 1
    # 绝不能回密钥
    assert "api_key" not in payload and "api_key" not in str(payload)


async def test_status_endpoint_ignores_agent_id(monkeypatch):
    """传不传 agent_id 结果一样——这条已经和智能体无关了。"""
    from app.api.portal.endpoints import system as system_endpoint

    _apply_config(monkeypatch, {"image_generation_enabled": "1"})
    with_agent = await system_endpoint.get_image_generation_status(
        agent_id="agent-1", user={"user_id": 1}
    )
    without_agent = await system_endpoint.get_image_generation_status(
        agent_id=None, user={"user_id": 1}
    )
    assert with_agent["system_enabled"] is True
    assert with_agent == without_agent


async def test_partial_success_keeps_first_image(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    """要 2 张时第二张挂了：第一张不能跟着一起丢。"""
    patch_config({"image_max_images_per_call": "2"})
    calls: list[dict] = []
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        state["n"] += 1
        if state["n"] == 2:
            return httpx.Response(500, json={"error": "上游炸了"})
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="两只猫", n=2)

    assert result["ok"] is True, result
    assert result["partial"] is True
    assert len(result["images"]) == 1
    assert "上游炸了" in result["warning"]
    assert len(calls) == 2


async def test_non_png_response_falls_back_to_requested_size(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    """网关回 jpg 时 PNG 头解析不出来：宽高要退回请求尺寸，不能给前端 null。"""
    jpeg_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0fakejpeg").decode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "image_url", "image_url": {"url": jpeg_url}}
                            ]
                        }
                    }
                ]
            },
        )

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫", size="1664x928")

    image = result["images"][0]
    assert image["mime_type"] == "image/jpeg"
    assert (image["width"], image["height"]) == (1664, 928)
    # 标出来源是估算，前端/模型才知道这不是实测值
    assert image["size_estimated"] is True


# ── 「测试生成」：真跑一次但绝不落盘 ──────────────────────────────


async def test_preview_returns_data_url_and_writes_nothing(
    patch_config, captured_artifacts, monkeypatch
):
    """设置页的测试生成：回 data URL 预览，且一张产物都不许登记。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    result = await tools.preview_image_generation(prompt="一只猫", size="1664x928")

    assert result["image"].startswith("data:image/png;base64,")
    assert result["mime_type"] == "image/png"
    assert (result["width"], result["height"]) == (1, 1)
    assert result["size"] == "1664x928"
    assert result["bytes"] == len(_PNG_1PX)
    # 关键：不落库 → publish 一次都没被调用
    assert captured_artifacts == []


async def test_preview_uses_body_overrides_for_unsaved_changes(
    patch_config, monkeypatch
):
    """改了地址/模型但没保存，也要能先试通：请求体优先于库里的值。"""
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    await tools.preview_image_generation(
        prompt="一只猫",
        base_url="https://draft.example.com/v1",
        model="draft-model",
        size="928x1664",
    )

    url, body = seen[0]
    assert url == "https://draft.example.com/v1/chat/completions"
    assert body["model"] == "draft-model"
    assert body["size"] == "928x1664"


async def test_preview_ignores_masked_key(patch_config, monkeypatch):
    """前端手里是脱敏串（abc****wxyz），拿它调必 401：必须回落库里的真实密钥。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    await tools.preview_image_generation(prompt="一只猫", api_key="sk-****1234")

    assert seen[0]["authorization"] == "Bearer sk-test"


async def test_preview_rejects_when_switch_off(patch_config):
    patch_config({"image_generation_enabled": "0"})
    with pytest.raises(ValueError, match="未开启"):
        await tools.preview_image_generation(prompt="一只猫")


async def test_preview_rejects_size_outside_whitelist(patch_config):
    with pytest.raises(ValueError, match="不在允许范围"):
        await tools.preview_image_generation(prompt="一只猫", size="1920x1080")


# ── 原生 OpenAI 图像协议（/images/generations） ──────────────────


async def test_default_api_format_is_openai_images():
    """没配 image_api_format 时要落到原生图像协议：它是唯一让尺寸/张数生效的那个。"""
    assert tools.normalize_api_format("") == tools.API_FORMAT_OPENAI_IMAGES
    assert tools.normalize_api_format("bogus") == tools.API_FORMAT_OPENAI_IMAGES
    assert tools.normalize_api_format("OpenAI_Chat") == tools.API_FORMAT_OPENAI_CHAT


async def test_image_api_request_shape(patch_config, monkeypatch):
    """原生协议：打 /images/generations，带 prompt/n/size，不带 messages。"""
    patch_config({"image_api_format": "openai_images"})
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json=_image_api_response())

    _patch_http(monkeypatch, handler)
    await tools.preview_image_generation(prompt="一只猫", size="1664x928")

    url, body = seen[0]
    assert url == "https://api.example.com/v1/images/generations"
    assert body == {
        "model": "test-image-model",
        "prompt": "一只猫",
        "n": 1,
        "size": "1664x928",
    }
    assert "messages" not in body


async def test_image_api_publishes_b64_json(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    """data[].b64_json 要能落成产物，尺寸从 PNG 头里读出来。"""
    patch_config({"image_api_format": "openai_images"})

    _patch_http(monkeypatch, lambda request: httpx.Response(200, json=_image_api_response()))
    result = await tools.generate_image.ainvoke({"prompt": "一只猫", "size": "1024x1024"})

    assert result["ok"] is True
    assert len(result["images"]) == 1
    assert (result["images"][0]["width"], result["images"][0]["height"]) == (1, 1)
    assert captured_artifacts[0]["artifact_type"] == "image"


async def test_image_api_takes_only_first_image(patch_config, patch_context, monkeypatch):
    """响应里给了多张也只取第一张：n 固定为 1，避免一次调用悄悄产出多张计费图。"""
    patch_config({"image_api_format": "openai_images"})
    counts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        counts.append(body["n"])
        return httpx.Response(200, json=_image_api_response(count=3))

    _patch_http(monkeypatch, handler)
    result = await tools.generate_image.ainvoke({"prompt": "一只猫"})

    assert counts == [1]
    assert len(result["images"]) == 1


async def test_image_api_downloads_url_when_no_b64(patch_config, monkeypatch):
    """有渠道只回 url 不回 b64。这时要自己把图下回来，否则等于不出图。"""
    patch_config({"image_api_format": "openai_images"})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(
                200, json={"data": [{"url": "https://cdn.example.com/a.png", "b64_json": None}]}
            )
        # 二次下载
        return httpx.Response(200, content=_PNG_1PX, headers={"content-type": "image/png"})

    _patch_http(monkeypatch, handler)
    result = await tools.preview_image_generation(prompt="一只猫")

    assert requests[-1] == "https://cdn.example.com/a.png"
    assert result["image"].startswith("data:image/png;base64,")


async def test_image_api_rejects_non_http_url(patch_config):
    """渠道给的地址不一定是 http(s)：不能拿它去发起任意请求。

    契约是「失败抛 _DownloadError 并带上原因」——上层要能把「图没取回来」
    和「模型没出图」分开报，所以这里不能再静默返回 None。
    """
    with pytest.raises(tools._DownloadError):
        await tools._download_image_bytes("file:///etc/passwd", 5.0)


async def test_image_api_sniffs_jpeg_mime(patch_config, captured_artifacts, monkeypatch):
    """渠道回 JPEG 时 mime 要跟着变，否则产物按 PNG 存会解不开。"""
    import base64 as _b64

    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 64
    patch_config({"image_api_format": "openai_images"})

    _patch_http(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"data": [{"b64_json": _b64.b64encode(jpeg).decode()}]}
        ),
    )
    result = await tools.preview_image_generation(prompt="一只猫")

    assert result["mime_type"] == "image/jpeg"


async def test_image_api_empty_data_raises(patch_config, monkeypatch):
    patch_config({"image_api_format": "openai_images"})
    _patch_http(monkeypatch, lambda request: httpx.Response(200, json={"data": []}))

    with pytest.raises(ValueError, match="没有返回图片"):
        await tools.preview_image_generation(prompt="一只猫")


async def test_preview_honours_passed_api_format(patch_config, monkeypatch):
    """改了协议还没保存就点「测试生成」：必须按传入的协议发，不能读库里的旧值。"""
    patch_config({"image_api_format": "openai_chat"})
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json=_image_api_response())

    _patch_http(monkeypatch, handler)
    result = await tools.preview_image_generation(prompt="一只猫", api_format="openai_images")

    url, body = seen[0]
    assert url.endswith("/images/generations")
    assert "prompt" in body and "messages" not in body
    assert result["api_format"] == "openai_images"


async def test_preview_ignores_unknown_api_format(patch_config, monkeypatch):
    """认不出的格式不能覆盖库里的值，否则一次手滑就把线上协议改掉了。"""
    patch_config({"image_api_format": "openai_images"})
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_image_api_response())

    _patch_http(monkeypatch, handler)
    await tools.preview_image_generation(prompt="一只猫", api_format="gemini_native")

    assert seen[0].endswith("/images/generations")


async def test_read_timeout_is_not_retried(patch_config, monkeypatch):
    """读超时说明请求已送达上游，此时可能已经出图计费——重发就是再买一张。"""
    patch_config({"image_api_format": "openai_images"})
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise httpx.ReadTimeout("read timed out", request=request)

    _patch_http(monkeypatch, handler)
    with pytest.raises(tools._TransientError, match="超时"):
        await tools.preview_image_generation(prompt="一只猫")

    assert len(calls) == 1, "读超时不能自动重试，否则用户会为一张被丢弃的图付费"


async def test_connect_error_is_retried_once(patch_config, monkeypatch):
    """连接没建立起来时请求根本没到上游，重试是安全的，且只重试一次。"""
    patch_config({"image_api_format": "openai_images"})
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=_image_api_response())

    _patch_http(monkeypatch, handler)
    result = await tools.preview_image_generation(prompt="一只猫")

    assert len(calls) == 2
    assert result["image"].startswith("data:image/png;base64,")


async def test_download_rejects_oversized_response(patch_config, monkeypatch):
    """渠道给的图片地址来自上游响应，超大响应不能整块吃进内存。"""
    oversized = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=oversized, headers={"content-length": str(10 ** 12)}
        )

    _patch_http(monkeypatch, handler)
    with pytest.raises(tools._DownloadError, match="超过"):
        await tools._download_image_bytes("https://cdn.example.com/a.png", 5.0)


# ── 上游错误分类 ─────────────────────────────────────────────


async def test_upstream_401_reports_auth_problem(patch_config, patch_context, monkeypatch):
    """401 要说「密钥无效」，不能笼统报成连接失败——排查方向完全不同。"""
    _patch_http(
        monkeypatch,
        lambda request: httpx.Response(401, text='{"error":"invalid api key"}'),
    )

    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is False
    assert "鉴权失败" in result["error"]
    assert "401" in result["error"]


async def test_upstream_404_reports_model_problem(patch_config, patch_context, monkeypatch):
    _patch_http(
        monkeypatch, lambda request: httpx.Response(404, text='{"error":"no such model"}')
    )

    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is False
    assert "找不到该模型" in result["error"]


async def test_upstream_429_is_retried_once(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    """限流意味着请求在受理层就被拒了，没有出图——重试一次是安全的。"""
    monkeypatch.setattr(tools, "_UPSTREAM_RETRY_BACKOFF_SECONDS", 0)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(429, text='{"error":"rate limited"}')
        return httpx.Response(200, json=_image_response())

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫")

    assert len(calls) == 2, "429 应该退避后重试一次"
    assert result["ok"] is True, result


async def test_retry_path_read_timeout_is_wrapped(
    patch_config, patch_context, monkeypatch
):
    """重试期间的读超时不能逃成裸 ReadTimeout。

    曾经内层 except 只捕获连接类错误，重试时的读超时会直接冒出本函数，
    「不再自动重发以免重复计费、可调大超时」这段说明整段丢失。
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        raise httpx.ReadTimeout("slow")

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is False
    assert "超时" in result["error"]
    assert "不再自动重发" in result["error"]
    assert "已重试一次" in result["error"], "要说明这是重试阶段失败的"


# ── 外链下载与 SSRF ──────────────────────────────────────────


def test_download_host_guard_blocks_loopback_and_metadata(patch_config):
    """回环与云元数据地址一律不代发请求；私网 CDN 要放行，否则私有化部署直接不可用。"""
    for blocked in (
        "http://127.0.0.1:6379/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/x.png",
        "file:///etc/passwd",
        "http://[::1]/x.png",
    ):
        assert tools._is_blocked_download_host(blocked) is True, blocked

    for allowed in (
        "https://cdn.example.com/a.png",
        "http://minio.internal:9000/bucket/a.png",
        "http://10.0.0.8/a.png",
        "http://192.168.1.20/a.png",
    ):
        assert tools._is_blocked_download_host(allowed) is False, allowed


async def test_remote_download_failure_does_not_claim_no_image(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    """图已生成但外链取不回时，不能报「模型没有返回图片」。

    否则用户被告知「模型没出图」去换模型，而真实原因是网络/CDN，
    并且这次调用其实已经计费。
    """
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
        return httpx.Response(403, text="denied")

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is False
    assert "已经生成" in result["error"]
    assert "没有返回图片" not in result["error"]


async def test_gif_product_is_saved_with_gif_extension(
    patch_config, patch_context, captured_artifacts, monkeypatch
):
    """嗅探表认 GIF，扩展名映射也得跟上，否则动图以 .png 落盘、浏览器白块。"""
    import base64 as _b64

    gif = b"GIF89a" + b"\x00" * 64
    patch_config({"image_api_format": "openai_chat"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/gif;base64,"
                                        + _b64.b64encode(gif).decode()
                                    },
                                }
                            ]
                        }
                    }
                ],
            },
        )

    _patch_http(monkeypatch, handler)
    result = await _call_generate(prompt="一只猫")

    assert result["ok"] is True
    assert captured_artifacts[0]["filename"].endswith(".gif")
