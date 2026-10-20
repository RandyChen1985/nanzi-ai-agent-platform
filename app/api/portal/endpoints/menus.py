"""菜单与权限树接口。

- `GET /nav` 面向所有登录用户，返回**服务端已按权限过滤**的导航树；
- `GET /tree` 面向角色/用户管理，返回可勾选的权限树（含只读功能点）；
- 其余端点面向菜单管理页，写操作统一要求 `element:menu:edit`。

菜单的增删改会由现有审计中间件自动落盘（它记录所有 `/api` 请求），因此这里不重复写审计。
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key, require_any_permission, require_permission
from app.core.orm import get_db_session
from app.services.menu_service import (
    MenuNotFoundError,
    MenuService,
    MenuValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class MenuNodeCreate(BaseModel):
    node_type: str = Field(description="group 或 external")
    label: str
    parent_code: Optional[str] = None
    icon: Optional[str] = None
    url: Optional[str] = None
    open_mode: Optional[str] = None
    slug: Optional[str] = None
    enabled: bool = True
    desktop_only: bool = False
    description: Optional[str] = None


class MenuNodeUpdate(BaseModel):
    label: Optional[str] = None
    icon: Optional[str] = None
    parent_code: Optional[str] = None
    url: Optional[str] = None
    open_mode: Optional[str] = None
    slug: Optional[str] = None
    enabled: Optional[bool] = None
    desktop_only: Optional[bool] = None
    is_landing: Optional[bool] = None
    sort_order: Optional[int] = None
    description: Optional[str] = None
    # 仅用于显式拒绝：内置菜单的路径不可改，传了不同值会报错而不是静默忽略。
    path: Optional[str] = None


class MenuReorderItem(BaseModel):
    code: str
    parent_code: Optional[str] = None
    sort_order: int


class MenuReorderRequest(BaseModel):
    items: List[MenuReorderItem]


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/nav")
async def get_nav(
    version: Optional[int] = Query(default=None, description="客户端已持有的菜单版本号"),
    user: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """当前用户可见的导航树。

    `version` 与客户端快照一致时只回 `{"unchanged": true}`，避免每次进后台都重传整棵树。
    """
    service = MenuService(db)
    return await service.get_nav(user, version)


@router.get("/tree")
async def get_permission_tree(
    user: Dict[str, Any] = Depends(
        require_any_permission(
            ("element", "element:role:edit"),
            ("menu", "menu:system:menus"),
        )
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """角色/用户管理里的权限树（菜单 → 页面 → 功能点）。"""
    service = MenuService(db)
    payload = await service.get_permission_tree()
    payload["table_ready"] = await service.table_ready()
    return payload


@router.get("/catalog")
async def get_menu_catalog(
    user: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """登录用户可读的菜单骨架，供个人中心「我的权限」按层级展示。

    只含分组 / 内置页面 / 功能点的名称，不含外链与地址——个人中心面向所有用户，
    不应顺带暴露内部系统的名字。
    """
    service = MenuService(db)
    return await service.get_permission_tree(include_external=False)


@router.get("")
async def list_menus(
    user: Dict[str, Any] = Depends(require_permission("menu", "menu:system:menus")),
    db: AsyncSession = Depends(get_db_session),
):
    """菜单管理页用：全量节点（含停用项）。"""
    service = MenuService(db)
    return {
        "nodes": await service.get_admin_nodes(),
        "table_ready": await service.table_ready(),
        "version": await service.get_version(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_menu(
    payload: MenuNodeCreate,
    user: Dict[str, Any] = Depends(require_permission("element", "element:menu:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    service = MenuService(db)
    try:
        return await service.create_node(payload.model_dump(exclude_unset=True))
    except MenuValidationError as exc:
        raise _bad_request(exc) from exc


@router.put("/{node_id}")
async def update_menu(
    node_id: int,
    payload: MenuNodeUpdate,
    user: Dict[str, Any] = Depends(require_permission("element", "element:menu:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    service = MenuService(db)
    try:
        return await service.update_node(node_id, payload.model_dump(exclude_unset=True))
    except MenuValidationError as exc:
        raise _bad_request(exc) from exc
    except MenuNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_menu(
    node_id: int,
    user: Dict[str, Any] = Depends(require_permission("element", "element:menu:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    service = MenuService(db)
    try:
        await service.delete_node(node_id)
    except MenuValidationError as exc:
        raise _bad_request(exc) from exc
    except MenuNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return None


@router.post("/reorder")
async def reorder_menus(
    payload: MenuReorderRequest,
    user: Dict[str, Any] = Depends(require_permission("element", "element:menu:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    service = MenuService(db)
    try:
        updated = await service.reorder([item.model_dump() for item in payload.items])
    except MenuValidationError as exc:
        raise _bad_request(exc) from exc
    return {"updated": updated}


@router.post("/reset")
async def reset_builtin_menus(
    user: Dict[str, Any] = Depends(require_permission("element", "element:menu:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    service = MenuService(db)
    try:
        restored = await service.reset_builtin()
    except MenuValidationError as exc:
        raise _bad_request(exc) from exc
    return {"restored": restored}
