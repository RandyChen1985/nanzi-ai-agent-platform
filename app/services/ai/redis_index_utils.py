"""RediSearch 向量索引的公共探测 / 重建逻辑。

背景：``metadata`` / ``example`` / ``memory`` 三个向量索引此前各自实现
``FT.INFO`` → ``FT.CREATE``，且都只判断「索引是否存在」，从不校验索引的
``DIM`` 是否等于系统配置 ``embed_dimensions``。后果是维度变更后
「检查/创建索引」静默变成空操作，旧维度索引被保留：

* HASH 文档照旧写入成功（``upsert_*`` 返回 True，列表页走 SCAN 也看得到）；
* RediSearch 因维度不匹配拒绝索引该文档（``hash_indexing_failures`` 上涨）；
* 向量检索静默失效，返回空结果。

本模块把判断收敛到一处：只有「索引存在 **且** 维度与配置一致」才算已就绪；
维度不一致时重建索引，默认 ``FT.DROPINDEX`` 不带 ``DD``，即保留 HASH 文档。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, MutableMapping, Optional, Sequence

logger = logging.getLogger(__name__)

#: ensure 成功结果的进程内缓存时长（秒）。仅缓存「已确认存在且维度一致」。
ENSURE_CACHE_TTL = 300.0

#: 最近一次 ensure 的结构化结果，供「检查/创建索引」接口回报真实动作。
_LAST_RESULTS: Dict[str, Dict[str, Any]] = {}


def _as_text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def _pairs(raw: Any) -> Optional[List[tuple]]:
    """把 FT.INFO 回复统一成 ``[(key, value), ...]``（兼容 RESP2 扁平数组与 RESP3 map）。"""
    if isinstance(raw, dict):
        return list(raw.items())
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return [(raw[i], raw[i + 1]) for i in range(0, len(raw) - 1, 2)]
    return None


def index_vector_dim(info: Any) -> Optional[int]:
    """从 ``FT.INFO`` 回复中解析向量字段的 DIM；解析不到返回 ``None``。"""
    items = _pairs(info)
    if not items:
        return None
    for key, value in items:
        if _as_text(key) != "attributes":
            continue
        attrs = [value] if isinstance(value, dict) else value
        if not isinstance(attrs, (list, tuple)):
            return None
        for attr in attrs:
            fields = _pairs(attr)
            if not fields:
                continue
            kv = {_as_text(k): v for k, v in fields}
            if _as_text(kv.get("type")).upper() != "VECTOR":
                continue
            try:
                return int(kv.get("dim"))
            except (TypeError, ValueError):
                return None
    return None


async def read_index_state(redis: Any, index_name: str) -> Dict[str, Any]:
    """探测索引：``{"exists": bool, "dim": Optional[int]}``。

    索引不存在（``FT.INFO`` 抛错）与索引存在但维度解析失败都会返回 ``dim=None``，
    由 ``exists`` 区分。
    """
    try:
        info = await redis.execute_command("FT.INFO", index_name)
    except Exception:
        return {"exists": False, "dim": None}
    if not info:
        return {"exists": False, "dim": None}
    return {"exists": True, "dim": index_vector_dim(info)}


async def drop_index(redis: Any, index_name: str, *, keep_documents: bool = True) -> bool:
    """删除索引。

    ``keep_documents=True``（默认）不带 ``DD``，只删索引、保留 HASH 文档——
    记忆摘要的 HASH 就是用户数据本体，带 ``DD`` 会直接删掉。
    """
    args: List[Any] = ["FT.DROPINDEX", index_name]
    if not keep_documents:
        args.append("DD")
    try:
        await redis.execute_command(*args)
        return True
    except Exception as e:
        logger.warning("[RediSearch] FT.DROPINDEX %s failed: %s", index_name, e)
        return False


def record_ensure_result(index_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    _LAST_RESULTS[index_name] = result
    return result


def last_ensure_result(index_name: str) -> Optional[Dict[str, Any]]:
    return _LAST_RESULTS.get(index_name)


def reset_ensure_results() -> None:
    """测试辅助：清空最近结果记录。"""
    _LAST_RESULTS.clear()


async def ensure_vector_index(
    redis: Any,
    *,
    index_name: str,
    prefix: str,
    schema: Callable[[int], Sequence[Any]],
    dim: int,
    log_tag: str,
    cache: Optional[MutableMapping[str, float]] = None,
    force: bool = False,
    cache_ttl: float = ENSURE_CACHE_TTL,
) -> Dict[str, Any]:
    """确保索引存在且维度与配置一致，返回结构化结果。

    结果字段：``ok`` / ``action``（``ok`` | ``cached`` | ``created`` |
    ``recreated`` | ``drop_failed`` | ``failed``）/ ``index_name`` /
    ``index_dim``（重建时是**旧**维度）/ ``configured_dim`` / ``message``。

    ``force=True``（启动、手动「检查/创建索引」）绕过进程内缓存，总是回源探测。
    ``cache=None`` 表示不使用进程内缓存（调用方会先 ``FT.DROPINDEX`` 时不可传
    cache，否则可能命中旧结论而跳过重建）。
    """
    now = time.monotonic()
    cache_key = f"{index_name}|{dim}"
    if cache is not None and not force:
        expires = cache.get(cache_key)
        if expires and now < expires:
            return record_ensure_result(
                index_name,
                {
                    "ok": True,
                    "action": "cached",
                    "index_name": index_name,
                    "index_dim": dim,
                    "configured_dim": dim,
                },
            )
        cache.pop(cache_key, None)

    state = await read_index_state(redis, index_name)
    if state["exists"] and state["dim"] == dim:
        if cache is not None:
            cache[cache_key] = now + cache_ttl
        return record_ensure_result(
            index_name,
            {
                "ok": True,
                "action": "ok",
                "index_name": index_name,
                "index_dim": dim,
                "configured_dim": dim,
            },
        )

    action = "created"
    old_dim: Optional[int] = None
    if state["exists"]:
        old_dim = state["dim"]
        action = "recreated"
        if old_dim is None:
            logger.warning(
                "[%s] %s 已存在但无法解析向量维度，按不一致处理并重建",
                log_tag,
                index_name,
            )
        else:
            logger.warning(
                "[%s] %s 向量维度不一致（索引=%s，配置=%s），重建索引（保留 HASH 文档）",
                log_tag,
                index_name,
                old_dim,
                dim,
            )
        if not await drop_index(redis, index_name, keep_documents=True):
            return record_ensure_result(
                index_name,
                {
                    "ok": False,
                    "action": "drop_failed",
                    "index_name": index_name,
                    "index_dim": old_dim,
                    "configured_dim": dim,
                    "message": f"删除旧索引 {index_name} 失败，请检查 Redis 连接",
                },
            )

    try:
        await redis.execute_command(
            "FT.CREATE",
            index_name,
            "ON",
            "HASH",
            "PREFIX",
            "1",
            prefix,
            "SCHEMA",
            *schema(dim),
        )
    except Exception as e:
        logger.warning("[%s] FT.CREATE failed: %s", log_tag, e)
        return record_ensure_result(
            index_name,
            {
                "ok": False,
                "action": "failed",
                "index_name": index_name,
                "index_dim": old_dim,
                "configured_dim": dim,
                "message": f"创建索引 {index_name} 失败：{e}",
            },
        )

    logger.info("[%s] Created index %s dim=%s", log_tag, index_name, dim)
    if cache is not None:
        cache[cache_key] = time.monotonic() + cache_ttl
    return record_ensure_result(
        index_name,
        {
            "ok": True,
            "action": action,
            "index_name": index_name,
            "index_dim": old_dim,
            "configured_dim": dim,
        },
    )
