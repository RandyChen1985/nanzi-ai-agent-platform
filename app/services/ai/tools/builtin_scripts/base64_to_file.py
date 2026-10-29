#!/usr/bin/env python3
"""base64_to_file — 把 base64 字符串还原成文件，写入工作区并登记为可下载产物。

契约：stdin 收 JSON 参数，stdout 输出 JSON（含 artifacts），退出码非 0 表示失败。
"""

import base64
import binascii
import json
import os
import re
import sys
from pathlib import Path

DATA_URI_PATTERN = re.compile(r"^data:[^;]*;base64,", re.IGNORECASE)
UNSAFE_FILENAME = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


def fail(message, code=2):
    print(message, file=sys.stderr)
    sys.exit(code)


def workspace_root():
    return Path.cwd().resolve()


def ensure_inside(target, root):
    if target != root and not str(target).startswith(str(root) + os.sep):
        fail("路径越界：只能写入当前会话工作区")
    return target


def main():
    try:
        params = json.load(sys.stdin)
    except ValueError as exc:
        fail("参数不是合法 JSON：%s" % exc)
    if not isinstance(params, dict):
        fail("参数必须是 JSON 对象")

    raw = str(params.get("data") or "").strip()
    if not raw:
        fail("缺少必填参数 data")
    raw = DATA_URI_PATTERN.sub("", raw)
    raw = "".join(raw.split())

    filename = str(params.get("filename") or "").strip()
    if not filename:
        fail("缺少必填参数 filename")
    if UNSAFE_FILENAME.search(filename) or filename in (".", ".."):
        fail("filename 只能是文件名，不能包含路径分隔符或特殊字符")

    try:
        payload = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        fail("data 不是合法的 base64：%s" % exc)

    root = workspace_root()
    target = ensure_inside((root / filename).resolve(), root)
    if target.exists() and params.get("overwrite") is not True:
        fail("文件已存在：%s；如需覆盖请把 overwrite 设为 true" % filename)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)

    relative = target.relative_to(root).as_posix()
    json.dump(
        {
            "path": relative,
            "size": len(payload),
            "artifacts": [relative],
        },
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
