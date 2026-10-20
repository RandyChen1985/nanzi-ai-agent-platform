"""Vue 模板语法级校验：把 `vite build` 才会暴露的错误提前到 pytest。

背景：给按钮加 `v-if` 时，某个 `<button>` 上原本已有一个 `v-if`，于是出现
`Duplicate attribute`。`vue-tsc --noEmit` 只做类型检查，不报这类模板语法错误，
直到 `vite build` 才失败。这里用 `@vue/compiler-sfc` 把全部 `.vue` 模板编译一遍。

覆盖范围：`frontend/src/**/*.vue`（node_modules / dist 除外）。
已知忽略：模板里内联 `<script>` 的 side-effect 提示（AgentDebug.vue）。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests/frontend/compile_vue_templates.cjs"

pytestmark = pytest.mark.no_infrastructure


def test_all_vue_templates_compile():
    completed = subprocess.run(
        ["node", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, (
        f"模板编译失败：{completed.stdout}\n{completed.stderr}"
    )

    payload = json.loads(completed.stdout)
    assert payload["problems"] == [], "存在模板语法错误：" + "；".join(payload["problems"])
    assert payload["total"] >= 200, "扫描到的 .vue 数量异常，检查路径是否写错"
