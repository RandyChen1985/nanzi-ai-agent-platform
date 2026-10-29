#!/usr/bin/env python3
"""file_to_base64 — 把一个工作区文件读取为 base64 文本。

契约：stdin 收 JSON 参数，stdout 输出 JSON，退出码非 0 表示失败。
路径只允许落在当前会话工作区内（/workspace 下的相对路径）。
"""

import base64
import json
import mimetypes
import os
import sys
from pathlib import Path

# base64 会把体积放大约 4/3，而 stdout 结果上限是 80KB：读 10MB 只会得到一段
# 被截断的非法 JSON。默认与上限都压到 48KB（约 64KB base64）；更大的文件应使用
# 「发布生成文件」让用户直接下载，而不是把内容塞进模型上下文。
DEFAULT_MAX_BYTES = 48 * 1024
MAX_INPUT_BYTES = 48 * 1024


def fail(message, code=2):
    print(message, file=sys.stderr)
    sys.exit(code)


def workspace_root():
    return Path.cwd().resolve()


def resolve_path(raw):
    text = str(raw or "").strip()
    if not text:
        fail("缺少必填参数 path")
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

    path, relative = resolve_path(params.get("path"))
    if not path.is_file():
        fail("文件不存在：%s" % relative)

    raw_max_bytes = params.get("max_bytes")
    try:
        max_bytes = (
            DEFAULT_MAX_BYTES if raw_max_bytes is None or raw_max_bytes == ""
            else int(raw_max_bytes)
        )
    except (TypeError, ValueError):
        fail("max_bytes 必须是整数")
    if max_bytes <= 0:
        fail("max_bytes 必须为正数")
    # 旧版 schema 的默认值是 10MB，模型也可能照抄；这里统一收敛到安全上限，
    # 避免 stdout 被截断后返回一段无法解析的 base64。
    if max_bytes > MAX_INPUT_BYTES:
        max_bytes = MAX_INPUT_BYTES

    size = path.stat().st_size
    if size > max_bytes:
        fail(
            "文件大小 %d 字节超过上限 %d 字节；内置工具最多读取 48KB，"
            "更大文件请改用「发布生成文件」让用户直接下载" % (size, max_bytes)
        )

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with_data_uri = params.get("with_data_uri") is True
    if with_data_uri:
        encoded = "data:%s;base64,%s" % (mime, encoded)

    json.dump(
        {
            "path": relative,
            "size": size,
            "mime": mime,
            "encoding": "base64",
            "with_data_uri": with_data_uri,
            "data": encoded,
        },
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
