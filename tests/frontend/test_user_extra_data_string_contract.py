"""前端契约：用户「预留扩展数据 (JSON)」的值必须一律按字符串保存。

回归背景：可视化编辑器曾对看起来像数字/布尔的值做类型推断（``Number(val)``），
第三方同步写入的雪花 ID ``"116573125898872954882"`` 因此被改写成
``116573125898872960000`` —— 精度丢失且类型由 string 变成 number，保存后不可还原。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 行为验证：静态断言看不出「长数字会不会被 Number 改写」，这里用 Node 真跑纯函数
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_extra_data_roundtrip_behaviour_via_node():
    script = Path("tests/frontend/user_extra_data_roundtrip.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "用户扩展数据序列化测试全部通过" in proc.stdout


# --------------------------------------------------------------------------- #
# 静态契约：编辑器不得再做类型推断，必须走共享纯函数
# --------------------------------------------------------------------------- #
def test_users_view_delegates_to_shared_helper():
    src = _source("frontend/src/views/Users.vue")
    assert "pairsToExtraDataJson(extraDataPairs.value)" in src
    assert "extraDataJsonToPairs(raw)" in src
    assert 'from "../utils/userExtraData"' in src


def test_users_view_no_longer_infers_number_or_boolean():
    src = _source("frontend/src/views/Users.vue")
    assert "Number(val)" not in src, "扩展数据的值不得再做数字推断"
    assert 'val.toLowerCase() === "true"' not in src, "扩展数据的值不得再做布尔推断"


def test_shared_helper_is_dependency_free():
    src = _source("frontend/src/utils/userExtraData.ts")

    for exported in (
        "export function pairsToExtraDataJson(",
        "export function extraDataJsonToPairs(",
        "export function isExtraDataJsonObject(",
    ):
        assert exported in src
    # 零运行时依赖：不得引入 axios 或 @/ 别名导入（否则 Node 无法直接加载）
    assert "from 'axios'" not in src
    assert "from '@/utils/axios'" not in src


def test_visual_editor_hints_string_values():
    src = _source("frontend/src/views/Users.vue")
    assert "值统一按字符串保存" in src
