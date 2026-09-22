"""元数据同步任务的临时日志与状态服务。

实现已收敛到通用的 ``TaskLogService``（Redis Hash 状态 + Redis Stream 事件）；
此处只保留元数据侧的键前缀、``dataset_id`` 语义与历史事件字段
（``completed_documents`` / ``total_documents``），对外 API 与 Redis 键格式不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.task_log_service import TaskLogService


@dataclass(frozen=True)
class SyncTask:
    task_id: str
    dataset_id: int
    status: str


class MetadataSyncLogService(TaskLogService):
    """使用 Redis Hash + Stream 保存一个同步任务的短期状态和日志。"""

    TASK_KEY = "metadata_sync:task:{task_id}"
    STREAM_KEY = "metadata_sync:events:{task_id}"
    TASK_ID_PREFIX = "sync"

    async def create_task(self, dataset_id: int) -> SyncTask:  # type: ignore[override]
        record = await super().create_task(
            scope="metadata_sync",
            meta={"dataset_id": int(dataset_id)},
        )
        return SyncTask(
            task_id=record.task_id,
            dataset_id=int(dataset_id),
            status=record.status,
        )

    async def belongs_to_dataset(self, task_id: str, dataset_id: int) -> bool:
        task = await self.get_task(task_id)
        return bool(task and task.get("dataset_id") == dataset_id)

    async def publish(  # type: ignore[override]
        self,
        task_id: str,
        *,
        event: str,
        stage: str,
        message: str,
        progress: int | None = None,
        completed_documents: int | None = None,
        total_documents: int | None = None,
        error_detail: str | None = None,
    ) -> dict[str, Any]:
        task = await self.get_task(task_id)
        return await super().publish(
            task_id,
            event=event,
            stage=stage,
            message=message,
            progress=progress,
            error_detail=error_detail,
            extra={
                "dataset_id": (task or {}).get("dataset_id"),
                "completed_documents": completed_documents,
                "total_documents": total_documents,
            },
        )


metadata_sync_log_service = MetadataSyncLogService()
