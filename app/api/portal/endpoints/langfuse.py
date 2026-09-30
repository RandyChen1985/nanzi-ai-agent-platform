"""Langfuse LLM 链路追踪的配置接口。

与 ``/system/configs``（通用键值配置）刻意分开：Langfuse 配置是独立单行表、
字段强类型、密钥单独加密，掩码与返回结构都与通用配置不同。

权限沿用系统配置页的菜单权限 + 一个独立的功能权限
``element:system:langfuse_save``（遵守 ``menu:*`` 控菜单、``element:*`` 控功能的约定）。
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.dependencies import require_permission
from app.services.ai.observability import config_store, manager, settings

logger = logging.getLogger(__name__)

router = APIRouter()

READ_PERMISSION = ("menu", "menu:system:config")
WRITE_PERMISSION = ("element", "element:system:langfuse_save")

_STATUS_LABELS = {
    settings.STATUS_DISABLED: "已关闭",
    settings.STATUS_NOT_CONFIGURED: "缺少连接配置",
    settings.STATUS_ENABLED: "已启用",
    settings.STATUS_DEGRADED: "异常（降级为不追踪）",
}


class LangfuseConfigUpdateRequest(BaseModel):
    """所有字段可选：只提交需要变更的项，未提交的字段保持原值。"""

    enabled: Any = None
    host: Any = None
    public_key: Any = None
    secret_key: Any = None
    sample_rate: Any = None
    capture_content: Any = None
    environment: Any = None
    release: Any = None
    timeout_seconds: Any = None
    trace_url_template: Any = None


class LangfuseProbeRequest(BaseModel):
    """连通性探测的草稿值：全部可选，未提交的字段回落到已保存配置。

    存在的意义：让用户「填完就能测」，不必先保存；尤其是密码框留空（＝不修改）时，
    secret_key 不提交即可复用已保存密钥。
    """

    enabled: Any = None
    host: Any = None
    public_key: Any = None
    secret_key: Any = None
    timeout_seconds: Any = None


def _runtime_status() -> Dict[str, Any]:
    """当前进程的真实运行态：配置状态 + client 是否已建好 + 最近错误。

    这里刻意不重复返回 environment / release —— 它们是「配置值」，由配置视图提供，
    运行态字段只描述「进程内实际生效得怎么样」，避免两者同名相互覆盖。
    """
    snapshot = settings.get_snapshot()
    return {
        "status": snapshot.status,
        "status_label": _STATUS_LABELS.get(snapshot.status, snapshot.status),
        "status_detail": snapshot.last_error,
        "client_active": manager.current_client() is not None,
    }


async def _build_response() -> Dict[str, Any]:
    """配置公开视图 + 运行态（不含密钥明文）。"""
    config = await config_store.load_config()
    payload = config_store.to_public_view(config)
    payload["updated_by"] = config.get("updated_by")
    payload.update(_runtime_status())
    return payload


@router.get("")
async def get_langfuse_config(
    user: Dict = Depends(require_permission(*READ_PERMISSION)),
):
    """读取 Langfuse 配置（不返回密钥明文）与当前运行状态。"""
    del user
    try:
        return await _build_response()
    except Exception as exc:
        logger.error("读取 Langfuse 配置失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"读取 Langfuse 配置失败: {exc}") from exc


@router.put("")
async def update_langfuse_config(
    request: LangfuseConfigUpdateRequest,
    user: Dict = Depends(require_permission(*WRITE_PERMISSION)),
):
    """保存 Langfuse 配置；非法值返回 400，并立即刷新进程内快照。"""
    payload = {key: value for key, value in request.model_dump().items() if value is not None}
    try:
        await config_store.save_config(payload, changed_by=user.get("user_name", "admin"))
    except config_store.LangfuseConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("保存 Langfuse 配置失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"保存 Langfuse 配置失败: {exc}") from exc

    # 立即生效：不等 30s 的定时刷新，避免「保存了但状态卡片还是旧的」。
    try:
        await settings.refresh_snapshot()
    except Exception as exc:
        logger.warning("Langfuse 配置已保存但快照刷新失败: %s", exc)

    return {
        "status": "success",
        "message": "配置已保存",
        "data": await _build_response(),
    }


@router.post("/test")
async def test_langfuse_connection(
    request: Optional[LangfuseProbeRequest] = None,
    user: Dict = Depends(require_permission(*WRITE_PERMISSION)),
):
    """用当前生效配置（叠加表单草稿值）做一次真实连通性与鉴权探测。

    启动路径不做阻塞式检查，只有管理员显式点击时才发起请求。
    本接口**只读**：不写库、不改快照。
    """
    del user
    overrides = None
    if request is not None:
        # 只丢弃显式 None（＝未提交）；空串保留，交由 manager 按「未填写」回落。
        overrides = {key: value for key, value in request.model_dump().items() if value is not None}
    try:
        success, message = await manager.test_connection(overrides)
    except Exception as exc:
        logger.warning("Langfuse 连通性探测异常: %s", exc)
        return {"success": False, "message": f"探测失败: {type(exc).__name__}: {exc}"}
    return {"success": success, "message": message}
