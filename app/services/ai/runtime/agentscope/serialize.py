"""JSON-safe serialization for AgentScope runtime payloads.

AgentScope / tool hooks may leak un-awaited coroutines into agent.state.
Pydantic ``model_dump(mode="json")`` then raises:
``Unable to serialize unknown type: <class 'coroutine'>``.
Resolve awaitables first, then dump JSON-compatible structures.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from pydantic import TypeAdapter

logger = logging.getLogger(__name__)


async def resolve_awaitables(
    value: Any,
    *,
    path: str = "value",
    awaitable_cache: dict[int, Any] | None = None,
) -> Any:
    """Recursively await leaked awaitables in nested dict/list/tuple payloads."""
    awaitable_cache = awaitable_cache if awaitable_cache is not None else {}
    if inspect.isawaitable(value):
        cache_key = id(value)
        if cache_key in awaitable_cache:
            return awaitable_cache[cache_key]
        try:
            resolved = await value
            resolved = await resolve_awaitables(
                resolved,
                path=path,
                awaitable_cache=awaitable_cache,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to resolve awaitable while serializing {path}: {exc}",
            ) from exc
        awaitable_cache[cache_key] = resolved
        return resolved
    if isinstance(value, dict):
        return {
            key: await resolve_awaitables(
                item,
                path=f"{path}.{key}",
                awaitable_cache=awaitable_cache,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            await resolve_awaitables(
                item,
                path=f"{path}[{index}]",
                awaitable_cache=awaitable_cache,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, tuple):
        return tuple(
            [
                await resolve_awaitables(
                    item,
                    path=f"{path}[{index}]",
                    awaitable_cache=awaitable_cache,
                )
                for index, item in enumerate(value)
            ],
        )
    if isinstance(value, set):
        return [
            await resolve_awaitables(
                item,
                path=f"{path}[]",
                awaitable_cache=awaitable_cache,
            )
            for item in value
        ]
    return value


def _to_python_tree(value: Any) -> Any:
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return value.model_dump(mode="python")
        except TypeError:
            return value.model_dump()
    return value


async def serialize_jsonable(
    value: Any,
    *,
    path: str = "value",
    awaitable_cache: dict[int, Any] | None = None,
) -> Any:
    """Convert a runtime object/tree into a JSON-compatible Python structure."""
    python_value = _to_python_tree(value)
    resolved = await resolve_awaitables(
        python_value,
        path=path,
        awaitable_cache=awaitable_cache,
    )
    try:
        return TypeAdapter(Any).dump_python(resolved, mode="json")
    except Exception as exc:
        # 兜底：state 中可能混入不可 JSON 化的运行期对象（如 StreamRepetitionDetector）。
        # 若直接抛错会让整条中断/挂起流程失败、前端拿不到 permission_request_id。这里降级为把
        # 未知对象字符串化，保证快照仍可保存、确认流程可继续。
        logger.warning(
            "JSON-serializing %r failed (%s); sanitizing unknown objects as str()",
            path,
            exc,
        )
        return _json_safe(resolved)


def _json_safe(value: Any) -> Any:
    """递归把任意结构转换为 JSON 安全结构；未知对象降级为 str()，绝不抛错。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {_json_safe(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _json_safe(dump(mode="python"))
        except Exception:
            pass
    try:
        if isinstance(value, type):
            return str(value.__name__)
        return str(value)
    except Exception:
        return None


async def serialize_agent_state(
    state: Any,
    *,
    awaitable_cache: dict[int, Any] | None = None,
) -> dict[str, Any]:
    """Serialize AgentScope agent.state for Redis / pending snapshots."""
    serialized = await serialize_jsonable(
        state,
        path="agent_state",
        awaitable_cache=awaitable_cache,
    )
    if isinstance(serialized, dict):
        return serialized
    return {"value": serialized}
