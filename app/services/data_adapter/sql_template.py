"""SQL Lab 模板渲染的统一入口：沙箱渲染 + 渲染后安全校验。

为什么要把这件事抽成一个共用模块：

1. 五个数据库适配器此前各自用 ``jinja2.Environment()`` 渲染 SQL。普通
   ``Environment`` 不做任何限制，模板里可以写任意 Python 表达式，例如
   ``{{ ''.__class__.__mro__[1].__subclasses__() }}``，攻击者据此可以遍历
   子类、拿到任意可调用对象，最终执行系统命令（SSTI → RCE）。
   统一换成 ``SandboxedEnvironment`` 可以一次堵住五个入口，也避免五份实现
   日后各自修改、逐渐漂移。

2. 只读安全校验必须针对「最终真正发给数据库的 SQL」，而不是模板本身。
   模板 ``SELECT * FROM t WHERE id = {{ user_id }}`` 看起来是只读的，但
   ``user_id`` 可以渲染成 ``1; DROP TABLE t`` 这样的文本，渲染后的语句已经
   不是只读查询了。如果只在渲染前校验，等于校验了一份永远不会执行的字符串，
   写操作可以借渲染绕过只读闸门。因此本模块在渲染完成后会再次调用适配器
   自己的 ``_validate_sql_safety``，保证调用方拿到的是「已校验、即将执行」
   的 SQL。
"""

from typing import Any, Callable, Mapping, Optional

from jinja2 import BaseLoader, Undefined
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

from .base import SQLSafetyError


class SqlLabUndefined(Undefined):
    """未定义变量渲染为 NULL，保持既有 SQL Lab 行为。

    注意：这里继承的是普通 ``Undefined``，沙箱对属性访问的限制由
    ``SandboxedEnvironment`` 提供，不会因为自定义 Undefined 而失效。
    """

    def __str__(self) -> str:
        return "NULL"

    def __html__(self) -> str:
        return "NULL"

    def __iter__(self):
        return iter([])

    def __bool__(self) -> bool:
        return False


# 进程级单例：环境本身不持有请求状态，复用可以避免每次渲染都重建环境。
# BaseLoader 不访问文件系统，模板只能来自调用方传入的字符串。
SQL_LAB_ENV = SandboxedEnvironment(loader=BaseLoader(), undefined=SqlLabUndefined)

# 只有出现这些标记才需要走 Jinja 渲染。普通 SQL 里可能出现单个 ``{`` 或 ``}``
# （例如 JSON 字面量），无脑渲染反而会误伤，所以先做一次标记探测。
_TEMPLATE_MARKERS = ("{{", "{%", "{#")


def has_template_syntax(sql: str) -> bool:
    """判断 SQL 是否包含 Jinja 模板标记。"""
    return any(marker in sql for marker in _TEMPLATE_MARKERS)


def render_sql_template(
    sql: str,
    params: Optional[Mapping[str, Any]] = None,
    validate: Optional[Callable[[str], None]] = None,
) -> str:
    """在沙箱中渲染 SQL 模板，并对最终 SQL 执行安全校验。

    Args:
        sql: 待渲染的 SQL 模板，也允许是已经渲染好的普通 SQL。
        params: 模板变量，缺失变量由 ``SqlLabUndefined`` 渲染为 NULL。
        validate: 安全校验回调，各适配器传入继承自基类的
            ``_validate_sql_safety``。无论是否发生渲染都会对最终 SQL 调用一次，
            因此调用方不需要再单独判断「要不要校验渲染结果」。

    Returns:
        最终应执行的 SQL 字符串。

    Raises:
        SQLSafetyError: 模板触发沙箱限制（等价于安全违规），或最终 SQL 未通过
            只读校验（由 validate 抛出）。
        jinja2.exceptions.TemplateError: 模板语法/渲染错误（普通调用方可降级处理）。
    """
    source = sql if isinstance(sql, str) else str(sql or "")
    rendered = source

    # 没有模板标记时渲染结果是原样字符串，跳过编译可以少一次解析开销。
    if has_template_syntax(source):
        try:
            rendered = SQL_LAB_ENV.from_string(source).render(**dict(params or {}))
        except SecurityError as exc:
            # 沙箱拒绝的表达式通常是在访问下划线属性/内部对象，属于确定的危险用法。
            # 这里必须直接拒绝，不能让调用方把它当成普通模板语法错误而“回退原始 SQL”，
            # 否则攻击载荷会被原封不动地发给数据库，安全边界也会变得含糊。
            raise SQLSafetyError(
                f"安全策略违规：SQL 模板包含沙箱不允许的表达式: {exc}"
            ) from exc

    # 关键：校验的是 rendered（最终 SQL），而不是 source（模板）。
    # 参数值可以在渲染阶段把 ``; DROP ...`` 之类的片段带进最终语句，只校验
    # 模板会漏掉这类借渲染完成的注入。
    if validate is not None:
        validate(rendered)

    return rendered
