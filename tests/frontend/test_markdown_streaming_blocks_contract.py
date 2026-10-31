"""契约：流式正文按「稳定块」切分后逐块渲染（缓存命中，只重算尾块）。

背景：长回答流式输出时，`segments` 每次 content 变化都把整篇重新走一遍
markdown-it + highlight.js + DOMParser 清洗，成本随正文长度线性增长——短回答
流畅、长回答/含代码块时断断续续。切分后已完成块的 HTML 可复用，视觉结果不变
（拼回同一个 `v-html` 容器）。

切分必须**保守**：只在「空行 + 下一行不是列表/引用/表格/缩进续行」处断开，
且绝不在未闭合的代码围栏内断开，否则会把一个列表拆成两个、把代码切碎。
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/markdownStreaming.ts"
RENDERER = ROOT / "frontend/src/components/MessageRenderer.vue"
pytestmark = pytest.mark.no_infrastructure


def _split(text: str):
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
const blocks = moduleRef.exports.splitStableMarkdownBlocks({json.dumps(text, ensure_ascii=False)});
process.stdout.write(JSON.stringify(blocks));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_plain_paragraphs_split_on_blank_line():
    blocks = _split("第一段\n\n第二段")
    assert blocks == ["第一段\n\n", "第二段"]


def test_join_restores_the_original_text():
    """切分只做分组，不允许丢字、丢空行。"""
    text = "开头\n\n## 小标题\n\n正文一\n\n- 列表项 A\n- 列表项 B\n\n```python\nx = 1\n\ny = 2\n```\n\n结尾"
    assert "".join(_split(text)) == text


def test_list_block_is_not_split_between_items():
    """列表项之间常有空行（宽松列表），拆开会让编号/符号重新开始。

    切分原则是「宁可少切」：说明段与列表可以合在一块（渲染结果不变），
    但列表项之间绝不能断开。
    """
    blocks = _split("说明\n\n- A\n\n- B\n")
    assert "".join(blocks) == "说明\n\n- A\n\n- B\n"
    assert len([block for block in blocks if "- A" in block or "- B" in block]) == 1


def test_nested_fence_with_longer_outer_marker_is_not_split():
    """```` 包裹的 ``` 是内容，不能被当成闭合围栏（否则代码块被腰斩）。"""
    text = "````markdown\n# 示例\n```python\na = 1\n\nb = 2\n```\n````"
    blocks = _split(text)
    assert "".join(blocks) == text
    assert len(blocks) == 1


def test_fence_only_closes_with_same_marker():
    """``` 开启的围栏不会被内部的 ~~~ 闭合。"""
    text = "```js\nconst a = 1;\n~~~\n\nconst b = 2;\n```\n\n结尾"
    blocks = _split(text)
    assert "".join(blocks) == text
    assert len(blocks) == 2
    assert blocks[0].rstrip().endswith("```")  # 围栏整体留在第一块里


def test_multiline_list_items_are_not_split():
    """列表项带缩进补充说明时，项之间不能断开（否则变成两个列表）。"""
    for text in (
        "- 项目一\n  项目一补充说明\n\n- 项目二\n  项目二补充说明",
        "1. 第一步\n   说明一\n\n2. 第二步\n   说明二",
    ):
        blocks = _split(text)
        assert "".join(blocks) == text
        assert len(blocks) == 1


def test_nested_list_continuation_is_not_split():
    text = "- A\n\n  - A1\n\n- B"
    blocks = _split(text)
    assert "".join(blocks) == text
    assert len(blocks) == 1


def test_fenced_code_blank_lines_do_not_split():
    blocks = _split("```python\na = 1\n\nb = 2\n```")
    assert len(blocks) == 1


def test_unclosed_fence_never_splits():
    """流式中代码围栏常常还没闭合，这时不能在任何空行处断开。"""
    blocks = _split("```python\na = 1\n\nb = 2")
    assert len(blocks) == 1


def test_blockquote_and_table_continuations_are_not_split():
    quote = _split("> A\n\n> B")
    assert len(quote) == 1
    table = _split("| a | b |\n\n| c | d |")
    assert len(table) == 1


def test_indented_continuation_is_not_split():
    """缩进代码块/续行不能因为空行被切开。"""
    text = "说明\n\n    code line\n\n    code line 2"
    blocks = _split(text)
    assert "".join(blocks) == text
    assert len([block for block in blocks if "code line" in block]) == 1


def test_renderer_wires_block_split_and_cache():
    source = RENDERER.read_text(encoding="utf-8")
    assert "splitStableMarkdownBlocks" in source
    # 缓存必须是组件内的（不能跨组件/跨消息复用），且要有清理入口。
    assert "markdownBlockHtmlCache" in source
    assert "markdownBlockHtmlCache.clear()" in source
