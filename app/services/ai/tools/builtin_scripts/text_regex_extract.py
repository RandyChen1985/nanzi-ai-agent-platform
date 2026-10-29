#!/usr/bin/env python3
"""text_regex_extract — 用正则从文本中批量提取匹配内容。

契约：stdin 收 JSON 参数，stdout 输出 JSON，退出码非 0 表示失败。
"""

import json
import re
import sys

DEFAULT_LIMIT = 100


def fail(message, code=2):
    print(message, file=sys.stderr)
    sys.exit(code)


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

    pattern = str(params.get("pattern") or "")
    if not pattern:
        fail("缺少必填参数 pattern")

    try:
        group = int(params.get("group") or 0)
    except (TypeError, ValueError):
        fail("group 必须是整数")
    try:
        limit = int(params.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        fail("limit 必须是整数")
    if limit <= 0:
        fail("limit 必须为正数")

    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        fail("正则表达式不合法：%s" % exc)

    if compiled.groups < group or group < 0:
        fail("group=%d 超出该正则的捕获组数量（共 %d 个）" % (group, compiled.groups))

    matches = []
    truncated = False
    for index, match in enumerate(compiled.finditer(text)):
        # 用「第 limit+1 个匹配」判定截断：恰好 limit 个匹配不算截断
        if index >= limit:
            truncated = True
            break
        matches.append(match.group(group))

    json.dump(
        {
            "count": len(matches),
            "truncated": truncated,
            "group": group,
            "matches": matches,
        },
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
