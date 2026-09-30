"""Langfuse LLM 链路追踪的配置接口。

与 ``/system/configs``（通用键值配置）刻意分开：Langfuse 配置是独立单行表、
字段强类型、密钥单独加密，掩码与返回结构都与通用配置不同。

权限沿用系统配置页的菜单权限 + 一个独立的功能权限
``element:system:langfuse_save``（遵守 ``menu:*`` 控菜单、``element:*`` 控功能的约定）。
"""

import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.dependencies import require_permission
from app.services.ai.observability import (
    config_store,
    export_health,
    manager,
    selfcheck,
    settings,
)

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

# Langfuse 的 trace id 是 32 位十六进制；用它做格式校验，避免任意字符串被拼进外部请求。
_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


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


def _export_health_view() -> Dict[str, Any]:
    """导出健康度的公开视图（进程内状态，多实例各自独立）。"""
    snap = export_health.snapshot()
    return {
        "last_success_at": snap.last_success_at.isoformat() if snap.last_success_at else None,
        "last_failure_at": snap.last_failure_at.isoformat() if snap.last_failure_at else None,
        "last_error": snap.last_error,
        "success_count": snap.success_count,
        "failure_count": snap.failure_count,
        "consecutive_failures": snap.consecutive_failures,
    }


async def _build_response() -> Dict[str, Any]:
    """配置公开视图 + 运行态（不含密钥明文）。"""
    config = await config_store.load_config()
    payload = config_store.to_public_view(config)
    payload["updated_by"] = config.get("updated_by")
    payload.update(_runtime_status())
    payload["export_health"] = _export_health_view()
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


@router.post("/selfcheck")
async def run_langfuse_selfcheck(
    user: Dict = Depends(require_permission(*WRITE_PERMISSION)),
):
    """发出一条走真实 OTel 管线的自检 trace，并返回层 1（导出是否被接收）结论。

    会等一次 ``flush``（成功时通常数百毫秒；导出失败时最坏一个 timeout），
    因此耗时可能达数秒——这是刻意的：只有等到导出结果才算真正验证。
    """
    del user
    try:
        result = await selfcheck.run_selfcheck()
    except Exception as exc:
        logger.warning("Langfuse 自检执行异常: %s", exc)
        return {
            "ok": False,
            "trace_id": None,
            "export_confirmed": False,
            "message": f"自检执行失败：{type(exc).__name__}: {exc}",
        }
    return {
        "ok": result.ok,
        "trace_id": result.trace_id,
        "export_confirmed": result.export_confirmed,
        "message": result.message,
    }


@router.get("/selfcheck/{trace_id}")
async def lookup_langfuse_selfcheck(
    trace_id: str,
    user: Dict = Depends(require_permission(*WRITE_PERMISSION)),
):
    """层 2：反查自检 trace 是否已在 Langfuse 可查询（摄取有延迟，需轮询）。"""
    del user
    if not _TRACE_ID_PATTERN.match(trace_id):
        raise HTTPException(status_code=400, detail="trace_id 必须是 32 位十六进制字符串")
    try:
        found, message = await manager.query_trace_exists(trace_id)
    except Exception as exc:
        logger.warning("Langfuse trace 反查异常: %s", exc)
        return {"found": False, "message": f"查询失败：{type(exc).__name__}: {exc}"}
    return {"found": found, "message": message}
