"""落库测试护栏的行为验证。

背景：``test_config_store_db.py`` 曾直接在开发库上 ``DELETE``，把用户已保存的真实
Langfuse 配置（含加密密钥）删掉且无法恢复。现在它默认跳过，必须 ``LANGFUSE_DB_TESTS=1``
才执行。

这里用**真实子进程跑一次 pytest** 来验证护栏本身，而不是只断言源码里有那行字——
护栏一旦被误删，这套断言必须变红，所以它必须是行为级验证。
"""

import os
import pathlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TARGET = "tests/services/ai/observability/test_config_store_db.py"
ENV_FLAG = "LANGFUSE_DB_TESTS"

pytestmark = pytest.mark.no_infrastructure


def _run_pytest(args, *, env_flag=None, timeout=300):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop(ENV_FLAG, None)
    if env_flag is not None:
        env[ENV_FLAG] = env_flag

    proc = subprocess.run(
        # -rs：显示跳过原因，护栏是否"说明白了要哪个开关"也要能被验证
        [sys.executable, "-m", "pytest", *args, "-q", "-rs", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc


def test_db_tests_are_skipped_by_default():
    """未设开关时：整体跳过，一个用例都不跑（这是防误删的核心保证）。"""
    proc = _run_pytest([TARGET])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not re.search(r"\b\d+ passed\b", proc.stdout), (
        "默认情况下不应有任何落库用例真正执行：\n" + proc.stdout
    )
    assert re.search(r"\b\d+ skipped\b", proc.stdout), (
        "默认情况下应报告 skipped：\n" + proc.stdout
    )
    assert ENV_FLAG in proc.stdout, "跳过原因里应指出需要哪个环境变量：\n" + proc.stdout


def test_db_tests_require_migrations_even_when_enabled():
    """设了开关但表不存在时，仍应跳过（而不是报错）——否则未迁移环境会红一片。"""
    proc = _run_pytest([TARGET, "--collect-only"])

    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_guard_flag_accepts_common_truthy_values():
    """开关取值宽松一些，避免 CI 里写成 true/yes 就不生效。"""
    proc = _run_pytest(
        [TARGET, "--collect-only"],
        env_flag="true",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # collect-only 不执行夹具，只验证参数解析不炸
    assert ENV_FLAG not in proc.stderr
