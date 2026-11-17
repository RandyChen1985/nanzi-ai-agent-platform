"""流水页的拒因文案必须覆盖后端能产生的每一个码。

这条契约是补出来的，因为踩过：后端 `DENIAL_REASONS` 早就把
`structured_without_path` 翻成了「这条凭据是结构化的，但没指定取哪个字段」，
而前端 `RESULT_LABEL` 里没有这个键，`resultLabel` 的兜底是「去掉 `denied_by_`
前缀原样展示」——于是流水页上出现一串 `structured_without_path`，调用报错里
却是中文。同一次失败，两个地方两种说法，对不上号。

反向不强制：前端多出来的 `denied_disabled` / `denied_expired` 是历史流水的
写法（`RESULT_LABEL` 注释里写明了），继续保留才能正常显示老记录。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INJECTION = ROOT / "app/services/secrets/injection.py"
LABELS = ROOT / "frontend/src/utils/secretLabels.ts"


def _backend_codes() -> set[str]:
    src = INJECTION.read_text(encoding="utf-8")
    block = re.search(r"DENIAL_REASONS = \{(.*?)\n\}", src, re.S)
    assert block, "injection.py 里找不到 DENIAL_REASONS"
    return set(re.findall(r'^\s*"([a-z_]+)":', block.group(1), re.M))


def _frontend_codes() -> set[str]:
    src = LABELS.read_text(encoding="utf-8")
    block = re.search(r"RESULT_LABEL[^{]*\{(.*?)\n\}", src, re.S)
    assert block, "secretLabels.ts 里找不到 RESULT_LABEL"
    return set(re.findall(r"^\s*([a-z_]+):", block.group(1), re.M))


#: 后端会写进流水 `result` 列、但不在 `DENIAL_REASONS` 里的成功态取值。
#: 它们是「配错了但还能跑」的信号，混进「成功」就永远没人发现。
_RESULT_CODES_OUTSIDE_DENIALS = {
    "success",
    "success_fallback",
}


def test_success_states_also_have_labels():
    """`success_fallback` 是补出来的：后端 resolver 会写它，前端原来没映射。

    只断言 `DENIAL_REASONS` 不够——失败原因之外还有成功态，一样会以英文码
    出现在流水页上。
    """
    missing = sorted(_RESULT_CODES_OUTSIDE_DENIALS - _frontend_codes())
    assert not missing, "这些结果码前端没有映射，流水页会直接显示英文：" + "、".join(missing)


def test_every_backend_denial_has_a_frontend_label():
    missing = sorted(_backend_codes() - _frontend_codes())
    assert not missing, (
        "这些拒因后端有中文文案、前端没有映射，流水页会直接显示英文码："
        + "、".join(missing)
    )


def test_labels_are_not_empty_strings():
    src = LABELS.read_text(encoding="utf-8")
    block = re.search(r"RESULT_LABEL[^{]*\{(.*?)\n\}", src, re.S)
    empties = re.findall(r"^\s*([a-z_]+):\s*''", block.group(1), re.M)
    assert not empties, "文案为空会让流水页显示一个空白格：" + "、".join(empties)
