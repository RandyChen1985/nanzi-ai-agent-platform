from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import json
import logging

from app.models.changelog import MetaChangelog
from app.models.metadata import MetaDataset, MetaTable, MetaColumn, MetaMetric, MetaRelationship

logger = logging.getLogger(__name__)

class ChangelogService:
    
    @staticmethod
    async def log_change(
        db: AsyncSession,
        resource_type: str,
        resource_id: str,
        operation: str,
        user_id: Optional[int] = None,
        user_name: Optional[str] = None,
        old_data: Optional[Dict[str, Any]] = None,
        new_data: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None
    ):
        """记录变更日志"""
        try:
            # 计算变更字段（仅对于更新操作）
            changed_fields = None
            if operation == "update" and old_data and new_data:
                changed_fields = ChangelogService._calculate_changed_fields(old_data, new_data)
            
            changelog = MetaChangelog(
                resource_type=resource_type,
                resource_id=resource_id,
                operation=operation,
                old_data=old_data,
                new_data=new_data,
                changed_fields=changed_fields,
                user_id=user_id,
                user_name=user_name,
                reason=reason
            )

            async with db.begin_nested():
                db.add(changelog)
                await db.flush()
            logger.info(f"记录变更日志: {resource_type}:{resource_id} - {operation} by {user_name}")

        except Exception as e:
            logger.error(f"记录变更日志失败: {e}")
            # 不抛出异常，避免影响主业务；使用 savepoint 防止 flush 失败污染外层事务
            
    @staticmethod
    def _calculate_changed_fields(old_data: Dict[str, Any], new_data: Dict[str, Any]) -> List[str]:
        """计算变更的字段列表"""
        changed_fields = []
        
        # 检查所有可能的字段
        all_keys = set(old_data.keys()) | set(new_data.keys())
        
        for key in all_keys:
            old_val = old_data.get(key)
            new_val = new_data.get(key)
            
            # 处理JSON字段的深度比较
            if isinstance(old_val, (dict, list)) or isinstance(new_val, (dict, list)):
                if json.dumps(old_val, sort_keys=True) != json.dumps(new_val, sort_keys=True):
                    changed_fields.append(key)
            else:
                if old_val != new_val:
                    changed_fields.append(key)
                    
        return changed_fields
    
    @staticmethod
    async def get_changelog_by_resource(
        db: AsyncSession,
        resource_type: str,
        resource_id: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[MetaChangelog]:
        """获取指定资源的变更历史"""
        stmt = select(MetaChangelog).where(
            and_(
                MetaChangelog.resource_type == resource_type,
                MetaChangelog.resource_id == resource_id
            )
        ).order_by(desc(MetaChangelog.created_at)).offset(offset).limit(limit)
        
        result = await db.execute(stmt)
        return result.scalars().all()
    
    @staticmethod
    async def get_dataset_changelog(
        db: AsyncSession,
        dataset_id: int,
        limit: int = 50,
        offset: int = 0
    ) -> List[MetaChangelog]:
        """获取数据集及其所有子对象的变更历史"""
        # 1. dataset 本身
        conditions = [
            and_(MetaChangelog.resource_type == "dataset", MetaChangelog.resource_id == str(dataset_id))
        ]

        # 2. 表：resource_id 格式为 "{dataset_id}:{physical_name}"，用 LIKE 匹配
        conditions.append(
            and_(
                MetaChangelog.resource_type == "table",
                MetaChangelog.resource_id.like(f"{dataset_id}:%")
            )
        )

        # 3. 指标：先取该数据集下的所有 metric id，再匹配
        metric_id_stmt = select(MetaMetric.id).where(MetaMetric.dataset_id == dataset_id)
        metric_id_result = await db.execute(metric_id_stmt)
        metric_ids = [str(row[0]) for row in metric_id_result.fetchall()]
        # 同时匹配历史上已删除的（用 name 存的 resource_id），这里直接放宽用 dataset 下的 metric name
        metric_name_stmt = select(MetaMetric.name).where(MetaMetric.dataset_id == dataset_id)
        metric_name_result = await db.execute(metric_name_stmt)
        metric_names = [row[0] for row in metric_name_result.fetchall()]
        if metric_names:
            conditions.append(
                and_(
                    MetaChangelog.resource_type == "metric",
                    MetaChangelog.resource_id.in_(metric_names)
                )
            )

        # 4. 关系：取该数据集下所有表的 ID，找涉及这些表的 relationship id
        table_id_stmt = select(MetaTable.id).where(MetaTable.dataset_id == dataset_id)
        table_id_result = await db.execute(table_id_stmt)
        table_ids = [row[0] for row in table_id_result.fetchall()]
        if table_ids:
            rel_id_stmt = select(MetaRelationship.id).where(
                or_(
                    MetaRelationship.source_table_id.in_(table_ids),
                    MetaRelationship.target_table_id.in_(table_ids)
                )
            )
            rel_id_result = await db.execute(rel_id_stmt)
            rel_ids = [str(row[0]) for row in rel_id_result.fetchall()]
            if rel_ids:
                conditions.append(
                    and_(
                        MetaChangelog.resource_type == "relationship",
                        MetaChangelog.resource_id.in_(rel_ids)
                    )
                )

        stmt = select(MetaChangelog).where(
            or_(*conditions)
        ).order_by(desc(MetaChangelog.created_at)).offset(offset).limit(limit)

        result = await db.execute(stmt)
        return result.scalars().all()
    
    @staticmethod
    async def get_changelog_with_filters(
        db: AsyncSession,
        resource_type: Optional[str] = None,
        operation: Optional[str] = None,
        user_id: Optional[int] = None,
        user_name: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[MetaChangelog]:
        """根据条件筛选变更日志"""
        conditions = []
        
        if resource_type:
            conditions.append(MetaChangelog.resource_type == resource_type)
        if operation:
            conditions.append(MetaChangelog.operation == operation)
        if user_id:
            conditions.append(MetaChangelog.user_id == user_id)
        if user_name:
            conditions.append(MetaChangelog.user_name == user_name)
        if start_date:
            conditions.append(MetaChangelog.created_at >= start_date)
        if end_date:
            conditions.append(MetaChangelog.created_at <= end_date)
            
        stmt = select(MetaChangelog)
        if conditions:
            stmt = stmt.where(and_(*conditions))
            
        stmt = stmt.order_by(desc(MetaChangelog.created_at)).offset(offset).limit(limit)
        
        result = await db.execute(stmt)
        return result.scalars().all()
    
    @staticmethod
    async def get_changelog_stats(
        db: AsyncSession,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """获取变更统计信息"""
        start_date = datetime.now() - timedelta(days=days)
        
        stmt = select(
            func.date(MetaChangelog.created_at).label('change_date'),
            MetaChangelog.resource_type,
            MetaChangelog.operation,
            func.count(MetaChangelog.id).label('change_count'),
            func.count(func.distinct(MetaChangelog.user_id)).label('user_count'),
            func.count(func.distinct(MetaChangelog.resource_id)).label('resource_count')
        ).where(
            MetaChangelog.created_at >= start_date
        ).group_by(
            func.date(MetaChangelog.created_at),
            MetaChangelog.resource_type,
            MetaChangelog.operation
        ).order_by(
            desc(func.date(MetaChangelog.created_at)),
            MetaChangelog.resource_type,
            MetaChangelog.operation
        )
        
        result = await db.execute(stmt)
        return [dict(row._mapping) for row in result.fetchall()]
    
    @staticmethod
    def _extract_model_data(model_instance) -> Dict[str, Any]:
        """从模型实例提取数据字典"""
        if hasattr(model_instance, '__dict__'):
            return {
                key: value for key, value in model_instance.__dict__.items()
                if not key.startswith('_') and not callable(value)
            }
        return {}
    
    @staticmethod
    def _safe_json_dumps(data: Any) -> str:
        """安全的JSON序列化"""
        try:
            return json.dumps(data, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning(f"JSON序列化失败: {e}")
            return "{}"
