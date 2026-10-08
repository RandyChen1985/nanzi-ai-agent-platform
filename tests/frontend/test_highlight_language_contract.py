"""画布代码高亮语言判定契约。

背景：`ChatCanvas.vue` 原先用**文件名子串**推断 highlight.js 语言，导致一批真实
文件名判错——最典型的是 `.json` 含子串 `js`（`.js`on），在第 181 行就被 `javascript`
分支截走，于是第 186 行的 `json` 分支成了**不可达代码**；同一根因还让 `assets.txt`
判成 typescript（asse-`ts`）、`crash.log` 判成 bash（含 `sh`），而 `main.py` 因为
文件名不含 "python" 三字反而丢成纯文本。

修复方式：改为**扩展名优先**的映射（零依赖纯函数，可被 Node 直接加载做行为断言），
仅当无扩展名或扩展名未知时才回落到「整词」文本线索——刻意不再使用 `js`/`ts`/`sh`
这类两字母裸子串。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 行为验证：字符串子串断言看不出「.json 会不会被判成 javascript」，这里用 Node 真跑
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_highlight_language_behaviour_via_node():
    """覆盖静态断言覆盖不到的核心风险：扩展名判定与文件名子串陷阱。"""
    script = Path("tests/frontend/highlight_language.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "高亮语言判定测试全部通过" in proc.stdout


def test_highlight_language_helper_is_exported_and_dependency_free():
    """判定逻辑必须在零依赖模块里，否则 Node 无法直接加载做行为测试。"""
    source = _source("frontend/src/utils/documentPreviewFormats.ts")

    assert "export function resolveHighlightLanguage(" in source
    # 保持零运行时依赖（该模块不引入 axios / 别名导入）
    assert "from '@/utils/axios'" not in source
    assert "from 'axios'" not in source
    # json 必须映射到 hljs 的 json 语言，而不是借用 javascript
    assert "'.json': 'json'" in source


def test_canvas_uses_shared_highlight_language_helper():
    """画布必须复用共享判定，不得自己再写一份子串推断。"""
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    assert "resolveHighlightLanguage" in canvas, "画布未复用共享的高亮语言判定"
    # 仍然要保留 hljs 的语言存在性守卫（未注册语言需回落纯文本转义）
    assert "hljs.getLanguage(" in canvas


def test_canvas_no_longer_infers_language_from_bare_two_letter_substrings():
    """`js` / `ts` / `sh` 这类两字母裸子串是本次缺陷的根因，不得回归。

    注意：`title.includes('html')` / `title.includes('xml')` 用于**内容类型**判定
    （isHtmlContent 等），不在本次约束范围内，故只针对高亮链用过的三个裸子串。
    """
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    for trap in ("title.includes('js')", "title.includes('ts')", "title.includes('sh')"):
        assert trap not in canvas, f"高亮语言判定不得再使用裸子串 {trap}"

    # 原先不可达的 json 分支应随实现一起移出画布
    assert "lang = 'json'" not in canvas
