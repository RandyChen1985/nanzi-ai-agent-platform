"""技能全文预载的体积上限必须与上下文侧裁剪上限对齐。

预载是直接写进 system prompt 的，不经过工具结果那条 ``truncate_for_context``
管线。若允许配置到 262144 字符，超长 SKILL.md 会整段占满上下文，而模型对它的
可见度并不比 64Ki 更高。
"""

from __future__ import annotations

import pytest

from app.services.ai.skills.injector import SkillInjector

pytestmark = pytest.mark.no_infrastructure

CONTEXT_OUTPUT_LIMIT = 64 * 1024


@pytest.mark.asyncio
async def test_full_load_max_bytes_is_clamped_to_context_limit(monkeypatch):
    async def fake_get(key, default=None):
        return {
            "skill_auto_full_load_enabled": "true",
            "skill_auto_full_load_min_score": "0.75",
            "skill_auto_full_load_max_count": "1",
            "skill_auto_full_load_max_bytes": "262144",
        }.get(key, default)

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    policy = await SkillInjector.resolve_skill_full_load_policy()

    assert policy["max_bytes"] == CONTEXT_OUTPUT_LIMIT


@pytest.mark.asyncio
async def test_full_load_max_bytes_respects_smaller_configured_value(monkeypatch):
    async def fake_get(key, default=None):
        return {
            "skill_auto_full_load_enabled": "true",
            "skill_auto_full_load_min_score": "0.75",
            "skill_auto_full_load_max_count": "1",
            "skill_auto_full_load_max_bytes": "32768",
        }.get(key, default)

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    policy = await SkillInjector.resolve_skill_full_load_policy()

    assert policy["max_bytes"] == 32768
