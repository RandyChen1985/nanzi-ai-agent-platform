"""模型管理「上下文与输出」快捷档位契约。

需求来源（用户）：截图指出输入上下文的快捷档位到 256K 就断了、输出到 64K 就断了，
右边空着一大片；要求补 512K / 1M 之类，输入与输出两列档位一致对齐，
按钮适当缩小避免窄弹窗里显示不下 / 换行错位。

锁定三件事：

1. 两列共用**同一组**档位数组（不再各自一套），且覆盖到 1M；
2. 档位用固定列栅格排布，窄弹窗（max-w-xl）下两列逐行对齐、不靠换行兜底；
3. `formatTokenSize` 对 2^20 走 M 分支——否则 1048576 会显示成 `1024K`。
"""

from pathlib import Path

import json
import re
import subprocess

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
MODEL_REGISTRY_MODULE = "frontend/src/components/system/ModelRegistry.vue"
MODEL_REGISTRY = (ROOT / MODEL_REGISTRY_MODULE).read_text(encoding="utf-8")

EXPECTED_PRESETS = [8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576]


def _format_token_sizes(values: list[int]) -> list[str]:
    """直接跑组件里的真实 `formatTokenSize`（从 SFC 源码里取函数体，用 node 求值）。"""
    match = re.search(r"const formatTokenSize = \(value\?: number \| null\) => \{.*?\n\}", MODEL_REGISTRY, re.S)
    assert match, "ModelRegistry.vue 里找不到 formatTokenSize 实现"
    script = f"""
const ts = require('./frontend/node_modules/typescript');
const fnSource = {json.dumps(match.group(0))};
const code = ts.transpileModule('export ' + fnSource, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }},
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', code)(moduleRef, moduleRef.exports);
const values = {json.dumps(values)};
process.stdout.write(JSON.stringify(values.map((n) => moduleRef.exports.formatTokenSize(n))));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_context_and_output_share_the_same_preset_ladder():
    """输入上下文与输出上限必须是同一组档位，且补到 512K / 1M。"""
    assert f"const tokenSizePresets = {EXPECTED_PRESETS}" in MODEL_REGISTRY
    assert MODEL_REGISTRY.count('v-for="size in tokenSizePresets"') == 2
    # 旧的「各一套」数组必须消失，避免两列再次跑偏
    assert "contextSizePresets" not in MODEL_REGISTRY
    assert "outputTokenPresets" not in MODEL_REGISTRY


def test_preset_row_uses_fixed_columns_for_alignment():
    """固定 4 列栅格：两列档位逐行对齐，窄弹窗下也不会挤压换行。"""
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in MODEL_REGISTRY
    assert ".token-preset-row {" in MODEL_REGISTRY
    assert "font-size: 0.6875rem;" in MODEL_REGISTRY
    assert "white-space: nowrap;" in MODEL_REGISTRY


def test_format_token_size_renders_mega_scale():
    """1M 档位显示 1M 而不是 1024K；K 档与非整档保持原语义。"""
    assert _format_token_sizes([8192, 131072, 262144, 524288, 1048576, 2097152]) == [
        "8K",
        "128K",
        "256K",
        "512K",
        "1M",
        "2M",
    ]
    assert _format_token_sizes([1500000, 0, None]) == ["1500000", "", ""]
