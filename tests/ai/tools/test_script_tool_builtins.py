"""内置脚本工具：脚本行为 + 「代码定义 ↔ 迁移 SQL」对账。

对账的必要性：内置脚本源码在代码里，而 ``sys_script_tools`` 里的行由迁移 seed。
两边一旦漂移，就会出现「页面描述与真实脚本不一致」这种最难排查的问题。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.ai.tools.script_tool_builtins import (
    BUILTIN_SCRIPT_TOOLS,
    get_builtin_script_tool,
)

ROOT = Path(__file__).resolve().parents[3]
MYSQL_MIGRATION = ROOT / "db-prod/V200-add-script-tools.sql"
PG_MIGRATION = ROOT / "db-prod-pg/V100-add-script-tools.sql"


def run_script(name: str, params: dict, cwd: Path) -> subprocess.CompletedProcess:
    builtin = get_builtin_script_tool(name)
    assert builtin is not None, name
    script = ROOT / "app/services/ai/tools/builtin_scripts" / builtin.script_file
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(params),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=30,
    )


# --------------------------------------------------------------------------- #
# 行为
# --------------------------------------------------------------------------- #
def test_file_to_base64_roundtrip(tmp_path):
    (tmp_path / "a.txt").write_text("hello nanzi", encoding="utf-8")
    completed = run_script("file_to_base64", {"path": "a.txt"}, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["size"] == 11
    assert payload["encoding"] == "base64"
    assert payload["data"]


def test_file_to_base64_keeps_legacy_large_max_bytes_working_for_small_file(tmp_path):
    """旧 schema 的 10MB 默认值可能仍在数据库里；小文件不能被它带崩。"""
    (tmp_path / "a.txt").write_text("hello nanzi", encoding="utf-8")
    completed = run_script(
        "file_to_base64", {"path": "a.txt", "max_bytes": 10 * 1024 * 1024}, tmp_path
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["size"] == 11


def test_file_to_base64_rejects_file_above_hard_cap(tmp_path):
    """超过 48KB 时宁可明确失败，也不能返回被 stdout 截断的非法 JSON。"""
    (tmp_path / "big.bin").write_bytes(b"x" * (48 * 1024 + 1))
    completed = run_script(
        "file_to_base64", {"path": "big.bin", "max_bytes": 10 * 1024 * 1024}, tmp_path
    )
    assert completed.returncode != 0
    assert "48KB" in completed.stderr
    assert "发布生成文件" in completed.stderr


def test_file_to_base64_rejects_path_escape(tmp_path):
    completed = run_script("file_to_base64", {"path": "../etc/passwd"}, tmp_path)
    assert completed.returncode != 0
    assert "越界" in completed.stderr


def test_base64_to_file_writes_artifact(tmp_path):
    completed = run_script("base64_to_file", {"data": "aGVsbG8=", "filename": "out.bin"}, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["artifacts"] == ["out.bin"]
    assert (tmp_path / "out.bin").read_bytes() == b"hello"


def test_base64_to_file_refuses_overwrite_by_default(tmp_path):
    (tmp_path / "out.bin").write_bytes(b"old")
    completed = run_script("base64_to_file", {"data": "aGVsbG8=", "filename": "out.bin"}, tmp_path)
    assert completed.returncode != 0
    assert "已存在" in completed.stderr


def test_base64_to_file_string_false_does_not_overwrite(tmp_path):
    """模型有时把布尔参数写成字符串 "false"；不能按 Python 真值误覆盖文件。"""
    (tmp_path / "out.bin").write_bytes(b"old")
    completed = run_script(
        "base64_to_file",
        {"data": "aGVsbG8=", "filename": "out.bin", "overwrite": "false"},
        tmp_path,
    )
    assert completed.returncode != 0
    assert "已存在" in completed.stderr
    assert (tmp_path / "out.bin").read_bytes() == b"old"


def test_base64_to_file_rejects_path_in_filename(tmp_path):
    completed = run_script(
        "base64_to_file", {"data": "aGVsbG8=", "filename": "../escape.bin"}, tmp_path
    )
    assert completed.returncode != 0


def test_file_digest_matches_hashlib(tmp_path):
    import hashlib

    (tmp_path / "a.txt").write_bytes(b"abc")
    completed = run_script("file_digest", {"target": "a.txt", "algorithm": "sha256"}, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["digest"] == hashlib.sha256(b"abc").hexdigest()
    assert payload["source"] == "file"


def test_file_digest_string_false_is_not_treated_as_text(tmp_path):
    completed = run_script(
        "file_digest",
        {"target": "missing.txt", "algorithm": "sha256", "is_text": "false"},
        tmp_path,
    )
    assert completed.returncode != 0
    assert "文件不存在" in completed.stderr


def test_text_regex_extract_group_and_limit(tmp_path):
    params = {
        "text": "订单 A100，订单 B200，订单 C300",
        "pattern": r"订单 ([A-Z]\d+)",
        "group": 1,
        "limit": 2,
    }
    completed = run_script("text_regex_extract", params, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["matches"] == ["A100", "B200"]
    assert payload["truncated"] is True


def test_text_redact_masks_all_types(tmp_path):
    text = "13812345678 zhang.san@example.com 11010119900307123X 6222021234567890123"
    completed = run_script("text_redact", {"text": text}, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert "138****5678" in payload["text"]
    assert "z***@example.com" in payload["text"]
    assert payload["redacted"]["id_card"] == 1
    assert payload["redacted"]["bank_card"] == 1
    assert "13812345678" not in payload["text"]


# --------------------------------------------------------------------------- #
# 定义与迁移对账
# --------------------------------------------------------------------------- #
def _seed_names(sql: str) -> set[str]:
    return set(re.findall(r"'builtin-script-[a-z0-9-]+',\s*\n\s*'([a-z0-9_]+)'", sql))


@pytest.mark.parametrize("path", [MYSQL_MIGRATION, PG_MIGRATION])
def test_migration_seed_matches_code_definitions(path):
    sql = path.read_text(encoding="utf-8")
    assert _seed_names(sql) == {tool.name for tool in BUILTIN_SCRIPT_TOOLS}


@pytest.mark.parametrize("path", [MYSQL_MIGRATION, PG_MIGRATION])
def test_migration_keeps_builtin_rows_codeless(path):
    """内置源码在代码里：迁移里的 code 列必须是 NULL，否则会出现两份源头。"""
    sql = path.read_text(encoding="utf-8")
    assert "'builtin', 'python', NULL" in sql


def test_every_definition_has_a_script_file():
    for tool in BUILTIN_SCRIPT_TOOLS:
        script = ROOT / "app/services/ai/tools/builtin_scripts" / tool.script_file
        assert script.is_file(), f"{tool.name} 缺少脚本文件 {tool.script_file}"
        assert tool.code.strip()


def test_requirements_are_empty_for_builtins():
    """第一版内置脚本只用标准库：沙箱镜像不用改就能跑通整条链路。"""
    for tool in BUILTIN_SCRIPT_TOOLS:
        assert tool.requirements == ()


def test_mysql_migration_splits_into_expected_statements():
    """描述里的 ``data:<mime>;base64,`` 含分号——不能被 SQL 拆分器斩断。

    这条断言盯的是「迁移在真实部署脚本下是否可执行」：用仓库自带的拆分器跑一遍，
    语句数必须正好是「建表」+「seed 内置工具」两条。PG 走 psql 整文件执行，不适用。
    """
    import importlib.util

    module_path = ROOT / "db-prod/apply_sql.py"
    spec = importlib.util.spec_from_file_location("db_prod_apply_sql", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    statements = [
        item
        for item in module.split_sql_statements(MYSQL_MIGRATION.read_text(encoding="utf-8"))
        if item.strip()
    ]
    assert len(statements) == 2, [item[:60] for item in statements]
    assert statements[0].upper().lstrip("-\n ").startswith("CREATE TABLE")
    assert "INSERT" in statements[1].upper()
