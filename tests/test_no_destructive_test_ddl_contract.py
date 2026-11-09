"""测试不得对业务库执行任何 DDL（契约）。

测试用的 engine 就是 ``app.core.orm.engine``，也就是 ``.env`` 里的
``MYSQL_DB``（本机是 ``aiagent``）——项目没有独立测试库，也没有
``TEST_DATABASE_URL`` 之类的覆盖开关。所以测试里的 ``create_all`` /
``drop_all`` 动的都是**开发/业务库的真表**，约定是：

* 表结构只由迁移脚本创建，测试**不建表、不删表**；
* 表不存在时让测试直接报 1146/1142，提醒人先去跑迁移，而不是被测试悄悄补一张
  结构不对的表。

两条都踩过坑：

1. ``tests/api/portal/test_secret_endpoints.py`` 曾在 module 级 teardown 里
   ``drop_all`` 五张 ``sys_secret_*`` 表，造成「执行 V209 → 表建好 → 跑一遍
   测试 → 接口 503 报 sys_secrets 不存在」，看起来像迁移自己丢了。
2. 四个模块原来的 setup 用 ``create_all(checkfirst=True)`` 兜底建表：表被删掉后
   它们会建出一张**缺索引、缺 COMMENT 的裸表**，而 V209 写的是
   ``CREATE TABLE IF NOT EXISTS``，之后再执行迁移也补不回索引——「表在」不等于
   「表对」。

用 AST 而不是正则：注释和字符串里出现 ``create_all`` / ``drop_all`` 字样是允许的
（本来就是解释为什么不能这么做），只有真正的调用才算违规。
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

TESTS_DIR = Path(__file__).resolve().parent

DDL_CALLS = ("create_all", "drop_all")

SECRET_TEST_MODULES = [
    "core/test_secret_center.py",
    "core/test_secret_injection.py",
    "core/test_secret_resolver.py",
    "api/portal/test_secret_endpoints.py",
]


def _iter_test_files():
    return sorted(TESTS_DIR.rglob("test_*.py"))


def _ddl_linenos(path: Path, names=DDL_CALLS):
    """返回该文件里 ``xxx.create_all(...)`` / ``xxx.drop_all(...)`` 调用的行号。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in names:
            hits.append(node.lineno)
    return hits


def test_no_test_drops_business_tables():
    offenders = {}
    for path in _iter_test_files():
        linenos = _ddl_linenos(path, names=("drop_all",))
        if linenos:
            offenders[str(path.relative_to(TESTS_DIR.parent))] = linenos

    assert not offenders, (
        "测试引擎连的是业务库（.env 的 MYSQL_DB），禁止在测试里 drop_all："
        "它会真的删掉开发库的表，之后接口就会 503 报「表不存在」。"
        f"违规位置（文件 -> 行号）: {offenders}。"
        "清数据请改用 DELETE，表结构请交给迁移脚本管理。"
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


def test_create_all_only_on_isolated_sqlite():
    """``create_all`` 只允许建在一次性内存库上（``sqlite://``）。

    不是所有 ``create_all`` 都有害：``tests/test_metadata_relationship_count_batch.py``
    用 ``create_engine("sqlite://")`` 建临时表来验批量 SQL，进程结束即消失，与业务库
    无关，这种是正确用法，不该被清掉。

    真正要防的是「用 ``app.core.orm.engine``（业务库）调 ``create_all``」——那会建出
    缺索引/缺 COMMENT 的裸表，且迁移脚本的 ``IF NOT EXISTS`` 补不回来。所以规则是：
    出现 ``create_all`` 的测试文件必须自带 SQLite 隔离库。
    """
    offenders = {}
    for path in _iter_test_files():
        linenos = _ddl_linenos(path, names=("create_all",))
        if not linenos:
            continue
        source = path.read_text(encoding="utf-8").lower()
        if "sqlite" not in source:
            offenders[str(path.relative_to(TESTS_DIR.parent))] = linenos

    assert not offenders, (
        "测试里的 create_all 只能建在内存 SQLite（create_engine(\"sqlite://\")）上；"
        "用业务库 engine 建表会留下缺索引的裸表，且之后跑迁移也补不齐。"
        f"违规位置（文件 -> 行号）: {offenders}"
    )


def test_secret_migration_is_referenced_by_tests_contract():
    """双保险：迁移脚本必须存在，避免「测试不建表」之后没有任何建表来源。"""
    root = TESTS_DIR.parent
    for rel in ("db-prod/V209-add-credential-center.sql", "db-prod-pg/V109-add-credential-center.sql"):
        assert (root / rel).is_file(), f"缺少凭据中心迁移脚本: {rel}"
