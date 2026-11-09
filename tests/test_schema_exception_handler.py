"""缺表例外要能自己说清楚，而不是把原因埋进另一个异常。

这条保护最初写出来时，处理器里用了 `logger.error(...)` 却没有定义 `logger`——
于是「你少建了 sys_secret_grants 这张表」在界面上显示成
`name 'logger' is not defined`。**诊断信息被自己的日志语句吃掉了**，
比没有处理器更难查。这个文件就是钉住这件事。
"""

import pytest
from sqlalchemy.exc import ProgrammingError

from app.main import database_schema_exception_handler


class _Url:
    path = "/api/portal/secrets/admin"


class _State:
    trace_id = "trace-test"


class _Request:
    url = _Url()
    state = _State()


def _missing_table_error(message: str) -> ProgrammingError:
    return ProgrammingError("SELECT 1", {}, Exception(message))


async def test_missing_secret_table_points_at_the_migration():
    resp = await database_schema_exception_handler(
        _Request(),
        _missing_table_error("(1146, \"Table 'aiagent.sys_secret_grants' doesn't exist\")"),
    )

    assert resp.status_code == 503
    body = resp.body.decode("utf-8")
    # 三件事都要说：哪张表、该跑哪个脚本、原始消息（非生产环境）
    assert "sys_secret_grants" in body
    assert "V209" in body


async def test_handler_does_not_swallow_other_programming_errors():
    """不是缺表的 ProgrammingError 必须原样抛出，否则真 bug 会变成 503 静默。"""
    with pytest.raises(ProgrammingError):
        await database_schema_exception_handler(
            _Request(), _missing_table_error("syntax error at or near SELECT")
        )


async def test_postgres_undefined_table_is_recognised():
    resp = await database_schema_exception_handler(
        _Request(),
        _missing_table_error('relation "sys_secret_bindings" does not exist'),
    )
    assert resp.status_code == 503
    assert "sys_secret_bindings" in resp.body.decode("utf-8")
