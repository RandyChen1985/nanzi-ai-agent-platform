"""内置脚本工具的唯一定义（源码与元数据都在这里）。

设计取舍：内置脚本源码**不进数据库**。
- 改内置脚本 = 改本文件（或 ``builtin_scripts/*.py``），不必再写一条 UPDATE 迁移；
- 内置脚本可以被用户覆盖代码/参数/依赖（保存后标记「已覆盖」），但不能改名；
- 未覆盖的 ``sys_script_tools`` 行只承载启停、分组与展示信息。

注意：迁移里的种子 ``parameter_schema`` / 依赖是新建环境用的快照，不能只靠
``INSERT IGNORE`` 更新已有环境；但未覆盖的内置行在运行时和列表接口都以本文件
定义为准，已覆盖行才以库里的 code/schema/依赖/超时为准。

新增一个内置脚本的步骤：
1. 在 ``builtin_scripts/`` 下写脚本（stdin 收 JSON、stdout 出 JSON、失败退出码非 0）；
2. 在本文件追加一条 ``BuiltinScriptTool``；
3. 同步更新 MySQL / PostgreSQL 迁移里的种子元数据（新建环境快照；已有环境靠
   「恢复内置默认」或覆盖行逻辑，不依赖 ``INSERT IGNORE`` 更新）；
4. 契约测试会核对「本文件 ↔ 迁移 SQL」是否漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent / "builtin_scripts"


@dataclass(frozen=True)
class BuiltinScriptTool:
    name: str
    display_name: str
    description: str
    parameter_schema: Dict[str, Any]
    group_name: Optional[str]
    script_file: str
    requirements: Tuple[str, ...] = ()
    timeout_seconds: Optional[int] = None

    @property
    def tool_id(self) -> str:
        """稳定 id：迁移与运行时都用它，改名会变成新工具而不是覆盖旧行。"""
        return "builtin-script-" + self.name.replace("_", "-")

    @property
    def code(self) -> str:
        return (_SCRIPT_DIR / self.script_file).read_text(encoding="utf-8")


BUILTIN_SCRIPT_TOOLS: Tuple[BuiltinScriptTool, ...] = (
    BuiltinScriptTool(
        name="file_to_base64",
        display_name="文件转 base64",
        description=(
            "把一个工作区文件读取为 base64 文本，可选输出 data:<mime>;base64, 前缀。"
            "适合把图片、PDF 等二进制交给只接受 base64 / data-uri 的下游接口，或内嵌到 HTML、Markdown 中。"
        ),
        parameter_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "工作区内文件路径，如 uploads/a.pdf 或 /workspace/uploads/a.pdf",
                },
                "with_data_uri": {
                    "type": "boolean",
                    "description": "是否输出 data:<mime>;base64, 前缀",
                    "default": False,
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "最大读取字节数，默认和上限均为 49152（48KB；base64 后约 64KB，接近单次输出上限）",
                    "default": 49152,
                },
            },
            "required": ["path"],
        },
        group_name="通用文件",
        script_file="file_to_base64.py",
    ),
    BuiltinScriptTool(
        name="base64_to_file",
        display_name="base64 转文件",
        description=(
            "把一段 base64 字符串（允许带 data-uri 前缀）还原成文件并写入工作区，"
            "同时登记为可下载产物。适合把接口返回的 base64 落成用户能下载的附件。"
        ),
        parameter_schema={
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "base64 文本，可带 data:<mime>;base64, 前缀"},
                "filename": {"type": "string", "description": "输出文件名（不含路径），如 report.pdf"},
                "overwrite": {"type": "boolean", "description": "同名文件是否覆盖", "default": False},
            },
            "required": ["data", "filename"],
        },
        group_name="通用文件",
        script_file="base64_to_file.py",
    ),
    BuiltinScriptTool(
        name="file_digest",
        display_name="计算文件/文本摘要",
        description=(
            "计算文件或文本的 md5 / sha1 / sha256 摘要，"
            "用于校验传输完整性、比对版本或生成对账指纹。"
        ),
        parameter_schema={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "文件路径；当 is_text=true 时按文本内容计算"},
                "algorithm": {
                    "type": "string",
                    "description": "摘要算法",
                    "enum": ["md5", "sha1", "sha256"],
                    "default": "sha256",
                },
                "is_text": {"type": "boolean", "description": "target 是否为文本而非文件路径", "default": False},
            },
            "required": ["target"],
        },
        group_name="通用文件",
        script_file="file_digest.py",
    ),
    BuiltinScriptTool(
        name="text_regex_extract",
        display_name="正则批量提取",
        description=(
            "用正则从文本中批量提取匹配内容，可指定捕获组。"
            "适合从日志、邮件、报表里抽取订单号、金额、IP 等字段。"
        ),
        parameter_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待提取的原始文本"},
                "pattern": {"type": "string", "description": "Python 正则表达式"},
                "group": {"type": "integer", "description": "取第几个捕获组，0 表示整体匹配", "default": 0},
                "limit": {"type": "integer", "description": "最多返回多少条", "default": 100},
            },
            "required": ["text", "pattern"],
        },
        group_name="通用文本",
        script_file="text_regex_extract.py",
    ),
    BuiltinScriptTool(
        name="text_redact",
        display_name="敏感信息脱敏",
        description=(
            "对手机号、身份证号、邮箱、银行卡号做脱敏，输出可以对外发送的文本。"
            "适合把工单、日志、报表交给外部前先做一轮清洗。"
        ),
        parameter_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待脱敏文本"},
                "targets": {
                    "type": "array",
                    "description": "要脱敏的类型，默认全部",
                    "items": {"type": "string", "enum": ["phone", "id_card", "email", "bank_card"]},
                },
            },
            "required": ["text"],
        },
        group_name="通用文本",
        script_file="text_redact.py",
    ),
)

BUILTIN_SCRIPT_TOOL_MAP: Dict[str, BuiltinScriptTool] = {tool.name: tool for tool in BUILTIN_SCRIPT_TOOLS}


def get_builtin_script_tool(name: str) -> Optional[BuiltinScriptTool]:
    return BUILTIN_SCRIPT_TOOL_MAP.get(str(name or "").strip())
