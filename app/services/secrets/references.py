"""``${secret:<key>[.<field>]}`` 引用解析：从一条**已绑定**的凭据里取字段。

## 为什么不按 key 直接查库

设成「按 key 取值」看起来更顺手，但会绕过凭据中心最核心的一条约束：**绑定是白名单**。
绑定表 ``sys_secret_bindings`` 记录的是「哪个工具的哪个槽位可以用哪条凭据」，注入层
按槽位解析、按场景/域名/授权逐项判定。如果引用语法能直接按 key 取明文，那就等于
任何写了 ``${secret:xxx}`` 的地方都能读到 ``xxx``，白名单形同虚设、审计也记不到槽位。

所以这里的语义是**字段选择**，不是跨凭据寻址：

1. 注入层照旧按槽位解析出「本次该用哪条凭据」（绑定 / 授权 / 场景 / 域名全部照跑）；
2. 值里出现 ``${secret:key.field}`` 时，``key`` 只用来**断言**「我引的是这条」；
3. key 与本次解析到的凭据对不上 → 直接拒绝并给出可读原因，不静默退回整段明文。

第 3 条很关键：退回整段意味着把一坨 JSON（含所有密码字段）塞进 ``Authorization`` 头，
请求失败还好，万一对方接受就成了把整份凭据发出去。

## 点号歧义

``secret_key`` 本身允许点号（``jira.prod.token``），``mail.smtp.host`` 因此有三种读法。
这里通过「以本次解析到的真实 key 为准」消歧：整体等于 key 或 ``<key>.<path>`` 才成立，
其余一律视为不匹配——宁可报错，也不猜。
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

#: 引用语法。``${CREDENTIAL:...}`` 是另一套（key 是 header 名），由
#: ``tool_credentials`` 负责，两者互不重叠。
SECRET_REF_PATTERN = re.compile(r"\$\{secret:([^{}\s]+)\}")

#: ``${CREDENTIAL:标识}`` 占位符。和 ``${secret:...}`` 的区别是**不校验标识**：
#: 它由平台写在共享配置里（值里往往是管理员那条凭据），普通用户绑自己的凭据去覆盖时
#: 标识必然对不上。取哪一部分由当前绑定自己的 ``value_path`` 决定。
#:
#: 它也可以只是值的一部分（``Bearer ${CREDENTIAL:x}``），所以按「片段」匹配替换，
#: 不能当成整段值。
CREDENTIAL_REF_PATTERN = re.compile(r"\$\{CREDENTIAL:[^{}]*\}", re.IGNORECASE)

#: 字段路径里的单段：字母数字下划线连字符，或纯数字（数组下标）。
_PATH_SEGMENT_RE = re.compile(r"^(?:[A-Za-z0-9_-]+|\d+)$")


class ReferenceError(ValueError):
    """引用解不开。``kind`` 供注入层翻成给人看的原因码。"""

    #: 凭据值不是 JSON，取不了字段
    NOT_JSON = "reference_not_json"
    #: 路径上的字段不存在（或路径穿过了标量）
    FIELD_MISSING = "reference_field_missing"
    #: 引用里的 key 与本次解析到的凭据不是同一条
    KEY_MISMATCH = "reference_mismatch"
    #: 语法本身不合法（空路径段、非法字符）
    BAD_SYNTAX = "reference_bad_syntax"

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


def has_secret_reference(text: Any) -> bool:
    """这段文本里有 ``${secret:...}`` 引用吗。"""
    return isinstance(text, str) and SECRET_REF_PATTERN.search(text) is not None


def iter_references(text: str) -> list[str]:
    """取出全部 ``${secret:...}`` 表达式（不含外层包装）。"""
    if not isinstance(text, str):
        return []
    return SECRET_REF_PATTERN.findall(text)


def split_key_and_path(expression: str, known_key: str) -> tuple[str, tuple[str, ...]]:
    """把表达式拆成 ``(key, 字段路径)``，**以真实 key 为准**。

    - ``expression == known_key`` → 整段取值，路径为空；
    - ``expression`` 以 ``<known_key>.`` 开头 → 后半段按点号切成路径；
    - 其余 → 返回 ``(expression, ())``，由调用方按 KEY_MISMATCH 拒绝。
    """
    if not expression:
        raise ReferenceError(ReferenceError.BAD_SYNTAX, "引用为空")

    candidate = expression.strip()
    # 以点开头 / 连续点：都是漏写 key 或漏写字段的笔误。
    # 明确报语法错误，别让它退化成「另一条凭据」的 KEY_MISMATCH——
    # 两者都拦得住，但让人看懂自己要改什么更重要。
    if candidate.startswith(".") or ".." in candidate:
        raise ReferenceError(
            ReferenceError.BAD_SYNTAX, f"引用里有空的凭据标识或字段名: {expression}"
        )

    known = (known_key or "").strip().lower()
    lowered = candidate.lower()

    if known and lowered == known:
        return known, ()

    if known and lowered.startswith(known + "."):
        # 只把 key 做大小写无关匹配，**路径保留原样**：JSON 的键名区分大小写，
        # 整串 lower 会把 accessToken 变成 accesstoken，取值必然失败。
        rest = candidate[len(known) + 1 :]
        segments = rest.split(".")
        # 空段一律算语法错误：`db..host` / `db.host.` 是笔误，
        # 悄悄过滤空段会让它看起来「成功」。
        if any(not segment for segment in segments):
            raise ReferenceError(
                ReferenceError.BAD_SYNTAX, f"字段路径里有空段: {expression}"
            )
        for segment in segments:
            if not _PATH_SEGMENT_RE.match(segment):
                raise ReferenceError(
                    ReferenceError.BAD_SYNTAX, f"字段路径含非法字符: {segment}"
                )
        return known, tuple(segments)

    return lowered, ()


def stringify_field_value(value: Any) -> str:
    """把取到的字段值变成字符串——给 HTTP 头、环境变量这类**天生是文本**的位置用。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, float) and value.is_integer():
        # 端口写成 465.0 时给出 465，避免把 465.0 塞进 URL / 端口参数
        return str(int(value))
    return str(value)


pick_field_typed = None  # 占位，真实定义见下（保持模块导入顺序直观）


def pick_field_typed(plaintext: str, path: Sequence[str]) -> Any:  # type: ignore[no-redef]
    """从凭据明文里取出路径指向的值，**保留 JSON 里的原始类型**。

    参数注入要的是「一个值」，不是「一段文本」：`port: 465` 应作为数字交给 MCP/JSON body，
    变成 `"465"` 会被对方的 schema 判类型不符。所以这里返回原类型，
    由调用方决定要不要字符串化（见 ``stringify_field_value``）。

    ``path`` 为空时原样返回明文（单值凭据 / 整段取用）。
    """
    if not path:
        return plaintext

    try:
        data: Any = json.loads(plaintext)
    except (TypeError, ValueError) as exc:
        raise ReferenceError(
            ReferenceError.NOT_JSON,
            "凭据值不是 JSON，无法按字段取用；改用单值凭据或把值存成结构化对象",
        ) from exc

    current = data
    walked: list[str] = []
    for segment in path:
        walked.append(segment)
        where = ".".join(walked)
        if isinstance(current, dict):
            if segment not in current:
                raise ReferenceError(ReferenceError.FIELD_MISSING, f"没有字段 {where}")
            current = current[segment]
        elif isinstance(current, list):
            if not segment.isdigit():
                raise ReferenceError(ReferenceError.FIELD_MISSING, f"{where} 不是数组下标")
            index = int(segment)
            if index >= len(current):
                raise ReferenceError(ReferenceError.FIELD_MISSING, f"{where} 下标越界")
            current = current[index]
        else:
            raise ReferenceError(
                ReferenceError.FIELD_MISSING, f"路径 {where} 穿过了非对象值"
            )

    return current


def pick_field(plaintext: str, path: Sequence[str]) -> str:
    """``pick_field_typed`` 的字符串版——引用渲染与 header/env 注入用这个。

    - JSON 解析失败但确实要取字段 → ``NOT_JSON``（**不**退回整段明文）；
    - 路径不存在 → ``FIELD_MISSING``；
    - 取到对象/数组 → 序列化成紧凑 JSON；布尔转 ``true``/``false``；``null`` → 空串。
    """
    return stringify_field_value(pick_field_typed(plaintext, path))


def render_references(
    text: str,
    *,
    known_key: str,
    plaintext: str,
    collect: list[str] | None = None,
) -> str:
    """把文本里的引用整体替换成取值结果，其余文本（如 ``Bearer ``）原样保留。

    任何一个引用解不开都直接抛异常：**不做「失败就跳过」**，否则会出现
    ``Authorization: Bearer ${secret:mail.smtp.password}`` 原样发出去这种情况——
    对方多半回 401，用户看到的是「鉴权失败」而不是「引用写错了」。
    """
    if not has_secret_reference(text):
        return text

    def _sub(match: re.Match[str]) -> str:
        key, path = split_key_and_path(match.group(1), known_key)
        if key != (known_key or "").strip().lower():
            raise ReferenceError(
                ReferenceError.KEY_MISMATCH,
                f"引用的是 {key}，但当前槽位绑定的是 {known_key}",
            )
        picked = pick_field(plaintext, path)
        if collect is not None:
            # 单个字段值也要进脱敏池：外部服务回显的往往是「invalid token: xxx」
            # 而不会带上整段 JSON，只登记整段等于没登记。
            collect.append(picked)
        return picked

    return SECRET_REF_PATTERN.sub(_sub, text)
