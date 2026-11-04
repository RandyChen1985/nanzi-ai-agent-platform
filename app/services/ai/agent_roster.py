"""主助手欢迎语：从已启用系统智能体动态生成专家清单。"""
from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional

AGENT_ROSTER_PLACEHOLDER = "{agent_roster}"

# 委派深度上限是 1：团主专家被别的智能体委派而来时（深度 1）不能再向下委派。
# 团队介绍里往往写着「先问 A 再问 B」，模型会照着做然后撞 depth_exceeded，
# 所以身份段必须自带这条边界。
#
# 这句是**兜底**：调用方能拿到执行期深度（``get_current_agent_context().delegation_depth``）
# 时，深度 > 0 会改用确定句（见 build_team_identity_context 的 is_delegated 分支），
# 不再依赖模型自己去猜「我是不是被委派的」。读不到深度时才落到这条条件句上。
TEAM_DELEGATION_BOUNDARY_HINT = (
    "（边界）如果你本身就是被其他智能体委派来处理某个子任务的，你无法再向下委派；"
    "此时请直接给出结论，或说明该由谁来做，不要调用 `sub_agent_call`。"
)


def build_team_identity_context(
    team_profile: Optional[Mapping[str, Any]],
    *,
    has_members: bool,
    delegation_depth: int = 0,
) -> Optional[str]:
    """团主专家的身份段：我是哪个团的主专家、团队定位与流程约束是什么。

    ``team_profile`` 是 ``ai_agents.team_profile``（``{name, intro}``）。两处都为空
    时返回 None，调用方据此决定这一节要不要渲染。

    刻意不放进 ``description``：那份描述会出现在**父级**的委派通讯录里被用作选人
    依据，团队介绍里的「先找元数据专家」这类措辞会把路由带偏。这里注入的是当前
    智能体自己的提示词，父级看不到。

    ``delegation_depth`` 是**执行期**才知道的信息（``MainAgentContext.delegation_depth``）。
    深度 > 0 表示这一轮它本身就是被别的智能体委派来的（例如它同时是另一个团的子专家），
    此时「需要成员就用 sub_agent_call」这句必须反过来写：深度上限是 1，它再调只会拿到
    ``depth_exceeded``，而模型照着前一句去试就会先白失败一次、再降级作答。构建提示词的
    地方用 ``get_current_agent_context()`` 就能读到深度，不必等到工具调用阶段。
    """
    profile = team_profile if isinstance(team_profile, Mapping) else {}
    name = str(profile.get("name") or "").strip()
    intro = str(profile.get("intro") or "").strip()
    if not name and not intro:
        return None

    is_delegated = int(delegation_depth or 0) > 0

    parts: List[str] = []
    parts.append(f"## 你的团队：{name}" if name else "## 你的团队")
    parts.append(f"你是「{name}」的主专家。" if name else "你是这个专家团的主专家。")
    if intro:
        if is_delegated:
            # 介绍里常写「先问 A 再问 B」，这一轮它执行不了，只能拿来判断该由谁做
            parts.append("你的团队定位与分工如下，供你判断这类问题该交给哪位成员：")
        else:
            parts.append("团队的定位、协作流程与能力边界如下，遇到对应场景必须遵守：")
        parts.append(intro)
    if is_delegated:
        parts.append(
            "但你这一轮是被其他智能体委派来处理某个子任务的，只能用你自己的能力作答："
            "不要调用 `sub_agent_call`，委派深度已到上限，那样调用只会失败。"
            "若这件事确实需要你团队里某位成员，请直接说明该由谁来做。"
        )
    elif has_members:
        parts.append(
            "需要成员专长时，用 `sub_agent_call` 把子任务交给对应成员，不要自己勉强回答。"
        )
        # 团介绍里常见「总入口不查明细 / 不要代做」这类约束，模型容易读成「我什么都不做，
        # 让用户自己去找人」，于是明明有成员却只回一句「请你自己去那边办」。这里把
        # 「转交」与「代做」的界线划清：转交是团主的本职，不是越权。
        parts.append(
            "「交给成员办」不算「你代为执行」：团队介绍里若写了「不要代做 / 不查不写」，"
            "指的是你别自己动手，而不是别调用成员。把任务转给该做的人正是你的职责，"
            "不要退化成让用户自己去找人。"
        )
        # 反向也要堵住：别把「该转」读成「什么都转」。团主自己也是一个完整的智能体，
        # 它能独立答好的事不该为了用上团队而绕一圈。
        parts.append(
            "反过来，你能独立答好的事就自己答完，不要为了显得在用团队而转交；"
            "判断标准只有一条：这件事是否需要某位成员的专长或它的专属数据。"
        )
        # 但确实存在「就是不该委派」的成员（典型是写操作：必须由用户亲手选定、亲手确认）。
        # 这类规则通常写在成员的职责描述里，这里明确它对**那位成员**生效，不要被泛化到全团。
        parts.append(
            "例外：如果某位成员的职责描述里明确写了「必须直选 / 禁止委派」（通常是写操作），"
            "就按它说的办——把用户引导到那位专家；这条只对那一位生效，不要拿它挡掉其他成员的委派。"
        )
    else:
        # 团主但成员清单为空：常见中间态（开了「专家团主专家」、写好团介绍，调度范围还没
        # 配或还没发布）。这里必须说死「你现在转不了」，否则模型会照着团介绍里的分工承诺
        # 「我帮你转给某位专家」，下一句又改口「请你自己去那边办」——用户看到的是前后矛盾。
        parts.append(
            "当前这个团还没有可调用的成员，你现在无法把任务转交给别人："
            "不要承诺「我帮你转给某位专家」，也不要说「已为你转交」。"
            "若这件事需要更专的同事来做，直接说明该找谁、要准备哪些信息。"
        )
    # 深度已知且 > 0 时上面已经写明确了，条件句只留给「读不到深度」的兜底路径
    if not is_delegated:
        parts.append(TEAM_DELEGATION_BOUNDARY_HINT)
    return "\n\n".join(parts)


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
    configured_tools: Optional[Iterable[Any]] = None,
    tool_name: str = "sub_agent_call",
) -> List[Any]:
    """解析本次请求可委派的智能体（欢迎语清单、子代理通讯录共用）。

    ``configured_tools`` 是**当前智能体生效版本**的工具配置：配置了调度白名单就按名单
    收窄，否则维持系统级全量。不传时按「未配置」处理——这条回落是安全的（放宽到系统
    级全量），但会让配了白名单的智能体在通讯录里多列几个，所以调用方必须尽量传。

    候选口径与目录/sub_agent_call 共用：这里若走 UI 可见性口径（``list_agents`` 按
    ``created_by`` 过滤）会把全部系统级智能体剃成空清单，于是欢迎语永远显示
    「暂无其他可调度专家智能体」、子代理通讯录恒空。
    """
    from app.services.ai.tools.agent_delegate_tool import (
        resolve_delegation_allowlist,
        resolve_effective_delegable_agents,
    )

    allowlist_configured, allowlist = resolve_delegation_allowlist(
        configured_tools,
        tool_name=tool_name,
    )
    raw_user_id = None
    user_name = None
    is_admin = False
    if user_info:
        raw_user_id = user_info.get("user_id") or user_info.get("id")
        user_name = user_info.get("user_name") or user_info.get("username")
        is_admin = user_info.get("role") == "admin"
    return await resolve_effective_delegable_agents(
        session,
        user_id=raw_user_id,
        is_admin=is_admin,
        user_name=user_name,
        current_agent_id=current_agent_id,
        allowlist_configured=allowlist_configured,
        allowlist=allowlist,
    )



def build_sub_agents_context(
    delegable_agents: Iterable[Any],
    *,
    team_profile: Optional[Mapping[str, Any]] = None,
    delegation_depth: int = 0,
) -> Optional[str]:
    """供 sub_agent_call 工具使用的技术向通讯录（与欢迎语清单数据源一致）。

    团主专家额外带上身份段：对它来说这份清单就是「我的团队」，介绍里写的流程与
    能力边界属于必须遵守的约定，只列成员名字等于让它知道有人、却不知道该怎么配合。

    ``team_profile`` 有内容而名单为空时**仍然返回身份段**（不返回 None）：用户写了
    团介绍却还没配调度范围是常见中间态，此时模型至少该知道自己是团长、该找谁，
    而不是对团的存在一无所知。
    """
    agents = [agent for agent in (delegable_agents or [])]
    team_block = build_team_identity_context(
        team_profile,
        has_members=bool(agents),
        delegation_depth=delegation_depth,
    )

    sub_agent_lines = []
    for agent in agents:
        display = getattr(agent, "display_name", None) or getattr(agent, "name", "")
        desc = getattr(agent, "description", None) or "无描述"
        caps = ", ".join(getattr(agent, "capabilities", None) or [])
        sub_agent_lines.append(
            f"- **标识 (agent_name)**: `{agent.name}` (展示名: {display})\n"
            f"  **职责描述**: {desc}\n"
            f"  **核心能力**: [{caps}]"
        )
    if not sub_agent_lines:
        return team_block
    roster = (
        "## 可委派子智能体清单 (可用通讯录)\n"
        "当且仅当你使用 `sub_agent_call` 工具时，可以通过传入 `agent_name`（标识）来调用以下已启用的智能体：\n\n"
        + "\n\n".join(sub_agent_lines)
    )
    return f"{team_block}\n\n{roster}" if team_block else roster
