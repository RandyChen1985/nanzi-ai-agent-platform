"""原型目录（prototypes/）的约定契约。

防的是三件事：原型被放到别的目录、命名不合规、新增原型后忘了刷新总览页。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "prototypes"
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.html$")


def _html_files() -> list[Path]:
    return sorted(p for p in DIR.glob("*.html") if p.name != "index.html")


def test_every_prototype_is_dated_and_kebab_case():
    for p in _html_files():
        assert NAME_RE.match(p.name), f"原型命名不合规：{p.name}（应为 YYYY-MM-DD-topic.html）"


def test_index_lists_every_prototype():
    """总览页是生成物：目录里有几个原型，ITEMS 就得有几条，且文件名对得上。"""
    page = (DIR / "index.html").read_text(encoding="utf-8")
    listed = set(re.findall(r'"file":\s*"([^"]+)"', page))
    actual = {p.name for p in _html_files()}
    assert listed == actual, f"总览页与目录不一致，请跑 python prototypes/build_index.py\n目录多出：{actual - listed}\n总览多出：{listed - actual}"


def test_generator_script_exists():
    assert (DIR / "build_index.py").exists()
    src = (DIR / "build_index.py").read_text(encoding="utf-8")
    assert "index.html" in src and "prototype-kind" in src


def test_spec_doc_exists_and_states_rules():
    doc = DIR / "prototypes.md"
    assert doc.exists(), "缺少 prototypes/prototypes.md"
    text = doc.read_text(encoding="utf-8")
    for keyword in ("YYYY-MM-DD", "build_index.py", "自包含", "index.html"):
        assert keyword in text, f"规范文档缺少关键约定：{keyword}"


def test_agents_md_points_agents_here():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "prototypes/" in agents
    assert "build_index.py" in agents
    # 旧存放点不能再作为推荐位置出现
    assert "html/mock" not in agents


def test_prototypes_are_self_contained():
    """原型要能双击打开、断网也能看：只有外链文字可以指向 http，加载类资源必须内联。"""
    loading = re.compile(
        r"<(?:script|img|iframe|source|video|audio)[^>]+src=[\"'](https?://[^\"']+)"
        r"|<link[^>]+href=[\"'](https?://[^\"']+)",
        re.I,
    )
    for p in _html_files():
        html = p.read_text(encoding="utf-8", errors="ignore")
        external = [a or b for a, b in loading.findall(html)]
        assert not external, f"{p.name} 加载了外部资源：{external[:3]}"
