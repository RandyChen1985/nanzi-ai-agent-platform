"""Tool: search cross-session memory (summaries + optional history)."""
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from app.services.ai.tools.tool_compat import tool

from app.core.context import get_current_agent_context
from app.services.ai.daily_summary_service import DailySummaryService
from app.services.ai.memory_index_service import MemoryIndexService
from app.services.ai.session_summary_service import SessionSummaryService
from app.services.memory_config_service import MemoryConfigService

logger = logging.getLogger(__name__)


def parse_date_from_query(query: Optional[str]) -> Optional[str]:
    """解析查询词中的相对时间词或具体日期格式，统一输出为 YYYY-MM-DD 格式。"""
    if not query:
        return None

    query_clean = query.strip()

    # 1. 相对时间词
    if "今天" in query_clean:
        return date.today().isoformat()
    if "昨天" in query_clean:
        return (date.today() - timedelta(days=1)).isoformat()
    if "前天" in query_clean:
        return (date.today() - timedelta(days=2)).isoformat()

    # 2. X天前 (如 3天前)，限制在90天内以防溢出或无意义的时间检索
    m = re.search(r"(\d+)\s*天前", query_clean)
    if m:
        try:
            days = int(m.group(1))
            if 0 < days <= 90:
                return (date.today() - timedelta(days=days)).isoformat()
        except ValueError:
            pass

    # 3. 绝对日期格式：
    # 匹配 2026-05-28, 2026/05/28, 2026年5月28日, 5月28日, 5月28号, 5-28, 5.28
    m = re.search(r"(?:(20\d{2})[-/年])?(\d{1,2})[-/月.](\d{1,2})[日号]?", query_clean)
    if m:
        year_str, month_str, day_str = m.groups()
        year = int(year_str) if year_str else date.today().year
        month = int(month_str)
        day = int(day_str)
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    return None


_RECENCY_ONLY_PATTERN = re.compile(
    r"^(?:最近|近期|上次|之前|以前|以往|过往|历史|以往对话|历史对话|最近的对话|最近的聊天|前几次|以往会话|之前的对话|回顾|回顾对话)$",
    re.IGNORECASE,
)


def _is_recency_only_query(query: Optional[str]) -> bool:
    """判断查询词是否仅为纯粹的时间泛指词（如'最近'、'近期'、'上次'、'历史对话'等），

    而没有具体的业务主题关键词。这类查询应该直接按时间倒序召回活跃会话，而非作为关键词过滤。
    """
    if not query:
        return False
    cleaned = re.sub(r"[\s\-_，,。？！?!]+", "", query.strip())
    if not cleaned:
        return False
    return bool(_RECENCY_ONLY_PATTERN.match(cleaned))


@tool
async def memory_search(
    scope: str = "summary",
    query: Optional[str] = None,
    conversation_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """检索当前用户跨会话历史：摘要列表或指定会话消息明细。当用户问「今天我们聊了啥」「最近/上次聊了什么」「有没有讨论过」「回顾历史对话」时必须优先调用；新会话 messages 为空不代表从未聊过。禁止未调用本工具就声称「第一次对话」或编造历史。scope=summary 查跨会话摘要（有 query 则语义检索，支持解析昨天、今天等时间词）；scope=history 需 conversation_id 拉该会话 Redis 明细；scope=both 先摘要再明细。user_id 由系统自动隔离，勿传入他人 ID。

    Args:
        scope: summary | history | both
        query: 检索关键词或自然语言问句（如「今天」「机房」），用于摘要语义/关键词匹配
        conversation_id: history/both 时目标会话 ID
        limit: 返回条数上限
    """
    if not await MemoryConfigService.get_bool("memory_service_enabled", True):
        return "记忆服务未启用，无法检索历史会话。"

    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "无法识别当前用户，拒绝检索记忆。"

    top_k = limit if limit is not None else await MemoryConfigService.get_int("memory_search_knn_top_k", 5)
    scope_norm = (scope or "summary").strip().lower()
    if scope_norm not in ("summary", "history", "both"):
        scope_norm = "summary"

    uid = str(ctx.user_id)
    target_day = parse_date_from_query(query)

    daily_summary_data = None
    day_sessions = []
    day_cids = set()

    # 如果有具体时间范围且属于摘要相关的 scope，优先获取该日期的记忆
    if target_day and scope_norm in ("summary", "both"):
        try:
            # 1. 尝试获取该日期的每日摘要 (Daily Summary)
            daily_summary_data = await DailySummaryService.get_daily_summary(uid, target_day)
            # 2. 尝试获取该日期下发生的具体会话摘要列表
            day_sessions = await MemoryIndexService.list_session_summaries_for_day(uid, target_day)
            day_cids = {s.get("conversation_id") for s in day_sessions if s.get("conversation_id")}
        except Exception as e:
            logger.warning("[memory_search] failed to fetch daily memory for date=%s: %s", target_day, e)

    # 3. 规范化检索 query：针对“最近”、“近期”、“上次”、“历史”、“过往”等无具体业务关键词的泛时间提问，
    # 转换为 query=None 进行纯时间倒序（Last Active）摘要召回，避免被文本推导式过滤为 0 条。
    is_recency_only_query = _is_recency_only_query(query)
    effective_query = None if is_recency_only_query else (query.strip() if query else None)

    # 执行常规搜索
    try:
        data = await SessionSummaryService.search_for_user(
            user_id=uid,
            query=effective_query,
            scope=scope_norm,
            conversation_id=conversation_id,
            limit=top_k,
        )
    except Exception as e:
        logger.error("[memory_search] failed: %s", e)
        return f"记忆检索失败: {e}"

    # 4. 兜底回退：如果指定了具体关键词且未匹配到任何结果，同时未限制特定日期，
    # 则自动回退拉取最近活跃的会话摘要，防止因关键词字面偏差导致明明有历史却完全无法召回。
    used_fallback_recency = False
    if (
        scope_norm in ("summary", "both")
        and not data.get("summaries")
        and not day_sessions
        and not daily_summary_data
        and effective_query
        and not target_day
    ):
        try:
            fallback_data = await SessionSummaryService.search_for_user(
                user_id=uid,
                query=None,
                scope="summary",
                limit=top_k,
            )
            if fallback_data.get("summaries"):
                data["summaries"] = fallback_data["summaries"]
                used_fallback_recency = True
        except Exception as e:
            logger.warning("[memory_search] recency fallback failed: %s", e)

    lines = []

    # A. 渲染特定日期每日摘要 (Daily Summary)
    if daily_summary_data:
        lines.append(f"## 目标日期 ({target_day}) 的每日摘要")
        title = daily_summary_data.get("title") or "未命名"
        summary = daily_summary_data.get("summary") or ""
        lines.append(f"### **{title}**")
        lines.append(f"- **日终摘要**: {summary}")

        def append_list(label: str, field_name: str):
            val = daily_summary_data.get(field_name)
            if val:
                try:
                    lst = json.loads(val) if isinstance(val, str) else val
                    if isinstance(lst, list) and lst:
                        lines.append(f"- **{label}**: {', '.join(str(x) for x in lst)}")
                except Exception:
                    pass

        append_list("讨论主题", "topics")
        append_list("达成决策", "decisions")
        append_list("遗留待办", "open_items")
        append_list("相关实体", "entities")
        lines.append("")

    # B. 渲染特定日期当天会话摘要 (Session Summaries)
    if day_sessions:
        lines.append(f"## 目标日期 ({target_day}) 的会话摘要列表")
        for i, s in enumerate(day_sessions, 1):
            ts = int(s.get("last_active") or 0)
            active_time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else "未知"
            lines.append(
                f"{i}. **{s.get('title', '未命名')}** (活跃时间: {active_time_str})\n"
                f"   - conversation_id: `{s.get('conversation_id')}`\n"
                f"   - 摘要: {s.get('summary', '')}\n"
            )
        lines.append("")

    # C. 渲染其他匹配的全局会话摘要 (并去重已在当天列表中展示的会话)
    global_summaries = data.get("summaries") or []
    filtered_global = [s for s in global_summaries if s.get("conversation_id") not in day_cids]

    if filtered_global:
        if used_fallback_recency:
            lines.append(f"未找到与关键词「{effective_query}」直接相关的历史会话，已为您呈现最近活跃的会话摘要：\n")
        elif is_recency_only_query:
            lines.append("## 最近活跃的会话摘要\n")
        elif target_day:
            lines.append("## 其他匹配的全局会话摘要\n")
        else:
            lines.append("## 匹配的会话摘要\n")

        for i, s in enumerate(filtered_global, 1):
            score = s.get("score")
            score_txt = f" (相关度: {score:.3f})" if (score is not None and not used_fallback_recency and not is_recency_only_query) else ""
            ts = int(s.get("last_active") or 0)
            active_time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else "未知"
            lines.append(
                f"{i}. **{s.get('title', '未命名')}**{score_txt}\n"
                f"   - conversation_id: `{s.get('conversation_id')}`\n"
                f"   - 最后活跃: {active_time_str}\n"
                f"   - 摘要: {s.get('summary', '')}\n"
            )
    elif not target_day and not day_sessions and not daily_summary_data:
        lines.append("未找到匹配的会话摘要。")

    # D. 渲染指定的具体会话消息明细
    history = data.get("history") or []
    if history:
        cid = data.get("conversation_id") or conversation_id
        lines.append(f"\n## 会话明细 ({cid})\n")
        for m in history:
            role = m.get("role", "?")
            content = (m.get("content") or "")[:1500]
            lines.append(f"- **{role}**: {content}\n")

    return "\n".join(lines) if lines else "无记忆数据。"
