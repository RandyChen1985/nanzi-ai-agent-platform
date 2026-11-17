"""Contract: 前端确认操作统一使用页面内确认弹窗，不调用浏览器原生 confirm。"""

import re


ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]

#: 显式原生调用：任何宿主对象上的 confirm，一律拦。
_WINDOW_CONFIRM = re.compile(r"(?<![A-Za-z])(?:window|self|top|parent)\s*\.\s*confirm\(")

#: 裸 confirm 调用。只有在文件**没有**引入自建确认框时才拦——`useConfirm` 的
#: `await confirm({...})` 与原生 `confirm("...")` 字面同形，但来源不同：前者正是
#: 这条契约想要的方向（页面内弹窗），后者才是要禁的东西。
_BARE_CONFIRM = re.compile(r"(?<![A-Za-z_$.])confirm\(")


def test_frontend_has_no_native_confirm_calls():
    offenders = []
    for source_path in (ROOT / "frontend/src").rglob("*.vue"):
        source = source_path.read_text(encoding="utf-8")
        uses_own_confirm = "useConfirm" in source
        for number, line in enumerate(source.split("\n"), 1):
            if _WINDOW_CONFIRM.search(line):
                offenders.append(f"{source_path.relative_to(ROOT)}:{number}")
            elif _BARE_CONFIRM.search(line) and not uses_own_confirm:
                offenders.append(f"{source_path.relative_to(ROOT)}:{number}")

    assert offenders == [], "改用 @/composables/useConfirm 的确认框: " + ", ".join(offenders)
