"""智能体分享：把自己的智能体发一份副本给同事。

语义边界（容易和「授权」搞混，写在这里备查）：

* 分享 **不是** 授权共用。接收方接受后得到一个**全新的、属于自己的**智能体，
  需要重新命名并走向导确认配置；源与副本从此互不影响。
* 复制走**分享那一刻的快照**，不是接受时现读源。这样源后来被改、被删、被提升为
  系统级，都不影响已经发出的分享——「我分享给你的是当时那一版」这个承诺才成立。
* 系统级智能体**不提供**分享。它的可用范围由管理员统一授权治理（下架要按授权表
  统计受影响人数并发通知），所有者若能自己往外发副本，等于绕开这套治理。
* 接收方可能是普通用户，源配置里属于分享人的私有 MCP / 被禁用的技能对他不可见，
  所以复制时必须剔除，否则副本一创建就带着一堆「无权访问」的告警。
"""
from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.models.agent import AIAgent, AIAgentVersion
from app.services.ai.agent_system_publication_service import (
    ACTION_SHARE_ACCEPT,
    ACTION_SHARE_REJECT,
    ACTION_SHARE_REVOKE,
    ACTION_SHARE_SEND,
)

from app.models.agent_share import (
    DEFAULT_EXPIRE_DAYS,
    STATUS_ACCEPTED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_REVOKED,
    STATUS_WITHDRAWN,
    AgentShareRequest,
)

SNAPSHOT_VERSION = 1
SHARE_MESSAGE_MAX = 500


class AgentShareService:
    """分享记录的生命周期。"""

    # ------------------------------------------------------------------ 基础设施

    @staticmethod
    def resolve_actor(user: Any) -> tuple[bool, Optional[str], Optional[int]]:
        if not user:
            return False, None, None
        if isinstance(user, dict):
            raw_id = user.get("user_id") or user.get("id")
            try:
                uid = int(raw_id) if raw_id is not None else None
            except (TypeError, ValueError):
                uid = None
            return user.get("role") == "admin", user.get("user_name"), uid
        raw_id = getattr(user, "id", None)
        try:
            uid = int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            uid = None
        return getattr(user, "role", None) == "admin", getattr(user, "user_name", None), uid

    # None=未探测；True=已建表；False=缺表
    _TABLE_STATE: Optional[bool] = None

    @staticmethod
    async def supports_share(session: AsyncSession) -> bool:
        """表是否已建好。

        代码通常会先于迁移上线，缺表时分享功能整体不可用即可，
        不能让智能体中心的其它接口跟着 500。

        探测必须放进 SAVEPOINT：MySQL 上一条失败语句会让整个事务进入错误状态，
        直接 ``rollback()`` 会把**调用方的业务事务**一起回滚——表现为别的接口
        突然抛 MissingGreenlet（实例被 expire 后再访问属性就触发 IO）。
        结论会缓存，热路径不重复探测。
        """
        from sqlalchemy import text

        if AgentShareService._TABLE_STATE is not None:
            return AgentShareService._TABLE_STATE

        try:
            async with session.begin_nested():
                await session.execute(
                    text("SELECT 1 FROM ai_agent_share_requests LIMIT 1")
                )
        except Exception:
            AgentShareService._TABLE_STATE = False
            return False

        AgentShareService._TABLE_STATE = True
        return True

    @staticmethod
    def reset_probe_state_for_tests(state: Optional[bool] = None) -> None:
        AgentShareService._TABLE_STATE = state

    # ------------------------------------------------------------------ 快照

    @staticmethod
    def build_snapshot(agent: AIAgent, version: Optional[AIAgentVersion]) -> Dict[str, Any]:
        """把「元数据 + 版本配置」拍成一份可独立还原的 JSON。"""
        return {
            "snapshot_version": SNAPSHOT_VERSION,
            "agent": {
                "name": agent.name,
                "display_name": agent.display_name,
                "description": agent.description,
                "avatar_url": agent.avatar_url,
                "capabilities": copy.deepcopy(agent.capabilities),
                "agent_type": agent.agent_type,
                "engine_type": agent.engine_type,
                "engine_config": copy.deepcopy(agent.engine_config),
                "owner_group": agent.owner_group,
            },
            "version": (
                {
                    "model_name": version.model_name,
                    "temperature": version.temperature,
                    "synthesis_model_name": version.synthesis_model_name,
                    "synthesis_temperature": version.synthesis_temperature,
                    "allow_model_switch": bool(getattr(version, "allow_model_switch", True)),
                    "system_prompt": version.system_prompt,
                    "tools": copy.deepcopy(version.tools),
                    "toolcall_timeout_seconds": version.toolcall_timeout_seconds,
                    "skills_custom": bool(getattr(version, "skills_custom", False)),
                    "skills": copy.deepcopy(version.skills),
                    "welcome_config": copy.deepcopy(version.welcome_config),
                    "source_version_number": version.version_number,
                }
                if version is not None
                else None
            ),
        }

    @staticmethod
    async def _pick_snapshot_version(
        session: AsyncSession, agent_id: str
    ) -> Optional[AIAgentVersion]:
        """优先取线上版本：分享出去的是「正在用的那份配置」，不是某个半成品草稿。"""
        from sqlalchemy import case

        rows = await session.execute(
            select(AIAgentVersion)
            .where(AIAgentVersion.agent_id == agent_id)
            .order_by(
                case((AIAgentVersion.status == "PUBLISHED", 0), else_=1),
                AIAgentVersion.version_number.desc(),
            )
            .limit(1)
        )
        return rows.scalars().first()

    # ------------------------------------------------------------------ 摘要

    @staticmethod
    def summarize(snapshot: Any) -> Dict[str, Any]:
        """从快照里抽一份「这个智能体是干嘛的」摘要给列表用。

        只给名字和分享时间的话，接收方根本判断不了要不要接受——尤其名字是
        ``dddd`` 这种的时候。模型、工具数、提示词开头都比名字有信息量。
        """
        payload = snapshot if isinstance(snapshot, dict) else {}
        if isinstance(snapshot, (str, bytes, bytearray)):
            try:
                payload = json.loads(snapshot)
            except (ValueError, TypeError):
                payload = {}

        meta = payload.get("agent") or {}
        version = payload.get("version") or {}
        prompt = str(version.get("system_prompt") or "")
        tools = version.get("tools") or []
        skills = version.get("skills") or []

        def _count(items: Any) -> int:
            if isinstance(items, (list, tuple, set)):
                return len([item for item in items if item])
            return 0

        return {
            "description": meta.get("description") or None,
            "model_name": version.get("model_name") or None,
            "source_version_number": version.get("source_version_number"),
            "tool_count": _count(tools),
            "skill_count": _count(skills),
            "prompt_excerpt": (prompt[:160] + "…") if len(prompt) > 160 else (prompt or None),
            "prompt_length": len(prompt),
            # 提示词：接受前要看清楚「这东西到底怎么说话」，前端默认折叠。
            # 但列表接口不能无上限：有人写几万字提示词时，一页 50 条能把响应体
            # 撑到几 MB，首屏直接卡住。截断到 4000 字，够看清风格了。
            "system_prompt": (prompt[:4000] + "…") if len(prompt) > 4000 else (prompt or None),
            "tool_names": [
                (item.get("name") if isinstance(item, dict) else str(item))
                for item in (tools if isinstance(tools, (list, tuple)) else [])
                if item
            ][:30],
            "welcome_message": (
                (version.get("welcome_config") or {}).get("welcome_message")
                if isinstance(version.get("welcome_config"), dict)
                else None
            ),
        }

    # ------------------------------------------------------------------ 事件

    @staticmethod
    async def _record_event(
        session: AsyncSession,
        *,
        agent_id: str,
        action: str,
        actor_name: Optional[str],
        actor_id: Optional[int],
        reason: Optional[str] = None,
    ) -> None:
        """把分享动作写进发布历史。

        源智能体可能已经被删（分享记录仍要能查），所以查不到就跳过——
        审计写失败不该让分享本身失败。
        """
        from app.services.ai.agent_system_publication_service import (
            AgentSystemPublicationService,
        )

        agent = await session.get(AIAgent, agent_id)
        if not agent:
            return
        try:
            await AgentSystemPublicationService.record_event(
                session,
                agent=agent,
                action=action,
                actor_name=actor_name or "",
                actor_id=actor_id,
                reason=reason,
            )
        except Exception:
            # 事件表缺失/写入失败都不能回滚掉分享结果
            pass

    # ------------------------------------------------------------------ 分享

    @staticmethod
    async def create_shares(
        session: AsyncSession,
        agent_id: str,
        recipient_user_names: List[str],
        user: Any,
        message: Optional[str] = None,
    ) -> List[AgentShareRequest]:
        """把智能体分享给一个或多个人。每人一条独立记录，各自接受、各自过期。"""
        from app.models.user import User

        is_admin, username, user_id = AgentShareService.resolve_actor(user)

        agent = await session.get(AIAgent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="智能体不存在")

        if not is_admin and agent.created_by != username:
            raise HTTPException(status_code=403, detail="只有智能体所有者可以分享")

        # 系统级是平台生产基线，可用范围只能由管理员授权治理。
        # 界面会隐藏入口，这里必须再挡一次——接口是公开可调的。
        if bool(agent.is_system):
            raise HTTPException(
                status_code=409,
                detail="系统级智能体不支持分享，如需他人使用请由管理员授权",
            )

        if not agent.is_enabled:
            raise HTTPException(status_code=409, detail="已停用的智能体不能分享")

        version = await AgentShareService._pick_snapshot_version(session, agent_id)
        if version is None:
            raise HTTPException(status_code=409, detail="该智能体还没有任何版本，无法分享")

        snapshot = json.dumps(
            AgentShareService.build_snapshot(agent, version), ensure_ascii=False
        )

        # 目标用户必须存在且启用：转给停用账号等于换个方式变成孤儿数据
        wanted = sorted({str(name).strip() for name in recipient_user_names if str(name or "").strip()})
        if not wanted:
            raise ValueError("请至少选择一个接收人")
        if username and username in wanted:
            raise HTTPException(status_code=400, detail="不能分享给自己")

        rows = await session.execute(
            select(User.id, User.user_name, User.status).where(User.user_name.in_(wanted))
        )
        found = {str(row[1]): (int(row[0]), row[2]) for row in rows.all()}
        missing = [name for name in wanted if name not in found]
        if missing:
            raise HTTPException(
                status_code=404, detail=f"以下用户不存在：{'、'.join(missing)}"
            )
        disabled = [name for name in wanted if found[name][1] != 1]
        if disabled:
            raise HTTPException(
                status_code=409, detail=f"以下账号已停用，无法分享：{'、'.join(disabled)}"
            )

        note = (message or "").strip()[:SHARE_MESSAGE_MAX] or None
        expires_at = datetime.now() + timedelta(days=DEFAULT_EXPIRE_DAYS)

        created: List[AgentShareRequest] = []
        for name in wanted:
            uid = found[name][0]
            record = AgentShareRequest(
                id=str(uuid.uuid4()),
                agent_id=agent_id,
                agent_name=agent.name,
                agent_display_name=agent.display_name,
                owner_user_id=user_id,
                owner_user_name=username,
                recipient_user_id=uid,
                recipient_user_name=name,
                snapshot=snapshot,
                snapshot_version=SNAPSHOT_VERSION,
                status=STATUS_PENDING,
                share_message=note,
                created_at=datetime.now(),
                expires_at=expires_at,
            )
            session.add(record)
            created.append(record)

        await session.flush()

        from app.services.ai.agent_system_notifier import AgentSystemNotifier

        display_label = agent.display_name or agent.name or "智能体"
        for name in wanted:
            try:
                await AgentSystemNotifier.notify_agent_shared(
                    session,
                    agent_id=agent_id,
                    display_name=display_label,
                    owner_name=username or "",
                    recipient_user_id=found[name][0],
                    message=note,
                )
            except Exception:
                # 通知失败不回滚分享本身
                pass

        names = "、".join(wanted)
        await AgentShareService._record_event(
            session,
            agent_id=agent_id,
            action=ACTION_SHARE_SEND,
            actor_name=username,
            actor_id=user_id,
            reason=f"分享给 {names}",
        )
        return created

    # ------------------------------------------------------------------ 收件箱

    @staticmethod
    async def _expire_if_needed(session: AsyncSession, record: AgentShareRequest) -> bool:
        """惰性过期：不依赖定时任务，读到时顺手判定。

        返回是否真的改了状态——调用方据此决定要不要 commit。只 flush 不提交的话，
        请求一结束改动就被丢弃，数据库里过期记录永远停在 PENDING，审计数据是脏的。
        """
        if record.status != STATUS_PENDING or not record.expires_at:
            return False
        if record.expires_at > datetime.now():
            return False
        record.status = STATUS_EXPIRED
        record.responded_at = datetime.now()
        await session.flush()
        return True

    @staticmethod
    async def _expire_many(
        session: AsyncSession, records: List[AgentShareRequest]
    ) -> bool:
        changed = False
        for record in records:
            if await AgentShareService._expire_if_needed(session, record):
                changed = True
        return changed

    @staticmethod
    async def list_received(
        session: AsyncSession, user: Any, *, only_pending: bool = False
    ) -> List[AgentShareRequest]:
        _, _, user_id = AgentShareService.resolve_actor(user)
        if user_id is None:
            return []
        stmt = (
            select(AgentShareRequest)
            .where(AgentShareRequest.recipient_user_id == user_id)
            .order_by(AgentShareRequest.created_at.desc())
        )
        if only_pending:
            stmt = stmt.where(AgentShareRequest.status == STATUS_PENDING)
        records = list((await session.execute(stmt)).scalars().all())
        expired = await AgentShareService._expire_many(session, records)
        # 过期判定改过状态，重新筛一遍，避免把刚过期的仍当作待处理返回
        if only_pending:
            records = [r for r in records if r.status == STATUS_PENDING]
        # GET 接口里唯一会写数据的场景：把过期状态落库，否则永远是 PENDING
        if expired:
            await session.commit()
        return records

    @staticmethod
    async def list_for_agent(
        session: AsyncSession, agent_id: str, user: Any
    ) -> List[AgentShareRequest]:
        is_admin, username, _ = AgentShareService.resolve_actor(user)
        agent = await session.get(AIAgent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="智能体不存在")
        if not is_admin and agent.created_by != username:
            raise HTTPException(status_code=403, detail="只有智能体所有者可以查看分享记录")
        rows = await session.execute(
            select(AgentShareRequest)
            .where(AgentShareRequest.agent_id == agent_id)
            .order_by(AgentShareRequest.created_at.desc())
        )
        records = list(rows.scalars().all())
        if await AgentShareService._expire_many(session, records):
            await session.commit()
        return records

    # ------------------------------------------------------------------ 接受 / 拒绝

    @staticmethod
    async def _get_pending_for_recipient(
        session: AsyncSession, share_id: str, user: Any
    ) -> AgentShareRequest:
        _, _, user_id = AgentShareService.resolve_actor(user)
        # 行锁：两个人同时点「接受」时，没有它会双双通过 PENDING 校验、各生成一份副本。
        # SQLite 会忽略 FOR UPDATE，测试环境行为不变。
        record = await session.get(AgentShareRequest, share_id, with_for_update=True)
        if not record or record.recipient_user_id != user_id:
            raise HTTPException(status_code=404, detail="分享记录不存在")
        await AgentShareService._expire_if_needed(session, record)
        if record.status != STATUS_PENDING:
            detail = {
                STATUS_ACCEPTED: "该分享已被接受",
                STATUS_REJECTED: "该分享已被拒绝",
                STATUS_EXPIRED: "该分享已过期",
                STATUS_REVOKED: record.share_reason or "该分享已作废",
            }.get(record.status, "该分享已不可用")
            raise HTTPException(status_code=409, detail=detail)
        return record

    @staticmethod
    async def accept(
        session: AsyncSession,
        share_id: str,
        user: Any,
        *,
        name: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> AIAgent:
        """接受分享，按快照生成一份属于接收方的新智能体。

        副本走**向导**：版本为 DRAFT、``onboarding_step`` 停在向导第一屏，
        接收方需要重新命名并逐项确认后才发布。这是有意为之——分享出去的是配置，
        不是「一个可以直接上线的成品」。
        """
        from app.services.ai.agent_manager import AgentManagerService
        from app.services.ai.skills.policy import drop_forbidden_skill_items  # noqa: F401

        record = await AgentShareService._get_pending_for_recipient(session, share_id, user)
        _, username, user_id = AgentShareService.resolve_actor(user)

        payload = json.loads(record.snapshot or "{}")
        meta = payload.get("agent") or {}
        version_data = payload.get("version") or {}

        # 快照缺失就拒绝，不要造一个空壳副本：用户拿到一个没有提示词、没有工具、
        # 名字还叫 xxx-copy 的东西，比直接报错更难排查。
        if not meta or not version_data:
            raise HTTPException(
                status_code=409,
                detail="这条分享缺少配置快照，无法生成副本；请让分享人重新分享一次",
            )

        # 名字不是必填：不给就按源标识符生成。
        # 用户明确指定了就用他的，只在重名时才递增——不要把人家的名字也拼上 -copy。
        requested = (name or "").strip()
        if requested:
            new_name = await AgentShareService._unique_agent_name(
                session, requested, prefer_exact=True
            )
        else:
            new_name = await AgentShareService._unique_agent_name(
                session, meta.get("name") or "agent"
            )

        tools = copy.deepcopy(version_data.get("tools"))
        tools, dropped_tools = await AgentManagerService.drop_inaccessible_mcp_tools(
            session, tools, user
        )
        skills, dropped_skills = await AgentShareService._drop_forbidden_skills(
            session, version_data.get("skills"), user_id
        )

        new_agent_id = str(uuid.uuid4())
        agent = AIAgent(
            id=new_agent_id,
            name=new_name,
            display_name=(display_name or "").strip() or meta.get("display_name") or new_name,
            description=meta.get("description"),
            avatar_url=meta.get("avatar_url"),
            capabilities=copy.deepcopy(meta.get("capabilities")),
            agent_type=meta.get("agent_type"),
            # 停在向导第一步：副本必须重新命名，然后逐项确认配置才发布
            onboarding_step="BASIC",
            is_system=False,
            sort_order=0,
            is_enabled=True,
            created_by=username or None,
            engine_type=meta.get("engine_type"),
            engine_config=copy.deepcopy(meta.get("engine_config")),
            owner_group=meta.get("owner_group"),
        )
        session.add(agent)

        source_label = record.agent_display_name or record.agent_name or "他人分享"
        comment = f"接受分享自 {source_label}"
        if version_data.get("source_version_number"):
            comment += f" v{version_data['source_version_number']}"
        if dropped_tools or dropped_skills:
            comment += f"（已剔除 {len(dropped_tools) + len(dropped_skills)} 个无权使用的配置项）"

        session.add(
            AIAgentVersion(
                id=str(uuid.uuid4()),
                agent_id=new_agent_id,
                version_number=1,
                model_name=version_data.get("model_name"),
                temperature=version_data.get("temperature"),
                synthesis_model_name=version_data.get("synthesis_model_name"),
                synthesis_temperature=version_data.get("synthesis_temperature"),
                allow_model_switch=bool(version_data.get("allow_model_switch", True)),
                system_prompt=version_data.get("system_prompt") or "",
                tools=tools,
                toolcall_timeout_seconds=version_data.get("toolcall_timeout_seconds"),
                skills_custom=bool(version_data.get("skills_custom", False)),
                skills=skills,
                welcome_config=copy.deepcopy(version_data.get("welcome_config")),
                # 草稿：接收方要走向导确认后再自己发布上线
                status="DRAFT",
                comment=comment,
            )
        )

        record.status = STATUS_ACCEPTED
        record.responded_at = datetime.now()
        record.new_agent_id = new_agent_id

        try:
            await session.flush()
        except IntegrityError as exc:
            # ai_agents.name 是全局唯一。两个人同时接受、或刚好有人抢先注册了
            # 同一个标识符时，_unique_agent_name 查到的「可用名」插入时仍可能撞车。
            # 回滚整条接受动作（分享记录回到 PENDING），让用户换个名字重试，
            # 而不是抛一个 500 让他以为系统坏了。
            await session.rollback()
            if "name" in str(getattr(exc, "orig", exc)).lower():
                raise HTTPException(
                    status_code=409, detail="该名称已被占用，请换一个名字重试"
                ) from exc
            raise

        from app.services.ai.agent_system_notifier import AgentSystemNotifier

        try:
            await AgentSystemNotifier.notify_share_accepted(
                session,
                agent_id=record.agent_id,
                display_name=record.agent_display_name or record.agent_name or "智能体",
                owner_user_id=record.owner_user_id,
                recipient_name=username or "",
            )
        except Exception:
            pass

        await AgentShareService._record_event(
            session,
            agent_id=record.agent_id,
            action=ACTION_SHARE_ACCEPT,
            actor_name=username,
            actor_id=user_id,
            reason=f"{record.recipient_user_name or '接收人'} 已接受并生成副本",
        )
        return agent

    @staticmethod
    async def _unique_agent_name(
        session: AsyncSession, base: str, *, prefer_exact: bool = False
    ) -> str:
        """生成一个不被占用的物理标识符。

        * ``prefer_exact=True``（用户自己填了名字）：先试原名，重名才 ``-2``、``-3``；
        * 否则（用户没填）：直接给 ``xxx-copy``，重名再 ``xxx-copy-2``。

        物理标识符全局唯一且用户可见，重名时直接报错会让人反复试；
        这里给出下一个可用值，用户之后想改再改。
        """
        from app.services.ai.agent_manager import AgentManagerService

        normalized = AgentManagerService.normalize_agent_name(base)
        if not normalized:
            normalized = "agent"

        if prefer_exact:
            if not await AgentManagerService.find_agent_name_conflict(session, normalized):
                return normalized
            for index in range(2, 100):
                candidate = f"{normalized}-{index}"
                if not await AgentManagerService.find_agent_name_conflict(session, candidate):
                    return candidate
            return f"{normalized}-{uuid.uuid4().hex[:6]}"

        # 去掉可能已经带的 -copy / -copy-N，避免出现 xxx-copy-copy
        stripped = re.sub(r"-copy(-\d+)?$", "", normalized) or "agent"

        candidate = f"{stripped}-copy"
        if not await AgentManagerService.find_agent_name_conflict(session, candidate):
            return candidate
        for index in range(2, 100):
            candidate = f"{stripped}-copy-{index}"
            if not await AgentManagerService.find_agent_name_conflict(session, candidate):
                return candidate
        return f"{stripped}-{uuid.uuid4().hex[:6]}"

    @staticmethod
    async def _drop_forbidden_skills(
        session: AsyncSession, skills: Any, user_id: Optional[int]
    ) -> tuple[List[str], List[str]]:
        """剔除接收方被禁用、且属于公共范围的技能。

        与聊天时的口径一致：个人技能不走禁用名单（同名公共技能被禁不该连累它）。
        这里只处理平台已知的禁用集合，判断不出来时保留——宁可多带一个，
        也不要静默丢配置。
        """
        items = [str(item) for item in (skills or []) if item]
        if not items or user_id is None:
            return items, []
        try:
            from app.services.permission_service import PermissionService

            perms = await PermissionService(session).get_user_permissions(int(user_id))
            forbidden = {
                str(sid).strip()
                for sid in (perms.permissions.forbidden_skills or [])
                if str(sid).strip()
            }
        except Exception:
            return items, []
        if not forbidden:
            return items, []
        kept = [sid for sid in items if sid not in forbidden]
        dropped = [sid for sid in items if sid in forbidden]
        return kept, dropped

    @staticmethod
    async def reject(session: AsyncSession, share_id: str, user: Any) -> AgentShareRequest:
        record = await AgentShareService._get_pending_for_recipient(session, share_id, user)
        record.status = STATUS_REJECTED
        record.responded_at = datetime.now()
        await session.flush()

        _, username, user_id = AgentShareService.resolve_actor(user)
        await AgentShareService._record_event(
            session,
            agent_id=record.agent_id,
            action=ACTION_SHARE_REJECT,
            actor_name=username,
            actor_id=user_id,
            reason=f"{record.recipient_user_name or '接收人'} 拒绝了这个分享",
        )
        return record

    @staticmethod
    async def list_sent_shares(
        session: AsyncSession, user: Any
    ) -> List[AgentShareRequest]:
        """我发出的分享。

        与技能/MCP 分享保持一致：分享人也要能看到「我发出去的东西现在什么状态」，
        不能发完就查不到——尤其是对方一直没接受的时候。
        """
        _, _, user_id = AgentShareService.resolve_actor(user)
        if user_id is None:
            return []
        rows = await session.execute(
            select(AgentShareRequest)
            .where(AgentShareRequest.owner_user_id == user_id)
            .order_by(AgentShareRequest.created_at.desc())
        )
        records = list(rows.scalars().all())
        if await AgentShareService._expire_many(session, records):
            await session.commit()
        return records

    @staticmethod
    async def withdraw(session: AsyncSession, share_id: str, user: Any) -> AgentShareRequest:
        """分享人撤回一条还没被接受的分享。

        过期是自动的，但「我发错了想收回」得给个主动入口，否则只能等 7 天。
        """
        _, username, user_id = AgentShareService.resolve_actor(user)
        record = await session.get(AgentShareRequest, share_id)
        if not record:
            raise HTTPException(status_code=404, detail="分享记录不存在")
        if record.owner_user_id != user_id:
            raise HTTPException(status_code=403, detail="只有分享人可以撤回")

        await AgentShareService._expire_if_needed(session, record)
        if record.status != STATUS_PENDING:
            raise HTTPException(status_code=409, detail="该分享已处理，无法撤回")

        record.status = STATUS_WITHDRAWN
        record.responded_at = datetime.now()
        record.share_reason = "分享人主动撤回"
        await session.flush()

        from app.services.ai.agent_system_notifier import AgentSystemNotifier

        try:
            await AgentSystemNotifier.notify_share_withdrawn(
                session,
                agent_id=record.agent_id,
                display_name=record.agent_display_name or record.agent_name or "智能体",
                owner_name=username or "",
                recipient_user_id=record.recipient_user_id,
            )
        except Exception:
            pass

        await AgentShareService._record_event(
            session,
            agent_id=record.agent_id,
            action=ACTION_SHARE_REVOKE,
            actor_name=username,
            actor_id=user_id,
            reason=f"撤回了给 {record.recipient_user_name or '接收人'} 的分享",
        )
        return record

    @staticmethod
    async def menu_counts(session: AsyncSession, user: Any) -> Dict[str, int]:
        """侧边菜单角标：待接受 / 待对方处理 / 我发出的总数。"""
        from sqlalchemy import func

        _, _, user_id = AgentShareService.resolve_actor(user)
        if user_id is None:
            return {"received_pending": 0, "sent_pending": 0, "sent_total": 0}

        # 过期的要先落状态，否则角标会把已经失效的记录也算进去。
        # 上面两个方法已经各自 commit 过，这里不用再处理。
        await AgentShareService.list_received(session, user, only_pending=True)
        await AgentShareService.list_sent_shares(session, user)

        async def _count(*conditions) -> int:
            result = await session.execute(
                select(func.count()).select_from(AgentShareRequest).where(*conditions)
            )
            return int(result.scalar() or 0)

        return {
            "received_pending": await _count(
                AgentShareRequest.recipient_user_id == user_id,
                AgentShareRequest.status == STATUS_PENDING,
            ),
            "sent_pending": await _count(
                AgentShareRequest.owner_user_id == user_id,
                AgentShareRequest.status == STATUS_PENDING,
            ),
            "sent_total": await _count(AgentShareRequest.owner_user_id == user_id),
        }

    # ------------------------------------------------------------------ 作废

    @staticmethod
    async def revoke_pending_for_agent(
        session: AsyncSession, agent_id: str, reason: str
    ) -> int:
        """智能体变成系统级时，把还没被接受的分享全部作废。

        否则接收方仍能凭快照造出一份副本——等于绕开系统级的授权治理。
        """
        rows = await session.execute(
            select(AgentShareRequest).where(
                AgentShareRequest.agent_id == agent_id,
                AgentShareRequest.status == STATUS_PENDING,
            )
        )
        records = list(rows.scalars().all())
        now = datetime.now()
        for record in records:
            record.status = STATUS_REVOKED
            record.responded_at = now
            record.share_reason = reason[:500]
        if records:
            await session.flush()

            # 逐个告诉接收方「这条作废了、该去哪儿申请」。设计里这是唯一的补偿，
            # 不发的话对方只会看到记录凭空消失。通知失败不能反过来挡住作废，
            # 用 SAVEPOINT 隔离：作废是安全约束，通知是尽力而为。
            from app.services.ai.agent_system_notifier import AgentSystemNotifier

            for record in records:
                try:
                    async with session.begin_nested():
                        await AgentSystemNotifier.notify_share_revoked(
                            session,
                            agent_id=agent_id,
                            display_name=record.agent_display_name or record.agent_name or "智能体",
                            recipient_user_id=int(record.recipient_user_id),
                            reason=reason,
                        )
                except Exception as exc:  # pragma: no cover - 通知失败不影响作废
                    logger.warning(f"通知分享作废失败（share={record.id}）：{exc}")

            await AgentShareService._record_event(
                session,
                agent_id=agent_id,
                action=ACTION_SHARE_REVOKE,
                actor_name="system",
                actor_id=None,
                reason=f"{len(records)} 个未接受的分享已作废：{reason}"[:500],
            )
        return len(records)
