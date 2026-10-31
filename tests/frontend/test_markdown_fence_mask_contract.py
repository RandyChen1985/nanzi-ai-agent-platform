"""契约：代码块分段前先把围栏内的反引号屏蔽，避免非贪婪正则提前闭合。

背景：`MessageRenderer` 用一个非贪婪正则找 ``` 代码块，遇到「代码内容本身含三反引号」
（例如演示如何包裹含反引号的代码）会提前闭合，把剩余内容当成正文、把下一段标题
当成语言名——表现为代码卡片内容被腰斩、后续段落错乱。

做法：跑正则前把「围栏内部行」的反引号等长替换成占位符（长度不变，`match.index`
仍对应原文），匹配到的捕获组再还原。围栏边界按 CommonMark 规则判定（同字符且
长度 ≥ 开门才闭合），所以 ```` 包裹的 ``` 不会被误判。
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/markdownFenceMask.ts"
RENDERER = ROOT / "frontend/src/components/MessageRenderer.vue"
pytestmark = pytest.mark.no_infrastructure


def _run(fn: str, text: str):
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
const result = moduleRef.exports.{fn}({json.dumps(text, ensure_ascii=False)});
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def _mask(text: str) -> str:
    return _run("maskFenceInnerBackticks", text)


def _unmask(text: str) -> str:
    return _run("unmaskFenceInnerBackticks", text)


def test_plain_text_is_untouched():
    text = "普通段落，没有围栏。"
    assert _mask(text) == text


def test_normal_code_block_keeps_its_fences():
    text = "```python\nprint(1)\n```"
    assert _mask(text) == text


def test_backticks_inside_code_are_masked():
    """代码演示里出现的 ``` 是内容，必须屏蔽，否则正则会提前闭合。"""
    text = '```python\nsample = "```python\\nprint(1)\\n```"\n```'
    masked = _mask(text)
    body_lines = masked.split("\n")[1]
    assert "`" not in body_lines
    # 闭合围栏保留，长度不变（match.index 才能继续对应原文）。
    assert masked.split("\n")[-1] == "```"
    assert len(masked) == len(text)


def test_unmask_restores_the_original():
    text = '```python\nsample = "```python\\nprint(1)\\n```"\n```'
    assert _unmask(_mask(text)) == text


def test_longer_outer_fence_is_not_closed_by_inner_marker():
    text = "````markdown\n```python\nprint(1)\n```\n````"
    masked = _mask(text)
    # 内层 ``` 属于内容 → 被屏蔽；只有最后 4 个反引号的闭合行保留。
    inner = masked.split("\n")[1:4]
    assert all("`" not in line for line in inner)
    assert masked.split("\n")[-1] == "````"


def test_tilde_fence_is_handled_too():
    text = "~~~js\nconst a = 1;\n~~~"
    assert _mask(text) == text


def test_renderer_wires_mask_and_restore():
    source = RENDERER.read_text(encoding="utf-8")
    assert "maskFenceInnerBackticks" in source
    assert "unmaskFenceInnerBackticks" in source
