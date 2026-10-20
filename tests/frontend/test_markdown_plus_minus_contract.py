"""契约测试：typographer 不得把 `+-` 改成 `±`（用户实测：设备名「2000+-」显示成「2000±」）。

行为断言在 `markdown_plus_minus.mjs` 里（真跑 markdown-it）；这里锁住"接线没被摘掉"。
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _code_only(source: str) -> str:
    """去掉块注释与行注释：注释里说明"官方原来怎么写"不应触发断言。"""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(
        line for line in without_block.splitlines() if not line.strip().startswith("//")
    )


def _rare_regex_line(source: str) -> str:
    for line in source.splitlines():
        if line.strip().startswith("const RARE_RE"):
            return line
    raise AssertionError("markdownTypographer.ts 里找不到 RARE_RE 定义")


def test_plus_minus_markdown_behaviour_via_node():
    node = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "tests/frontend/markdown_plus_minus.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"
    assert "行为测试通过" in node.stdout


def test_both_body_and_preview_renderers_register_the_rule():
    source = _source("frontend/src/utils/markdown.ts")

    # 正文渲染器与画布/预览渲染器都要装（少一个就会出现"这里正常那里不正常"）
    assert source.count("registerTypographerWithoutPlusMinus(") >= 2
    # 只是替换 replacements 规则，不是把 typographer 整体关掉
    assert source.count("typographer: true") >= 2


def test_safe_preview_renderer_registers_the_rule():
    source = _source("frontend/src/utils/safeMarkdown.ts")

    assert "registerTypographerWithoutPlusMinus(" in source
    assert "typographer: true" in source


def test_rule_module_no_longer_replaces_plus_minus():
    source = _source("frontend/src/utils/markdownTypographer.ts")
    code = _code_only(source)

    # 官方那条替换必须消失（注释里可以保留"官方原来怎么写"的说明）
    assert r"replace(/\+-/g" not in code
    assert "±" not in code
    # 预判正则也不能再把 `+-` 当成触发条件，否则纯 `+-` 文本仍会进入替换分支
    rare_regex_line = _rare_regex_line(source)
    assert r"\+-" not in rare_regex_line, rare_regex_line
    assert "--" in rare_regex_line, rare_regex_line


def test_rule_module_is_dependency_free():
    """零依赖：行为测试靠 `node --experimental-strip-types` 直接加载它。"""
    source = _source("frontend/src/utils/markdownTypographer.ts")

    assert "from 'markdown-it'" not in source
    assert "from \"markdown-it\"" not in source
    assert "from 'vue'" not in source
    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("import "), f"不应有运行时 import：{stripped}"
