"""知识库配额：默认值、配置覆盖、超限文案。

文案必须带「现在多少 / 上限多少」，否则用户看到「超出配额」只能来问客服。
"""

from types import SimpleNamespace

import pytest

from app.services import knowledge_quota_service as mod
from app.services.knowledge_quota_service import KnowledgeQuotaService, QuotaExceeded

pytestmark = pytest.mark.no_infrastructure


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _Session:
    def __init__(self, count):
        self.count = count

    async def execute(self, stmt):
        return _Result(self.count)


class _Client:
    def __init__(self, total):
        self.total = total

    async def count_documents(self, dataset_id):
        return self.total


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(mod.ConfigService, "get", lambda key, default=None: _async(store.get(key, default)))
    yield store


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_defaults_when_config_missing(_patch_config):
    limits = await KnowledgeQuotaService.limits()

    assert limits["max_datasets_per_user"] == 50
    assert limits["max_documents_per_dataset"] == 2000


@pytest.mark.asyncio
async def test_config_overrides_defaults(_patch_config):
    _patch_config[mod.DATASET_LIMIT_KEY] = "3"
    _patch_config[mod.DOCUMENT_LIMIT_KEY] = "10"

    limits = await KnowledgeQuotaService.limits()

    assert limits["max_datasets_per_user"] == 3
    assert limits["max_documents_per_dataset"] == 10


@pytest.mark.asyncio
async def test_garbage_config_falls_back_instead_of_blocking(_patch_config):
    _patch_config[mod.DATASET_LIMIT_KEY] = "abc"

    limits = await KnowledgeQuotaService.limits()

    assert limits["max_datasets_per_user"] == 50


@pytest.mark.asyncio
async def test_create_rejected_at_limit_with_actionable_message(_patch_config):
    _patch_config[mod.DATASET_LIMIT_KEY] = "3"

    with pytest.raises(QuotaExceeded) as exc:
        await KnowledgeQuotaService.ensure_can_create_dataset(_Session(3), user_id=7)

    message = str(exc.value)
    assert "3 个" in message
    assert "删除" in message


@pytest.mark.asyncio
async def test_create_allowed_below_limit(_patch_config):
    _patch_config[mod.DATASET_LIMIT_KEY] = "3"

    await KnowledgeQuotaService.ensure_can_create_dataset(_Session(2), user_id=7)


@pytest.mark.asyncio
async def test_upload_rejected_when_exceeding_document_limit(_patch_config):
    _patch_config[mod.DOCUMENT_LIMIT_KEY] = "10"

    with pytest.raises(QuotaExceeded) as exc:
        await KnowledgeQuotaService.ensure_can_upload(_Client(total=10), "ds-1", incoming=1)

    assert "已有 10 个" in str(exc.value)
    assert "上限 10 个" in str(exc.value)


@pytest.mark.asyncio
async def test_upload_passes_when_engine_does_not_report_total(_patch_config):
    """拿不到 total 时不拦：宁可放过一次，也别让引擎不回 total 就完全传不了。"""
    _patch_config[mod.DOCUMENT_LIMIT_KEY] = "10"

    await KnowledgeQuotaService.ensure_can_upload(_Client(total=None), "ds-1", incoming=1)


@pytest.mark.asyncio
async def test_limit_zero_means_unlimited(_patch_config):
    _patch_config[mod.DOCUMENT_LIMIT_KEY] = "0"
    _patch_config[mod.DATASET_LIMIT_KEY] = "0"

    await KnowledgeQuotaService.ensure_can_upload(_Client(total=99999), "ds-1", incoming=5)
    await KnowledgeQuotaService.ensure_can_create_dataset(_Session(99999), user_id=7)


@pytest.mark.asyncio
async def test_limits_read_from_system_config(monkeypatch):
    """配额不是写死的：默认值只是兜底，配置项优先。"""
    from app.services import knowledge_quota_service as mod

    async def fake_get(key, default=None):
        return {
            "knowledge_quota_max_datasets_per_user": "5",
            "knowledge_quota_max_documents_per_dataset": "800",
        }.get(key, default)

    monkeypatch.setattr(mod.ConfigService, "get", staticmethod(fake_get))

    limits = await mod.KnowledgeQuotaService.limits()

    assert limits == {"max_datasets_per_user": 5, "max_documents_per_dataset": 800}


@pytest.mark.asyncio
async def test_limits_fall_back_to_defaults_when_config_missing_or_junk(monkeypatch):
    from app.services import knowledge_quota_service as mod

    async def fake_get(key, default=None):
        # 缺失返回 None；脏值（历史上直接改过库）也要走兜底
        return "abc" if key.endswith("per_user") else None

    monkeypatch.setattr(mod.ConfigService, "get", staticmethod(fake_get))

    limits = await mod.KnowledgeQuotaService.limits()

    assert limits["max_datasets_per_user"] == mod.DEFAULT_DATASET_LIMIT
    assert limits["max_documents_per_dataset"] == mod.DEFAULT_DOCUMENT_LIMIT


def test_non_integer_quota_config_is_rejected_on_save():
    """保存时拦住，别让管理员以为改成功了（读取侧是静默回退）。"""
    from app.services.config_service import validate_config_update

    validate_config_update("knowledge_quota_max_datasets_per_user", "50")

    with pytest.raises(ValueError):
        validate_config_update("knowledge_quota_max_datasets_per_user", "abc")
