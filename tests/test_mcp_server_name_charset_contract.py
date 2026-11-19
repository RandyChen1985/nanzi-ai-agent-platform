"""MCP 服务名只允许 ASCII：中文/全角/空格在工具标识、地址与审计流水里都要转义。

这些不是「风格偏好」而是实际约束：服务名会拼成 ``server_name:tool_name`` 作为工具标识，
还会出现在探测地址、日志与审计流水里，中文与全角字符在这些位置要么需要转义、要么在
复制粘贴与命令行里直接坏掉。

存量不追溯：库里已有含空格/中文的历史名与平台 Echo 的中文名，所以校验只在「新建」与
「名字确实被改动」时执行（见 `app/services/mcp/naming.py` 的模块说明）。
"""
from pathlib import Path

import pytest

from app.services.mcp.naming import (
    SERVER_NAME_ALLOWED_DESC,
    is_valid_server_name_charset,
    validate_server_name_charset,
)

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
MCP_ENDPOINT = ROOT / "app/api/portal/endpoints/mcp.py"
SHARE_SERVICE = ROOT / "app/services/mcp_share_service.py"


def test_accepts_ascii_names():
    for name in ("priv-4y-hcp", "pub-1-hcp", "mcp_private_name-1", "NanZi_Echo"):
        assert validate_server_name_charset(name) == name
        assert is_valid_server_name_charset(name) is True


def test_rejects_chinese_and_fullwidth():
    for name in ("priv-4y-工具", "工具", "ａｂｃ", "priv-4y-hcp 测试"):
        assert is_valid_server_name_charset(name) is False
        with pytest.raises(ValueError) as exc_info:
            validate_server_name_charset(name)
        assert SERVER_NAME_ALLOWED_DESC in str(exc_info.value)


def test_rejects_spaces_and_colons():
    # 空格与冒号曾经是「静默归一化」的：中文后缀被替换成空串后名字直接变成空，
    # 用户只看到保存按钮点不动。现在必须直接给出原因。
    for name in ("my tool", "priv-4y my-tool", "a:b"):
        with pytest.raises(ValueError):
            validate_server_name_charset(name)


def test_rejects_empty_and_strips():
    with pytest.raises(ValueError) as exc_info:
        validate_server_name_charset("   ")
    assert "不能为空" in str(exc_info.value)
    assert validate_server_name_charset("  priv-4y-hcp  ") == "priv-4y-hcp"


def test_endpoints_validate_new_names_but_not_legacy_ones():
    """契约：新建必校验；更新只在名字改动时校验（否则存量中文名连改备注都保存不了）。"""
    source = MCP_ENDPOINT.read_text(encoding="utf-8")

    assert "from app.services.mcp.naming import validate_server_name_charset" in source
    # 创建端点：无条件校验
    assert "server_name = validate_server_name_charset(server_name)" in source
    # 更新端点：包在「名字确实变了」的分支里
    assert "if server.server_name != server_name:" in source
    update_block = source.split("if server.server_name != server_name:", 1)[1][:400]
    assert "validate_server_name_charset(server_name, label=\"新的服务显示名称\")" in update_block


def test_accept_share_validates_custom_name():
    """分享接受时手填的落地名同样要过字符集，否则等于绕过新建校验。"""
    source = SHARE_SERVICE.read_text(encoding="utf-8")

    assert "from app.services.mcp.naming import validate_server_name_charset" in source
    assert 'validate_server_name_charset(requested, label="服务名")' in source
