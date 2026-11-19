"""MCP 服务名的字符集约束。

为什么只允许 ASCII：服务名会进入工具标识 ``server_name:tool``、真机探测地址、日志与
审计流水，中文/全角/空格在复制粘贴、命令行、正则与跨系统传输里都要额外转义，而名字
本身只是给人看的标识——用 ASCII 后缀没有任何能力损失。

存量不追溯：库里已有含空格或中文的历史名（测试遗留数据、平台 Echo 的
「NanZi Echo 测试 MCP」），所以校验只在**新建**与**名字确实被改动**时执行，
编辑备注等其它字段不受影响。
"""
import re
from typing import Any

SERVER_NAME_ALLOWED_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SERVER_NAME_ALLOWED_DESC = "英文字母、数字、连字符（-）和下划线（_）"


def is_valid_server_name_charset(value: Any) -> bool:
    return bool(SERVER_NAME_ALLOWED_PATTERN.match(str(value or "").strip()))


def validate_server_name_charset(value: Any, *, label: str = "服务显示名称") -> str:
    """校验名字字符集并返回 strip 后的文本；为空或不合法时抛 ValueError。"""
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空")
    if not SERVER_NAME_ALLOWED_PATTERN.match(text):
        raise ValueError(f"{label}只能包含{SERVER_NAME_ALLOWED_DESC}，不支持中文、空格与全角字符")
    return text
