"""分页页码输入跳转：把用户输入解析成一个合法的目标页。

背景：聊天日志 783 个会话、审计日志更多，只有上一页/下一页按钮时要翻几十页。
这里把「输入什么就跳到第几页」的解析收成纯函数，边界（空、非数字、越界、小数）
都由它兜住，页面里只负责绑定输入框和刷新。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/pagination.ts"
pytestmark = pytest.mark.no_infrastructure


def _run(expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(MODULE)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => {{ return await (async () => {{ {expression} }})(); }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_plain_number_jumps_to_that_page():
    assert _run("return api.parsePageInput('7', 1, 10)") == 7


def test_surrounding_spaces_are_tolerated():
    assert _run("return api.parsePageInput('  7 ', 1, 10)") == 7


def test_empty_or_garbage_keeps_current_page():
    """输入框清空或乱输时不能把人扔到第 1 页或 NaN 页。"""
    assert _run("return api.parsePageInput('', 4, 10)") == 4
    assert _run("return api.parsePageInput('abc', 4, 10)") == 4


def test_below_range_clamps_to_first_page():
    assert _run("return api.parsePageInput('0', 4, 10)") == 1
    assert _run("return api.parsePageInput('-5', 4, 10)") == 1


def test_beyond_range_clamps_to_last_page():
    """输错大数字时停在最后一页，而不是请求一个空页。"""
    assert _run("return api.parsePageInput('999', 4, 10)") == 10


def test_decimal_input_uses_the_integer_part():
    assert _run("return api.parsePageInput('3.7', 1, 10)") == 3


def test_total_pages_at_least_one():
    """没有数据时总页数是 1，跳转不能返回 0。"""
    assert _run("return api.parsePageInput('5', 1, 0)") == 1
