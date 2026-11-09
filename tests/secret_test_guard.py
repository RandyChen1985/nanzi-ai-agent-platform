"""测试不得清空凭据表。

这里记的是一次真实事故：``test_secret_*.py`` 的 fixture 直接用
``DELETE FROM sys_secrets`` 清表，而这些测试连的是**开发库**，不是独立的测试库。
结果每跑一次用例就把开发者自己建的凭据全删了——包括那条正准备联调的系统级
Token。删得干干净净，没有任何报错，因为 DELETE 成功。

根因不是「忘了加 where」，而是「清表」这个思路本身：测试凭什么认为整张表都是
它的？开发库里可能有别人的数据、有正在联调的数据。

正确做法是**只清理自己建的那几条**：用例开始前记下已存在的 id，结束时删掉
「不在快照里」的。这样——

* 绝不碰任何已有数据（哪怕它看起来像测试垃圾）；
* 用例之间仍然互不干扰，每个用例收尾就把自己建的清干净；
* 异常路径也走 finally。
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import delete, select

from app.models.secret import (
    SecretBinding,
    SecretGrant,
    SecretUsageLog,
    SecretUserPref,
    UserSecret,
)


async def snapshot_secret_ids(session) -> set[str]:
    """记下此刻库里已有的凭据 id。

    这份快照就是「不属于测试」的清单——清理时一律跳过。
    """
    rows = await session.execute(select(UserSecret.id))
    return {str(row[0]) for row in rows}


async def snapshot_user_prefs(session) -> set[str]:
    rows = await session.execute(select(SecretUserPref.user_id))
    return {str(row[0]) for row in rows}


async def purge_created_after(
    session,
    *,
    keep_secret_ids: Iterable[str],
    keep_user_ids: Iterable[str] = (),
) -> int:
    """删掉快照之后新建的凭据及其关联记录，返回删掉的凭据条数。

    先删关联表再删主表：这几张表之间没有外键，顺序反过来会留下孤儿记录。
    """
    keep = {str(item) for item in keep_secret_ids}
    new_ids = [
        str(row[0])
        for row in (await session.execute(select(UserSecret.id)))
        if str(row[0]) not in keep
    ]
    if new_ids:
        await session.execute(
            delete(SecretUsageLog).where(SecretUsageLog.secret_id.in_(new_ids))
        )
        await session.execute(
            delete(SecretBinding).where(SecretBinding.secret_id.in_(new_ids))
        )
        await session.execute(
            delete(SecretGrant).where(SecretGrant.secret_id.in_(new_ids))
        )
        await session.execute(delete(UserSecret).where(UserSecret.id.in_(new_ids)))

    keep_users = {str(item) for item in keep_user_ids}
    stale_users = [
        str(row[0])
        for row in (await session.execute(select(SecretUserPref.user_id)))
        if str(row[0]) not in keep_users
    ]
    if stale_users:
        await session.execute(
            delete(SecretUserPref).where(SecretUserPref.user_id.in_(stale_users))
        )

    await session.commit()
    return len(new_ids)
