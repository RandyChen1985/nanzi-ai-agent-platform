"""MCP 工具定义探测与变更处置。

## 为什么探测不能复用 ``sync_tools()``

``sync_tools()`` 是**应用**语义：整批覆盖 ``tool_description`` / ``parameter_schema``、
把远端已删除的工具置 ``is_available=False``、参数消失时清掉 ``SecretBinding``。
如果「进页面看一眼」走的是它，等于「看了一眼就应用了」——远端一次瞬时故障、一次
权限抖动，都会被静默写成本地的新定义。所以这里另起一条**只读**链路：

- 探测：只调 ``tools/list``，只往 ``sys_mcp_tool_definition_changes`` 写差异，不动
  ``sys_mcp_tool_cache`` 的任何业务字段（description / schema / is_available /
  is_published 一个都不碰）。
- 应用：用户确认后，才调 ``sync_tools()`` 整批落地。

## 状态的三个层次

1. **工具级**：``McpToolCache.definition_hash`` 记「已确认的远端定义版本」。探测拿远端
   当前定义的指纹跟它比，相同就什么都不写。
2. **变更级**：一处差异一条记录，``pending`` → ``applied`` / ``dismissed``。
3. **服务级**：``McpServer.last_probe_*`` 记这次探测本身的结果（成功 / 有差异 / 不可达），
   与 ``last_sync_at`` 严格分开——探测每天都跑且从不写业务字段，混用会让「多久没同步」
   这个指标彻底失真。

## 三个刻意的取舍

- **空结果是故障，不是删除**：远端返回 0 个工具而本地还有可用工具时，按「不可达」处理
  并跳过差异计算，与 ``sync_tools`` 的 ``McpSyncGuardError`` 同一个口径。否则一次权限
  抖动就能把整个服务的工具报成「全部删除」。
- **首次探测只建立基线**：``definition_hash`` 为空的历史数据，第一次探测直接补写指纹，
  不产生变更。否则上线当天每个工具都会报一条「定义变更」，待办直接被噪音淹没。
- **差异消失就撤单**：某条 pending 对应的差异这次没再出现（远端改回去了），直接删掉该
  pending 行。留着会变成「点进去发现没变化」的历史包袱，而它从没被人看到过，删掉不
  损失信息。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, func, or_, select, update

from app.core.orm import AsyncSessionLocal
from app.models.mcp import McpServer, McpToolCache, McpToolDefinitionChange
from app.services.mcp.definition_diff import (
    canonical_schema,
    definition_fingerprint,
    diff_tool_definition,
    summarize_change,
    tool_annotations,
    tool_input_schema,
)

logger = logging.getLogger(__name__)

#: 探测结果状态（写 ``sys_mcp_servers.last_probe_status``）
PROBE_OK = "ok"
PROBE_CHANGED = "changed"
PROBE_UNREACHABLE = "unreachable"
PROBE_NEEDS_USER = "needs_user"
PROBE_SKIPPED_DISABLED = "skipped_disabled"

#: 触发来源（写 ``detected_by``）
DETECTED_NIGHTLY = "nightly"
DETECTED_PAGE = "page"
DETECTED_HOT_CALL = "hot_call"
DETECTED_MANUAL = "manual"

#: 错误文本落库上限。远端报错里可能带完整请求报文（含 header），不做截断会泄露到页面。
ERROR_TEXT_LIMIT = 500

#: 一条 pending 里塞给「忽略」用的远端指纹键。用下划线前缀与业务字段区分，
#: 前端渲染 before/after 时跳过它。
HASH_KEY = "_definition_hash"

#: 单次批量探测的并发上限。每个探测要建 SSE / streamable HTTP 会话，开太多会把
#: 本机 fd 与远端连接数一起吃满。
DEFAULT_PROBE_CONCURRENCY = 3

#: 单个服务的探测超时。``list_remote_tools`` 本身没有超时保护，远端「TCP 通了但不回
#: tools/list」时它会一直挂着；而夜间巡检是 ``max_instances=1``，一次永不返回就会让
#: 之后每天那一趟全被 APScheduler 跳过——探测功能等于永久停摆，直到进程重启。
PROBE_TIMEOUT_SECONDS = 45


async def _load_server_snapshot(server_id: str) -> Optional[Dict[str, Any]]:
    """读服务元数据与现有工具缓存（只读会话，读完立刻释放，不跨网络调用持有连接）。"""
    async with AsyncSessionLocal() as db:
        server = (
            await db.execute(select(McpServer).where(McpServer.id == server_id))
        ).scalar_one_or_none()
        if not server:
            return None
        tools = (
            await db.execute(select(McpToolCache).where(McpToolCache.server_id == server_id))
        ).scalars().all()
        return {
            "server": {
                "id": server.id,
                "server_name": server.server_name,
                "enabled_status": server.enabled_status,
                "user_assertion_enabled": bool(getattr(server, "user_assertion_enabled", False)),
            },
            "tools": [
                {
                    "tool_name": tool.tool_name,
                    "tool_description": tool.tool_description,
                    "parameter_schema": tool.parameter_schema,
                    "is_available": bool(getattr(tool, "is_available", True)),
                    "definition_hash": getattr(tool, "definition_hash", None),
                }
                for tool in tools
            ],
        }


def _schema_annotations(parameter_schema: Any) -> Optional[Dict[str, Any]]:
    """从库里存的 ``parameter_schema`` 里取回 annotations。

    同步时 annotations 被塞进 schema 的 ``x-nanzi-mcp-annotations`` 键（见
    ``sync_tools``），所以探测比对前要从同一位置取出来，否则每次都会误报
    「annotations 变更」。
    """
    if isinstance(parameter_schema, str):
        try:
            parameter_schema = json.loads(parameter_schema)
        except (TypeError, ValueError):
            return None
    if not isinstance(parameter_schema, dict):
        return None
    annotations = parameter_schema.get("x-nanzi-mcp-annotations")
    return annotations if isinstance(annotations, dict) and annotations else None


def _remote_tool_map(server_name: str, remote_tools: Sequence[Any]) -> Dict[str, Dict[str, Any]]:
    """把远端工具列表整理成 ``{全名: 规范化定义}``。"""
    remote_map: Dict[str, Dict[str, Any]] = {}
    for tool in remote_tools:
        raw_name = getattr(tool, "name", None)
        if raw_name is None and isinstance(tool, dict):
            raw_name = tool.get("name")
        if not raw_name:
            continue
        description = getattr(tool, "description", None)
        if description is None and isinstance(tool, dict):
            description = tool.get("description")
        schema = canonical_schema(tool_input_schema(tool))
        annotations = tool_annotations(tool)
        full_name = f"{server_name}:{raw_name}"
        remote_map[full_name] = {
            "description": description,
            "schema": schema,
            "annotations": annotations,
            "definition_hash": definition_fingerprint(description, schema, annotations),
        }
    return remote_map


def _truncate_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    return text[:ERROR_TEXT_LIMIT]


async def _record_probe_state(
    server_id: str,
    *,
    status: str,
    error: Optional[str] = None,
    count_failure: bool = False,
) -> None:
    """写回服务级探测状态。成功一次把连续失败次数归零。"""
    async with AsyncSessionLocal() as db:
        values: Dict[str, Any] = {
            "last_probe_at": datetime.now(),
            "last_probe_status": status,
        }
        if error is not None:
            values["last_probe_error"] = error
        if status in (PROBE_OK, PROBE_CHANGED):
            values["probe_failure_count"] = 0
            values["last_probe_error"] = None
        if count_failure:
            server = (
                await db.execute(select(McpServer).where(McpServer.id == server_id))
            ).scalar_one_or_none()
            if not server:
                return
            values["probe_failure_count"] = int(getattr(server, "probe_failure_count", 0) or 0) + 1
        await db.execute(update(McpServer).where(McpServer.id == server_id).values(**values))
        await db.commit()


async def probe_server(
    server_id: str,
    *,
    user_info: Optional[Dict[str, Any]] = None,
    detected_by: str = DETECTED_MANUAL,
    skip_disabled: bool = False,
) -> Dict[str, Any]:
    """探测单个服务的工具定义差异（只读，绝不改动已生效的定义）。

    返回::

        {
          "server_id": ..., "server_name": ..., "status": "ok|changed|...",
          "new_changes": 3, "pending_changes": 3, "tool_count": 18,
          "baseline_tools": 18,   # 本次建立基线的工具数（首次探测）
          "changes": [{"kind":..., "tool_name":..., "severity":..., "summary":...}],
          "error": None,
        }
    """
    snapshot = await _load_server_snapshot(server_id)
    if not snapshot:
        return {
            "server_id": server_id,
            "server_name": None,
            "status": PROBE_UNREACHABLE,
            "new_changes": 0,
            "pending_changes": 0,
            "tool_count": 0,
            "baseline_tools": 0,
            "changes": [],
            "error": "MCP 服务不存在",
        }

    server = snapshot["server"]
    server_name = server["server_name"]
    result: Dict[str, Any] = {
        "server_id": server_id,
        "server_name": server_name,
        "status": PROBE_OK,
        "new_changes": 0,
        "pending_changes": 0,
        "tool_count": 0,
        "baseline_tools": 0,
        "changes": [],
        "error": None,
    }

    if skip_disabled and int(server["enabled_status"] or 0) != 1:
        await _record_probe_state(server_id, status=PROBE_SKIPPED_DISABLED)
        result["status"] = PROBE_SKIPPED_DISABLED
        return result

    # UserContext 服务的 tools/list 也要按用户签名。后台巡检没有「当前操作者」，
    # 发出去的请求必然被远端拒；这里提前收手并如实标注，比让远端报一个 401 更清楚。
    if server["user_assertion_enabled"] and not str((user_info or {}).get("user_id") or "").strip():
        await _record_probe_state(server_id, status=PROBE_NEEDS_USER)
        result["status"] = PROBE_NEEDS_USER
        result["error"] = "该服务启用了用户身份签名，后台无操作者身份，需在页面内探测"
        return result

    from app.services.ai.tools.mcp_client import McpClientService

    cached_tools = snapshot["tools"]
    cached_available = sum(1 for tool in cached_tools if tool["is_available"])

    try:
        # 超时必须在这里兜住，不能只靠 ``probe_servers`` 那层：
        # 「立即探测」接口与活跃会话热刷新都是**直接**调 ``probe_server``，
        # 而 ``list_remote_tools`` 自身没有超时——远端 TCP 通却不回 tools/list 时，
        # 接口会一直挂着不返回，后台 Task 也会滞留在 ``_HOT_PROBE_TASKS`` 里。
        remote_tools = await asyncio.wait_for(
            McpClientService.list_remote_tools(server_id, user_info=user_info),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError):
        error = f"探测超时（{PROBE_TIMEOUT_SECONDS} 秒内未返回 tools/list）"
        logger.warning("[MCP] 定义探测超时 %s", server_name)
        await _record_probe_state(
            server_id, status=PROBE_UNREACHABLE, error=error, count_failure=True
        )
        result["status"] = PROBE_UNREACHABLE
        result["error"] = error
        return result
    except Exception as exc:  # noqa: BLE001 - 探测失败不能冒泡到调度器/接口
        logger.warning("[MCP] 定义探测失败 %s: %s", server_name, exc)
        error = _truncate_error(exc)
        await _record_probe_state(
            server_id, status=PROBE_UNREACHABLE, error=error, count_failure=True
        )
        result["status"] = PROBE_UNREACHABLE
        result["error"] = error
        return result

    remote_map = _remote_tool_map(server_name, remote_tools)

    # 远端 0 工具但本地有可用工具：这是故障信号，不是「全部删除」。与 sync_tools 的
    # McpSyncGuardError 同口径——差别只在于探测直接跳过本次差异计算。
    if not remote_map and cached_available:
        error = (
            f"远端返回 0 个工具，但本地仍有 {cached_available} 个可用工具；"
            "疑似权限抖动或服务未就绪，已跳过本次差异计算"
        )
        await _record_probe_state(
            server_id, status=PROBE_UNREACHABLE, error=error, count_failure=True
        )
        result["status"] = PROBE_UNREACHABLE
        result["error"] = error
        return result

    dismissed_keys = await _load_dismissed_change_keys(server_id)
    baseline_updates: List[tuple] = []
    changes, baseline_tools = _compute_changes(
        server_name, cached_tools, remote_map, dismissed_keys, baseline_updates
    )
    new_counter: List[int] = []
    pending_changes = await _persist_changes(
        server_id,
        changes,
        detected_by=detected_by,
        baseline_updates=baseline_updates,
        new_counter=new_counter,
    )

    result["status"] = PROBE_CHANGED if changes else PROBE_OK
    result["changes"] = changes
    # ``new_changes`` 只算「这次新落库的」：一条待确认的差异如果没人处理，它每天
    # 都会被重新算出来；用总数当「新增」会让夜间巡检每天给管理员重发一次站内信。
    result["new_changes"] = new_counter[0] if new_counter else 0
    result["pending_changes"] = pending_changes
    result["tool_count"] = len(remote_map)
    result["baseline_tools"] = baseline_tools
    await _record_probe_state(server_id, status=result["status"])
    return result


def _compute_changes(
    server_name: str,
    cached_tools: Sequence[Dict[str, Any]],
    remote_map: Dict[str, Dict[str, Any]],
    dismissed_keys: Optional[set] = None,
    baseline_out: Optional[List[tuple]] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """比对本地缓存与远端定义，返回（变更列表, 建立基线的工具数）。

    这里只计算、不写库，也不改动 ``cached_tools`` 任何字段——它拿到的是快照副本。

    ``dismissed_keys`` 是「人已经明确忽略过」的 (tool_name, change_kind) 集合。
    只有 ``tool_added`` / ``tool_removed`` 需要它：其余类型忽略时会把
    ``definition_hash`` 推进到远端值，下次自然不再报；而这两种没有「远端定义」可
    推进（新增的工具本地没有缓存行、删除的工具远端没有定义），不额外记一笔就会
    每天凌晨把同一条重新报出来，最后没人再看这个待办。
    """
    dismissed_keys = dismissed_keys or set()
    cached_by_name = {tool["tool_name"]: tool for tool in cached_tools}
    changes: List[Dict[str, Any]] = []
    baseline_tools = 0

    for full_name, remote in remote_map.items():
        cached = cached_by_name.get(full_name)
        if cached is None:
            if (full_name, "tool_added") in dismissed_keys:
                continue
            changes.append(
                {
                    "tool_name": full_name,
                    "kind": "tool_added",
                    "severity": "notice",
                    "param": None,
                    "before": None,
                    "after": {
                        "description": remote["description"],
                        HASH_KEY: remote["definition_hash"],
                    },
                    "summary": "远端新增工具",
                }
            )
            continue

        # 首次探测只建立基线：历史数据没有指纹，若把「指纹为空」当成「变了」，
        # 上线当天每个工具都会报一条变更，待办会被噪音淹掉。
        #
        # 但基线**必须落库**（经 ``baseline_out`` 交给 ``_persist_changes`` 回写）：
        # 只计数不写的话，下次探测又是「没有指纹」→ 永远在建基线，远端改了什么
        # 一次都报不出来，整个功能等于没上线。
        #
        # 基线取**本地当前生效定义**而不是远端定义：远端此刻可能早就和本地不一样了，
        # 以远端为基线等于把那批「已经改了但我们不知道」的存量差异一次性认定为已确认
        # —— 正是这次要解决的问题被自己的初始化抹掉。
        if not cached.get("definition_hash"):
            if baseline_out is not None:
                baseline_out.append(
                    (
                        full_name,
                        definition_fingerprint(
                            cached.get("tool_description"),
                            cached.get("parameter_schema"),
                            _schema_annotations(cached.get("parameter_schema")),
                        ),
                    )
                )
            baseline_tools += 1
            continue

        if cached["definition_hash"] == remote["definition_hash"]:
            continue

        tool_changes = diff_tool_definition(
            tool_name=full_name,
            before_description=cached.get("tool_description"),
            before_schema=cached.get("parameter_schema"),
            before_annotations=_schema_annotations(cached.get("parameter_schema")),
            after_description=remote["description"],
            after_schema=remote["schema"],
            after_annotations=remote["annotations"],
        )
        if not tool_changes:
            # 指纹不同但拆不出具体差异（例如只有 schema 里的 title/description 之类
            # 我们不比对的字段变了）：不制造无法解读的待办，等同步把它对齐。
            logger.info("[MCP] %s 指纹不一致但无可报告的细粒度差异", full_name)
            continue
        changes.extend(tool_changes)

    for cached in cached_tools:
        if cached["tool_name"] in remote_map:
            continue
        if not cached["is_available"]:
            # 之前已经标记过下线，不重复报。
            continue
        if (cached["tool_name"], "tool_removed") in dismissed_keys:
            # 人已经确认过「知道这个工具远端没了，但我不打算处理」，别再每天问一遍。
            continue
        changes.append(
            {
                "tool_name": cached["tool_name"],
                "kind": "tool_removed",
                "severity": "breaking",
                "param": None,
                "before": {
                    "description": cached.get("tool_description"),
                    HASH_KEY: cached.get("definition_hash"),
                },
                "after": None,
                "summary": "远端已删除该工具",
            }
        )
    return changes, baseline_tools


#: 落库文本预算。``before_value`` / ``after_value`` 在 MySQL 里是 TEXT，上限
#: **65535 字节**（不是字符），而参数快照会把 ``items`` / ``anyOf`` 整段嵌进去——
#: 极端 schema 超长时写入报 ``Data too long``，整批探测的 commit 全部回滚。
#:
#: 裁剪必须发生在 ``json.dumps`` **之前**、且按 UTF-8 字节算：直接对序列化结果做
#: ``text[:N]`` 会写出半截 JSON，而 `_row_param` / `_row_definition_hash` /
#: `summarize_change` 全靠 ``json.loads`` 读它——解析失败的结果是「忽略」推不动
#: 指纹（每天重报）、参数去重键失配、摘要退化成「工具定义变更」。中文一个字 3 字节，
#: 按字符数算的 6 万「字符」实际可能是 180 KiB，照样超限。
_MAX_PAYLOAD_BYTES = 48_000
#: 单个字符串字段（描述最长的就是它）的字符上限；正常工具描述远小于此。
_STRING_FIELD_CHARS = 2_000
#: 兜底时仍要保住的关键键：没有 param 就无法去重，没有指纹就无法推进「已忽略」。
_PAYLOAD_KEY_KEYS = ("param", HASH_KEY, "type", "required")


def _shrink_payload(value: Any) -> Any:
    """递归截短过长的字符串字段，结构本身保持完整（结果仍能 json.dumps）。"""
    if isinstance(value, str):
        if len(value) <= _STRING_FIELD_CHARS:
            return value
        return value[:_STRING_FIELD_CHARS] + "…"
    if isinstance(value, dict):
        return {key: _shrink_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_shrink_payload(item) for item in value]
    return value


def _dump_payload(value: Any) -> Optional[str]:
    """序列化落库片段：任何情况下都产出**合法 JSON** 且不超 byte 预算。"""
    if value is None:
        return None
    text = json.dumps(_shrink_payload(value), ensure_ascii=False)
    if len(text.encode("utf-8")) <= _MAX_PAYLOAD_BYTES:
        return text

    # 还是太大（键极多 / 层级极深）：只留解读摘要必需的键，但仍带 param 与指纹。
    if isinstance(value, dict):
        minimal = {
            key: _shrink_payload(value[key])
            for key in _PAYLOAD_KEY_KEYS
            if key in value
        }
        minimal["truncated"] = True
        text = json.dumps(minimal, ensure_ascii=False)
        if len(text.encode("utf-8")) <= _MAX_PAYLOAD_BYTES:
            return text

    # 最后兜底：宁可直接退化成占位，也不能写半截 JSON 进库。
    placeholder: Dict[str, Any] = {"truncated": True}
    if isinstance(value, dict):
        if value.get("param"):
            placeholder["param"] = str(value["param"])[:200]
        if value.get(HASH_KEY):
            placeholder[HASH_KEY] = value[HASH_KEY]
    return json.dumps(placeholder, ensure_ascii=False)


def _change_payload(change: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """把变更的 before/after 片段序列化成落库文本。"""
    before = change.get("before")
    after = change.get("after")
    if change.get("kind") == "tool_removed" and isinstance(before, dict):
        after = {HASH_KEY: change.get("definition_hash")}
    if isinstance(after, dict) and change.get("definition_hash"):
        after = {**after, HASH_KEY: change["definition_hash"]}
    if isinstance(before, dict) and change.get("param"):
        before = {**before, "param": change["param"]}
    if isinstance(after, dict) and change.get("param"):
        after = {**after, "param": change["param"]}
    return _dump_payload(before), _dump_payload(after)


def _change_key(
    tool_name: str, change_kind: str, param: Optional[str]
) -> tuple:
    """变更的去重键。

    必须带 ``param``：一个工具同时改两个参数的描述（或加两个可选参数）会产生两条
    ``change_kind`` 相同的记录，只按 (tool_name, kind) 去重的话，第二次探测里
    ``dict`` 构造就会让其中一条「脱管」——它既不会被更新、也不在 ``observed`` 里，
    于是永远躺在 pending 里变成点进去看不到内容的僵尸行。
    """
    return (tool_name, change_kind, (param or ""))


def _row_param(row: McpToolDefinitionChange) -> str:
    """从已落库的 before/after 里取参数名；非参数级变更为空串。"""
    for raw in (row.after_value, row.before_value):
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("param"):
            return str(payload["param"])
    return ""


async def _load_dismissed_change_keys(server_id: str) -> set:
    """该服务里「人已明确忽略过」的 (tool_name, change_kind)。

    只给 ``tool_added`` / ``tool_removed`` 用（原因见 ``_compute_changes``）。
    永久生效：忽略是一种决定，不该过几天又自己冒出来。
    """
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    McpToolDefinitionChange.tool_name,
                    McpToolDefinitionChange.change_kind,
                ).where(
                    McpToolDefinitionChange.server_id == server_id,
                    McpToolDefinitionChange.status == "dismissed",
                    McpToolDefinitionChange.change_kind.in_(
                        ("tool_added", "tool_removed")
                    ),
                )
            )
        ).all()
    return {(str(row[0]), str(row[1])) for row in rows}


async def _persist_changes(
    server_id: str,
    changes: Sequence[Dict[str, Any]],
    *,
    detected_by: str,
    baseline_updates: Optional[Sequence[tuple]] = None,
    new_counter: Optional[List[int]] = None,
) -> int:
    """把差异写进变更表，返回该服务当前 pending 总数。

    去重口径：同一 (tool_name, change_kind, param) 只保留一条 pending，重复探测覆盖
    after_value 而不是叠加。同时把「这次没再出现的 pending」清掉——差异消失了
    就不该继续挂在待办里。

    ``new_counter``（可选 out 参数）会被追加「本次真正新建的条数」。夜间巡检靠它决定
    要不要发站内信：只按「算出来的差异总数」判断，会让一条没处理的 pending 每天凌晨
    都把管理员吵一遍。
    """
    now = datetime.now()
    batch_id = str(uuid.uuid4())
    created = 0

    async with AsyncSessionLocal() as db:
        existing_rows = (
            await db.execute(
                select(McpToolDefinitionChange).where(
                    McpToolDefinitionChange.server_id == server_id,
                    McpToolDefinitionChange.status == "pending",
                )
            )
        ).scalars().all()
        existing_by_key: Dict[tuple, List[McpToolDefinitionChange]] = {}
        for row in existing_rows:
            existing_by_key.setdefault(
                _change_key(row.tool_name, row.change_kind, _row_param(row)), []
            ).append(row)

        for change in changes:
            key = _change_key(change["tool_name"], change["kind"], change.get("param"))
            before_text, after_text = _change_payload(change)
            bucket = existing_by_key.get(key) or []
            row = bucket.pop(0) if bucket else None
            if row is not None:
                row.severity = change.get("severity") or "notice"
                row.before_value = before_text
                row.after_value = after_text
                row.detected_at = now
                row.detected_by = detected_by
                row.batch_id = batch_id
                continue
            db.add(
                McpToolDefinitionChange(
                    id=str(uuid.uuid4()),
                    batch_id=batch_id,
                    server_id=server_id,
                    tool_name=change["tool_name"],
                    change_kind=change["kind"],
                    severity=change.get("severity") or "notice",
                    before_value=before_text,
                    after_value=after_text,
                    detected_at=now,
                    detected_by=detected_by,
                    status="pending",
                )
            )
            created += 1

        # 差异消失 → 撤单：这条 pending 从没被人处理过，删掉不丢信息；留着只会变成
        # 「点进去发现没变化」的历史包袱。同键的多余历史行（早期没有 param 维度时
        # 攒下来的）也在这里一并清掉。
        for key, rows in existing_by_key.items():
            for row in rows:
                await db.delete(row)

        # 存量工具的基线指纹：只写 definition_hash，不碰描述/参数/发布状态——
        # 探测的「只读」边界是不改动任何**已生效**定义，而指纹只是一份记账。
        for tool_name, hash_value in baseline_updates or ():
            await db.execute(
                update(McpToolCache)
                .where(
                    McpToolCache.server_id == server_id,
                    McpToolCache.tool_name == tool_name,
                )
                .values(definition_hash=hash_value)
            )

        await db.flush()

        # definition_changed_at 只是「这工具当前有未确认差异」的标记，跟着 pending 走。
        pending_rows = (
            await db.execute(
                select(McpToolDefinitionChange.tool_name).where(
                    McpToolDefinitionChange.server_id == server_id,
                    McpToolDefinitionChange.status == "pending",
                )
            )
        ).scalars().all()
        pending_tool_names = set(pending_rows)
        cached_rows = (
            await db.execute(select(McpToolCache).where(McpToolCache.server_id == server_id))
        ).scalars().all()
        for cached in cached_rows:
            has_pending = cached.tool_name in pending_tool_names
            if has_pending and not getattr(cached, "definition_changed_at", None):
                cached.definition_changed_at = now
            elif not has_pending and getattr(cached, "definition_changed_at", None):
                cached.definition_changed_at = None

        await db.commit()
        if new_counter is not None:
            new_counter.append(created)
        return len(pending_rows)


async def probe_servers(
    server_ids: Sequence[str],
    *,
    user_info: Optional[Dict[str, Any]] = None,
    detected_by: str = DETECTED_NIGHTLY,
    concurrency: int = DEFAULT_PROBE_CONCURRENCY,
    skip_disabled: bool = False,
) -> List[Dict[str, Any]]:
    """批量探测，限制并发。

    单个服务探测失败不影响其它服务——夜间巡检要的是「尽量都探一遍」，
    不能因为一个服务连不上就整批放弃。
    """
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one(sid: str) -> Dict[str, Any]:
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    probe_server(
                        sid,
                        user_info=user_info,
                        detected_by=detected_by,
                        skip_disabled=skip_disabled,
                    ),
                    timeout=PROBE_TIMEOUT_SECONDS,
                )
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning(
                    "[MCP] 定义探测超时 %ss，按不可达处理: %s", PROBE_TIMEOUT_SECONDS, sid
                )
                await _record_probe_state(
                    sid,
                    status=PROBE_UNREACHABLE,
                    error=f"探测超时（{PROBE_TIMEOUT_SECONDS} 秒内未返回 tools/list）",
                    count_failure=True,
                )
                return {
                    "server_id": sid,
                    "server_name": None,
                    "status": PROBE_UNREACHABLE,
                    "new_changes": 0,
                    "pending_changes": 0,
                    "tool_count": 0,
                    "baseline_tools": 0,
                    "changes": [],
                    "error": f"探测超时（{PROBE_TIMEOUT_SECONDS}s）",
                }
            except Exception as exc:  # noqa: BLE001 - 批量场景下不能因单点失败中断
                logger.exception("[MCP] 定义探测异常 %s", sid)
                return {
                    "server_id": sid,
                    "server_name": None,
                    "status": PROBE_UNREACHABLE,
                    "new_changes": 0,
                    "pending_changes": 0,
                    "tool_count": 0,
                    "baseline_tools": 0,
                    "changes": [],
                    "error": _truncate_error(exc),
                }

    if not server_ids:
        return []
    return list(await asyncio.gather(*(_one(sid) for sid in server_ids)))


async def apply_changes(
    change_ids: Sequence[str],
    *,
    user_id: Optional[int] = None,
    user_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """应用所选变更。

    **粒度是「按服务整批同步」**，不是「只应用勾选的那几条」。原因：``sync_tools()``
    是整批覆盖语义，它会把远端当前定义一整套写进缓存、并顺手清理参数消失后留下的
    凭据槽。如果硬要「只应用 A 不应用 B」，就得在它之外再写一套局部写库逻辑，
    两条写路径迟早会漂移——而漂移的代价是凭据槽与工具定义对不上。

    于是这里的行为是：选中任意一条 → 该服务整体对齐远端 → 该服务所有 pending 一起
    结清（``sync_tools`` 内部已经这么做了，这里只补记处理人）。
    """
    requested = [str(cid) for cid in change_ids if cid]
    if not requested:
        return {"applied": 0, "servers": [], "errors": []}
    # 本次操作的时间界线：结清时只补写「这次同步顺带处理掉」的记录，不能把几个月前
    # 系统自动应用的历史行也改成当前操作人。
    #
    # 取整到秒是必须的：``handled_at`` 在 MySQL 里是 DATETIME（无小数秒），写入时
    # 微秒会被舍入。界线带微秒时，同一秒内完成的同步可能出现
    # ``handled_at(18:20:10) >= started_at(18:20:10.6→11)`` 为假的边界，同服务未勾选
    # 的那批记录 handled_by 依旧是 NULL。
    started_at = datetime.now().replace(microsecond=0)

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(McpToolDefinitionChange).where(
                    McpToolDefinitionChange.id.in_(requested),
                    McpToolDefinitionChange.status == "pending",
                )
            )
        ).scalars().all()
        server_ids = sorted({row.server_id for row in rows})
        # 记下 (id, server_id)：下面按「同步成功的服务」结清时不再回查 detached 实例
        pending_rows = [(str(row.id), str(row.server_id)) for row in rows]

    if not server_ids:
        return {"applied": 0, "servers": [], "errors": []}

    from app.services.ai.tools.mcp_client import McpClientService, McpSyncGuardError

    applied_servers: List[str] = []
    errors: List[Dict[str, str]] = []
    for server_id in server_ids:
        try:
            await McpClientService.sync_tools(server_id, user_info=user_info)
        except McpSyncGuardError as exc:
            # 0 工具保护触发：这是保护而不是缺陷，如实回报给人，不改动任何状态。
            errors.append({"server_id": server_id, "error": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - 单个服务失败不能连累其它服务
            logger.warning("[MCP] 应用变更失败 %s: %s", server_id, exc)
            errors.append({"server_id": server_id, "error": _truncate_error(exc)})
            continue
        applied_servers.append(server_id)

    applied = 0
    if applied_servers:
        settled_servers = set(applied_servers)
        settle_ids = [cid for cid, sid in pending_rows if sid in settled_servers]
        async with AsyncSessionLocal() as db:
            # 这里**自己**结清，而不是等 sync_tools 的那条 update 顺手改：apply 的返回值
            # 语义是「本次处理掉几条」，如果它依赖另一条路径的副作用，一旦那边调整条件
            # 就会静默变成「应用成功但回报 0 条」。两边写的都是 status=applied，重入无害。
            handled_at = datetime.now()
            result = await db.execute(
                update(McpToolDefinitionChange)
                .where(
                    or_(
                        McpToolDefinitionChange.id.in_(settle_ids),
                        # 应用是「整服务同步」语义：用户没勾的那些差异其实也一起生效了
                        # （``sync_tools`` 会把该服务所有 pending 结清）。只给勾选的补
                        # 处理人，其余会显示成「系统自动应用」，看起来像探测自己动手了。
                        and_(
                            McpToolDefinitionChange.server_id.in_(list(settled_servers)),
                            McpToolDefinitionChange.status == "applied",
                            McpToolDefinitionChange.handled_by.is_(None),
                            McpToolDefinitionChange.handled_at >= started_at,
                        ),
                    )
                )
                .values(status="applied", handled_at=handled_at, handled_by=user_id)
            )
            applied = int(result.rowcount or 0)
            await db.commit()

    return {"applied": applied, "servers": applied_servers, "errors": errors}


async def dismiss_changes(
    change_ids: Sequence[str],
    *,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """忽略所选变更。

    「忽略」的语义是**我确认过这个差异，但选择不应用**——所以要把该工具的
    ``definition_hash`` 推进到这条变更记录的远端指纹。不推进的话，远端一动没动、
    人已经确认过，明天巡检还会报同一件事，最后没人再看这个待办。
    """
    requested = [str(cid) for cid in change_ids if cid]
    if not requested:
        return {"dismissed": 0}

    now = datetime.now()
    dismissed = 0
    touched: Dict[str, set] = {}

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(McpToolDefinitionChange).where(
                    McpToolDefinitionChange.id.in_(requested),
                    McpToolDefinitionChange.status == "pending",
                )
            )
        ).scalars().all()

        for row in rows:
            hash_value = _row_definition_hash(row)
            row.status = "dismissed"
            row.handled_at = now
            row.handled_by = user_id
            dismissed += 1
            touched.setdefault(row.server_id, set()).add(row.tool_name)
            await db.flush()
            # 同一工具若还有别的 pending（例如描述改了、参数也改了，只忽略了其中一条），
            # 就不能推进指纹——否则剩下那条永远不会再被发现。
            still_pending = (
                await db.execute(
                    select(McpToolDefinitionChange.id).where(
                        McpToolDefinitionChange.server_id == row.server_id,
                        McpToolDefinitionChange.tool_name == row.tool_name,
                        McpToolDefinitionChange.status == "pending",
                    )
                )
            ).first()
            if still_pending or not hash_value:
                continue
            await db.execute(
                update(McpToolCache)
                .where(
                    McpToolCache.server_id == row.server_id,
                    McpToolCache.tool_name == row.tool_name,
                )
                .values(definition_hash=hash_value, definition_changed_at=None)
            )
        await db.commit()

    return {"dismissed": dismissed, "servers": sorted(touched)}


def _row_definition_hash(row: McpToolDefinitionChange) -> Optional[str]:
    """从变更记录里取「远端当前定义的指纹」。

    存在 after/before 片段的 ``_definition_hash`` 里（见 ``_change_payload``），
    这样不必为「忽略」再拉一次远端。
    """
    for raw in (row.after_value, row.before_value):
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            value = payload.get(HASH_KEY)
            if isinstance(value, str) and value:
                return value
    return None


#: 允许的状态取值（接口层校验用）
CHANGE_STATUSES = ("pending", "applied", "dismissed")


async def list_changes(
    *,
    status: Optional[str] = "pending",
    server_id: Optional[str] = None,
    change_kinds: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
    visible_server_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """按条件查变更记录，附带中文摘要与服务名。

    ``visible_server_ids`` 用来做归属裁剪（非管理员只能看自己的个人服务）。
    必须在 **SQL 里**过滤，不能查出来再在 Python 里删——那样 ``total`` 会变成
    「本页剩下的条数」，翻页器给出的总页数是假的。
    """
    from app.models.mcp import McpServer

    conditions = []
    if status and status != "all":
        conditions.append(McpToolDefinitionChange.status == status)
    if server_id:
        conditions.append(McpToolDefinitionChange.server_id == server_id)
    if change_kinds:
        conditions.append(McpToolDefinitionChange.change_kind.in_(list(change_kinds)))
    if since is not None:
        conditions.append(McpToolDefinitionChange.detected_at >= since)
    if visible_server_ids is not None:
        # 空集合要变成「谁也匹配不上」，而不是「不过滤」
        conditions.append(
            McpToolDefinitionChange.server_id.in_(list(visible_server_ids) or [""])
        )

    async with AsyncSessionLocal() as db:
        stmt = (
            select(McpToolDefinitionChange, McpServer.server_name)
            .join(McpServer, McpServer.id == McpToolDefinitionChange.server_id, isouter=True)
            .order_by(McpToolDefinitionChange.detected_at.desc())
            .limit(max(1, min(int(limit), 500)))
            .offset(max(0, int(offset)))
        )
        if conditions:
            stmt = stmt.where(*conditions)
        rows = (await db.execute(stmt)).all()

        # 计数走 COUNT(*) 而不是「把 id 全拉回来再 len」：变更表只增不删（已处理
        # 的记录要留作历史），几个月后一次翻页就是把几万行 id 读进内存只为了数个数。
        total_stmt = select(func.count()).select_from(McpToolDefinitionChange)
        if conditions:
            total_stmt = total_stmt.where(*conditions)
        total = int((await db.execute(total_stmt)).scalar() or 0)

    items = []
    for row, server_name in rows:
        items.append(
            {
                "id": row.id,
                "batch_id": row.batch_id,
                "server_id": row.server_id,
                "server_name": server_name,
                "tool_name": row.tool_name,
                "tool_short_name": row.tool_name.split(":", 1)[-1],
                "change_kind": row.change_kind,
                "severity": row.severity,
                "before_value": _safe_json(row.before_value),
                "after_value": _safe_json(row.after_value),
                "summary": summarize_change(row.change_kind, row.before_value, row.after_value),
                "detected_at": row.detected_at.isoformat() if row.detected_at else None,
                "detected_by": row.detected_by,
                "status": row.status,
                "handled_at": row.handled_at.isoformat() if row.handled_at else None,
                "handled_by": row.handled_by,
            }
        )
    return {"items": items, "total": total}


def _safe_json(raw: Optional[str]) -> Any:
    """把落库的 JSON 文本还原，并剥掉内部用的指纹键。"""
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(payload, dict):
        payload = {k: v for k, v in payload.items() if k != HASH_KEY}
    return payload


async def notify_admins_about_changes(results: Sequence[Dict[str, Any]]) -> int:
    """把一次巡检发现的定义变更汇总成**一条**站内信，发给所有启用中的管理员。

    刻意不「一个服务一条」：一次巡检可能同时命中好几个服务，逐条发的结果是管理员
    打开通知中心看到一屏红色，反而不知道该先看哪个。这里只发一条，点进去到
    「定义变更」页签按服务筛选。

    返回落库条数（= 收到通知的管理员数）。
    """
    changed = [
        item for item in results
        if item.get("status") == PROBE_CHANGED and int(item.get("new_changes") or 0) > 0
    ]
    if not changed:
        return 0

    from app.models.user import User as PlatformUser
    from app.services.portal_notification_service import PortalNotificationService

    server_ids = [item["server_id"] for item in changed]
    async with AsyncSessionLocal() as db:
        admin_rows = await db.execute(
            select(PlatformUser.id).where(
                PlatformUser.role == "admin", PlatformUser.status == 1
            )
        )
        admin_ids = [int(row[0]) for row in admin_rows if row[0] is not None]
        if not admin_ids:
            return 0

        # 「影响已发布工具」比「变了多少条」更能说明这件事要不要马上处理：
        # 未发布工具改了参数，没有任何智能体在用。
        published_rows = await db.execute(
            select(McpToolDefinitionChange.tool_name)
            .join(
                McpToolCache,
                (McpToolCache.server_id == McpToolDefinitionChange.server_id)
                & (McpToolCache.tool_name == McpToolDefinitionChange.tool_name),
            )
            .where(
                McpToolDefinitionChange.server_id.in_(server_ids),
                McpToolDefinitionChange.status == "pending",
                McpToolCache.is_published.is_(True),
            )
        )
        affected_published = len({row[0] for row in published_rows if row[0]})
        total_changes = sum(int(item.get("new_changes") or 0) for item in changed)
        names = [item.get("server_name") or item["server_id"] for item in changed]

        title = f"{len(changed)} 个 MCP 服务的工具定义有更新"
        content = (
            f"夜间巡检发现 {'、'.join(names[:3])}"
            f"{' 等' if len(names) > 3 else ''} 的工具定义与本地不一致，"
            f"共 {total_changes} 处待确认"
            f"{f'，其中影响 {affected_published} 个已发布工具' if affected_published else ''}。"
            "探测只记录差异，尚未改动任何工具；确认后再应用。"
        )

        for admin_id in admin_ids:
            await PortalNotificationService.create(
                db,
                user_id=admin_id,
                title=title,
                content=content,
                level="warning" if affected_published else "info",
                category="mcp_definition_change",
                resource_type="mcp_definition_change",
                metadata={
                    "action": "open_mcp_definition_changes",
                    "server_ids": server_ids,
                    "total_changes": total_changes,
                    "affected_published": affected_published,
                },
            )
        await db.commit()
    return len(admin_ids)
