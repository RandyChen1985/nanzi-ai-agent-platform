"""系统设置里「测试生成 / 测试改图 / 拉模型列表」的配置覆盖与校验。

设置页有一个特殊诉求：**改了还没保存就要能先试通**。所以测试接口不只收 prompt，
还要收开关、白名单、前缀、超时这些当前输入值——不然「点测试」验的是库里那一套，
管理员会得出「改了没用」的错误结论。

这里测的是这条覆盖链路本身，以及两处容易踩的坑：
- 白名单被清空在后端等于「不限尺寸」，必须在写入时拦住；
- 尺寸写法 ``1024*1024`` 不归一的话，白名单比较会永远对不上。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.ai.tools import image_generation_tool as tools
from app.services.config_service import validate_config_update

pytestmark = pytest.mark.no_infrastructure

_CONFIG = {
    "image_generation_enabled": "0",
    "image_api_format": "openai_images",
    "image_api_base_url": "https://api.example.com/v1",
    "image_api_key": "sk-test",
    "image_model": "test-image-model",
    "image_default_size": "1024x1024",
    "image_allowed_sizes": "1024x1024,1664x928",
    "image_timeout_seconds": "30",
    "image_max_images_per_call": "1",
    "image_prompt_prefix": "",
}


@pytest.fixture
def patch_config(monkeypatch):
    async def _get_many(defaults):
        return {key: _CONFIG.get(key, default) for key, default in defaults.items()}

    monkeypatch.setattr(tools.ConfigService, "get_many", staticmethod(_get_many))


def _patch_http(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(tools.httpx, "AsyncClient", factory)


# ══════════════════ 覆盖项 ══════════════════


def test_overrides_do_not_touch_the_saved_config():
    base = {"enabled": False, "allowed_sizes": ["1024x1024"], "prompt_prefix": "", "timeout_seconds": 30.0}

    merged = tools._apply_preview_overrides(
        base,
        enabled=True,
        allowed_sizes=["512x512"],
        prompt_prefix="watercolor",
        timeout_seconds=120,
    )

    assert merged["enabled"] is True
    assert merged["allowed_sizes"] == ["512x512"]
    assert merged["prompt_prefix"] == "watercolor"
    assert merged["timeout_seconds"] == 120.0
    # 传进去的那份不能被就地改掉：它可能是别处缓存的配置对象。
    assert base["enabled"] is False
    assert base["allowed_sizes"] == ["1024x1024"]


def test_override_size_text_is_normalized():
    """白名单里写成 1024*1024 也能用：工具侧归一成 1024x1024 去比较。"""
    merged = tools._apply_preview_overrides(
        {"allowed_sizes": []}, allowed_sizes=["1024*1024", "1664X928"]
    )
    assert merged["allowed_sizes"] == ["1024x1024", "1664x928"]


def test_override_ignores_empty_and_nonpositive_timeout():
    base = {"timeout_seconds": 30.0}
    assert tools._apply_preview_overrides(base, timeout_seconds=None)["timeout_seconds"] == 30.0
    assert tools._apply_preview_overrides(base, timeout_seconds=0)["timeout_seconds"] == 30.0
    assert tools._apply_preview_overrides(base, timeout_seconds=-5)["timeout_seconds"] == 30.0


async def test_preview_runs_even_when_saved_switch_is_off(patch_config, monkeypatch):
    """库里开关是关的，但设置页刚把开关拨开还没保存——测试就该按「开着」跑。

    否则报的是「请先打开开关」，而管理员眼前的开关明明是开的，只会以为页面坏了。
    """
    payload = {
        "id": "chatcmpl-1",
        "model": "test-image-model",
        "data": [
            {
                "b64_json": (
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
                    "IQAAAABJRU5ErkJggg=="
                )
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    _patch_http(monkeypatch, handler)

    result = await tools.preview_image_generation(
        prompt="一只猫",
        enabled=True,
        allowed_sizes=["1024x1024"],
        timeout_seconds=45,
    )

    assert result["api_format"] == "openai_images"
    assert result["size"] == "1024x1024"
    assert result["bytes"] > 0


async def test_preview_still_blocked_when_override_says_off(patch_config):
    with pytest.raises(ValueError, match="总开关未开启"):
        await tools.preview_image_generation(prompt="一只猫", enabled=False)


async def test_preview_validates_size_against_overridden_whitelist(patch_config, monkeypatch):
    """白名单被临时改成只含 512x512 时，传 1024x1024 就应该被拒——而不是按库里的白名单放行。"""
    with pytest.raises(ValueError, match="不在允许范围内"):
        await tools.preview_image_generation(
            prompt="一只猫",
            size="1024x1024",
            enabled=True,
            allowed_sizes=["512x512"],
        )


# ══════════════════ 白名单校验 ══════════════════


@pytest.mark.parametrize("size", ["1024x1024", "1024*1024", "1664 X 928", "512×512"])
def test_allowed_sizes_accepts_common_separators(size):
    validate_config_update("image_allowed_sizes", size)


@pytest.mark.parametrize("value", ["", "   ", ",,,", "1024", "big", "1024x"])
def test_allowed_sizes_rejects_empty_and_malformed(value):
    """空白名单在后端语义是「不限尺寸」，比只留一项更宽松——保存这一步就要拦住。"""
    with pytest.raises(ValueError):
        validate_config_update("image_allowed_sizes", value)


def test_allowed_sizes_reports_the_bad_item():
    with pytest.raises(ValueError, match="huge"):
        validate_config_update("image_allowed_sizes", "1024x1024,huge")


# ══════════════════ 模型列表 ══════════════════


async def test_list_models_falls_back_to_saved_key_when_masked(patch_config, monkeypatch):
    """设置页回显的是脱敏串，直接拿去请求必然 401——要回落库里的真实密钥。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"data": [{"id": "flux-dev"}]})

    _patch_http(monkeypatch, handler)

    result = await tools.list_image_models(base_url=None, api_key="abc****wxyz")

    assert seen == ["Bearer sk-test"]
    assert result["models"] == ["flux-dev"]
