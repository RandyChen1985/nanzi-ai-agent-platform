"""折叠态「深度思考」摘要与字数的纯函数行为契约。

摘要规则（压平 Markdown、定长截断回退句读、进行中尾部跟随、字数口径）必须可单测：
它是纯展示派生数据、不落库，出问题只能靠这里的断言兜住。
这里直接用仓库既有的 node transpile harness 跑 `reasoningPreview.ts`。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

MODULE = "frontend/src/utils/reasoningPreview.ts"


def _run_typescript(expression: str):
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
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_preview_constants_match_spec():
    assert _run_typescript(
        """
return {
  max: api.REASONING_PREVIEW_MAX_CHARS,
  min: api.REASONING_PREVIEW_MIN_CHARS,
  minVisible: api.REASONING_CHAR_COUNT_MIN_VISIBLE,
};
"""
    ) == {"max": 80, "min": 40, "minVisible": 10}


def test_flatten_strips_block_markers():
    assert _run_typescript(
        """
return api.flattenReasoningText("### 分析步骤\\n- 先看目录\\n- 再确认命名\\n> 引用一句\\n1. 收尾");
"""
    ) == "分析步骤 先看目录 再确认命名 引用一句 收尾"


def test_flatten_drops_fence_and_rule_lines_but_keeps_inner_text():
    assert _run_typescript(
        """
return api.flattenReasoningText("先看代码\\n```python\\nprint(1)\\n```\\n---\\n再看结果");
"""
    ) == "先看代码 print(1) 再看结果"


def test_flatten_strips_inline_marks_and_link_urls():
    assert _run_typescript(
        """
return api.flattenReasoningText("读 [官方文档](https://example.com/x) 的 **要点** 与 `code`");
"""
    ) == "读 官方文档 的 要点 与 code"


def test_flatten_keeps_underscores_in_identifiers():
    """`_` 系标记刻意不剥离：snake_case 与 dunder 文件名远比 `_斜体_` 常见。"""
    assert _run_typescript(
        """
return api.flattenReasoningText("跑 analyze_md_charts.py 与 __init__.py，还有 *.md");
"""
    ) == "跑 analyze_md_charts.py 与 __init__.py，还有 *.md"


def test_flatten_drops_table_separator_row():
    assert _run_typescript(
        """
return api.flattenReasoningText("| 维度 | 说明 |\\n| --- | --- |\\n| 层级 | 标题树 |");
"""
    ) == "维度 说明 层级 标题树"


def test_head_preview_falls_back_to_sentence_break_above_floor():
    """正文 121 字、句号落在第 61 字 → 收在句号之后。"""
    assert _run_typescript(
        """
const result = api.resolveReasoningPreview("甲".repeat(60) + "。" + "乙".repeat(60));
return { text: result.text, truncated: result.truncated };
"""
    ) == {"text": "甲" * 60 + "。…", "truncated": True}


def test_head_preview_ignores_sentence_break_below_floor():
    """首个句读落在第 20 字（低于 40 下界）→ 不回退，写满 80 字再截断。"""
    assert _run_typescript(
        """
const result = api.resolveReasoningPreview("甲".repeat(20) + "。" + "乙".repeat(99));
return { length: result.text.length, endsWithEllipsis: result.text.endsWith("…") };
"""
    ) == {"length": 81, "endsWithEllipsis": True}


def test_head_preview_hard_cuts_when_no_sentence_break_exists():
    assert _run_typescript(
        """
const result = api.resolveReasoningPreview("甲".repeat(120));
return { length: result.text.length, truncated: result.truncated };
"""
    ) == {"length": 81, "truncated": True}


def test_tail_preview_follows_latest_content_and_retreats_to_boundary():
    """pending 时取尾部，并在尾部窗口内回退到句读之后（不把标点留在开头）。"""
    assert _run_typescript(
        """
const result = api.resolveReasoningPreview("甲".repeat(25) + "。" + "乙".repeat(74), { pending: true });
return { text: result.text, truncated: result.truncated };
"""
    ) == {"text": "…" + "乙" * 74, "truncated": True}


def test_tail_preview_skips_prefix_when_text_is_short():
    assert _run_typescript(
        """
const result = api.resolveReasoningPreview("刚开口想", { pending: true });
return { text: result.text, truncated: result.truncated, charCount: result.charCount };
"""
    ) == {"text": "刚开口想", "truncated": False, "charCount": 4}


def test_short_text_is_returned_whole():
    assert _run_typescript(
        """
const result = api.resolveReasoningPreview("直接给出结论，不做拆分。");
return { text: result.text, truncated: result.truncated, charCount: result.charCount };
"""
    ) == {"text": "直接给出结论，不做拆分。", "truncated": False, "charCount": 12}


def test_empty_and_markup_only_content_yields_empty_preview():
    assert _run_typescript(
        """
const cases = ["", "   \\n\\n  ", "---\\n```py\\n```"];
return cases.map(raw => {
  const result = api.resolveReasoningPreview(raw);
  return { text: result.text, charCount: result.charCount, truncated: result.truncated };
});
"""
    ) == [
        {"text": "", "charCount": 0, "truncated": False},
        {"text": "", "charCount": 0, "truncated": False},
        {"text": "", "charCount": 0, "truncated": False},
    ]


def test_char_count_excludes_marks_and_whitespace():
    assert _run_typescript(
        """
return {
  marked: api.reasoningCharCount("**你好** 世界"),
  plain: api.reasoningCharCount("hello world"),
};
"""
    ) == {"marked": 4, "plain": 10}


def test_conclusion_sentence_is_preferred_over_head_cut():
    """总长超过上限时，优先挑「命中标记词且长度像一句话」的句子，而不是硬截前 80 字。"""
    assert _run_typescript(
        """
const raw = "先明确口径。" + "甲".repeat(30) + "。所以卡点不是受理量而是二线转派排队。" + "乙".repeat(30) + "。";
const result = api.resolveReasoningPreview(raw);
return { text: result.text, truncated: result.truncated };
"""
    ) == {"text": "所以卡点不是受理量而是二线转派排队。…", "truncated": True}


def test_conclusion_sentence_out_of_length_range_falls_back_to_head_cut():
    """标记词句过长（像整段）时不采用，退回定长截取 + 句读回退。"""
    assert _run_typescript(
        """
const raw = "先明确口径。" + "甲".repeat(60) + "。所以" + "乙".repeat(70) + "。";
const result = api.resolveReasoningPreview(raw);
return { endsWithEllipsis: result.text.endsWith("…"), length: result.text.length };
"""
    ) == {"endsWithEllipsis": True, "length": 68}


def test_pending_preview_ignores_conclusion_rule():
    """进行中始终跟随最新内容，不能被结论句规则抢走。"""
    assert _run_typescript(
        """
const raw = "先明确口径。" + "甲".repeat(30) + "。所以卡点不是受理量而是二线转派排队。" + "乙".repeat(40) + "。";
const result = api.resolveReasoningPreview(raw, { pending: true });
return { startsWithEllipsis: result.text.startsWith("…"), containsHead: result.text.includes("先明确口径") };
"""
    ) == {"startsWithEllipsis": True, "containsHead": False}
