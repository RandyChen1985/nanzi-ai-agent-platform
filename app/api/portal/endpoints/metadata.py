from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Any
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

from app.core.orm import get_db_session
from app.services.metadata_service import MetadataService
from app.schemas.metadata import (
    DatasetCreate, DatasetUpdate, DatasetResponse, DatasetDetailResponse, 
    TableCreate, TableResponse,
    MetricSchema, MetricResponse,
    RelationshipSchema, RelationshipResponse
)
from app.models.user import User
from app.models.permission import Role
from app.core.dependencies import require_admin, get_current_user, require_permission
from app.core.errors import ErrorCode
from app.services.permission_service import PermissionService

router = APIRouter()

# --- Dataset APIs ---

@router.get("/datasets", response_model=List[DatasetResponse])
async def list_datasets(
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata"))
):
    """获取所有数据集 (需菜单权限)"""
    datasets = await MetadataService.get_datasets(conn)
    await MetadataService.repair_stale_local_sync_flags(datasets)
    return datasets

@router.post("/datasets", response_model=DatasetResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def create_dataset(
    dataset: DatasetCreate, 
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """创建数据集 (管理员或具有编辑权限的用户)"""
    exists = await MetadataService.get_dataset_by_name(conn, dataset.name)
    if exists:
        raise HTTPException(status_code=400, detail="数据集名称已存在")
    
    return await MetadataService.create_dataset(
        conn, 
        dataset.model_dump(),
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="创建数据集"
    )

@router.get("/datasets/{dataset_id}", response_model=DatasetDetailResponse)
async def get_dataset(
    dataset_id: int, 
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata"))
):
    """获取单个数据集详情 (需菜单权限)"""
    is_admin = user.get("role") == "admin"
    ds = await MetadataService.get_dataset_by_id(
        conn, 
        dataset_id, 
        user_id=user.get("id"), 
        is_admin=is_admin
    )
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")
    await MetadataService.repair_stale_local_sync_flags([ds])
    return ds


@router.get("/datasets/{dataset_id}/permissions")
async def get_metadata_dataset_permissions(
    dataset_id: int,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """获取元数据数据集授权的所有角色和用户列表"""
    from app.models.permission import ResourcePermission, Role
    from app.models.user import User
    from sqlalchemy import select

    stmt = select(ResourcePermission).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.resource_id == str(dataset_id),
        ResourcePermission.enabled == True
    )
    result = await conn.execute(stmt)
    perms = result.scalars().all()

    user_ids = [p.user_id for p in perms if p.user_id is not None]
    role_ids = [p.role_id for p in perms if p.role_id is not None]

    granted_users = []
    granted_roles = []

    if user_ids:
        user_stmt = select(User.id, User.user_name, User.real_name).where(User.id.in_(user_ids), User.status == 1)
        user_res = await conn.execute(user_stmt)
        granted_users = [{"id": u.id, "user_name": u.user_name, "real_name": u.real_name} for u in user_res.all()]

    if role_ids:
        role_stmt = select(Role.id, Role.code, Role.name).where(Role.id.in_(role_ids))
        role_res = await conn.execute(role_stmt)
        granted_roles = [{"id": r.id, "code": r.code, "name": r.name} for r in role_res.all()]

    return {
        "code": 0,
        "data": {
            "users": granted_users,
            "roles": granted_roles
        }
    }


class AddPermissionsRequest(BaseModel):
    target_type: str  # "user" 或 "role"
    target_ids: List[int]

class DeletePermissionRequest(BaseModel):
    target_type: str  # "user" 或 "role"
    target_id: int


@router.get("/candidates")
async def get_metadata_auth_candidates(
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("element", "element:metadata:edit"))
):
    """获取可用于数据集权限分配的活跃角色和用户候选列表 (需数据集编辑权限)"""
    from app.models.permission import Role
    from app.models.user import User
    from sqlalchemy import select

    role_stmt = select(Role.id, Role.code, Role.name)
    role_res = await conn.execute(role_stmt)
    roles = [{"id": r.id, "code": r.code, "name": r.name} for r in role_res.all()]

    user_stmt = select(User.id, User.user_name, User.real_name).where(User.status == 1)
    user_res = await conn.execute(user_stmt)
    users = [{"id": u.id, "user_name": u.user_name, "real_name": u.real_name} for u in user_res.all()]

    return {
        "code": 0,
        "data": {
            "roles": roles,
            "users": users
        }
    }


@router.post("/datasets/{dataset_id}/permissions", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def add_metadata_dataset_permissions(
    dataset_id: int,
    payload: AddPermissionsRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """添加元数据数据集的授权角色或用户 (需数据集编辑权限)"""
    from app.models.permission import ResourcePermission, UserRoleRelation
    from sqlalchemy import select
    
    perm_service = PermissionService(conn)
    affected_user_ids = set()

    for tid in payload.target_ids:
        # 查询是否已经有对应记录了
        stmt = select(ResourcePermission).where(
            ResourcePermission.resource_type == "metadata",
            ResourcePermission.resource_id == str(dataset_id)
        )
        if payload.target_type == "user":
            stmt = stmt.where(ResourcePermission.user_id == tid)
            affected_user_ids.add(tid)
        else:
            stmt = stmt.where(ResourcePermission.role_id == tid)
            # 获取该角色下的所有关联用户
            r_users_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == tid)
            r_users_res = await conn.execute(r_users_stmt)
            for uid in r_users_res.scalars().all():
                affected_user_ids.add(uid)

        result = await conn.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.enabled = True
        else:
            new_perm = ResourcePermission(
                resource_type="metadata",
                resource_id=str(dataset_id),
                enabled=True
            )
            if payload.target_type == "user":
                new_perm.user_id = tid
            else:
                new_perm.role_id = tid
            conn.add(new_perm)

    await conn.flush()

    # 清理受影响用户的缓存，使其变更即刻生效
    if affected_user_ids:
        await perm_service.invalidate_cached_permissions_for_users(affected_user_ids)
        from app.services.ai.config import AgentConfigProvider
        for uid in affected_user_ids:
            await AgentConfigProvider.invalidate_dataset_menu_cache(user_id=uid)

    return {"code": 0, "message": "权限配置已成功添加"}


@router.delete("/datasets/{dataset_id}/permissions", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def delete_metadata_dataset_permission(
    dataset_id: int,
    payload: DeletePermissionRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """移除元数据数据集关联的某个角色或用户授权 (需数据集编辑权限)"""
    from app.models.permission import ResourcePermission, UserRoleRelation
    from sqlalchemy import select, delete

    perm_service = PermissionService(conn)
    affected_user_ids = set()

    # 查出受影响的用户列表以作缓存清理
    if payload.target_type == "user":
        affected_user_ids.add(payload.target_id)
    else:
        r_users_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == payload.target_id)
        r_users_res = await conn.execute(r_users_stmt)
        for uid in r_users_res.scalars().all():
            affected_user_ids.add(uid)

    # 物理清除
    stmt = delete(ResourcePermission).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.resource_id == str(dataset_id)
    )
    if payload.target_type == "user":
        stmt = stmt.where(ResourcePermission.user_id == payload.target_id)
    else:
        stmt = stmt.where(ResourcePermission.role_id == payload.target_id)

    await conn.execute(stmt)
    await conn.flush()

    # 清理权限缓存
    if affected_user_ids:
        await perm_service.invalidate_cached_permissions_for_users(affected_user_ids)
        from app.services.ai.config import AgentConfigProvider
        for uid in affected_user_ids:
            await AgentConfigProvider.invalidate_dataset_menu_cache(user_id=uid)

    return {"code": 0, "message": "权限配置已成功移除"}


@router.put("/datasets/{dataset_id}", response_model=DatasetResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def update_dataset(
    dataset_id: int, 
    dataset: DatasetUpdate, 
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """更新数据集 (管理员或具有编辑权限的用户)"""
    updated = await MetadataService.update_dataset(
        conn, 
        dataset_id, 
        dataset.model_dump(exclude_unset=True),
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="更新数据集"
    )
    if not updated:
        raise HTTPException(status_code=404, detail="数据集不存在")
    return updated

@router.delete("/datasets/{dataset_id}", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def delete_dataset(
    dataset_id: int, 
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """删除数据集 (管理员或具有编辑权限的用户)"""
    await MetadataService.delete_dataset(
        conn, 
        dataset_id,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="删除数据集"
    )
    return {"message": "Deleted successfully"}

@router.get("/datasets/{dataset_id}/yaml", dependencies=[Depends(require_permission("element", "element:metadata:view_yaml"))])
async def get_dataset_yaml(dataset_id: int, conn: AsyncSession = Depends(get_db_session)):
    """获取该数据集导出给 AI 的 YAML 文本 (需查看 YAML 权限)"""
    return {
        "code": 200,
        "message": "success",
        "data": await MetadataService.export_dataset_yaml(conn, dataset_id)
    }

from app.services.metadata_rag_service import MetadataRagService
from fastapi import BackgroundTasks

@router.post("/datasets/{dataset_id}/rag/sync", dependencies=[Depends(require_permission("element", "element:metadata:sync"))])
async def sync_dataset_to_rag(
    dataset_id: int, 
    background_tasks: BackgroundTasks,
    conn: AsyncSession = Depends(get_db_session)
):
    """
    手动同步数据集元数据到 RAGFlow (管理员或具有同步权限的用户)
    """
    # 1. Verify exists
    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")
    
    # 2. Check status
    if ds.status != 1:
        return {"code": 400, "message": "该数据集已禁用，无法同步"}
    
    if ds.rag_sync_status == 1:
        return {"code": 400, "message": "该数据集正在同步中，请勿重复操作"}

    # 3. Trigger async task
    # We pass dataset_id. The service will open its own session or we use a factory.
    # To keep it simple and safe for BackgroundTasks, the service will handle its session.
    from app.core.orm import AsyncSessionLocal
    
    async def run_sync():
        async with AsyncSessionLocal() as session:
            await MetadataRagService.sync_dataset(session, dataset_id)

    background_tasks.add_task(run_sync)
    
    return {
        "code": 200, 
        "message": "同步任务已启动，请稍后刷新查看状态",
        "data": {"rag_sync_status": 1}
    }

# --- Metric APIs ---

@router.post("/datasets/{dataset_id}/metrics", response_model=MetricResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def create_metric(dataset_id: int, metric: MetricSchema, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """创建指标"""
    return await MetadataService.create_metric(
        conn, dataset_id, metric.model_dump(),
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="创建指标"
    )

@router.put("/metrics/{metric_id}", response_model=MetricResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def update_metric(metric_id: int, metric: MetricSchema, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """更新指标"""
    updated = await MetadataService.update_metric(
        conn, metric_id, metric.model_dump(exclude_unset=True),
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="更新指标"
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Metric not found")
    return updated

@router.delete("/metrics/{metric_id}", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def delete_metric(metric_id: int, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """删除指标"""
    await MetadataService.delete_metric(
        conn, metric_id,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="删除指标"
    )
    return {"message": "Metric deleted"}

@router.get("/datasets/{dataset_id}/metrics", response_model=List[MetricResponse])
async def list_metrics(dataset_id: int, conn: AsyncSession = Depends(get_db_session)):
    """获取数据集下的所有指标"""
    return await MetadataService.get_metrics_by_dataset(conn, dataset_id)

# --- Relationship APIs ---

@router.post("/datasets/{dataset_id}/relationships", response_model=RelationshipResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def create_relationship(dataset_id: int, rel: RelationshipSchema, conn: AsyncSession = Depends(get_db_session)):
    """创建表关联关系"""
    return await MetadataService.create_relationship(conn, dataset_id, rel.model_dump())

@router.put("/relationships/{rel_id}", response_model=RelationshipResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def update_relationship(rel_id: int, rel: RelationshipSchema, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """更新关联关系"""
    updated = await MetadataService.update_relationship(
        conn, rel_id, rel.model_dump(exclude_unset=True),
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="更新关系"
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return updated

@router.delete("/relationships/{rel_id}", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def delete_relationship(rel_id: int, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """删除关联关系"""
    await MetadataService.delete_relationship(
        conn, rel_id,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="删除关系"
    )
    return {"message": "Relationship deleted"}

@router.get("/datasets/{dataset_id}/relationships", response_model=List[RelationshipResponse])
async def list_relationships(dataset_id: int, conn: AsyncSession = Depends(get_db_session)):
    """获取数据集相关的关系"""
    return await MetadataService.get_relationships_by_dataset(conn, dataset_id)


@router.get("/all-tables")
async def list_all_tables(
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata")),
):
    """获取所有有权限数据集及其表，按数据集分组，用于跨数据集关联关系配置的目标表选择器。

    返回格式：
    [
      {
        "dataset_id": 1,
        "dataset_name": "hr_data",
        "display_name": "HR 人员数据",
        "tables": [
          {"id": 10, "physical_name": "employees", "term": "员工信息表"}
        ]
      }
    ]
    """
    user_id = int(user.get("user_id") or 0) or None
    is_admin = user.get("role") == "admin"
    return await MetadataService.get_all_tables_with_dataset(
        conn,
        user_id=user_id,
        is_admin=is_admin,
    )

# --- Table APIs ---

@router.post("/datasets/{dataset_id}/tables", response_model=TableResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def save_table(dataset_id: int, table: TableCreate, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """保存或更新表结构"""
    # Verify dataset exists
    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
         raise HTTPException(status_code=404, detail="数据集不存在")
         
    return await MetadataService.save_table_metadata(
        conn, 
        dataset_id, 
        table.model_dump(),
        user_id=int(user.get('user_id') or 0),
        user_name=user.get('user_name'),
        reason="保存表结构"
    )

@router.delete("/datasets/{dataset_id}/tables/{table_name}", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def delete_table(dataset_id: int, table_name: str, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """从数据集中删除表结构"""
    await MetadataService.delete_table_metadata(
        conn, dataset_id, table_name,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="删除表"
    )
    return {"message": "Table deleted successfully"}

@router.post("/datasets/{dataset_id}/metrics/recommend", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def recommend_metrics(dataset_id: int, conn: AsyncSession = Depends(get_db_session)):
    """
    智能推荐指标 (返回建议值，不直接入库)
    """
    # 1. Get Schema Context
    from app.services.metadata_service import MetadataService
    from app.services.metadata_generator import MetadataGeneratorService
    
    # Verify dataset exists and get context
    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")
         
    schema_yaml = await MetadataService.export_dataset_yaml(conn, dataset_id)
    
    # 2. Call Generator
    result = await MetadataGeneratorService.recommend_metrics(dataset_id, schema_yaml)
    
    return {
        "code": 200,
        "message": "success",
        "data": result
    }

@router.post("/datasets/{dataset_id}/enhance-metadata", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def enhance_dataset_metadata(dataset_id: int, conn: AsyncSession = Depends(get_db_session)):
    """
    AI 辅助生成元数据: 根据数据集下的表信息自动生成描述和标签
    """
    from app.services.metadata_service import MetadataService
    from app.services.metadata_generator import MetadataGeneratorService
    
    # 1. 获取数据集详情（包含表列表）
    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")
    
    # 2. 构建表摘要信息供 AI 分析
    table_list = []
    if hasattr(ds, "tables") and ds.tables:
        for tbl in ds.tables:
            table_list.append(f"- 物理名: {tbl.physical_name}, 业务术语: {tbl.term}")
    
    if not table_list:
        raise HTTPException(status_code=400, detail="该数据集尚未添加任何表，AI 无法分析")
        
    tables_summary = "\n".join(table_list)
    
    # 3. 调用 AI 生成器
    result = await MetadataGeneratorService.enhance_dataset_metadata(dataset_id, tables_summary)
    
    return {
        "code": 200,
        "message": "success",
        "data": result
    }

from app.services.metadata_generator import MetadataGeneratorService

@router.post("/tables/import", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def import_ddl(ddl: dict): # Expects {"ddl": "..."}
    """
    智能辅助: 解析 DDL 并返回预览用的 Metadata 结构 (管理员或具有智能导入权限的用户)
    注意：此接口不保存数据，只返回 AI 生成的建议值供前端填充表单
    """
    content = ddl.get("ddl", "")
    if not content:
        raise HTTPException(status_code=400, detail="DDL content cannot be empty")
        
    result = await MetadataGeneratorService.generate_from_ddl(content)
    return {
        "code": 200,
        "message": "success",
        "data": result
    }

from app.services.db_import_service import DBImportService
from app.schemas.metadata import DBConnectionConfig, DDLRequest

@router.post("/db/test-connection", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def test_db_connection(config: DBConnectionConfig):
    """测试外部数据库连接"""
    try:
        if config.type == "mysql":
            await DBImportService.test_mysql_connection(config.model_dump())
        elif config.type == "clickhouse":
            await DBImportService.test_clickhouse_connection(config.model_dump())
        elif config.type == "oracle":
            await DBImportService.test_oracle_connection(config.model_dump())
        elif config.type in DBImportService._sqlserver_type_aliases():
            await DBImportService.test_sqlserver_connection(config.model_dump())
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported DB type: {config.type}")
        return {"code": 200, "message": "Connection successful"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/db/tables", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def list_db_tables(config: DBConnectionConfig):
    """获取外部数据库表列表"""
    try:
        if config.type == "mysql":
            tables = await DBImportService.get_mysql_tables(config.model_dump())
        elif config.type == "clickhouse":
            tables = await DBImportService.get_clickhouse_tables(config.model_dump())
        elif config.type == "oracle":
            tables = await DBImportService.get_oracle_tables(config.model_dump())
        elif config.type in DBImportService._sqlserver_type_aliases():
            tables = await DBImportService.get_sqlserver_tables(config.model_dump())
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported DB type: {config.type}")
        return {"code": 200, "data": tables}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/db/ddl", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def get_db_ddl(request: DDLRequest):
    """获取指定表的 DDL"""
    try:
        config = request.config
        if config.type == "mysql":
            ddl = await DBImportService.get_mysql_ddl(config.model_dump(), request.tables)
        elif config.type == "clickhouse":
            ddl = await DBImportService.get_clickhouse_ddl(config.model_dump(), request.tables)
        elif config.type == "oracle":
            ddl = await DBImportService.get_oracle_ddl(config.model_dump(), request.tables)
        elif config.type in DBImportService._sqlserver_type_aliases():
            ddl = await DBImportService.get_sqlserver_ddl(config.model_dump(), request.tables)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported DB type: {config.type}")
        return {"code": 200, "data": ddl}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- DB Connection Config APIs ---

from app.schemas.db_connection import DbConnectionConfigCreate, DbConnectionConfigSafeResponse
from app.services.db_connection_service import DbConnectionService

@router.get("/db/connection-configs", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def list_db_connection_configs(
    conn: AsyncSession = Depends(get_db_session)
):
    """获取所有已保存的数据库连接配置"""
    configs = await DbConnectionService.list_configs(conn)
    result = [DbConnectionConfigSafeResponse.model_validate(c) for c in configs]
    return {"code": 200, "data": [r.model_dump() for r in result]}


@router.post("/db/connection-configs", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def create_db_connection_config(
    payload: DbConnectionConfigCreate,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """保存一条数据库连接配置（连接测试通过后调用）"""
    try:
        config = await DbConnectionService.create_config(
            conn,
            payload.model_dump(),
            user_id=int(user.get("user_id") or 0)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"code": 200, "data": DbConnectionConfigSafeResponse.model_validate(config).model_dump()}


@router.put("/db/connection-configs/{config_id}", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def update_db_connection_config(
    config_id: int,
    payload: DbConnectionConfigCreate,
    conn: AsyncSession = Depends(get_db_session)
):
    """更新指定数据库连接配置"""
    try:
        config = await DbConnectionService.update_config(conn, config_id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not config:
        raise HTTPException(status_code=404, detail="连接配置不存在")
    from app.services.pool_manager import DataSourcePoolManager
    await DataSourcePoolManager.invalidate_pool(config_id)
    return {"code": 200, "data": DbConnectionConfigSafeResponse.model_validate(config).model_dump()}


@router.delete("/db/connection-configs/{config_id}", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def delete_db_connection_config(
    config_id: int,
    conn: AsyncSession = Depends(get_db_session)
):
    """删除指定连接配置"""
    deleted = await DbConnectionService.delete_config(conn, config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="连接配置不存在")
    from app.services.pool_manager import DataSourcePoolManager
    await DataSourcePoolManager.invalidate_pool(config_id)
    return {"code": 200, "message": "已删除"}


class DebugSqlRequest(BaseModel):
    sql: str
    limit: int = 100


@router.post("/db/connection-configs/{config_id}/preview", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def preview_db_connection_sql(
    config_id: int,
    payload: DebugSqlRequest,
    conn: AsyncSession = Depends(get_db_session)
):
    """
    数据源直连在线调试 SQL 执行接口 (不经过外部 API HTTP 转发，直接通过本地自研 Adapter)
    """
    config = await DbConnectionService.get_config(conn, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="数据库配置不存在")

    try:
        from app.services.data_adapter.factory import get_adapter
        adapter = await get_adapter(config.name)
    except Exception as e:
        logger.exception("Failed to initialize database adapter for debug")
        raise HTTPException(status_code=400, detail=f"初始化数据库适配器失败: {str(e)}")

    try:
        # 执行带有 SELECT 强安全性拦截与行数截断保护的 preview 方法
        res = await adapter.preview(payload.sql, limit=min(max(int(payload.limit or 100), 1), 1000))
        return {"code": 200, "data": res}
    except Exception as e:
        logger.exception("Failed to execute debug SQL query")
        raise HTTPException(status_code=400, detail=f"执行 SQL 失败: {str(e)}")
