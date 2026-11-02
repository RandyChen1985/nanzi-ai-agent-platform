#!/usr/bin/env python3
"""扫描本目录下的原型 HTML，重新生成总览页 index.html。

用法：
    python prototypes/build_index.py

约定：
- 只收录 `YYYY-MM-DD-<topic>.html` 形式的文件；
- 标题取 HTML 里的 <title>，缺失时退回文件名；
- 类型按标题关键词推断，可用 <meta name="prototype-kind" content="..."> 覆盖。

新增或重命名原型后跑一次即可，总览页不需要手工维护。
"""

from __future__ import annotations

import html as html_lib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"
NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.html$")

KINDS = ("方案对比", "交互原型", "效果预览")
KIND_CLASS = {"方案对比": "compare", "交互原型": "mock", "效果预览": "preview"}


def read_meta(src: str) -> tuple[str, str | None]:
    """返回 (标题, 显式指定的类型)。"""
    m = re.search(r"<title>(.*?)</title>", src, re.S | re.I)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    # 标题尾部常带「（2026-10-30 原型）」这类日期注记，展示时去掉
    title = re.sub(r"\s*[（(]\s*20\d{2}-\d{2}-\d{2}[^）)]*[）)]\s*$", "", title)
    k = re.search(r'<meta\s+name=["\']prototype-kind["\']\s+content=["\']([^"\']+)["\']', src, re.I)
    return title, (k.group(1).strip() if k else None)


def guess_kind(title: str, stem: str) -> str:
    if re.search(r"对比|对照|变体|方案|改前|改后|vs|A/B", title, re.I):
        return "方案对比"
    if re.search(r"预览|效果", title) and not re.search(r"Mock|原型", title):
        return "效果预览"
    return "交互原型"


def collect() -> list[dict]:
    items: list[dict] = []
    for f in sorted(HERE.glob("*.html"), reverse=True):
        m = NAME_RE.match(f.name)
        if not m:
            if f.name != "index.html":
                print(f"  ! 跳过命名不合规的文件：{f.name}", file=sys.stderr)
            continue
        date, _ = m.groups()
        src = f.read_text(encoding="utf-8", errors="ignore")
        title, explicit = read_meta(src)
        title = title or f.stem
        kind = explicit if explicit in KINDS else guess_kind(title, f.stem)
        items.append({"file": f.name, "date": date, "title": title, "kind": kind})
    items.sort(key=lambda it: (it["date"], it["file"]), reverse=True)
    return items


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>原型总览 · Prototypes</title>
<style>
  :root{
    --primary:#1677ff; --ink:#111827; --ink-2:#4b5563; --ink-3:#6b7280; --ink-4:#9ca3af;
    --line:#e8ebf0; --line-2:#eef1f5; --bg:#f7f8fa;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
  .wrap{max-width:1000px;margin:0 auto;padding:40px 24px 64px}
  header h1{margin:0;font-size:24px;font-weight:700;letter-spacing:-.3px}
  header p{margin:8px 0 0;color:var(--ink-3);font-size:13px}
  header p b{color:var(--primary)}
  .bar{display:flex;align-items:center;gap:10px;margin:22px 0 14px}
  .search{position:relative;flex:1;max-width:420px}
  .search svg{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--ink-4)}
  .search input{width:100%;height:38px;padding:0 12px 0 34px;border:1px solid var(--line);
    border-radius:9px;background:#fff;font-size:13px;color:var(--ink);outline:none;
    transition:border-color .15s,box-shadow .15s}
  .search input:focus{border-color:var(--primary);box-shadow:0 0 0 3px rgba(22,119,255,.12)}
  .count{color:var(--ink-4);font-size:12px}
  .list{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
  .row{display:grid;grid-template-columns:88px minmax(0,1fr) 92px 24px;align-items:center;gap:14px;
    padding:12px 16px;border-top:1px solid var(--line-2);text-decoration:none;color:inherit;
    transition:background .12s}
  .row:first-child{border-top:0}
  .row:hover{background:#f8faff}
  .row:hover .t{color:var(--primary)}
  .d{color:var(--ink-4);font-size:12px;font-variant-numeric:tabular-nums}
  .t{font-size:13.5px;font-weight:500;transition:color .12s;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .k{justify-self:start;font-size:11px;font-weight:600;padding:1px 7px;border-radius:99px;
    border:1px solid transparent;white-space:nowrap}
  .k.compare{background:#fff4e5;color:#b45309;border-color:#fde3c0}
  .k.mock{background:#eaf2ff;color:#1677ff;border-color:#d6e6ff}
  .k.preview{background:#f3f0ff;color:#7c5cff;border-color:#e4dcff}
  .go{color:#d1d5db}
  .row:hover .go{color:var(--primary)}
  .empty{padding:48px;text-align:center;color:var(--ink-4);font-size:13px}
  footer{margin-top:18px;color:var(--ink-4);font-size:12px}
  footer code{font-size:11px;color:var(--ink-3)}
  footer a{color:var(--ink-3)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>原型总览</h1>
    <p>需求评审用的静态 HTML 原型，共 <b id="total">0</b> 个。命名与归档规范见
      <a href="prototypes.md" style="color:var(--primary)">prototypes.md</a>。</p>
  </header>

  <div class="bar">
    <div class="search">
      <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path stroke-linecap="round" d="M20 20l-3.6-3.6"/></svg>
      <input id="q" placeholder="搜索标题或文件名" autocomplete="off">
    </div>
    <span class="count" id="count"></span>
  </div>

  <div class="list" id="list"></div>
  <footer>
    本页由 <code>python prototypes/build_index.py</code> 生成，请勿手工编辑。<br>
    新增原型放在本目录，按 <code>YYYY-MM-DD-主题.html</code> 命名，再跑一次脚本。
  </footer>
</div>

<script>
const ITEMS = __DATA__;
const KIND_CLASS = { '方案对比': 'compare', '交互原型': 'mock', '效果预览': 'preview' };
const list = document.getElementById('list');
const q = document.getElementById('q');
const countEl = document.getElementById('count');
document.getElementById('total').textContent = ITEMS.length;

function esc(s) {
  return s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function render(kw) {
  const key = (kw || '').trim().toLowerCase();
  const rows = ITEMS.filter(it =>
    !key || it.title.toLowerCase().includes(key) || it.file.toLowerCase().includes(key));
  if (!rows.length) {
    list.innerHTML = '<div class="empty">没有匹配的原型</div>';
    countEl.textContent = '';
    return;
  }
  list.innerHTML = rows.map(it => `
    <a class="row" href="${it.file}" target="_blank" rel="noopener">
      <span class="d">${it.date}</span>
      <span class="t" title="${esc(it.file)}">${esc(it.title)}</span>
      <span class="k ${KIND_CLASS[it.kind] || 'mock'}">${esc(it.kind)}</span>
      <span class="go">›</span>
    </a>`).join('');
  countEl.textContent = key ? `${rows.length} / ${ITEMS.length}` : '';
}

q.addEventListener('input', e => render(e.target.value));
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); q.focus(); }
  if (e.key === 'Escape') { q.value = ''; render(''); q.blur(); }
});
render('');
</script>
</body>
</html>
"""


def main() -> int:
    items = collect()
    page = TEMPLATE.replace("__DATA__", json.dumps(items, ensure_ascii=False, indent=2))
    INDEX.write_text(page, encoding="utf-8")
    print(f"已生成 {INDEX}（{len(items)} 个原型）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
