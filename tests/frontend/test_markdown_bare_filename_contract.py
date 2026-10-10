"""契约测试：裸文件名不得被 markdown 的 fuzzy linkify 当成域名（用户实测的「点了打不开」）。

行为断言在 `markdown_bare_filename.mjs` 里（真跑 markdown-it）；这里锁住"接线没被摘掉"。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_bare_filename_markdown_behaviour_via_node():
    node = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "tests/frontend/markdown_bare_filename.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"
    assert "行为测试通过" in node.stdout


def test_markdown_module_registers_the_bare_filename_unlink():
    source = _source("frontend/src/utils/markdown.ts")

    assert "registerBareFilenameUnlink" in source
    # 正文渲染器与预览渲染器都要装（少一个就会出现"这里正常那里不正常"）
    assert source.count("registerBareFilenameUnlink(") >= 2


def test_bare_filename_helper_is_dependency_free():
    """零依赖：行为测试靠 `node --experimental-strip-types` 直接加载它。"""
    source = _source("frontend/src/utils/markdownBareFilename.ts")

    assert "from 'markdown-it'" not in source
    assert "from 'vue'" not in source
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") and " from " in stripped:
            assert stripped.endswith(".ts';"), stripped
