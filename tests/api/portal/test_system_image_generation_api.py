"""系统设置 · 图像生成相关端点的行为测试。

只测端点这一层「有没有把界面上的东西如实传下去、有没有把失败翻译成可读文案」，
不重复测工具内部逻辑（那部分见 ``tests/test_image_preview_overrides.py``）。

这层的价值在于：设置页有一个独特诉求——**改了还没保存就要能先试通**。端点若少传
一个字段，工具就会用库里那套配置去跑，管理员得到的结论和他眼前的界面不一致，
而这种错「功能看起来完全正常」，只能靠这一层断言守住。

直接 ``await`` 端点协程，不走 TestClient：这些端点除了权限依赖没有任何请求态，
手动传 ``user`` 比拉起一整套 HTTP + DB 更贴近要验证的东西。
"""

from __future__ import annotations

import pytest

from app.api.portal.endpoints import system

pytestmark = pytest.mark.no_infrastructure

_ADMIN = {"user_id": 1, "user_name": "tester"}


@pytest.fixture
def fake_preview(monkeypatch):
    """把工具层换成记录器，端点只管转发。"""
    calls: list[dict] = []

    async def _preview_generation(**kwargs):
        calls.append(("generate", kwargs))
        return {
            "image": "data:image/png;base64,AAA",
            "mime_type": "image/png",
            "width": 1024,
            "height": 1024,
            "size": kwargs.get("size") or "1024x1024",
            "model": kwargs.get("model") or "saved-model",
            "api_format": kwargs.get("api_format") or "openai_images",
            "bytes": 3,
            "elapsed_ms": 1200,
        }

    async def _preview_edit(**kwargs):
        calls.append(("edit", kwargs))
        return {
            "image": "data:image/png;base64,AAA",
            "mime_type": "image/png",
            "width": 512,
            "height": 512,
            "size": "512x512",
            "model": kwargs.get("model") or "saved-model",
            "bytes": 3,
            "elapsed_ms": 2400,
            "instruction": "把背景换成浅蓝色",
        }

    from app.services.ai.tools import image_generation_tool as tools

    monkeypatch.setattr(tools, "preview_image_generation", _preview_generation)
    monkeypatch.setattr(tools, "preview_image_edit", _preview_edit)
    return calls


# ══════════════════ 测试生成 ══════════════════


async def test_generate_test_forwards_unsaved_overrides(fake_preview):
    """设置页填的每一项都必须真的用到这次测试上。"""
    payload = system.ImageTestRequest(
        prompt="一只猫",
        size="1664x928",
        base_url="https://gw.example.com/v1",
        api_key="sk-new",
        model="flux-dev",
        api_format="openai_chat",
        enabled=False,
        allowed_sizes=["1664x928", "928x1664"],
        prompt_prefix="水彩风格",
        timeout_seconds=90,
    )

    result = await system.test_image_generation(payload=payload, user=_ADMIN)

    assert result["ok"] is True
    kind, kwargs = fake_preview[0]
    assert kind == "generate"
    assert kwargs == {
        "prompt": "一只猫",
        "size": "1664x928",
        "base_url": "https://gw.example.com/v1",
        "api_key": "sk-new",
        "model": "flux-dev",
        "api_format": "openai_chat",
        "enabled": False,
        "allowed_sizes": ["1664x928", "928x1664"],
        "prompt_prefix": "水彩风格",
        "timeout_seconds": 90,
    }


async def test_generate_test_keeps_none_when_form_is_empty(fake_preview):
    """没填的项要传 None（= 用库里已存值），不能悄悄变成空串或 0。"""
    payload = system.ImageTestRequest(prompt="一只猫")

    await system.test_image_generation(payload=payload, user=_ADMIN)

    _, kwargs = fake_preview[0]
    assert kwargs["enabled"] is None
    assert kwargs["allowed_sizes"] is None
    assert kwargs["prompt_prefix"] is None
    assert kwargs["timeout_seconds"] is None


# ══════════════════ 测试改图 ══════════════════


async def test_edit_test_forwards_enabled_and_timeout(fake_preview):
    payload = system.ImageEditTestRequest(
        base_url="https://gw.example.com/v1",
        model="flux-dev",
        api_format="openai_chat",
        enabled=True,
        timeout_seconds=150,
    )

    result = await system.test_image_edit(payload=payload, user=_ADMIN)

    assert result["ok"] is True
    kind, kwargs = fake_preview[0]
    assert kind == "edit"
    assert kwargs["enabled"] is True
    assert kwargs["timeout_seconds"] == 150


async def test_edit_test_failure_reports_unsupported(monkeypatch):
    """改图失败必须带 supports_edit=False：前端靠它区分「连接问题」与「这模型不能改图」。"""

    async def _boom(**kwargs):
        raise PermissionError("上游返回 400：model does not support image input")

    from app.services.ai.tools import image_generation_tool as tools

    monkeypatch.setattr(tools, "preview_image_edit", _boom)

    result = await system.test_image_edit(
        payload=system.ImageEditTestRequest(), user=_ADMIN
    )

    assert result["ok"] is False
    assert result["supports_edit"] is False
    assert "image input" in result["message"]


# ══════════════════ 错误翻译 ══════════════════


@pytest.mark.parametrize("exc", [PermissionError("密钥无效"), ValueError("请先填写 API 地址")])
async def test_expected_errors_become_messages_not_500(monkeypatch, exc):
    """工具层的「预期内失败」要变成 ok=False 的可读文案，不能冒成 HTTP 500。"""

    async def _raise(**kwargs):
        raise exc

    from app.services.ai.tools import image_generation_tool as tools

    monkeypatch.setattr(tools, "preview_image_generation", _raise)

    result = await system.test_image_generation(
        payload=system.ImageTestRequest(prompt="一只猫"), user=_ADMIN
    )

    assert result["ok"] is False
    assert result["message"] == str(exc)


async def test_unexpected_error_is_wrapped(monkeypatch):
    async def _raise(**kwargs):
        raise RuntimeError("boom")

    from app.services.ai.tools import image_generation_tool as tools

    monkeypatch.setattr(tools, "preview_image_generation", _raise)

    result = await system.test_image_generation(
        payload=system.ImageTestRequest(prompt="一只猫"), user=_ADMIN
    )

    assert result["ok"] is False
    assert result["message"].startswith("测试失败：")


# ══════════════════ 模型列表 ══════════════════


async def test_list_models_forwards_unsaved_key(monkeypatch):
    seen: list[dict] = []

    async def _fake(**kwargs):
        seen.append(kwargs)
        return {"models": ["flux-dev"], "endpoint": "https://gw.example.com/v1/models"}

    from app.services.ai.tools import image_generation_tool as tools

    monkeypatch.setattr(tools, "list_image_models", _fake)

    result = await system.list_image_models_endpoint(
        payload=system.ImageModelsRequest(api_key="sk-new"), user=_ADMIN
    )

    assert result["ok"] is True
    assert result["models"] == ["flux-dev"]
    assert seen[0]["api_key"] == "sk-new"


async def test_list_models_accepts_empty_body(monkeypatch):
    """不传 body 也要能跑：老前端没有这个字段，用库里已保存的地址与密钥。"""

    async def _fake(**kwargs):
        return {"models": [], "endpoint": ""}

    from app.services.ai.tools import image_generation_tool as tools

    monkeypatch.setattr(tools, "list_image_models", _fake)

    result = await system.list_image_models_endpoint(payload=None, user=_ADMIN)

    assert result["ok"] is True


# ══════════════════ 可用状态 ══════════════════


@pytest.mark.parametrize("enabled", [True, False])
async def test_status_never_leaks_secrets(monkeypatch, enabled):
    """这个接口普通登录用户就能调，返回体里绝不能出现密钥/地址这类配置。"""

    async def _fake_config():
        return {
            "enabled": enabled,
            "allowed_sizes": ["1024x1024"],
            "default_size": "1024x1024",
            "max_images_per_call": 2,
            "api_base_url": "https://internal-gw.example.com/v1",
            "api_key": "sk-secret-value",
            "model": "flux-dev",
            "api_format": "openai_images",
            "prompt_prefix": "内部前缀",
            "timeout_seconds": 120,
        }

    from app.services.ai.tools import image_generation_tool as tools

    monkeypatch.setattr(tools, "load_image_generation_config", _fake_config)

    payload = await system.get_image_generation_status(agent_id=None, user={"user_id": 1})

    assert payload["system_enabled"] is enabled
    assert payload["edit_system_enabled"] is enabled
    assert set(payload) == {
        "system_enabled",
        "allowed_sizes",
        "default_size",
        "max_images",
        "edit_system_enabled",
        "edit_unavailable_reason",
    }
    dumped = str(payload)
    assert "sk-secret-value" not in dumped
    assert "internal-gw.example.com" not in dumped
    if not enabled:
        assert payload["edit_unavailable_reason"]


# ══════════════════ 保存配置：被跳过的项要说出来 ══════════════════


async def test_update_configs_surfaces_skipped_items(monkeypatch):
    """密钥框回显脱敏串时服务层会跳过写入，端点必须把它带给前端。"""

    async def _bulk(updates, changed_by="system"):
        return {
            "updated": ["image_model"],
            "skipped": [{"key": "image_api_key", "reason": "这是脱敏后的原密钥，未改动"}],
        }

    monkeypatch.setattr(system.ConfigService, "bulk_update", staticmethod(_bulk))

    request = system.ConfigUpdateRequest(
        updates=[
            system.ConfigItem(key="image_model", value="flux-dev"),
            system.ConfigItem(key="image_api_key", value="abc****wxyz"),
        ]
    )

    result = await system.update_system_configs(request=request, user=_ADMIN)

    assert result["status"] == "success"
    assert result["updated"] == ["image_model"]
    assert result["skipped"][0]["key"] == "image_api_key"
