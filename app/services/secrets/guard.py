"""落库路径的统一入口。

三个出口共用同一份逻辑，避免各自实现导致口径不一致：

* ``AgentExecutionTrace.tool_input / tool_output``（工具原始入参出参）
* ``AgentExecutionHistory.query / summary / reasoning_content / process_timeline``
* Redis 运行态快照（30 天 TTL）

调用方只在**写库前**取一次扫描器，循环里反复用，不要每条记录都重建——
``SecretRegistry`` 有缓存，但 ``SecretScanner`` 的排序和去重是纯 CPU 开销。
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.secrets.registry import secret_registry
from app.services.secrets.scanner import SecretScanner
from app.utils.masking import mask_sensitive_data

logger = logging.getLogger(__name__)


async def build_storage_scanner() -> SecretScanner:
    """取一个带当前已知凭据快照的扫描器。"""
    secrets = await secret_registry.snapshot()
    return SecretScanner(secrets)


async def sanitize_for_storage(data: Any, *, field: str = "") -> Any:
    """单次调用的便捷入口；批量场景请自行 ``build_storage_scanner`` 复用。

    取快照失败时降级成只做键名脱敏（即改造前的行为），并留下告警。
    这是有意的 fail-open：审计写入不能因为「查配置失败」而整条丢掉，
    但降级本身必须能被看见。
    """
    if data is None:
        return None
    try:
        scanner = await build_storage_scanner()
    except Exception as exc:  # noqa: BLE001 - 见 docstring 的降级说明
        logger.warning(
            "[SecretGuard] 取凭据快照失败，降级为仅键名脱敏 field=%s: %s", field, exc
        )
        return mask_sensitive_data(data)
    return scanner.mask_data(data)
