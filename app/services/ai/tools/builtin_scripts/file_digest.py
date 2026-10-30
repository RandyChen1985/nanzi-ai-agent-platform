#!/usr/bin/env python3
"""file_digest — 计算文件或文本的 md5 / sha1 / sha256 摘要。

契约：stdin 收 JSON 参数，stdout 输出 JSON，退出码非 0 表示失败。
"""

import hashlib
import json
import os
import sys
from pathlib import Path

SUPPORTED = ("md5", "sha1", "sha256")
CHUNK = 1024 * 1024


def fail(message, code=2):
    print(message, file=sys.stderr)
    sys.exit(code)


def workspace_root():
    return Path.cwd().resolve()


def resolve_path(raw):
    text = str(raw or "").strip()
    if text.startswith("/workspace/"):
        text = text[len("/workspace/"):]
    text = text.lstrip("/")
    root = workspace_root()
    target = (root / text).resolve()
    if target != root and not str(target).startswith(str(root) + os.sep):
        fail("路径越界：只能读取当前会话工作区内的文件")
    return target, text


def main():
    try:
        params = json.load(sys.stdin)
    except ValueError as exc:
        fail("参数不是合法 JSON：%s" % exc)
    if not isinstance(params, dict):
        fail("参数必须是 JSON 对象")

    target_raw = params.get("target")
    if target_raw is None or str(target_raw) == "":
        fail("缺少必填参数 target")

    algorithm = str(params.get("algorithm") or "sha256").strip().lower()
    if algorithm not in SUPPORTED:
        fail("algorithm 只支持 %s" % " / ".join(SUPPORTED))

    digest = hashlib.new(algorithm)

    if params.get("is_text") is True:
        payload = str(target_raw).encode("utf-8")
        digest.update(payload)
        json.dump(
            {
                "algorithm": algorithm,
                "digest": digest.hexdigest(),
                "size": len(payload),
                "source": "text",
            },
            sys.stdout,
            ensure_ascii=False,
        )
        return

    path, relative = resolve_path(target_raw)
    if not path.is_file():
        fail("文件不存在：%s" % relative)

    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)

    json.dump(
        {
            "algorithm": algorithm,
            "digest": digest.hexdigest(),
            "size": size,
            "source": "file",
            "path": relative,
        },
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
