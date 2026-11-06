"""本轮 / 会话 MetaDataset 范围合并。"""

from __future__ import annotations

from typing import Any, Optional


def _normalize_id_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, (str, int)):
        raw = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def resolve_effective_metadata_dataset_ids(
    *,
    request_ids: Any = None,
    session_ids: Any = None,
    messages: Any = None,
) -> Optional[list[str]]:
    """本轮范围优先，否则会话挂载；皆空返回 None（不注入白名单）。"""
    request = merge_request_metadata_dataset_ids(request_ids=request_ids, messages=messages)
    if request:
        return request
    session = _normalize_id_list(session_ids)
    return session or None


def merge_request_metadata_dataset_ids(
    request_ids: Any = None,
    messages: Any = None,
) -> Optional[list[str]]:
    """顶层本轮 IDs 优先；缺失时仅从最后一条用户消息附件中提取范围。"""
    ids = _normalize_id_list(request_ids)
    if ids:
        return ids
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        for f in msg.get("files") or []:
            if not isinstance(f, dict) or f.get("type") != "metadata_dataset":
                continue
            for item in str(f.get("url") or "").split(","):
                value = item.strip()
                if value and value not in ids:
                    ids.append(value)
        break
    return ids or None

def normalize_bound_dataset_ids(raw: Any) -> list[str]:
    """规整 ``get_dataset_schema.metadata_dataset_ids`` 绑定值，只保留有效数据集 ID。

    这是**唯一实现**：``get_dataset_schema`` 入口用它过滤后再去查权限，列表接口用它
    统计「已绑定 N 个数据集」。两边口径必须一致，否则会出现「横幅说绑了 3 个、实际
    只有 1 个生效」——绑定值写成数据集名称时，运行时会被静默丢弃，所以这里也不计入。

    返回纯数字 ID（去重、保序）；全部无效时返回空列表（等价于未绑定）。
    """
    return [value for value in _normalize_id_list(raw) if value.isdigit()]


def extract_bound_metadata_dataset_ids(tools: Any) -> list[str]:
    """从智能体工具配置里取出 ``get_dataset_schema`` 的绑定数据集。

    工具配置在这次运行里可能是 dict（前端/DB 原样 JSON），也可能是
    ``ToolConfigItem`` 这类对象，两种都要认；只取第一个同名工具，与 registry
    ``_configure_runtime_tool_spec`` 的「一个工具一份绑定」保持一致。
    """
    for item in tools or []:
        if isinstance(item, dict):
            name = item.get("name")
            raw = item.get("metadata_dataset_ids")
        else:
            name = getattr(item, "name", None)
            raw = getattr(item, "metadata_dataset_ids", None)
        if str(name or "").strip() != "get_dataset_schema":
            continue
        return [str(value) for value in normalize_bound_dataset_ids(raw or [])]
    return []


def resolve_mounted_dataset_scope_ids() -> list[str]:
    """本轮真正生效的数据集范围：智能体绑定 > AgentContext > execution_options。

    空列表表示「未限定」。三条渠道都要读，因为它们在不同链路里生效：
      * ``AgentContext.bound_metadata_dataset_ids`` —— 智能体工具级绑定
        （``get_dataset_schema.metadata_dataset_ids``）。它与 registry 的
        schema 检索优先级一致，必须排在最前：绑定生效时模型只拿得到绑定数据集的
        schema，执行端若还按用户手选范围判定，会把自己系统生成的 SQL 拒掉；
      * ``AgentContext.metadata_dataset_ids`` —— pipeline 初始化时由
        ``agent_service`` 用 ``effective_metadata_dataset_ids``（已合并本轮请求 +
        会话固定）写入，工具执行期最可靠；
      * ``execution_options['metadata_dataset_scope']`` —— chat 入口写入，
        回答追问 / 只挂会话时可能只有这一条。

    这两处一直只被用来看日志、从没参与过滤，于是「会话固定了数据集 1」时
    模型照样能检索到用户有权限的其它数据集（同一张物理表可能注册在多个数据集里），
    日志还会诚实地打印「会话固定: 1 / 最终生效: 1、53、54」。
    """
    from app.core.context import get_current_agent_context, get_execution_option

    def collect(group: Any, into: list[str]) -> None:
        for item in group or []:
            # session 存的是 {id, name, ...} 字典，其余是裸 ID
            value = item.get("id") if isinstance(item, dict) else item
            text = str(value or "").strip()
            if text and text not in into:
                into.append(text)

    ids: list[str] = []
    ctx = get_current_agent_context()
    if ctx is not None:
        bound: list[str] = []
        collect(getattr(ctx, "bound_metadata_dataset_ids", None), bound)
        if bound:
            # 绑定优先：与 registry 的 get_dataset_schema 同口径，避免「schema 给 A、
            # 执行门禁按用户手选的 B 判定」导致整套 ChatBI 互相打架。
            return bound
        collect(getattr(ctx, "metadata_dataset_ids", None), ids)
    if ids:
        return ids

    scope = get_execution_option("metadata_dataset_scope", {}) or {}
    # 老请求里这个键可能是布尔开关，不是范围描述，直接忽略
    if not isinstance(scope, dict):
        return []
    collect(scope.get("request_ids"), ids)
    collect(scope.get("session"), ids)
    return ids


async def resolve_mounted_dataset_id_set(session: Any) -> Optional[set[int]]:
    """把本轮挂载值（可能是 ID，也可能是名称）解析成数据集 ID 集合。

    未限定范围时返回 ``None``（保持历史行为）。解析不出来时返回**空集合**——
    调用方据此 fail-closed，不会悄悄放宽成「全部数据集」。
    """
    raw = resolve_mounted_dataset_scope_ids()
    if not raw:
        return None

    from sqlalchemy import func, or_, select

    from app.models.metadata import MetaDataset

    wanted_ids: list[int] = []
    wanted_names: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        if text.isdigit():
            wanted_ids.append(int(text))
        else:
            wanted_names.add(text.lower())

    conditions = []
    if wanted_ids:
        conditions.append(MetaDataset.id.in_(wanted_ids))
    if wanted_names:
        conditions.append(func.lower(MetaDataset.name).in_(sorted(wanted_names)))
    if not conditions:
        return None

    rows = (
        await session.execute(select(MetaDataset.id).where(or_(*conditions)))
    ).scalars().all()
    return {int(item) for item in rows if item is not None}
