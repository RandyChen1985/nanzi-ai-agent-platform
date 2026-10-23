"""对外访问地址配置项的契约：系统配置 app_public_url → APP_PUBLIC_URL → 相对地址。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
MYSQL_MIG = ROOT / "db-prod/V183-rename-download-url-prefix-to-app-public-url.sql"
PG_MIG = ROOT / "db-prod-pg/V84-rename-download-url-prefix-to-app-public-url.sql"
SYSTEM_CONFIG_VUE = ROOT / "frontend/src/views/SystemConfig.vue"
DEPLOYMENT_CHECKLIST_VUE = ROOT / "frontend/src/components/system/DeploymentChecklist.vue"
SERVICE = ROOT / "app/services/app_public_url_service.py"


def test_rename_is_shipped_as_a_migration_in_both_dialects():
    for path in (MYSQL_MIG, PG_MIG):
        assert path.exists(), path
        source = path.read_text(encoding="utf-8")
        assert "system_configs" in source
        assert "app_public_url" in source
        assert "download_url_prefix" in source


def test_single_resolver_implements_the_documented_precedence():
    source = SERVICE.read_text(encoding="utf-8")

    assert 'APP_PUBLIC_URL_CONFIG_KEY = "app_public_url"' in source
    assert "async def get_app_public_url()" in source
    assert "ConfigService.get(APP_PUBLIC_URL_CONFIG_KEY)" in source
    assert "settings.APP_PUBLIC_URL" in source


def test_system_config_page_uses_the_renamed_key():
    source = SYSTEM_CONFIG_VUE.read_text(encoding="utf-8")

    assert "app_public_url: '" in source
    assert "item.key === 'app_public_url'" in source
    assert "download_url_prefix" not in source


def test_system_config_copy_explains_fallback_and_mcp_usage():
    source = SYSTEM_CONFIG_VUE.read_text(encoding="utf-8")
    idx = source.index("app_public_url: '")
    window = source[idx : idx + 400]

    assert "APP_PUBLIC_URL" in window
    assert "MCP" in window
    assert "重置" in window


def test_deployment_checklist_points_at_the_renamed_key():
    source = DEPLOYMENT_CHECKLIST_VUE.read_text(encoding="utf-8")

    assert "key: 'app_public_url'" in source
    assert "download_url_prefix" not in source
    assert "MCP" in source
