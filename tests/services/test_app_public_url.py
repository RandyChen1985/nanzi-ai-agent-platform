"""对外访问地址（app_public_url）的统一解析口径。

历史上系统配置项叫 ``download_url_prefix``，只覆盖下载/重置链接；MCP、报表通知、
网页预览各读各的环境变量。本文件钉住统一后的口径：

    系统配置 app_public_url  →  环境变量 APP_PUBLIC_URL  →  空串（相对地址）
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
MYSQL_MIG = ROOT / "db-prod/V183-rename-download-url-prefix-to-app-public-url.sql"
PG_MIG = ROOT / "db-prod-pg/V84-rename-download-url-prefix-to-app-public-url.sql"


@pytest.mark.asyncio
async def test_prefers_system_config_over_environment(monkeypatch):
    from app.services import app_public_url_service as module

    async def fake_get(key, default=None):
        assert key == "app_public_url"
        return "https://configured.example.com/"

    monkeypatch.setattr(module.ConfigService, "get", fake_get)
    monkeypatch.setattr(module.settings, "APP_PUBLIC_URL", "https://environment.example.com")

    assert await module.get_app_public_url() == "https://configured.example.com"


@pytest.mark.asyncio
async def test_uses_environment_when_system_config_is_blank(monkeypatch):
    from app.services import app_public_url_service as module

    async def fake_get(key, default=None):
        return "   "

    monkeypatch.setattr(module.ConfigService, "get", fake_get)
    monkeypatch.setattr(module.settings, "APP_PUBLIC_URL", "https://environment.example.com/")

    assert await module.get_app_public_url() == "https://environment.example.com"


@pytest.mark.asyncio
async def test_returns_empty_string_when_neither_is_configured(monkeypatch):
    from app.services import app_public_url_service as module

    async def fake_get(key, default=None):
        return None

    monkeypatch.setattr(module.ConfigService, "get", fake_get)
    monkeypatch.setattr(module.settings, "APP_PUBLIC_URL", None)

    assert await module.get_app_public_url() == ""


@pytest.mark.asyncio
async def test_falls_back_to_environment_when_config_read_fails(monkeypatch):
    """配置表不可用（迁移未跑/DB 抖动）时不能让下载、重置链接一起挂掉。"""

    from app.services import app_public_url_service as module

    async def boom(key, default=None):
        raise RuntimeError("system_configs is unavailable")

    monkeypatch.setattr(module.ConfigService, "get", boom)
    monkeypatch.setattr(module.settings, "APP_PUBLIC_URL", "https://environment.example.com")

    assert await module.get_app_public_url() == "https://environment.example.com"


@pytest.mark.asyncio
async def test_normalizes_whitespace_and_trailing_slashes(monkeypatch):
    from app.services import app_public_url_service as module

    async def fake_get(key, default=None):
        return "  https://configured.example.com/base/  "

    monkeypatch.setattr(module.ConfigService, "get", fake_get)

    assert await module.get_app_public_url() == "https://configured.example.com/base"


def test_migrations_rename_the_legacy_key_in_both_dialects():
    assert MYSQL_MIG.exists(), "缺少 MySQL 迁移脚本"
    assert PG_MIG.exists(), "缺少 PostgreSQL 迁移脚本"

    for path in (MYSQL_MIG, PG_MIG):
        source = path.read_text(encoding="utf-8")
        assert "system_configs" in source
        assert "download_url_prefix" in source
        assert "app_public_url" in source
        # 是「改名」而不是「再插一条」，否则旧值会被静默丢弃
        assert "UPDATE" in source.upper()
        assert "description" in source


def test_no_production_code_still_reads_the_legacy_key():
    """防止漏改：改名之后 app/ 与前端源码里不应该再有旧口径的读取点。

    允许注释/文档里提到历史键名（说明来源），所以后端只查真正的读取调用；
    前端则查裸键名，因为配置项的字面量就是读取点。
    """

    offenders: list[str] = []

    for path in (ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if (
            "get_download_url_prefix" in source
            or 'ConfigService.get("download_url_prefix")' in source
            or "ConfigService.get('download_url_prefix')" in source
        ):
            offenders.append(str(path.relative_to(ROOT)))

    for path in (ROOT / "frontend/src").rglob("*"):
        if path.suffix not in {".vue", ".ts"} or not path.is_file():
            continue
        if "download_url_prefix" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
