"""主助手欢迎语：从已启用系统智能体动态生成专家清单。"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional

AGENT_ROSTER_PLACEHOLDER = "{agent_roster}"


def format_agent_roster_markdown(
    delegable_agents: Iterable[Any],
    *,
    current_display_name: str,
    current_description: str,
) -> str:
    """生成首次交互「能力全景」用的 Markdown 列表。"""
    lines: List[str] = []
    for agent in delegable_agents or []:
        display = (getattr(agent, "display_name", None) or getattr(agent, "name", "") or "未命名").strip()
        slug = (getattr(agent, "name", "") or "").strip()
        desc = (getattr(agent, "description", None) or "").strip() or "暂无描述"
        if slug:
            lines.append(f"   - **{display}**（`{slug}`）：{desc}")
        else:
            lines.append(f"   - **{display}**：{desc}")

    current_label = (current_display_name or "主助手").strip()
    current_desc = (current_description or "").strip() or "负责通用问答、代码辅助及无法明确分类的请求。"
    lines.append(f"   - **{current_label}（当前）**：{current_desc}")

    if len(lines) == 1:
        lines.insert(0, "   - （当前暂无其他可调度专家智能体，您可直接向本助手提问。）")
    return "\n".join(lines)


def inject_agent_roster(system_prompt: Optional[str], roster_markdown: str) -> str:
    if not system_prompt or AGENT_ROSTER_PLACEHOLDER not in system_prompt:
        return system_prompt or ""
    roster = (roster_markdown or "").strip() or "   - （暂无专家智能体清单）"
    return system_prompt.replace(AGENT_ROSTER_PLACEHOLDER, roster)


async def resolve_delegable_system_agents_for_user(
    session: Any,
    *,
    user_info: Optional[dict],
    current_agent_id: Optional[str],
) -> List[Any]:
    from app.services.ai.agent_manager import AgentManagerService
    from app.services.ai.tools.agent_delegate_tool import resolve_runnable_delegable_system_agents

    active_agents = await AgentManagerService.list_agents(session)
    raw_user_id = None
    is_admin = False
    if user_info:
        raw_user_id = user_info.get("user_id") or user_info.get("id")
        is_admin = user_info.get("role") == "admin"
    return await resolve_runnable_delegable_system_agents(
        session,
        active_agents,
        user_id=raw_user_id,
        is_admin=is_admin,
        current_agent_id=current_agent_id,
    )


def build_sub_agents_context(delegable_agents: Iterable[Any]) -> Optional[str]:
    """供 sub_agent_call 工具使用的技术向通讯录（与欢迎语清单数据源一致）。"""
    sub_agent_lines = []
    for agent in delegable_agents or []:
        display = getattr(agent, "display_name", None) or getattr(agent, "name", "")
        desc = getattr(agent, "description", None) or "无描述"
        caps = ", ".join(getattr(agent, "capabilities", None) or [])
        sub_agent_lines.append(
            f"- **标识 (agent_name)**: `{agent.name}` (展示名: {display})\n"
            f"  **职责描述**: {desc}\n"
            f"  **核心能力**: [{caps}]"
        )
    if not sub_agent_lines:
        return None
    return (
        "## 可委派子智能体清单 (可用通讯录)\n"
        "当且仅当你使用 `sub_agent_call` 工具时，可以通过传入 `agent_name`（标识）来调用以下已启用的智能体：\n\n"
        + "\n\n".join(sub_agent_lines)
    )
