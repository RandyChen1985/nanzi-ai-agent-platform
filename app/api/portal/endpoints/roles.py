from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, desc, func
from app.core.dependencies import require_admin, require_permission
from app.core.orm import get_db_session
from app.models.permission import Role, ResourcePermission, UserRoleRelation
from app.schemas.permission import PermissionUpdate
from app.services.permission_service import PermissionService

router = APIRouter()

# --- Request Models ---

class CreateRoleRequest(BaseModel):
    code: str
    name: str
    description: Optional[str] = None

class UpdateRoleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class RoleResponse(BaseModel):
    id: int
    code: str
    name: str
    description: Optional[str]
    created_at: Optional[str]
    user_count: int = 0

class RoleListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: List[RoleResponse]

class BulkAssignUsersRequest(BaseModel):
    user_ids: List[int]

# --- Endpoints ---

@router.get("", response_model=RoleListResponse)
async def list_roles(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=1000),
    search: Optional[str] = None,
    admin: dict = Depends(require_permission("menu", "menu:system:roles")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    List all business roles.
    """
    stmt = select(Role).order_by(desc(Role.created_at))
    
    if search:
        stmt = stmt.where(Role.name.like(f"%{search}%") | Role.code.like(f"%{search}%"))
        
    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar()
    
    # Page
    stmt = stmt.offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    roles = result.scalars().all()
    
    items = []
    for role in roles:
        # Get user count (optional, can be optimized with subquery or separate call if slow)
        # Using simple query here
        user_count_stmt = select(func.count()).select_from(UserRoleRelation).where(UserRoleRelation.role_id == role.id)
        user_count = (await db.execute(user_count_stmt)).scalar()
        
        items.append({
            "id": role.id,
            "code": role.code,
            "name": role.name,
            "description": role.description,
            "created_at": role.created_at.isoformat() if role.created_at else None,
            "user_count": user_count
        })

    return {
        "total": total,
        "page": page,
        "size": size,
        "items": items
    }

@router.post("")
async def create_role(
    request: CreateRoleRequest,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create a new role.
    """
    # Check duplicate code
    existing = await db.execute(select(Role).where(Role.code == request.code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Role code already exists")
        
    new_role = Role(
        code=request.code,
        name=request.name,
        description=request.description
    )
    db.add(new_role)
    await db.commit()
    await db.refresh(new_role)
    
    return {
        "id": new_role.id,
        "code": new_role.code,
        "name": new_role.name,
        "description": new_role.description,
        "created_at": new_role.created_at.isoformat()
    }

@router.put("/{role_id}")
async def update_role(
    role_id: int,
    request: UpdateRoleRequest,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update role details.
    """
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
        
    if request.name is not None:
        role.name = request.name
    if request.description is not None:
        role.description = request.description
        
    await db.commit()
    await db.refresh(role)
    return {
        "id": role.id,
        "code": role.code,
        "name": role.name,
        "description": role.description,
        "created_at": role.created_at.isoformat()
    }

@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Delete a role.
    """
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    users_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == role_id)
    users_result = await db.execute(users_stmt)
    affected_user_ids = list(users_result.scalars().all())

    # Clean up permissions
    await db.execute(delete(ResourcePermission).where(ResourcePermission.role_id == role_id))

    # Clean up user relations
    await db.execute(delete(UserRoleRelation).where(UserRoleRelation.role_id == role_id))
    
    await db.delete(role)
    await db.commit()

    if affected_user_ids:
        perm_service = PermissionService(db)
        await perm_service.invalidate_cached_permissions_for_users(affected_user_ids)

    return {"message": "Role deleted successfully"}

# --- Role User Management ---

@router.get("/{role_id}/users")
async def get_role_users(
    role_id: int,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get users assigned to this role.
    """
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
        
    stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == role_id)
    result = await db.execute(stmt)
    user_ids = result.scalars().all()
    
    return {"user_ids": user_ids}

@router.post("/{role_id}/users")
async def bulk_assign_role_users(
    role_id: int,
    request: BulkAssignUsersRequest,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Bulk assign users to this role.
    This replaces all user assignments FOR THIS ROLE ONLY.
    It does not affect other roles assigned to the users.
    """
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    
    target_user_ids = set(request.user_ids)
    
    # 1. Get current user assignments for this role
    stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == role_id)
    result = await db.execute(stmt)
    current_user_ids = set(result.scalars().all())
    
    # 2. Identify users to add and remove
    ids_to_add = target_user_ids - current_user_ids
    ids_to_remove = current_user_ids - target_user_ids
    
    # 3. Perform removals
    if ids_to_remove:
        delete_stmt = delete(UserRoleRelation).where(
            UserRoleRelation.role_id == role_id,
            UserRoleRelation.user_id.in_(ids_to_remove)
        )
        await db.execute(delete_stmt)
        
    # 4. Perform additions
    for uid in ids_to_add:
        relation = UserRoleRelation(user_id=uid, role_id=role_id)
        db.add(relation)
        
    await db.commit()

    affected_user_ids = ids_to_add | ids_to_remove
    if affected_user_ids:
        perm_service = PermissionService(db)
        await perm_service.invalidate_cached_permissions_for_users(affected_user_ids)

    return {"message": f"Successfully updated role users: added {len(ids_to_add)}, removed {len(ids_to_remove)}"}

# --- Role Resource Permissions ---

@router.get("/{role_id}/permissions")
async def get_role_resources(
    role_id: int,
    request: Request,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get resources assigned to this role.
    """
    from app.core.v1_api_access import normalize_api_permission_ids

    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
        
    service = PermissionService(db)
    response = await service.get_role_permissions(role_id)
    response.permissions.apis = normalize_api_permission_ids(
        request.app,
        response.permissions.apis or [],
    )
    return response

@router.put("/{role_id}/permissions")
async def update_role_resources(
    role_id: int,
    permissions: PermissionUpdate,
    request: Request,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Assign resources to this role.
    """
    from app.core.v1_api_access import normalize_api_permission_ids

    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    permissions.apis = normalize_api_permission_ids(request.app, permissions.apis or [])
        
    service = PermissionService(db)
    await service.update_role_permissions(role_id, permissions)
    return {"message": "Role permissions updated successfully"}
