"""Excel 批量导入用户：模板生成、文件解析、角色匹配与初始密码。

这里只放**不碰数据库、不依赖 FastAPI** 的纯逻辑，端点负责鉴权、查重与落库。
把解析规则、角色匹配口径、密码规则集中到一处，单测可以直接钉住它们，不必为了
验证一行表头去启动数据库。

几条刻意的口径：

* 表头按「去空格/下划线/连字符/星号/冒号」后的别名匹配，管理员把模板列名改成
  「用户名」「邮箱*」也能识别，不因为一个星号就报「模板不正确」。
* 「角色」匹配业务角色（ai_agent_roles）的 code 或 name，忽略大小写与首尾空格。
  匹配不上不在这里兜底成默认角色——那样管理员看不到「对不上」，确认页也就无从调整。
* 初始密码只用大小写字母、数字与 ``PASSWORD_SPECIALS``，刻意排除逗号、引号、
  分号：结果页要支持复制成 TSV / 下载 CSV，密码里带分隔符会把表格列冲乱。
"""

from __future__ import annotations

import io
import re
import secrets
import string
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# 与端点、前端提示保持一致的上限
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 500

PASSWORD_LENGTH = 12
PASSWORD_SPECIALS = "!@#$%^&*_-+=?"

# 表头别名（比较前会先归一化）
HEADER_ALIASES: dict[str, set[str]] = {
    "user_name": {
        "账号",
        "账号名",
        "用户名",
        "用户账号",
        "登录名",
        "username",
        "user_name",
        "account",
        "login",
        "loginname",
    },
    "email": {
        "邮箱",
        "邮件",
        "电子邮箱",
        "电子邮件",
        "email",
        "mail",
        "e-mail",
    },
    "real_name": {
        "姓名",
        "名字",
        "真实姓名",
        "用户姓名",
        "name",
        "realname",
        "fullname",
    },
    "role": {
        "角色",
        "业务角色",
        "角色编码",
        "角色名称",
        "role",
        "rolecode",
        "rolename",
    },
}

TEMPLATE_HEADERS: list[tuple[str, str, int]] = [
    ("user_name", "账号*", 26),
    ("real_name", "姓名", 20),
    ("email", "邮箱*", 34),
    ("role", "角色", 26),
]


class UserImportError(ValueError):
    """可以直接展示给管理员的导入错误，不携带内部堆栈细节。"""


@dataclass(frozen=True)
class ParsedImportRow:
    """Excel 里的一行原始数据（未做业务校验）。"""

    row_number: int
    user_name: str
    email: str
    role_input: str
    real_name: str = ""


def normalize_header(value: Any) -> str:
    """把表头单元格归一化成可比较的键。

    去掉空白、下划线、连字符、星号、冒号与全角括号，并转小写；这样
    「账号 *」「用户名」「User_Name」都会落到同一个键上。
    """

    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-*:：()（）]+", "", text)


def cell_to_text(value: Any) -> str:
    """把单元格值转成文本。

    Excel 会把纯数字账号存成 float（``30049.0``），直接 ``str()`` 会得到
    ``30049.0`` 并让账号名带上小数点；这里对整数值的 float 还原成整数文本。
    """

    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _map_headers(row: Iterable[Any]) -> dict[str, int]:
    aliases = {
        field: {normalize_header(alias) for alias in values}
        for field, values in HEADER_ALIASES.items()
    }
    mapping: dict[str, int] = {}
    for index, cell in enumerate(row):
        key = normalize_header(cell)
        if not key:
            continue
        for field, names in aliases.items():
            if key in names and field not in mapping:
                mapping[field] = index
                break
    return mapping


def _row_is_empty(row: Iterable[Any]) -> bool:
    return all(cell_to_text(value) == "" for value in row)


def parse_import_workbook(content: bytes) -> list[ParsedImportRow]:
    """解析上传的 .xlsx，返回按文件顺序排列的数据行。

    只读第一个工作表；表头允许出现在前 5 行内（有些管理员习惯先写一行标题），
    但必须能找到「账号」列。空行跳过，超过 ``MAX_IMPORT_ROWS`` 直接拒绝，
    避免一次请求把服务端拖住。
    """

    if not content:
        raise UserImportError("上传的文件为空")

    if len(content) > MAX_IMPORT_BYTES:
        raise UserImportError("文件超过 2MB，请拆分后分批导入")

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl 对坏文件的异常类型不统一
        raise UserImportError("无法解析该文件，请确认上传的是 .xlsx 格式的 Excel") from exc

    try:
        if not workbook.worksheets:
            raise UserImportError("Excel 中没有工作表")
        sheet = workbook.worksheets[0]

        iterator = sheet.iter_rows(values_only=True)
        header_map: Optional[dict[str, int]] = None
        header_row_number = 0
        for index, row in enumerate(iterator, start=1):
            mapping = _map_headers(row)
            if "user_name" in mapping:
                header_map = mapping
                header_row_number = index
                break
            if index >= 5:
                break

        if header_map is None:
            raise UserImportError(
                "未找到「账号」列，请使用导入模板（表头：账号、姓名、邮箱、角色）"
            )
        if "email" not in header_map:
            raise UserImportError("未找到「邮箱」列，请使用导入模板（表头：账号、姓名、邮箱、角色）")

        parsed: list[ParsedImportRow] = []
        for offset, row in enumerate(iterator, start=header_row_number + 1):
            if _row_is_empty(row):
                continue

            def pick(field: str) -> str:
                index = header_map.get(field)
                if index is None or index >= len(row):
                    return ""
                return cell_to_text(row[index])

            parsed.append(
                ParsedImportRow(
                    row_number=offset,
                    user_name=pick("user_name"),
                    real_name=pick("real_name"),
                    email=pick("email"),
                    role_input=pick("role"),
                )
            )
            if len(parsed) > MAX_IMPORT_ROWS:
                raise UserImportError(
                    f"单次最多导入 {MAX_IMPORT_ROWS} 行，请拆分文件后分批导入"
                )

        if not parsed:
            raise UserImportError("Excel 中没有可导入的数据行")

        return parsed
    finally:
        workbook.close()


def _role_field(role: Any, field: str) -> str:
    if isinstance(role, dict):
        return cell_to_text(role.get(field))
    return cell_to_text(getattr(role, field, ""))


def match_role(role_input: str, roles: Iterable[Any]) -> Optional[Any]:
    """按业务角色的 code 或 name 精确匹配（忽略大小写与首尾空格）。

    先比 code 再比 name：code 是稳定业务键，name 可能被管理员改过；两者撞车时
    以 code 为准，避免同名角色让导入落到意料之外的那个上。
    """

    key = (role_input or "").strip().lower()
    if not key:
        return None

    role_list = list(roles)
    for role in role_list:
        if _role_field(role, "code").strip().lower() == key:
            return role
    for role in role_list:
        if _role_field(role, "name").strip().lower() == key:
            return role
    return None


def find_default_role(roles: Iterable[Any]) -> Optional[Any]:
    """找业务角色 ``default``；没有就返回 None（调用方据此提示手选）。"""

    for role in roles:
        if _role_field(role, "code").strip().lower() == "default":
            return role
    return None


def validate_user_name(user_name: str) -> Optional[str]:
    """校验账号名，返回错误文案；通过时返回 None。

    口径与 ``POST /management/users`` 一致：只要求非空、不超长、不含空白，
    不额外限制字符集——历史账号里有中文与下划线，收紧会挡住存量用户的同名重建。
    """

    text = (user_name or "").strip()
    if not text:
        return "账号不能为空"
    if len(text) > 50:
        return "账号长度不能超过 50 个字符"
    if any(char.isspace() for char in text):
        return "账号不能包含空格"
    return None


def validate_fixed_password(password: str, user_names: Iterable[str]) -> Optional[str]:
    """校验「固定密码」本身，以及是否包含任一待导入账号名。

    与 ``AuthService.validate_password_complexity`` 同一口径（长度 8-32、至少 3 类
    字符、不含空白；账号名长度 >= 3 时不能出现在密码里）。在批量建号**之前**
    对全部行做一次，避免固定密码不适用于某个账号时出现「前面建了、后面失败」的
    半批结果。
    """

    from app.services.auth_service import AuthService

    valid, message = AuthService.validate_password_complexity(password)
    if not valid:
        return message

    lowered = (password or "").lower()
    for raw_name in user_names:
        name = (raw_name or "").strip()
        if len(name) < 3:
            continue
        if name.lower() in lowered:
            return f"固定密码不能包含账号名「{name}」"
    return None


def generate_initial_password(user_name: str) -> str:
    """生成满足等保复杂度、且不包含用户名的随机初始密码。

    四类字符各保底一个，其余从并集随机补足，最后打乱——只用随机生成再校验会
    频繁撞到「缺符号」而反复重试。用户名参与校验（长度 >= 3 时不允许出现在密码里），
    与 ``AuthService.validate_password_complexity`` 同一口径。
    """

    # 局部导入：模板生成 / 解析的单测不该被 bcrypt / passlib 的导入成本拖慢
    from app.services.auth_service import AuthService

    random = secrets.SystemRandom()
    classes = [
        string.ascii_uppercase,
        string.ascii_lowercase,
        string.digits,
        PASSWORD_SPECIALS,
    ]
    alphabet = "".join(classes)

    for _ in range(100):
        chars = [random.choice(cls) for cls in classes]
        chars.extend(random.choice(alphabet) for _ in range(PASSWORD_LENGTH - len(chars)))
        random.shuffle(chars)
        password = "".join(chars)
        valid, _ = AuthService.validate_password_complexity(password, username=user_name)
        if valid:
            return password

    # 12 位、四类字符各至少一个，连续 100 次都不合格只可能是校验规则被改坏
    raise RuntimeError("无法生成符合复杂度要求的初始密码")


def _role_rows(roles: Iterable[Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for role in roles:
        rows.append(
            (
                _role_field(role, "code"),
                _role_field(role, "name"),
                _role_field(role, "description"),
            )
        )
    return rows


def build_template_workbook(roles: Iterable[Any]) -> bytes:
    """生成导入模板：Sheet1 为表头，Sheet2 为填写说明与内置角色清单。"""

    role_rows = _role_rows(roles)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "用户导入"

    header_fill = PatternFill("solid", fgColor="4F46E5")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    thin = Side(style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for column, (_, title, width) in enumerate(TEMPLATE_HEADERS, start=1):
        cell = sheet.cell(row=1, column=column, value=title)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border
        sheet.column_dimensions[get_column_letter(column)].width = width

    sheet.row_dimensions[1].height = 24
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(TEMPLATE_HEADERS))}1"

    # 账号列按文本格式，避免 Excel 把「00123」吞成 123、把长数字转成科学计数法
    for row in range(2, MAX_IMPORT_ROWS + 2):
        sheet.cell(row=row, column=1).number_format = "@"

    guide = workbook.create_sheet("填写说明")
    guide.column_dimensions["A"].width = 20
    guide.column_dimensions["B"].width = 34
    guide.column_dimensions["C"].width = 64

    guide["A1"] = "用户导入填写说明"
    guide["A1"].font = Font(bold=True, size=14, color="1E293B")
    guide.merge_cells("A1:C1")

    instructions = [
        "1. 仅支持 .xlsx 文件，单个文件不超过 2MB，单次最多导入 500 行。",
        "2. 「账号」必填，全平台唯一，不能包含空格，最长 50 个字符。",
        "3. 「姓名」选填，最长 50 个字符；不填时界面显示账号名。",
        "4. 「邮箱」必填，全平台唯一；已存在账号或已被占用的邮箱会在确认页标红并默认不勾选。",
        "5. 「角色」填写下表中的业务角色编码或名称；留空则使用默认角色 default。",
        "6. 角色与内置角色对不上的行会在确认页标黄，可在确认页重新选择；确认页还支持删除行与补充新增行。",
        "7. 导入账号均为普通用户身份；如需系统管理员，请在导入后到用户管理中单独调整。",
        "8. 初始密码可在确认页选择随机密码或固定密码；固定密码所有账号共用且不能包含账号名。密码只在结果页显示一次，请及时复制或下载，也可开启邮件发送。",
        "9. 示例：账号 zhangsan、姓名 张三、邮箱 zhangsan@example.com、角色 default。",
    ]
    for index, text in enumerate(instructions, start=3):
        cell = guide.cell(row=index, column=1, value=text)
        guide.merge_cells(start_row=index, start_column=1, end_row=index, end_column=3)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.font = Font(size=10, color="334155")

    role_header_row = 3 + len(instructions) + 1
    for column, title in enumerate(["角色编码", "角色名称", "说明"], start=1):
        cell = guide.cell(row=role_header_row, column=column, value=title)
        cell.fill = PatternFill("solid", fgColor="EEF2FF")
        cell.font = Font(bold=True, color="3730A3")
        cell.border = border

    if role_rows:
        for offset, (code, name, description) in enumerate(role_rows, start=1):
            guide.cell(row=role_header_row + offset, column=1, value=code).border = border
            guide.cell(row=role_header_row + offset, column=2, value=name).border = border
            guide.cell(row=role_header_row + offset, column=3, value=description).border = border
    else:
        cell = guide.cell(row=role_header_row + 1, column=1, value="（暂无业务角色）")
        guide.merge_cells(
            start_row=role_header_row + 1,
            start_column=1,
            end_row=role_header_row + 1,
            end_column=3,
        )
        cell.font = Font(size=10, color="94A3B8")

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
