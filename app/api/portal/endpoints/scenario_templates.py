from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_permission
from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.schemas.scenario_template import (
    ScenarioTemplateDetail,
    ScenarioTemplateInstallRequest,
    ScenarioTemplateInstallResponse,
    ScenarioTemplateInstanceSummary,
    ScenarioTemplatePrecheckResponse,
    ScenarioTemplateResourceOptionsResponse,
    ScenarioTemplateSummary,
)
from app.services.scenario_template_service import ScenarioTemplateService

router = APIRouter()


@router.get("", response_model=StandardResponse[List[ScenarioTemplateSummary]])
async def list_scenario_templates(
    _user: Dict[str, Any] = Depends(require_permission("menu", "menu:ai_chat")),
):
    return StandardResponse(data=ScenarioTemplateService.list_templates())


@router.get("/instances", response_model=StandardResponse[List[ScenarioTemplateInstanceSummary]])
async def list_scenario_template_instances(
    db: AsyncSession = Depends(get_db_session),
    _user: Dict[str, Any] = Depends(require_permission("menu", "menu:agent_management")),
):
    result = await ScenarioTemplateService.list_instances(db)
    return StandardResponse(data=result)


@router.get("/instances/{instance_id}", response_model=StandardResponse[ScenarioTemplateInstallResponse])
async def get_scenario_template_instance(
    instance_id: str,
    db: AsyncSession = Depends(get_db_session),
    _user: Dict[str, Any] = Depends(require_permission("menu", "menu:agent_management")),
):
    result = await ScenarioTemplateService.get_instance_install_result(db, instance_id)
    return StandardResponse(data=result)


@router.get("/{template_id}", response_model=StandardResponse[ScenarioTemplateDetail])
async def get_scenario_template(
    template_id: str,
    _user: Dict[str, Any] = Depends(require_permission("menu", "menu:ai_chat")),
):
    return StandardResponse(data=ScenarioTemplateService.get_template(template_id).detail())


@router.get("/{template_id}/resource-options", response_model=StandardResponse[ScenarioTemplateResourceOptionsResponse])
async def get_scenario_template_resource_options(
    template_id: str,
    db: AsyncSession = Depends(get_db_session),
    _user: Dict[str, Any] = Depends(require_permission("menu", "menu:agent_management")),
):
    result = await ScenarioTemplateService.resource_options(db, template_id)
    return StandardResponse(data=result)


@router.post("/{template_id}/precheck", response_model=StandardResponse[ScenarioTemplatePrecheckResponse])
async def precheck_scenario_template(
    template_id: str,
    body: ScenarioTemplateInstallRequest,
    db: AsyncSession = Depends(get_db_session),
    _user: Dict[str, Any] = Depends(require_permission("menu", "menu:agent_management")),
):
    result = await ScenarioTemplateService.precheck(db, template_id, body)
    return StandardResponse(data=result)


@router.post("/{template_id}/install", response_model=StandardResponse[ScenarioTemplateInstallResponse])
async def install_scenario_template(
    template_id: str,
    body: ScenarioTemplateInstallRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    await require_permission("element", "element:agent:create")(user, db)
    result = await ScenarioTemplateService.install(db, template_id, body, user=user)
    return StandardResponse(data=result)
