"""系统诊断 → 实例：多实例心跳聚合与别名维护。

权限口径（与「系统配置」页其它接口保持一致，不要退回成 ``require_admin``）：

* 读（列表 / 趋势）：``menu:system:config`` —— 能打开这个页面的账号就应该能看，
  否则会出现「有菜单权限、页面能进、一点实例就 403」的错配；
* 写（别名）：``element:system:config_save`` —— 别名是持久数据，跟「改系统配置」
  同一档，只有能保存配置的角色可写。

心跳里确实含内网 IP、镜像 tag、数据库/Redis 版本这类基础设施信息，所以仍然要求
鉴权（不能匿名）；但判据是权限码，不是 ``role == "admin"``：角色体系里
``menu:system:config`` 可以单独授予运维账号。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.dependencies import require_permission
from app.services import instance_registry

logger = logging.getLogger(__name__)

router = APIRouter()


class AliasPayload(BaseModel):
    # 空字符串表示清除别名，回退显示实例 ID
    alias: str = Field(default="", max_length=instance_registry.ALIAS_MAX_LENGTH)


@router.get("")
async def get_instances(
    user: Dict = Depends(require_permission("menu", "menu:system:config")),
) -> Dict[str, Any]:
    """聚合所有实例的心跳快照（含主从、中间件版本、资源、流量与告警）。"""
    return await instance_registry.list_instances()


@router.get("/{host}/series")
async def get_instance_series(
    host: str,
    hours: int = Query(24, ge=1, le=24, description="回溯小时数，趋势只保留 24 小时"),
    user: Dict = Depends(require_permission("menu", "menu:system:config")),
) -> Dict[str, Any]:
    return await instance_registry.get_instance_series(host, hours=hours)


@router.put("/{host}/alias")
async def update_instance_alias(
    host: str,
    payload: AliasPayload,
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
) -> Dict[str, Any]:
    """保存实例别名（存在不带 TTL 的 Redis 键里，心跳过期不影响）。"""
    if not host or len(host) > 128:
        raise HTTPException(status_code=400, detail="实例 ID 不合法")
    try:
        return await instance_registry.set_instance_alias(host, payload.alias)
    except Exception as exc:  # noqa: BLE001
        logger.error("保存实例别名失败: %s", exc)
        raise HTTPException(status_code=500, detail="保存别名失败")
