"""元数据 Schema 漂移异常告警与人机协同处置服务。

汇聚运行时报错反哺（方案 A）与物理结构巡检发现的 Schema 漂移差异，
为管理员提供差异看板与安全受控的人机处置（一键下线/忽略，绝不擅自修改线上元数据）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metadata import MetaColumn, MetaDataset, MetaSchemaDriftAlert, MetaTable

logger = logging.getLogger(__name__)


class MetadataDriftService:
    """Schema 漂移告警服务。"""

    @staticmethod
    async def record_runtime_stale_alert(
        *,
        dataset_id: Optional[int] = None,
        dataset_name: Optional[str] = None,
        table_name: str,
        column_name: str,
        error_sample: str = "",
    ) -> None:
        """从运行时异步记录一个未知列报错告警（非阻塞）。

        设计为独立打开数据库 Session，保证独立于外部长请求生命周期，并吞掉所有内部异常。
        支持传入 dataset_id 或 dataset_name，若均无则根据 table_name 智能反查。
        """
        try:
            from app.core.orm import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                await MetadataDriftService.record_drift_alert_core(
                    session,
                    dataset_id=dataset_id,
                    dataset_name=dataset_name,
                    table_name=table_name,
                    column_name=column_name,
                    drift_type="missing_in_db",
                    source="runtime",
                    error_sample=error_sample,
                )
                await session.commit()
        except Exception:
            logger.warning(
                f"[MetadataDrift] 异步记录运行时漂移告警失败 (dataset_name={dataset_name}, {table_name}.{column_name})",
                exc_info=True,
            )

    @staticmethod
    async def record_drift_alert_core(
        db: AsyncSession,
        *,
        dataset_id: Optional[int] = None,
        dataset_name: Optional[str] = None,
        table_name: str,
        column_name: str,
        drift_type: str = "missing_in_db",
        source: str = "runtime",
        error_sample: str = "",
    ) -> Optional[MetaSchemaDriftAlert]:
        """核心告警录入逻辑：同表同字段若已存在待处理告警，则累加频次；否则新增。"""
        resolved_dataset_id = dataset_id
        if not resolved_dataset_id and dataset_name:
            ds_stmt = select(MetaDataset.id).where(
                (func.lower(MetaDataset.name) == dataset_name.lower().strip())
                | (func.lower(MetaDataset.display_name) == dataset_name.lower().strip())
            )
            resolved_dataset_id = (await db.execute(ds_stmt)).scalar()

        if not resolved_dataset_id:
            # 根据物理表名反查 dataset_id
            t_stmt = select(MetaTable.dataset_id).where(
                func.lower(MetaTable.physical_name) == table_name.lower().strip()
            )
            resolved_dataset_id = (await db.execute(t_stmt)).scalar()

        if not resolved_dataset_id:
            logger.warning(
                f"[MetadataDrift] 无法关联有效数据集，跳过告警记录: {table_name}.{column_name}"
            )
            return None

        # 查询是否有同名待处理 (status=0) 的告警
        stmt = select(MetaSchemaDriftAlert).where(
            MetaSchemaDriftAlert.dataset_id == resolved_dataset_id,
            func.lower(MetaSchemaDriftAlert.table_name) == table_name.lower().strip(),
            func.lower(MetaSchemaDriftAlert.column_name) == column_name.lower().strip(),
            MetaSchemaDriftAlert.status == 0,
        )
        existing = (await db.execute(stmt)).scalars().first()

        if existing:
            existing.hit_count += 1
            if error_sample:
                existing.error_sample = str(error_sample)[:1000]
            existing.updated_at = datetime.now()
            # 尝试补充 table_id
            if not existing.table_id:
                t_stmt = select(MetaTable.id).where(
                    MetaTable.dataset_id == resolved_dataset_id,
                    func.lower(MetaTable.physical_name) == table_name.lower().strip(),
                )
                existing.table_id = (await db.execute(t_stmt)).scalar()
            return existing

        # 查询匹配的 table_id
        t_stmt = select(MetaTable.id).where(
            MetaTable.dataset_id == resolved_dataset_id,
            func.lower(MetaTable.physical_name) == table_name.lower().strip(),
        )
        matched_table_id = (await db.execute(t_stmt)).scalar()

        alert = MetaSchemaDriftAlert(
            dataset_id=resolved_dataset_id,
            table_id=matched_table_id,
            table_name=table_name.strip(),
            column_name=column_name.strip(),
            drift_type=drift_type,
            source=source,
            error_sample=str(error_sample)[:1000] if error_sample else None,
            hit_count=1,
            status=0,
        )
        db.add(alert)
        return alert

    @staticmethod
    async def get_dataset_drift_alerts(
        db: AsyncSession,
        dataset_id: int,
        *,
        status: Optional[int] = None,
    ) -> List[MetaSchemaDriftAlert]:
        """获取指定数据集的漂移告警列表，默认按待处理优先、检出时间倒序。"""
        stmt = select(MetaSchemaDriftAlert).where(
            MetaSchemaDriftAlert.dataset_id == dataset_id
        )
        if status is not None:
            stmt = stmt.where(MetaSchemaDriftAlert.status == status)

        stmt = stmt.order_by(
            MetaSchemaDriftAlert.status.asc(),
            MetaSchemaDriftAlert.hit_count.desc(),
            MetaSchemaDriftAlert.updated_at.desc(),
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_drift_summary(db: AsyncSession) -> Tuple[int, Dict[int, int]]:
        """获取全局待处理漂移告警总数及各数据集的待处理计数。"""
        stmt = (
            select(
                MetaSchemaDriftAlert.dataset_id,
                func.count(MetaSchemaDriftAlert.id).label("cnt"),
            )
            .where(MetaSchemaDriftAlert.status == 0)
            .group_by(MetaSchemaDriftAlert.dataset_id)
        )
        rows = (await db.execute(stmt)).all()
        dataset_counts: Dict[int, int] = {int(r[0]): int(r[1]) for r in rows}
        total_pending = sum(dataset_counts.values())
        return total_pending, dataset_counts

    @staticmethod
    async def _resolve_single_alert_core(
        db: AsyncSession,
        alert: MetaSchemaDriftAlert,
        action: str,
    ) -> Dict[str, Any]:
        """核心单项告警处置逻辑，不执行 db.commit()。"""
        column_dropped = False
        column_added = False
        message = ""

        if action == "drop_column":
            t_stmt = select(MetaTable).where(
                MetaTable.dataset_id == alert.dataset_id,
                func.lower(MetaTable.physical_name) == alert.table_name.lower(),
            )
            table = (await db.execute(t_stmt)).scalars().first()
            if table:
                c_stmt = select(MetaColumn).where(
                    MetaColumn.table_id == table.id,
                    func.lower(MetaColumn.physical_name) == alert.column_name.lower(),
                )
                col = (await db.execute(c_stmt)).scalars().first()
                if col:
                    await db.delete(col)
                    column_dropped = True

            alert.status = 1  # resolved
            alert.updated_at = datetime.now()
            message = f"已成功从元数据中下线字段 {alert.table_name}.{alert.column_name}"
            if column_dropped:
                message += "，建议随后点击「同步到 RAGFlow」更新向量知识库"

        elif action == "add_column":
            t_stmt = select(MetaTable).where(
                MetaTable.dataset_id == alert.dataset_id,
                func.lower(MetaTable.physical_name) == alert.table_name.lower(),
            )
            table = (await db.execute(t_stmt)).scalars().first()
            if not table:
                raise ValueError(f"元数据中未找到表 {alert.table_name}，无法直接添加字段")

            c_stmt = select(MetaColumn).where(
                MetaColumn.table_id == table.id,
                func.lower(MetaColumn.physical_name) == alert.column_name.lower(),
            )
            existing_col = (await db.execute(c_stmt)).scalars().first()
            if not existing_col:
                col_type = "String"
                col_desc = ""
                try:
                    ds_stmt = select(MetaDataset).where(MetaDataset.id == alert.dataset_id)
                    ds = (await db.execute(ds_stmt)).scalars().first()
                    if ds and ds.data_source:
                        from app.services.data_adapter.factory import get_adapter

                        adapter = await get_adapter(ds.data_source)
                        phys_cols = await adapter.get_columns(table_name=alert.table_name)
                        for pc in phys_cols:
                            if str(pc.get("name") or "").lower().strip() == alert.column_name.lower().strip():
                                col_type = pc.get("type") or "String"
                                col_desc = pc.get("comment") or ""
                                break
                except Exception as ex:
                    logger.warning(f"[MetadataDrift] 尝试获取物理列类型失败，使用默认值: {ex}")

                new_col = MetaColumn(
                    table_id=table.id,
                    physical_name=alert.column_name,
                    term=col_desc if col_desc else alert.column_name,
                    type=str(col_type)[:50],
                    description=col_desc if col_desc else None,
                    is_primary=0,
                )
                db.add(new_col)
                column_added = True

            alert.status = 1  # resolved
            alert.updated_at = datetime.now()
            message = f"已成功将字段 {alert.table_name}.{alert.column_name} 录入元数据表"
            if column_added:
                message += "，建议随后点击「同步到 RAGFlow」更新向量知识库"

        elif action == "sync_type":
            t_stmt = select(MetaTable).where(
                MetaTable.dataset_id == alert.dataset_id,
                func.lower(MetaTable.physical_name) == alert.table_name.lower(),
            )
            table = (await db.execute(t_stmt)).scalars().first()
            if not table:
                raise ValueError(f"元数据中未找到表 {alert.table_name}，无法同步字段类型")

            c_stmt = select(MetaColumn).where(
                MetaColumn.table_id == table.id,
                func.lower(MetaColumn.physical_name) == alert.column_name.lower(),
            )
            col = (await db.execute(c_stmt)).scalars().first()
            if not col:
                raise ValueError(f"元数据表 {alert.table_name} 中未找到字段 {alert.column_name}")

            # 优先直连物理库获取最新列类型
            phys_type = None
            try:
                ds_stmt = select(MetaDataset).where(MetaDataset.id == alert.dataset_id)
                ds = (await db.execute(ds_stmt)).scalars().first()
                if ds and ds.data_source:
                    from app.services.data_adapter.factory import get_adapter

                    adapter = await get_adapter(ds.data_source)
                    phys_cols = await adapter.get_columns(table_name=alert.table_name)
                    for pc in phys_cols:
                        if str(pc.get("name") or "").lower().strip() == alert.column_name.lower().strip():
                            phys_type = pc.get("type")
                            break
            except Exception as ex:
                logger.warning(f"[MetadataDrift] 尝试从物理库获取列类型失败，尝试从巡检记录解析: {ex}")

            # 若无法连通外部库，从 error_sample 解析实际物理类型
            if not phys_type and alert.error_sample:
                import re

                m = re.search(r"物理库实际为\s*([a-zA-Z0-9_()]+)", alert.error_sample)
                if m:
                    phys_type = m.group(1).strip()

            if not phys_type:
                phys_type = "String"

            old_type = col.type
            col.type = str(phys_type)[:50]

            alert.status = 1  # resolved
            alert.updated_at = datetime.now()
            message = f"已成功将字段 {alert.table_name}.{alert.column_name} 类型从 {old_type} 同步为物理库实际类型 {col.type}，建议随后点击「同步到 RAGFlow」更新向量知识库"

        elif action == "ignore":
            alert.status = 2  # ignored
            alert.updated_at = datetime.now()
            message = f"已忽略字段 {alert.table_name}.{alert.column_name} 的漂移提醒"

        else:
            raise ValueError(f"不支持的处置动作: {action}，仅支持 drop_column, add_column, sync_type 或 ignore")

        return {
            "alert_id": alert.id,
            "status": alert.status,
            "column_dropped": column_dropped,
            "column_added": column_added,
            "column_updated": action == "sync_type",
            "message": message,
        }

    @staticmethod
    async def resolve_alert(
        db: AsyncSession,
        alert_id: int,
        action: str,
    ) -> Dict[str, Any]:
        """管理员对单条告警进行人机协同处置（下线字段 / 录入元数据 / 忽略）。"""
        stmt = select(MetaSchemaDriftAlert).where(MetaSchemaDriftAlert.id == alert_id)
        alert = (await db.execute(stmt)).scalars().first()
        if not alert:
            raise ValueError(f"告警不存在: ID {alert_id}")

        res = await MetadataDriftService._resolve_single_alert_core(db, alert, action)
        await db.commit()
        if res.get("column_dropped") or res.get("column_added") or res.get("column_updated"):
            await MetadataDriftService._try_sync_local_vector({alert.dataset_id})
        return res

    @staticmethod
    async def batch_resolve_alerts(
        db: AsyncSession,
        dataset_id: int,
        action: str,
        drift_type: Optional[str] = None,
        alert_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """批量处置告警：可指定 alert_ids，或按 dataset_id + drift_type 批量处置。"""
        stmt = select(MetaSchemaDriftAlert).where(
            MetaSchemaDriftAlert.dataset_id == dataset_id,
            MetaSchemaDriftAlert.status == 0,
        )
        if alert_ids:
            stmt = stmt.where(MetaSchemaDriftAlert.id.in_(alert_ids))
        elif drift_type:
            stmt = stmt.where(MetaSchemaDriftAlert.drift_type == drift_type)

        alerts = (await db.execute(stmt)).scalars().all()
        if not alerts:
            return {"processed_count": 0, "message": "没有符合条件的待处理告警"}

        processed_count = 0
        failed_count = 0
        for alert in alerts:
            try:
                await MetadataDriftService._resolve_single_alert_core(db, alert, action)
                processed_count += 1
            except Exception as e:
                logger.warning(f"[MetadataDrift] 批量处理单项失败: alert_id={alert.id}, err={e}")
                failed_count += 1

        await db.commit()
        if action in ("drop_column", "add_column", "sync_type") and processed_count > 0:
            await MetadataDriftService._try_sync_local_vector({dataset_id})
        if failed_count:
            message = f"成功批量处置 {processed_count} 项告警，{failed_count} 项处置失败"
        else:
            message = f"成功批量处置 {processed_count} 项告警"
        return {
            "processed_count": processed_count,
            "failed_count": failed_count,
            "message": message,
        }

    @staticmethod
    async def get_all_drift_alerts(
        db: AsyncSession,
        *,
        status: Optional[int] = None,
        dataset_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """获取全库或指定数据集的漂移告警列表，并附带 dataset_name。"""
        stmt = (
            select(MetaSchemaDriftAlert, MetaDataset.name.label("dataset_name"))
            .outerjoin(MetaDataset, MetaSchemaDriftAlert.dataset_id == MetaDataset.id)
        )
        if dataset_id is not None and dataset_id > 0:
            stmt = stmt.where(MetaSchemaDriftAlert.dataset_id == dataset_id)
        if status is not None:
            stmt = stmt.where(MetaSchemaDriftAlert.status == status)

        stmt = stmt.order_by(
            MetaSchemaDriftAlert.status.asc(),
            MetaSchemaDriftAlert.hit_count.desc(),
            MetaSchemaDriftAlert.updated_at.desc(),
        )
        result = await db.execute(stmt)
        rows = result.all()
        items = []
        for alert, ds_name in rows:
            items.append({
                "id": alert.id,
                "dataset_id": alert.dataset_id,
                "dataset_name": ds_name or f"数据集 #{alert.dataset_id}",
                "table_id": alert.table_id,
                "table_name": alert.table_name,
                "column_name": alert.column_name,
                "drift_type": alert.drift_type,
                "source": alert.source,
                "error_sample": alert.error_sample,
                "hit_count": alert.hit_count,
                "status": alert.status,
                "created_at": alert.created_at,
                "updated_at": alert.updated_at,
            })
        return items

    @staticmethod
    async def batch_resolve_alerts_global(
        db: AsyncSession,
        action: str,
        drift_type: Optional[str] = None,
        alert_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """跨数据集全局批量处置告警。"""
        stmt = select(MetaSchemaDriftAlert).where(MetaSchemaDriftAlert.status == 0)
        if alert_ids:
            stmt = stmt.where(MetaSchemaDriftAlert.id.in_(alert_ids))
        elif drift_type:
            stmt = stmt.where(MetaSchemaDriftAlert.drift_type == drift_type)

        alerts = (await db.execute(stmt)).scalars().all()
        if not alerts:
            return {"processed_count": 0, "message": "没有符合条件的待处理告警"}

        processed_count = 0
        failed_count = 0
        for alert in alerts:
            try:
                await MetadataDriftService._resolve_single_alert_core(db, alert, action)
                processed_count += 1
            except Exception as e:
                logger.warning(f"[MetadataDrift] 全局批量处理单项失败: alert_id={alert.id}, err={e}")
                failed_count += 1

        await db.commit()
        if action in ("drop_column", "add_column", "sync_type") and processed_count > 0:
            ds_ids = {a.dataset_id for a in alerts if a.dataset_id}
            await MetadataDriftService._try_sync_local_vector(ds_ids)
        if failed_count:
            message = f"成功批量处置 {processed_count} 项告警，{failed_count} 项处置失败"
        else:
            message = f"成功批量处置 {processed_count} 项告警"
        return {
            "processed_count": processed_count,
            "failed_count": failed_count,
            "message": message,
        }

    @staticmethod
    async def _try_sync_local_vector(dataset_ids: Set[int]) -> None:
        """告警处置触发元数据物理列变更后，自动同步相关数据集的本地 Redis 向量与 Schema 缓存。"""
        if not dataset_ids:
            return
        try:
            from app.services.ai.metadata_index_service import MetadataIndexService
            for ds_id in dataset_ids:
                if ds_id and ds_id > 0:
                    await MetadataIndexService.sync_local_redis_vector(ds_id)
        except Exception as ex:
            logger.warning("[MetadataDrift] 处置后同步本地 Redis 向量失败: %s", ex)

