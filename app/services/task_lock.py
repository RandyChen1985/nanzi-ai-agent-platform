"""带自动续期的 Redis 互斥锁。

背景：向量重构这类任务可能跑很久（几十个数据集 + 上百条记忆逐条调 Embedding），
而 `SET NX EX 1800` 的锁会**在任务还没跑完时就过期**——后果是第二次点击不再被拦，
两个重构并发跑；同时 `TaskLogService` 的任务 Hash 若也随之过期，`publish` 会因
「任务不存在」静默失败，前端抽屉看起来像卡死。

`TaskLock` 把「抢锁 / 续期 / 释放」收敛到一处：

* 锁的 value 是**持有者标识**（这里用 task_id），释放与续期前先比对 value，
  绝不误删/误续别人的锁；
* `hold()` 上下文管理器在任务执行期间周期性续期，退出时（含异常）释放；
* TTL 仍保留：进程被 kill 时锁最多残留一个 TTL，不会永久锁死。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

#: 锁的兜底存活时间（秒）。进程异常退出时靠它自动解锁。
DEFAULT_TTL_SECONDS = 1800
#: 续期间隔：远小于 TTL，保证长任务中途不会掉锁。
DEFAULT_RENEW_INTERVAL_SECONDS = 60

#: 手动触发任务的锁 TTL：因为有续期心跳，进程**活着**时锁不会掉；进程一旦死掉
#: （崩溃 / 重启 / --reload），用户最多等这么久就能重新触发，不必干等 30 分钟。
TASK_LOCK_TTL_SECONDS = 180
#: 任务锁的续期间隔（必须显著小于 TTL）。
TASK_LOCK_RENEW_SECONDS = 60


class TaskLock:
    """一个具名 Redis 互斥锁。"""

    def __init__(
        self,
        key: str,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        renew_interval_seconds: int = DEFAULT_RENEW_INTERVAL_SECONDS,
        redis_client: Any | None = None,
    ):
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.renew_interval_seconds = max(1, min(renew_interval_seconds, max(1, ttl_seconds - 1)))
        self._redis_client = redis_client

    async def _redis(self):
        if self._redis_client is not None:
            return self._redis_client
        return await get_redis()

    async def acquire(self, owner: str) -> bool:
        """抢占锁；返回 False 表示已被别人持有。"""
        redis = await self._redis()
        return bool(await redis.set(self.key, owner, nx=True, ex=self.ttl_seconds))

    async def owner(self) -> Optional[str]:
        """当前持有者标识（无锁或读取失败返回 None）。"""
        try:
            redis = await self._redis()
            value = await redis.get(self.key)
        except Exception as e:  # pragma: no cover - 读锁失败按「无任务」处理
            logger.warning("[TaskLock] 读取锁 %s 失败: %s", self.key, e)
            return None
        if not value:
            return None
        return value if isinstance(value, str) else value.decode("utf-8", errors="replace")

    async def renew(self, owner: str) -> bool:
        """续期（仅当自己仍是持有者）。"""
        redis = await self._redis()
        if await self.owner() != owner:
            return False
        await redis.expire(self.key, self.ttl_seconds)
        return True

    async def release(self, owner: str) -> None:
        """释放（仅当自己仍是持有者），避免误删后来者的锁。"""
        try:
            redis = await self._redis()
            if await self.owner() != owner:
                return
            await redis.delete(self.key)
        except Exception as e:  # pragma: no cover - 释放失败不应掩盖主流程结果
            logger.warning("[TaskLock] 释放锁 %s 失败: %s", self.key, e)

    async def _renew_loop(self, owner: str) -> None:
        while True:
            await asyncio.sleep(self.renew_interval_seconds)
            try:
                if not await self.renew(owner):
                    logger.info("[TaskLock] 锁 %s 已被他人持有，停止续期", self.key)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[TaskLock] 续期锁 %s 失败: %s", self.key, e)

    @asynccontextmanager
    async def hold(self, owner: str) -> AsyncIterator[None]:
        """任务执行期间持续续期，退出时（含异常/取消）释放锁。"""
        renewer = asyncio.create_task(self._renew_loop(owner))
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                renewer.cancel()
            # CancelledError 继承自 BaseException，必须显式抑制，否则会盖住任务结果
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await renewer
            await self.release(owner)


#: 手动「重构本地向量数据」任务锁。值为 task_id，任务结束后释放。
LOCAL_VECTORS_LOCK_KEY = "nanzi:lock:rebuild_local_vectors"
#: 启动自动同步的防重入锁。启动链路的同步是 fire-and-forget、无法感知结束时间，
#: 因此**故意保留到 TTL 到期**；与手动重构分属不同的键，避免每次重启后
#: 「重构本地向量数据」在 30 分钟内被无谓地挡住。
STARTUP_LOCAL_VECTORS_LOCK_KEY = "nanzi:lock:startup_local_vectors"
#: 手动「重构记忆向量」任务锁。
MEMORY_VECTORS_LOCK_KEY = "nanzi:lock:rebuild_memory_vectors"


def local_vectors_lock(redis_client: Any | None = None) -> TaskLock:
    return TaskLock(
        LOCAL_VECTORS_LOCK_KEY,
        ttl_seconds=TASK_LOCK_TTL_SECONDS,
        renew_interval_seconds=TASK_LOCK_RENEW_SECONDS,
        redis_client=redis_client,
    )


def describe_lock_owner(owner: Optional[str]) -> str:
    """把锁持有者转成给人看的一句话（409 提示用）。

    历史版本或启动自动同步可能把非 task_id 的值写进锁里，此时不该假装它是一个
    可以在抽屉里查看的任务。
    """
    if not owner:
        return "未知来源"
    if str(owner).startswith("task_"):
        return f"任务 {owner}"
    return f"非任务持有者（{owner}）——通常是启动自动同步或历史版本残留"


def startup_local_vectors_lock(redis_client: Any | None = None) -> TaskLock:
    """启动同步的锁：**故意**用长 TTL 且不续期（它守护的是 fire-and-forget 同步）。"""
    return TaskLock(STARTUP_LOCAL_VECTORS_LOCK_KEY, redis_client=redis_client)


def memory_vectors_lock(redis_client: Any | None = None) -> TaskLock:
    return TaskLock(
        MEMORY_VECTORS_LOCK_KEY,
        ttl_seconds=TASK_LOCK_TTL_SECONDS,
        renew_interval_seconds=TASK_LOCK_RENEW_SECONDS,
        redis_client=redis_client,
    )
