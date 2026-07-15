from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal.endpoints.saved_reports import _get_user_role_ids as get_user_role_ids
from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services.data_portal_home_service import DataPortalHomeService


router = APIRouter()


@router.get("/home", response_model=StandardResponse[Dict[str, Any]], summary="获取我的数据首页")
async def get_data_portal_home(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    role_ids = await get_user_role_ids(db, user_id)
    payload = await DataPortalHomeService.build(db, user_id=user_id, role_ids=role_ids)
    return StandardResponse(data=payload)
