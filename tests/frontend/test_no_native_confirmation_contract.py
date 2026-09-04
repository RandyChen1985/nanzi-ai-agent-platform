"""Contract: 前端确认操作统一使用页面内确认弹窗，不调用浏览器原生 confirm。"""

import re


ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def test_frontend_has_no_native_confirm_calls():
    native_confirm = re.compile(r"(?<![A-Za-z])(?:window\.)?confirm\(")
    offenders = []
    for source_path in (ROOT / "frontend/src").rglob("*.vue"):
        source = source_path.read_text(encoding="utf-8")
        if native_confirm.search(source):
            offenders.append(str(source_path.relative_to(ROOT)))

    assert offenders == []
