
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, or_, cast, String, Integer, func
from sqlalchemy.orm import selectinload
from typing import List, Optional, Dict, Any
import asyncio
import logging
from app.models.metadata import MetaDataset, MetaTable, MetaColumn, MetaMetric, MetaRelationship
from app.services.ai.config import AgentConfigProvider
from app.services.changelog_service import ChangelogService


logger = logging.getLogger(__name__)

class MetadataService:
    
    # --- Dataset CRUD ---
    
    @staticmethod
    async def get_datasets(db: AsyncSession) -> List[MetaDataset]:
        # Optimized query to fetch counts
        stmt = select(MetaDataset).options(
            selectinload(MetaDataset.tables),
            selectinload(MetaDataset.metrics)
        )
        result = await db.execute(stmt)
        datasets = result.scalars().all()
        
        # Populate counts
        for ds in datasets:
            ds.table_count = len(ds.tables)
            ds.metric_count = len(ds.metrics)
            # Relationship count: Relationships are not directly on Dataset, but derived from tables
            # This could be expensive in a loop. For list view, maybe we can skip or do a separate count query?
            # Or assume we only need table/metric counts for list view? 
            # Request asked for "Digital labels on cards: Table count, Metric count, Relationship count".
            # So we need it. Let's do a quick query for each or optimize.
            # OPTIMIZATION: Get all table IDs for this dataset
            table_ids = [t.id for t in ds.tables]
            ds.relationship_count = 0 
            if table_ids:
                # We need a synchronous way or await in loop. Await in loop is fine for N<50 datasets.
                # But sqlalchemy objects in async session... 
                # Better: Pre-fetch all relationships or use count query.
                rel_count_stmt = select(func.count(MetaRelationship.id)).where(
                    (MetaRelationship.source_table_id.in_(table_ids)) | 
                    (MetaRelationship.target_table_id.in_(table_ids))
                )
                # We can't await inside this synchronous iteration if we return list directly.
                # We need to change structure to async loop.
                pass 
                
        # Since we need async execution for relationship counts, let's do it properly
        for ds in datasets:
            ds.table_count = len(ds.tables)
            ds.metric_count = len(ds.metrics)
            
            table_ids = [t.id for t in ds.tables]
            if table_ids:
                 rel_count_stmt = select(func.count(MetaRelationship.id)).where(
                    (MetaRelationship.source_table_id.in_(table_ids)) | 
                    (MetaRelationship.target_table_id.in_(table_ids))
                )
                 rel_count = await db.scalar(rel_count_stmt)
                 ds.relationship_count = rel_count or 0
            else:
                ds.relationship_count = 0
                
        return datasets

    @staticmethod
    async def get_dataset_by_id(
        db: AsyncSession, 
        dataset_id: int,
        user_id: Optional[int] = None,
        is_admin: bool = False
    ) -> Optional[MetaDataset]:
        stmt = select(MetaDataset).where(MetaDataset.id == dataset_id)

        # 1. Permission Filtering (if not admin)
        if not is_admin and user_id is not None:
            from app.models.permission import ResourcePermission, UserRoleRelation
            
            # Subquery to find role IDs for the user
            user_roles_stmt = select(UserRoleRelation.role_id).where(UserRoleRelation.user_id == user_id)
            
            # Subquery to check if user/role has permission for this specific dataset
            # resource_type='metadata' is used for datasets
            permitted_stmt = select(ResourcePermission.id).where(
                ResourcePermission.resource_type == 'metadata',
                ResourcePermission.resource_id == str(dataset_id),
                ResourcePermission.enabled == True,
                or_(
                    ResourcePermission.user_id == user_id,
                    ResourcePermission.role_id.in_(user_roles_stmt)
                )
            )
            
            # Apply filter
            stmt = stmt.where(permitted_stmt.exists())

        result = await db.execute(
            stmt.options(
                selectinload(MetaDataset.tables).selectinload(MetaTable.columns),
                selectinload(MetaDataset.metrics)
            )
        )
        ds = result.scalar_one_or_none()
        if ds:
            # Manually populate relationships
            ds.relationships = await MetadataService.get_relationships_by_dataset(db, dataset_id)
            
            # Populate counts
            ds.table_count = len(ds.tables)
            ds.metric_count = len(ds.metrics)
            ds.relationship_count = len(ds.relationships)
            
        return ds
        
    @staticmethod
    async def search_datasets(
        db: AsyncSession, 
        keyword: Optional[str] = None, # Changed param name to match usage, or keep as 'query'? Original was 'keyword' in this file but 'query' in others. Let's align with call usage. data_api calls with `query=dataset_name` passed to `search_datasets(..., query=...)`. Wait, data_api calls `search_datasets(session, query=dataset_name, ...)`. The signature in data_api call is `query`. The signature in this file was `keyword`.
        # Let's standardize on `query` to match the caller in data_api.py update.
        query: Optional[str] = None,
        status: int = 1,
        user_id: Optional[int] = None,
        is_admin: bool = False
    ) -> List[MetaDataset]:
        """
        Search for datasets based on name/display_name and permissions.
        """
        stmt = select(MetaDataset).where(MetaDataset.status == status)
        
        # 1. Permission Filtering (if not admin)
        if not is_admin and user_id is not None:
            from app.models.permission import ResourcePermission, UserRoleRelation
            
            # Subquery to find all resource_ids (dataset ids) this user has access to
            # via direct permission OR via role permission
            
            # 1. Get User's Role IDs
            user_roles_stmt = select(UserRoleRelation.role_id).where(UserRoleRelation.user_id == user_id)
            
            # 2. Get Permitted Resource IDs (Union of User-based and Role-based)
            # We match resource_type='metadata' (as established for datasets in this context)
            permitted_ids_stmt = select(cast(ResourcePermission.resource_id, Integer)).where(
                ResourcePermission.resource_type == 'metadata',
                ResourcePermission.enabled == True,
                or_(
                    ResourcePermission.user_id == user_id,
                    ResourcePermission.role_id.in_(user_roles_stmt)
                )
            )
            
            # 3. Apply Filter
            stmt = stmt.where(MetaDataset.id.in_(permitted_ids_stmt))
            
        # 2. Search Query Filtering
        if query:
            stmt = stmt.where(or_(
                MetaDataset.name.ilike(f"%{query}%"),
                MetaDataset.display_name.ilike(f"%{query}%")
            ))
            
        # Pre-load tables/metrics to avoid DetachedInstanceError in external loops
        stmt = stmt.options(
            selectinload(MetaDataset.tables),
            selectinload(MetaDataset.metrics),
        )
            
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_dataset_by_name(db: AsyncSession, name: str) -> Optional[MetaDataset]:
        result = await db.execute(
            select(MetaDataset).where(MetaDataset.name == name)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _mark_dataset_as_modified(db: AsyncSession, dataset_id: int):
        """
        标记数据集为“待同步”状态 (3)。
        仅当当前状态不是“同步中” (1) 时才更新。
        """
        # 查找当前状态
        stmt = select(MetaDataset.rag_sync_status).where(MetaDataset.id == dataset_id)
        current_status = await db.scalar(stmt)
        
        if current_status != 1:
            query = update(MetaDataset).where(MetaDataset.id == dataset_id).values(rag_sync_status=3)
            await db.execute(query)
            # 注意：调用者负责最后的 commit

    @staticmethod
    async def create_dataset(db: AsyncSession, data: dict, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None) -> MetaDataset:
        dataset = MetaDataset(**data)
        db.add(dataset)
        await db.flush()  # 获取ID但不提交
        
        # 记录变更日志
        new_data = {
            "id": dataset.id,
            "name": dataset.name,
            "display_name": dataset.display_name,
            "description": dataset.description,
            "tags": dataset.tags,
            "data_source": dataset.data_source,
            "status": dataset.status
        }
        await ChangelogService.log_change(
            db=db,
            resource_type="dataset",
            resource_id=str(dataset.id),
            operation="create",
            user_id=user_id,
            user_name=user_name,
            new_data=new_data,
            reason=reason
        )
        
        await db.commit()
        await db.refresh(dataset)
        await AgentConfigProvider.refresh_dataset_menu()

        # 同步本地 Redis 向量
        try:
            from app.services.ai.metadata_index_service import MetadataIndexService
            await MetadataIndexService.sync_local_redis_vector(dataset.id)
        except Exception as ex:
            logger.warning("[Local Redis Sync] Failed to trigger sync in create_dataset: %s", ex)

        return dataset
        
    @staticmethod
    async def update_dataset(db: AsyncSession, dataset_id: int, data: dict, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None) -> Optional[MetaDataset]:
        # 获取更新前的数据
        old_dataset = await MetadataService.get_dataset_by_id(db, dataset_id, is_admin=True)
        old_data = None
        if old_dataset:
            old_data = {
                "id": old_dataset.id,
                "name": old_dataset.name,
                "display_name": old_dataset.display_name,
                "description": old_dataset.description,
                "tags": old_dataset.tags,
                "data_source": old_dataset.data_source,
                "status": old_dataset.status
            }
        
        query = update(MetaDataset).where(MetaDataset.id == dataset_id).values(**data)
        await db.execute(query)
        await MetadataService._mark_dataset_as_modified(db, dataset_id)
        
        # 记录变更日志
        new_dataset = await MetadataService.get_dataset_by_id(db, dataset_id, is_admin=True)
        if new_dataset and old_data:
            new_data = {
                "id": new_dataset.id,
                "name": new_dataset.name,
                "display_name": new_dataset.display_name,
                "description": new_dataset.description,
                "tags": new_dataset.tags,
                "data_source": new_dataset.data_source,
                "status": new_dataset.status
            }
            await ChangelogService.log_change(
                db=db,
                resource_type="dataset",
                resource_id=str(dataset_id),
                operation="update",
                user_id=user_id,
                user_name=user_name,
                old_data=old_data,
                new_data=new_data,
                reason=reason
            )
        
        await db.commit()

        # 同步本地 Redis 向量
        try:
            from app.services.ai.metadata_index_service import MetadataIndexService
            await MetadataIndexService.sync_local_redis_vector(dataset_id)
        except Exception as ex:
            logger.warning("[Local Redis Sync] Failed to trigger sync in update_dataset: %s", ex)

        return await MetadataService.get_dataset_by_id(db, dataset_id, is_admin=True)

    @staticmethod
    async def delete_dataset(db: AsyncSession, dataset_id: int, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None):
        # 1. Get old data before delete
        old_dataset = await MetadataService.get_dataset_by_id(db, dataset_id, is_admin=True)
        old_data = None
        if old_dataset:
            old_data = {
                "id": old_dataset.id,
                "name": old_dataset.name,
                "display_name": old_dataset.display_name,
                "description": old_dataset.description,
                "tags": old_dataset.tags,
                "data_source": old_dataset.data_source,
                "status": old_dataset.status
            }
        
        # 2. Get RAG ID before delete
        result = await db.execute(select(MetaDataset.rag_dataset_id).where(MetaDataset.id == dataset_id))
        rag_kb_id = result.scalar_one_or_none()

        # 3. Delete Local
        query = delete(MetaDataset).where(MetaDataset.id == dataset_id)
        await db.execute(query)
        
        # 4. Record changelog before commit
        if old_data:
            await ChangelogService.log_change(
                db=db,
                resource_type="dataset",
                resource_id=str(dataset_id),
                operation="delete",
                user_id=user_id,
                user_name=user_name,
                old_data=old_data,
                reason=reason
            )
        
        await db.commit()
        await AgentConfigProvider.refresh_dataset_menu()

        # 级联删除本地 Redis 向量
        try:
            from app.services.ai.metadata_index_service import MetadataIndexService
            import asyncio
            asyncio.create_task(MetadataIndexService.delete_dataset_vectors(dataset_id))
        except Exception as ex:
            logger.warning("[Local Redis Sync] Failed to delete dataset vectors: %s", ex)

        # 5. Cascade Delete RAGFlow KB (Background or Fire-and-forget)
        if rag_kb_id:
            from app.services.metadata_rag_service import MetadataRagService
            # We don't await this to keep the API responsive, or handle it in BackgroundTasks.
            # For simplicity, we just trigger it.
            asyncio.create_task(MetadataRagService.delete_rag_dataset(rag_kb_id))

    # --- Table/Column CRUD (Simplified for now) ---
    
    @staticmethod
    async def save_table_metadata(db: AsyncSession, dataset_id: int, table_data: dict, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None) -> MetaTable:
        """
        Save or Update a table and its columns.
        table_data expects: 
        {
          "physical_name": "res_room",
          "term": "机房表",
          "columns": [...]
        }
        """
        # 1. Find existing table
        result = await db.execute(
            select(MetaTable).where(
                MetaTable.dataset_id == dataset_id,
                MetaTable.physical_name == table_data['physical_name']
            )
        )
        existing_table = result.scalar_one_or_none()
        
        # 2. Get old data for changelog
        old_data = None
        operation = "create"
        if existing_table:
            operation = "update"
            # Load existing table with columns for old data
            old_table_result = await db.execute(
                select(MetaTable)
                .options(selectinload(MetaTable.columns))
                .where(MetaTable.id == existing_table.id)
            )
            old_table_with_cols = old_table_result.scalar_one()
            
            old_data = {
                "physical_name": old_table_with_cols.physical_name,
                "term": old_table_with_cols.term,
                "description": old_table_with_cols.description,
                "synonyms": old_table_with_cols.synonyms,
                "columns": [
                    {
                        "physical_name": col.physical_name,
                        "term": col.term,
                        "type": col.type,
                        "description": col.description,
                        "enums": col.enums,
                        "synonyms": col.synonyms
                    }
                    for col in old_table_with_cols.columns
                ]
            }
        
        if existing_table:
            # Update Table
            existing_table.term = table_data.get('term', existing_table.term)
            existing_table.description = table_data.get('description', existing_table.description)
            existing_table.synonyms = table_data.get('synonyms', existing_table.synonyms)
        else:
            # Create Table
            existing_table = MetaTable(
                dataset_id=dataset_id,
                physical_name=table_data['physical_name'],
                term=table_data.get('term', table_data['physical_name']),
                description=table_data.get('description', ''),
                synonyms=table_data.get('synonyms', [])
            )
            db.add(existing_table)
            await db.flush() # flush to get ID
            
        # 2. Handle Columns (Upsert + Delete stale)
        incoming_col_names = [col['physical_name'] for col in table_data.get('columns', [])]
        
        # Delete columns that are NOT in the incoming list
        delete_stmt = delete(MetaColumn).where(
            MetaColumn.table_id == existing_table.id,
            MetaColumn.physical_name.not_in(incoming_col_names)
        )
        await db.execute(delete_stmt)

        current_cols = table_data.get('columns', [])
        for col_data in current_cols:
             result_col = await db.execute(
                 select(MetaColumn).where(
                     MetaColumn.table_id == existing_table.id,
                     MetaColumn.physical_name == col_data['physical_name']
                 )
             )
             existing_col = result_col.scalar_one_or_none()
             
             if existing_col:
                 # Update
                 existing_col.term = col_data.get('term', existing_col.term)
                 existing_col.description = col_data.get('description', existing_col.description)
                 existing_col.enums = col_data.get('enums', existing_col.enums)
                 existing_col.synonyms = col_data.get('synonyms', existing_col.synonyms)
             else:
                 # Create
                 new_col = MetaColumn(
                     table_id=existing_table.id,
                     physical_name=col_data['physical_name'],
                     term=col_data.get('term', col_data['physical_name']),
                     type=col_data.get('type', 'String'),
                     description=col_data.get('description', ''),
                     enums=col_data.get('enums', []),
                     synonyms=col_data.get('synonyms', [])
                 )
                 db.add(new_col)
        
        await MetadataService._mark_dataset_as_modified(db, dataset_id)
        
        # 3. Record changelog for table operation
        new_data = {
            "physical_name": existing_table.physical_name,
            "term": existing_table.term,
            "description": existing_table.description,
            "synonyms": existing_table.synonyms,
            "columns": table_data.get('columns', [])
        }
        
        await ChangelogService.log_change(
            db=db,
            resource_type="table",
            resource_id=f"{dataset_id}:{existing_table.physical_name}",
            operation=operation,
            user_id=user_id,
            user_name=user_name,
            old_data=old_data,
            new_data=new_data,
            reason=reason or f"{operation}表 {existing_table.physical_name}"
        )
        
        await db.commit()

        # 同步本地 Redis 向量
        try:
            from app.services.ai.metadata_index_service import MetadataIndexService
            await MetadataIndexService.sync_local_redis_vector(dataset_id)
        except Exception as ex:
            logger.warning("[Local Redis Sync] Failed to trigger sync in save_table_metadata: %s", ex)

        # Reload with columns
        result = await db.execute(
            select(MetaTable)
            .options(selectinload(MetaTable.columns))
            .where(MetaTable.id == existing_table.id)
        )
        await AgentConfigProvider.refresh_dataset_menu()
        return result.scalar_one()

    @staticmethod
    async def delete_table_metadata(db: AsyncSession, dataset_id: int, table_name: str, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None):
        """
        Delete a table by its physical name within a dataset.
        """
        # 1. Get old data BEFORE delete
        old_table_result = await db.execute(
            select(MetaTable)
            .options(selectinload(MetaTable.columns))
            .where(MetaTable.dataset_id == dataset_id, MetaTable.physical_name == table_name)
        )
        old_table = old_table_result.scalar_one_or_none()
        old_data = None
        table_id_str = table_name
        if old_table:
            table_id_str = f"{dataset_id}:{old_table.physical_name}"
            old_data = {
                "physical_name": old_table.physical_name,
                "term": old_table.term,
                "description": old_table.description,
                "synonyms": old_table.synonyms,
                "columns": [
                    {"physical_name": col.physical_name, "term": col.term, "type": col.type}
                    for col in old_table.columns
                ]
            }

        # 2. Delete
        query = delete(MetaTable).where(
            MetaTable.dataset_id == dataset_id,
            MetaTable.physical_name == table_name
        )
        await db.execute(query)
        await MetadataService._mark_dataset_as_modified(db, dataset_id)

        # 3. Record changelog
        if old_data:
            await ChangelogService.log_change(
                db=db,
                resource_type="table",
                resource_id=table_id_str,
                operation="delete",
                user_id=user_id,
                user_name=user_name,
                old_data=old_data,
                reason=reason or f"删除表 {table_name}"
            )

        await db.commit()

        # 同步本地 Redis 向量
        try:
            from app.services.ai.metadata_index_service import MetadataIndexService
            await MetadataIndexService.sync_local_redis_vector(dataset_id)
        except Exception as ex:
            logger.warning("[Local Redis Sync] Failed to trigger sync in delete_table_metadata: %s", ex)

        await AgentConfigProvider.refresh_dataset_menu()


    # --- Metrics CRUD ---

    @staticmethod
    async def create_metric(db: AsyncSession, dataset_id: int, data: dict, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None) -> MetaMetric:
        # Check uniqueness and handle Upsert
        result = await db.execute(
            select(MetaMetric).where(
                MetaMetric.dataset_id == dataset_id,
                MetaMetric.name == data['name']
            )
        )
        existing_metric = result.scalar_one_or_none()
        
        if existing_metric:
            # Update Existing
            logger.info(f"Metric '{data['name']}' already exists in dataset {dataset_id}. Updating fields.")
            existing_metric.display_name = data.get('display_name', existing_metric.display_name)
            existing_metric.description = data.get('description', existing_metric.description)
            existing_metric.calculation_logic = data.get('calculation_logic', existing_metric.calculation_logic)
            existing_metric.unit = data.get('unit', existing_metric.unit)
            existing_metric.updated_at = datetime.now()
            await MetadataService._mark_dataset_as_modified(db, dataset_id)
            await db.commit()
            await db.refresh(existing_metric)

            # 同步本地 Redis 向量
            try:
                from app.services.ai.metadata_index_service import MetadataIndexService
                await MetadataIndexService.sync_local_redis_vector(dataset_id)
            except Exception as ex:
                logger.warning("[Local Redis Sync] Failed to trigger sync in create_metric (existing): %s", ex)

            return existing_metric

        # Create New
        metric = MetaMetric(dataset_id=dataset_id, **data)
        db.add(metric)
        await MetadataService._mark_dataset_as_modified(db, dataset_id)
        
        # Record changelog for metric creation
        new_data = {
            "name": metric.name,
            "display_name": metric.display_name,
            "description": metric.description,
            "calculation_logic": metric.calculation_logic,
            "unit": metric.unit
        }
        
        await ChangelogService.log_change(
            db=db,
            resource_type="metric",
            resource_id=str(metric.name),
            operation="create",
            user_id=user_id,
            user_name=user_name,
            new_data=new_data,
            reason=reason or f"创建指标 {metric.name}"
        )
        
        await db.commit()
        await db.refresh(metric)

        # 同步本地 Redis 向量
        try:
            from app.services.ai.metadata_index_service import MetadataIndexService
            await MetadataIndexService.sync_local_redis_vector(dataset_id)
        except Exception as ex:
            logger.warning("[Local Redis Sync] Failed to trigger sync in create_metric: %s", ex)

        return metric

    @staticmethod
    async def get_metrics_by_dataset(db: AsyncSession, dataset_id: int) -> List[MetaMetric]:
        result = await db.execute(
            select(MetaMetric).where(MetaMetric.dataset_id == dataset_id)
        )
        return result.scalars().all()

    @staticmethod
    async def update_metric(db: AsyncSession, metric_id: int, data: dict, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None) -> Optional[MetaMetric]:
        # Need dataset_id to mark modified
        res = await db.execute(select(MetaMetric.dataset_id).where(MetaMetric.id == metric_id))
        dataset_id = res.scalar_one_or_none()

        query = update(MetaMetric).where(MetaMetric.id == metric_id).values(**data)
        await db.execute(query)
        
        # Record changelog for metric update
        # Get old data before update
        old_metric_result = await db.execute(select(MetaMetric).where(MetaMetric.id == metric_id))
        old_metric = old_metric_result.scalar_one_or_none()
        
        if old_metric and dataset_id:
            old_data = {
                "name": old_metric.name,
                "display_name": old_metric.display_name,
                "description": old_metric.description,
                "calculation_logic": old_metric.calculation_logic,
                "unit": old_metric.unit
            }
            
            new_data = {
                "name": data.get('name', old_metric.name),
                "display_name": data.get('display_name', old_metric.display_name),
                "description": data.get('description', old_metric.description),
                "calculation_logic": data.get('calculation_logic', old_metric.calculation_logic),
                "unit": data.get('unit', old_metric.unit)
            }
            
            await ChangelogService.log_change(
                db=db,
                resource_type="metric",
                resource_id=str(old_metric.name),
                operation="update",
                user_id=user_id,
                user_name=user_name,
                old_data=old_data,
                new_data=new_data,
                reason=reason or f"更新指标 {old_metric.name}"
            )
        
        if dataset_id:
            await MetadataService._mark_dataset_as_modified(db, dataset_id)
            
        await db.commit()

        # 同步本地 Redis 向量
        if dataset_id:
            try:
                from app.services.ai.metadata_index_service import MetadataIndexService
                await MetadataIndexService.sync_local_redis_vector(dataset_id)
            except Exception as ex:
                logger.warning("[Local Redis Sync] Failed to trigger sync in update_metric: %s", ex)

        result = await db.execute(select(MetaMetric).where(MetaMetric.id == metric_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def delete_metric(db: AsyncSession, metric_id: int, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None):
        # 1. Get old data BEFORE delete
        old_metric_result = await db.execute(select(MetaMetric).where(MetaMetric.id == metric_id))
        old_metric = old_metric_result.scalar_one_or_none()

        res = await db.execute(select(MetaMetric.dataset_id).where(MetaMetric.id == metric_id))
        dataset_id = res.scalar_one_or_none()

        # 2. Delete
        query = delete(MetaMetric).where(MetaMetric.id == metric_id)
        await db.execute(query)

        # 3. Record changelog (old_metric was fetched BEFORE delete)
        if old_metric:
            old_data = {
                "name": old_metric.name,
                "display_name": old_metric.display_name,
                "description": old_metric.description,
                "calculation_logic": old_metric.calculation_logic,
                "unit": old_metric.unit
            }
            await ChangelogService.log_change(
                db=db,
                resource_type="metric",
                resource_id=str(old_metric.name),
                operation="delete",
                user_id=user_id,
                user_name=user_name,
                old_data=old_data,
                reason=reason or f"删除指标 {old_metric.name}"
            )

        if dataset_id:
            await MetadataService._mark_dataset_as_modified(db, dataset_id)

        await db.commit()

        # 同步本地 Redis 向量
        if dataset_id:
            try:
                from app.services.ai.metadata_index_service import MetadataIndexService
                await MetadataIndexService.sync_local_redis_vector(dataset_id)
            except Exception as ex:
                logger.warning("[Local Redis Sync] Failed to trigger sync in delete_metric: %s", ex)

    # --- Relationships CRUD ---

    @staticmethod
    async def create_relationship(db: AsyncSession, dataset_id: int, data: dict, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None) -> MetaRelationship:
        rel = MetaRelationship(**data)
        db.add(rel)
        await db.flush()  # 先 flush 获取自增 ID，避免 changelog resource_id 为 None
        await MetadataService._mark_dataset_as_modified(db, dataset_id)
        
        # Record changelog for relationship creation
        new_data = {
            "source_table_id": rel.source_table_id,
            "target_table_id": rel.target_table_id,
            "join_condition": rel.join_condition,
            "join_type": rel.join_type,
            "description": rel.description
        }
        
        await ChangelogService.log_change(
            db=db,
            resource_type="relationship",
            resource_id=str(rel.id),
            operation="create",
            user_id=user_id,
            user_name=user_name,
            new_data=new_data,
            reason=reason or f"创建关系 {rel.source_table_id}->{rel.target_table_id}"
        )
        
        await db.commit()
        await db.refresh(rel)

        # 同步本地 Redis 向量
        try:
            from app.services.ai.metadata_index_service import MetadataIndexService
            await MetadataIndexService.sync_local_redis_vector(dataset_id)
        except Exception as ex:
            logger.warning("[Local Redis Sync] Failed to trigger sync in create_relationship: %s", ex)

        return rel

    @staticmethod
    async def get_relationships_by_dataset(db: AsyncSession, dataset_id: int) -> List[MetaRelationship]:
        # Find all relationships where source OR target table belongs to this dataset
        # This requires a JOIN
        
        # 1. Get Table IDs in this dataset
        stmt = select(MetaTable.id).where(MetaTable.dataset_id == dataset_id)
        result = await db.execute(stmt)
        table_ids = result.scalars().all()
        
        if not table_ids:
            return []
            
        # 2. Find relations
        stmt = select(MetaRelationship).where(
            (MetaRelationship.source_table_id.in_(table_ids)) | 
            (MetaRelationship.target_table_id.in_(table_ids))
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update_relationship(db: AsyncSession, rel_id: int, data: dict, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None) -> Optional[MetaRelationship]:
        # Find dataset_id and old data
        res = await db.execute(
            select(MetaTable.dataset_id)
            .join(MetaRelationship, MetaTable.id == MetaRelationship.source_table_id)
            .where(MetaRelationship.id == rel_id)
        )
        dataset_id = res.scalar_one_or_none()

        # Get old data BEFORE update
        old_rel_result = await db.execute(select(MetaRelationship).where(MetaRelationship.id == rel_id))
        old_rel = old_rel_result.scalar_one_or_none()
        old_data = None
        if old_rel:
            old_data = {
                "source_table_id": old_rel.source_table_id,
                "target_table_id": old_rel.target_table_id,
                "join_condition": old_rel.join_condition,
                "join_type": old_rel.join_type,
                "description": old_rel.description
            }

        query = update(MetaRelationship).where(MetaRelationship.id == rel_id).values(**data)
        await db.execute(query)

        # Record changelog
        if old_data:
            new_data = {
                "source_table_id": data.get("source_table_id", old_rel.source_table_id),
                "target_table_id": data.get("target_table_id", old_rel.target_table_id),
                "join_condition": data.get("join_condition", old_rel.join_condition),
                "join_type": data.get("join_type", old_rel.join_type),
                "description": data.get("description", old_rel.description)
            }
            await ChangelogService.log_change(
                db=db,
                resource_type="relationship",
                resource_id=str(rel_id),
                operation="update",
                user_id=user_id,
                user_name=user_name,
                old_data=old_data,
                new_data=new_data,
                reason=reason or f"更新关系 {rel_id}"
            )

        if dataset_id:
            await MetadataService._mark_dataset_as_modified(db, dataset_id)

        await db.commit()

        # 同步本地 Redis 向量
        if dataset_id:
            try:
                from app.services.ai.metadata_index_service import MetadataIndexService
                await MetadataIndexService.sync_local_redis_vector(dataset_id)
            except Exception as ex:
                logger.warning("[Local Redis Sync] Failed to trigger sync in update_relationship: %s", ex)

        result = await db.execute(select(MetaRelationship).where(MetaRelationship.id == rel_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def delete_relationship(db: AsyncSession, rel_id: int, user_id: Optional[int] = None, user_name: Optional[str] = None, reason: Optional[str] = None):
        # 1. Get old data and dataset_id BEFORE delete
        res = await db.execute(
            select(MetaTable.dataset_id)
            .join(MetaRelationship, MetaTable.id == MetaRelationship.source_table_id)
            .where(MetaRelationship.id == rel_id)
        )
        dataset_id = res.scalar_one_or_none()

        old_rel_result = await db.execute(select(MetaRelationship).where(MetaRelationship.id == rel_id))
        old_rel = old_rel_result.scalar_one_or_none()

        # 2. Delete
        query = delete(MetaRelationship).where(MetaRelationship.id == rel_id)
        await db.execute(query)

        # 3. Record changelog
        if old_rel:
            old_data = {
                "source_table_id": old_rel.source_table_id,
                "target_table_id": old_rel.target_table_id,
                "join_condition": old_rel.join_condition,
                "join_type": old_rel.join_type,
                "description": old_rel.description
            }
            await ChangelogService.log_change(
                db=db,
                resource_type="relationship",
                resource_id=str(rel_id),
                operation="delete",
                user_id=user_id,
                user_name=user_name,
                old_data=old_data,
                reason=reason or f"删除关系 {rel_id}"
            )

        if dataset_id:
            await MetadataService._mark_dataset_as_modified(db, dataset_id)

        await db.commit()

        # 同步本地 Redis 向量
        if dataset_id:
            try:
                from app.services.ai.metadata_index_service import MetadataIndexService
                await MetadataIndexService.sync_local_redis_vector(dataset_id)
            except Exception as ex:
                logger.warning("[Local Redis Sync] Failed to trigger sync in delete_relationship: %s", ex)

    # --- YAML Export Logic ---

    @staticmethod
    async def build_dataset_schema_chunk_contents(db: AsyncSession, dataset_id: int) -> List[str]:
        """按单表/指标块生成与向量索引一致的 YAML 正文列表。"""
        from app.services.config_service import ConfigService
        from app.services.metadata_rag_service import MetadataRagService

        dataset = await MetadataService.get_dataset_by_id(db, dataset_id, is_admin=True)
        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found")

        ds_source = dataset.data_source
        if not ds_source:
            ds_source = await ConfigService.get("external_sql_data_source", default="default_clickhouse")

        relationships = await MetadataService.get_relationships_by_dataset(db, dataset_id)
        chunks: List[str] = []
        for table in dataset.tables:
            if hasattr(table, "status") and table.status != 1:
                continue
            chunks.append(
                MetadataRagService.render_table_schema_yaml(
                    dataset,
                    table,
                    relationships,
                    data_source=ds_source,
                )
            )

        metrics_yaml = MetadataRagService.render_metrics_schema_yaml(dataset, data_source=ds_source)
        if metrics_yaml:
            chunks.append(metrics_yaml)
        return chunks

    @staticmethod
    async def export_dataset_yaml(db: AsyncSession, dataset_id: int) -> str:
        """
        Export the entire dataset as formatted schema chunks for LLM/RAG (canonical table YAML).
        """
        from app.services.schema_chunk_format import format_schema_chunk

        raw_chunks = await MetadataService.build_dataset_schema_chunk_contents(db, dataset_id)
        formatted: List[str] = []
        for index, content in enumerate(raw_chunks, start=1):
            piece = format_schema_chunk(index, content)
            if piece:
                formatted.append(piece)
        return "\n\n".join(formatted)
