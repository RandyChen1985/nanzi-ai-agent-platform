"""契约：只允许在本用例新建的临时库中建表。

仍使用应用数据库连接的测试依赖迁移脚本准备表结构，不能建表或删表。
早期凭据测试曾在清理时删掉开发库里的凭据表。现在检查直接调用和
``connection.run_sync(Base.metadata.create_all)`` 这类间接引用。
"""

import ast
from pathlib import Path

import pytest

from tests.temporary_database import init_infrastructure  # noqa: F401

pytestmark = pytest.mark.no_infrastructure

TESTS_DIR = Path(__file__).resolve().parent

DDL_CALLS = ("create_all", "drop_all")

SECRET_TEST_MODULES = [
    "core/test_secret_center.py",
    "core/test_secret_injection.py",
    "core/test_secret_resolver.py",
    "api/portal/test_secret_endpoints.py",
]


def _iter_test_sources():
    paths = set(TESTS_DIR.rglob("test_*.py"))
    paths.update(TESTS_DIR.rglob("conftest.py"))
    paths.add(TESTS_DIR / "temporary_database.py")
    return sorted(paths)


def _ddl_linenos(path: Path, names=DDL_CALLS):
    """定位方法引用，覆盖直接调用和传给 run_sync 的回调。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sorted({
        node.lineno for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr in names)
        or (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names)
    })


def test_no_test_drops_business_tables():
    offenders = {}
    for path in _iter_test_sources():
        linenos = _ddl_linenos(path, names=("drop_all",))
        if linenos:
            offenders[str(path.relative_to(TESTS_DIR.parent))] = linenos

    assert not offenders, (
        "测试禁止调用 drop_all，临时库由 temporary_database 统一清理。"
        f"违规位置（文件 -> 行号）: {offenders}。"
        "使用应用数据库的测试应清理本用例写入的记录。"
    )


def test_secret_test_modules_do_no_ddl():
    """凭据中心四个模块共用 ``sys_secret_*`` 五张表，一律不让它们自己建/删表。

    表由 ``db-prod/V209-add-credential-center.sql``（PG 为
    ``db-prod-pg/V109-add-credential-center.sql``）创建，执行时机由人控制。
    模块里只允许 ``DELETE`` 做数据隔离。
    """
    missing = [rel for rel in SECRET_TEST_MODULES if not (TESTS_DIR / rel).is_file()]
    assert not missing, f"凭据中心测试模块缺失: {missing}"

    offenders = {}
    for rel in SECRET_TEST_MODULES:
        linenos = _ddl_linenos(TESTS_DIR / rel)
        if linenos:
            offenders[rel] = linenos

    assert not offenders, (
        "凭据中心测试不得建表或删表：表结构由 V209/V210 迁移脚本负责，"
        "测试只做 DELETE 数据隔离。"
        f"违规位置（文件 -> 行号）: {offenders}"
    )


def test_create_all_is_centralized_in_the_temporary_database_fixture():
    offenders = {}
    for path in _iter_test_sources():
        if path == TESTS_DIR / "temporary_database.py":
            continue
        linenos = _ddl_linenos(path, names=("create_all",))
        if linenos:
            offenders[str(path.relative_to(TESTS_DIR.parent))] = linenos

    assert not offenders, (
        "需要建表的测试应使用 temporary_database，确保目标是本用例新建的临时库。"
        f"违规位置（文件 -> 行号）: {offenders}"
    )


def test_secret_migration_is_referenced_by_tests_contract():
    """双保险：迁移脚本必须存在，避免「测试不建表」之后没有任何建表来源。"""
    root = TESTS_DIR.parent
    for rel in ("db-prod/V209-add-credential-center.sql", "db-prod-pg/V109-add-credential-center.sql"):
        assert (root / rel).is_file(), f"缺少凭据中心迁移脚本: {rel}"
