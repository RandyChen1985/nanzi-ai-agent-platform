#!/usr/bin/env python3
"""text_redact — 对手机号、身份证号、邮箱、银行卡号做脱敏。

契约：stdin 收 JSON 参数，stdout 输出 JSON，退出码非 0 表示失败。
"""

import json
import re
import sys

ALL_TARGETS = ("phone", "id_card", "email", "bank_card")

# 顺序有意义：先处理长串（身份证/银行卡）再处理手机号，避免 18 位身份证的中间
# 11 位被手机号规则截走。
PATTERNS = {
    "id_card": re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3}[\dXx])(?!\d)"),
    "bank_card": re.compile(r"(?<!\d)(\d{12,15})(\d{4})(?!\d)"),
    "phone": re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)"),
    "email": re.compile(r"([A-Za-z0-9._%+-])([A-Za-z0-9._%+-]*)(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"),
}

APPLY_ORDER = ("id_card", "bank_card", "phone", "email")


def fail(message, code=2):
    print(message, file=sys.stderr)
    sys.exit(code)


def build_replacer(name):
    def repl(match):
        if name == "id_card":
            return match.group(1) + "*" * 8 + match.group(3)
        if name == "bank_card":
            return "*" * len(match.group(1)) + match.group(2)
        if name == "phone":
            return match.group(1) + "****" + match.group(3)
        return match.group(1) + "***" + match.group(3)

    return repl


def main():
    try:
        params = json.load(sys.stdin)
    except ValueError as exc:
        fail("参数不是合法 JSON：%s" % exc)
    if not isinstance(params, dict):
        fail("参数必须是 JSON 对象")

    text = params.get("text")
    if text is None:
        fail("缺少必填参数 text")
    text = str(text)

    raw_targets = params.get("targets")
    if not raw_targets:
        selected = set(ALL_TARGETS)
    else:
        if isinstance(raw_targets, str):
            raw_targets = [raw_targets]
        selected = set()
        for item in raw_targets:
            name = str(item or "").strip().lower()
            if not name:
                continue
            if name not in PATTERNS:
                fail("targets 只支持 %s" % " / ".join(ALL_TARGETS))
            selected.add(name)
        if not selected:
            selected = set(ALL_TARGETS)

    output = text
    counts = {}
    for name in APPLY_ORDER:
        if name not in selected:
            continue
        output, count = PATTERNS[name].subn(build_replacer(name), output)
        counts[name] = count

    json.dump(
        {
            "text": output,
            "redacted": counts,
            "total": sum(counts.values()),
        },
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
