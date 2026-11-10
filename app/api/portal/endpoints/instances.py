"""系统诊断 → 实例：多实例心跳聚合与别名维护。

这些端点是「运维视角」的只读信息（外加一个别名编辑），因此统一要求管理员身份：
心跳里包含内网 IP、镜像 tag、数据库版本这类基础设施信息，不适合按普通用户权限开放。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.dependencies import require_admin
from app.services import instance_registry

logger = logging.getLogger(__name__)

router = APIRouter()


class AliasPayload(BaseModel):
    # 空字符串表示清除别名，回退显示 hostname
    alias: str = Field(default="", max_length=instance_registry.ALIAS_MAX_LENGTH)


@router.get("")
async def get_instances(user: Dict = Depends(require_admin)) -> Dict[str, Any]:
    """聚合所有实例的心跳快照（含主从、中间件版本、资源、流量与告警）。"""
    return await instance_registry.list_instances()


@router.get("/{host}/series")
async def get_instance_series(
    host: str,
    hours: int = Query(24, ge=1, le=24, description="回溯小时数，趋势只保留 24 小时"),
    user: Dict = Depends(require_admin),
) -> Dict[str, Any]:
    return await instance_registry.get_instance_series(host, hours=hours)


@router.put("/{host}/alias")
async def update_instance_alias(
    host: str,
    payload: AliasPayload,
    user: Dict = Depends(require_admin),
) -> Dict[str, Any]:
    """保存实例别名（存在不带 TTL 的 Redis 键里，心跳过期不影响）。"""
    if not host or len(host) > 128:
        raise HTTPException(status_code=400, detail="实例 hostname 不合法")
    try:
        return await instance_registry.set_instance_alias(host, payload.alias)
    except Exception as exc:  # noqa: BLE001
        logger.error("保存实例别名失败: %s", exc)
        raise HTTPException(status_code=500, detail="保存别名失败")
