"""系统配置页要能开关资源广场的数据集目录（三个注册点缺一不可）。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"
# 配置种子并进了资源广场主迁移：同一个特性一次迁移到位，
# 部署时少一个「忘了执行」的机会
MYSQL = ROOT / "db-prod/V192-resource_square_and_metadata_audit.sql"
PG = ROOT / "db-prod-pg/V92-resource_square_and_metadata_audit.sql"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_switch_is_registered_in_all_three_places():
    page = _read(CONFIG)
    # 1) 简短说明
    assert "resource_square_show_datasets:" in page
    # 2) general 分类排序
    assert "'resource_square_show_datasets'," in page
    # 3) 布尔开关渲染列表
    assert "'resource_square_show_datasets'" in page


def test_migration_seeds_config_row_for_both_databases():
    mysql = _read(MYSQL)
    assert "INSERT IGNORE INTO `system_configs`" in mysql
    assert "resource_square_show_datasets" in mysql
    assert "'true'" in mysql

    pg = _read(PG)
    assert 'ON CONFLICT ("key") DO NOTHING' in pg
    assert "resource_square_show_datasets" in pg
