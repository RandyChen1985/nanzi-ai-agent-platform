"""通用的「长任务进度 + 实时日志」通道。

Redis Hash 保存任务状态、Redis Stream 保存事件流，配合 SSE 端点即可实现
「先回放历史事件、再实时推送」的抽屉式进度展示：即使任务在抽屉打开前就跑完了，
事件仍能从 Stream 回放出来。

首个使用方是元数据同步/巡检（``MetadataSyncLogService`` 继承本类），
随后是向量重构（本地元数据 + 案例、记忆向量）。
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    scope: str
    status: str


class TaskLogService:
    """一个任务的短期状态与事件流。

    子类通过覆盖 ``TASK_KEY`` / ``STREAM_KEY`` / ``TASK_ID_PREFIX`` 复用同一套
    读写实现，同时保持各自独立的 Redis 键。
    """

    TASK_KEY = "task_log:task:{task_id}"
    STREAM_KEY = "task_log:events:{task_id}"
    TASK_TTL_SECONDS = 1800
    STREAM_MAXLEN = 2000
    TERMINAL_EVENTS = frozenset({"completed", "failed"})
    ALLOWED_EVENTS = frozenset({"started", "progress", "completed", "failed"})
    TASK_ID_PREFIX = "task"

    #: 阶段键 → 中文展示名，供前端抽屉直接显示（找不到时回退到 stage 本身）。
    STAGE_LABELS: Dict[str, str] = {
        "lock": "获取重构锁",
        "drop": "删除旧索引",
        "index": "索引检查/重建",
        "count": "统计待重构数据",
        "metadata": "元数据重新向量化",
        "examples": "经验案例重新向量化",
        "scan": "扫描记忆记录",
        "reembed": "记忆重新向量化",
        "loading": "读取元数据",
        "knowledge_base": "检查知识库",
        "parsing": "触发文档解析",
        "completed": "已完成",
        "failed": "已失败",
    }

    def __init__(self, redis_client: Any | None = None):
        self._redis_client = redis_client

    async def _redis(self):
        if self._redis_client is not None:
            return self._redis_client
        return await get_redis()

    # --- 键 ---

    @classmethod
    def task_key(cls, task_id: str) -> str:
        return cls.TASK_KEY.format(task_id=task_id)

    @classmethod
    def stream_key(cls, task_id: str) -> str:
        return cls.STREAM_KEY.format(task_id=task_id)

    @classmethod
    def is_terminal(cls, event: str) -> bool:
        return event in cls.TERMINAL_EVENTS

    def new_task_id(self) -> str:
        return f"{self.TASK_ID_PREFIX}_{secrets.token_urlsafe(18)}"

    # --- 任务 ---

    async def create_task(
        self,
        scope: str,
        *,
        task_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        task_id = task_id or self.new_task_id()
        redis = await self._redis()
        mapping: Dict[str, str] = {
            "scope": str(scope),
            "status": "running",
            "started_at_ms": str(int(time.time() * 1000)),
        }
        for key, value in (meta or {}).items():
            mapping[str(key)] = "" if value is None else str(value)
        await redis.hset(self.task_key(task_id), mapping=mapping)
        await redis.expire(self.task_key(task_id), self.TASK_TTL_SECONDS)
        return TaskRecord(task_id=task_id, scope=str(scope), status="running")

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        redis = await self._redis()
        values = await redis.hgetall(self.task_key(task_id))
        if not values:
            return None
        result = dict(values)
        for numeric_key in ("started_at_ms", "dataset_id", "total"):
            if numeric_key in result and str(result[numeric_key]).lstrip("-").isdigit():
                result[numeric_key] = int(result[numeric_key])
        return result

    # --- 事件 ---

    async def publish(
        self,
        task_id: str,
        *,
        event: str,
        stage: str,
        message: str,
        progress: Optional[int] = None,
        done: Optional[int] = None,
        total: Optional[int] = None,
        error_detail: Optional[str] = None,
        failed_items: Optional[List[Dict[str, str]]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if event not in self.ALLOWED_EVENTS:
            raise ValueError(f"不支持的任务事件: {event}")
        if self.is_terminal(event) and stage != event:
            raise ValueError("终态事件的 stage 必须与 event 一致")

        task = await self.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        elapsed_ms = max(0, int(time.time() * 1000) - int(task.get("started_at_ms") or 0))
        payload: Dict[str, Any] = {
            "task_id": task_id,
            "scope": task.get("scope"),
            "event": event,
            "stage": stage,
            "stage_label": self.STAGE_LABELS.get(stage, stage),
            "message": message,
            "progress": progress,
            "elapsed_ms": elapsed_ms,
        }
        if done is not None:
            payload["done"] = done
        if total is not None:
            payload["total"] = total
        if error_detail:
            payload["error_detail"] = error_detail
        if failed_items:
            payload["failed_items"] = failed_items
        if extra:
            payload.update(extra)

        redis = await self._redis()
        await redis.xadd(
            self.stream_key(task_id),
            {"data": json.dumps(payload, ensure_ascii=False)},
            maxlen=self.STREAM_MAXLEN,
            approximate=True,
        )
        await redis.expire(self.stream_key(task_id), self.TASK_TTL_SECONDS)

        if self.is_terminal(event):
            await redis.hset(self.task_key(task_id), mapping={"status": event})
        # 任务 Hash 的 TTL 必须随事件一起续期：重构这类长任务可能跑过 30 分钟，
        # 若只在 create_task 时设一次 TTL，过期后 get_task 返回 None，
        # publish 会因「任务不存在」失败，前端抽屉从此收不到任何事件。
        await redis.expire(self.task_key(task_id), self.TASK_TTL_SECONDS)
        return payload

    async def read_events(
        self, task_id: str, *, after_id: str = "0-0", count: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        redis = await self._redis()
        # XRANGE 的 min 默认包含边界；使用 exclusive ID 避免 SSE 重复发送上一条事件。
        entries = await redis.xrange(
            self.stream_key(task_id), min=f"({after_id}", max="+", count=count
        )
        return self._decode_entries(entries)

    async def read_new_events(
        self, task_id: str, *, after_id: str, block_ms: int = 1000, count: int = 100
    ) -> List[Dict[str, Any]]:
        """阻塞等待一批新事件；Redis 不可用时由调用方处理连接结束。"""
        redis = await self._redis()
        result = await redis.xread(
            {self.stream_key(task_id): after_id}, block=block_ms, count=count
        )
        events: List[Dict[str, Any]] = []
        for _stream_name, entries in result or []:
            events.extend(self._decode_entries(entries))
        return events

    @staticmethod
    def _decode_entries(entries: Any) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for entry_id, fields in entries or []:
            raw = fields.get("data")
            if raw is None:
                continue
            item = json.loads(raw)
            item["id"] = entry_id
            events.append(item)
        return events


task_log_service = TaskLogService()
