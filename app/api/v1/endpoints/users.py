from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.dependencies import require_api_key, get_db_session, verify_v1_api_access
from app.models.user import User
from app.services.permission_service import PermissionService

router = APIRouter()

from typing import List, Optional
from pydantic import BaseModel, Field
from app.schemas.response import StandardResponse

class UserProfile(BaseModel):
    id: int = Field(..., description="用户ID", json_schema_extra={"example": 123})
    username: str = Field(..., description="用户名", json_schema_extra={"example": "admin"})
    display_name: str = Field(..., description="显示名称", json_schema_extra={"example": "Administrator"})
    role: str = Field(..., description="角色 (admin/user)", json_schema_extra={"example": "admin"})
    status: int = Field(..., description="状态 (1=启用, 0=禁用)", json_schema_extra={"example": 1})
    api_key: Optional[str] = Field(None, description="API Key (仅自己在查自己时可见)", json_schema_extra={"example": "sk-r8s7..."})
    roles: List[str] = Field(default_factory=list, description="所属角色列表", json_schema_extra={"example": ["admin", "editor"]})
    permissions: List[str] = Field(default_factory=list, description="权限列表", json_schema_extra={"example": ["api:read", "agent:write"]})

@router.get("/profile", 
    response_model=StandardResponse[UserProfile],
    dependencies=[Depends(verify_v1_api_access)],
    summary="获取用户画像",
    description="获取当前或指定用户的详细信息（包括角色和权限）。展示 API Key。",
    responses={
        404: {"description": "用户未找到"},
        403: {"description": "无权限"}
    }
)
async def get_user_profile(
    username: str = Query(None, description="目标用户名。如果不传，默认返回当前 API Key 所属用户的信息。"),
    current_user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get user profile details including roles and permissions.
    """
    
    # 1. Determine Target User
    if not username:
        # Default to self
        user_id = int(current_user["user_id"])
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
    else:
        # Querying specific user - No extra security check required per new requirements.
        # Any authorized user (verified by dependency) can search for any user.
        
        stmt = select(User).where(User.user_name == username)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Security: Do not expose profile of disabled users
    if user.status != 1:
        raise HTTPException(status_code=403, detail="User account is disabled")
        
    # 2. Get Permissions
    service = PermissionService(db)
    perms_resp = await service.get_user_permissions(user.id)
    
    # Flatten permissions for simple list view
    flat_permissions = []
    if perms_resp.permissions:
        p_dict = perms_resp.permissions.model_dump()
        for r_type, r_ids in p_dict.items():
            for r_id in r_ids:
                flat_permissions.append(f"{r_type}:{r_id}")

    # 3. Get Decrypted API Key
    from app.services.auth_service import AuthService
    
    # Security Rule: Hide API Key if a normal user queries an admin
    current_user_role = current_user.get("role", "user")
    should_hide_key = (current_user_role != "admin" and user.role == "admin")
    
    api_key = None
    if not should_hide_key:
        api_key = await AuthService.get_decrypted_api_key(user.id, db)
    
    # Construct Response
    data = UserProfile(
        id=user.id,
        username=user.user_name,
        display_name=user.remark or user.user_name,
        role=user.role,
        status=user.status,
        api_key=api_key,
        roles=perms_resp.roles,
        permissions=flat_permissions
    )
    
    return StandardResponse(data=data)
