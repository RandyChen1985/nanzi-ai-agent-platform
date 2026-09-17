import json
import logging
import secrets
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.redis import get_redis
from app.models.user import User
from app.utils.encryption import get_api_key_manager

logger = logging.getLogger(__name__)

TICKET_TTL_SECONDS = 300  # Ticket 一次性有效时长：5 分钟
SESSION_TOKEN_TTL_SECONDS = 86400  # Session Token 初始时长：24 小时 (滑动续期)


class EmbedService:
    """
    负责嵌入式组件 (EmbedChat) 的安全凭据生命周期管理：
    1. 签发短期一次性 Ticket (由宿主后端在内网发起，长期 API Key 不出内网)
    2. 兑换受限短期 Session Token (由前端 iframe 发起，一次性核销 Ticket)
    """

    @staticmethod
    async def create_ticket(
        operator_user: Dict[str, Any],
        target_username: Optional[str] = None,
        target_user_id: Optional[int] = None,
        agent_id: Optional[str] = None,
        allowed_origins: Optional[list[str]] = None,
        expires_in: int = TICKET_TTL_SECONDS,
        db: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        """
        为指定目标用户签发一次性短时 Ticket。
        - 若未提供 target_username/target_user_id，默认代表当前调用者自己。
        - 目标用户必须存在且为启用状态 (status == 1)。
        """
        redis = await get_redis()
        if not redis:
            raise RuntimeError("Redis service unavailable")

        # 1. 确定目标用户
        target_user: Optional[User] = None
        is_specifying_other = False

        if target_user_id is not None:
            if str(target_user_id) != str(operator_user.get("user_id")):
                is_specifying_other = True
            stmt = select(User).where(User.id == int(target_user_id))
            result = await db.execute(stmt)
            target_user = result.scalar_one_or_none()
        elif target_username:
            if str(target_username).strip() != str(operator_user.get("user_name")):
                is_specifying_other = True
            stmt = select(User).where(User.user_name == str(target_username).strip())
            result = await db.execute(stmt)
            target_user = result.scalar_one_or_none()
        else:
            # 默认使用当前操作人自身
            op_uid = int(operator_user.get("user_id", 0))
            stmt = select(User).where(User.id == op_uid)
            result = await db.execute(stmt)
            target_user = result.scalar_one_or_none()

        if not target_user:
            raise ValueError("目标用户不存在，请核对用户名或用户ID")

        if target_user.status != 1:
            raise PermissionError("目标用户账号已被禁用，无法签发嵌入凭证")

        # 代客身份安全拦截：若指定他人，必须是 admin 或具备「代他人签发嵌入凭证」权限
        # （API 权限码 POST:/api/v1/embed/tickets，可在后台「API 权限」中按角色分配）。
        # 该权限码的实际语义是「可代表他人调用签发接口」：/embed/* 在 V1 接口白名单内
        # 不做拦截，自己为自己签发也不需要本权限。
        # 历史上此处借用的是 GET:/api/v1/users/profile API 权限，已改为专用权限码——
        # 该 API 权限回归「获取用户信息」本义，不再兼作签发凭证。
        if is_specifying_other:
            if operator_user.get("role") != "admin":
                from app.services.permission_service import PermissionService
                perm_service = PermissionService(db)
                op_uid = int(operator_user.get("user_id", 0))
                has_perm = await perm_service.check_permission(
                    op_uid,
                    "api",
                    "POST:/api/v1/embed/tickets",
                )
                if not has_perm:
                    raise PermissionError(
                        "无权代他人签发 Ticket：需要「代他人签发嵌入凭证」权限"
                        "（POST:/api/v1/embed/tickets），或由管理员操作。"
                        "普通用户请留空或填写自己。"
                    )

        # 2. 生成高熵 Ticket 字符串
        ticket_id = f"emt_{secrets.token_urlsafe(24)}"
        ticket_key = f"embed:ticket:{ticket_id}"

        ttl = max(60, min(expires_in, 1800))  # 限制在 1 分钟 ~ 30 分钟之间，默认 300 秒

        ticket_payload = {
            "ticket": ticket_id,
            "user_id": str(target_user.id),
            "user_name": target_user.user_name,
            "real_name": target_user.real_name or target_user.user_name,
            "role": target_user.role,
            "dept_code": target_user.dept_code or "",
            "org_path": target_user.org_path or "",
            "extra_data": target_user.extra_data or "",
            "agent_id": agent_id or "",
            "allowed_origins": json.dumps(allowed_origins or []),
            "created_by_user_id": str(operator_user.get("user_id", "")),
        }

        # 3. 写入 Redis
        await redis.set(ticket_key, json.dumps(ticket_payload), ex=ttl)
        logger.info(
            "Embed ticket created: ticket=%s target_user=%s operator=%s ttl=%ds",
            ticket_id,
            target_user.user_name,
            operator_user.get("user_name"),
            ttl,
        )

        return {
            "ticket": ticket_id,
            "expires_in": ttl,
            "target_user": {
                "user_id": target_user.id,
                "user_name": target_user.user_name,
                "real_name": target_user.real_name or target_user.user_name,
            },
        }

    @staticmethod
    async def exchange_ticket(
        ticket: str,
        origin: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        原子核销 Ticket 并生成短期会话 Token (session_token)。
        - 只能成功核销一次 (One-Time Use)；
        - 核销后立即从 Redis 移除 Ticket，杜绝重放攻击；
        - 生成的 session_token 写入 auth 鉴权缓存，天然与现存所有 API Key 鉴权体系无缝兼容。
        """
        if not ticket or not isinstance(ticket, str) or not ticket.startswith("emt_"):
            raise ValueError("Invalid ticket format")

        redis = await get_redis()
        if not redis:
            raise RuntimeError("Redis service unavailable")

        ticket_key = f"embed:ticket:{ticket.strip()}"

        # 1. 原子获取并删除 (GETDEL) 防止并发重放
        raw_ticket_data = await redis.getdel(ticket_key)
        if not raw_ticket_data:
            raise ValueError("Ticket not found, expired, or already used")

        if isinstance(raw_ticket_data, bytes):
            raw_ticket_data = raw_ticket_data.decode("utf-8")

        ticket_data = json.loads(raw_ticket_data)

        # 2. 检查来源 Origin (若有配置限制)
        allowed_origins_raw = ticket_data.get("allowed_origins")
        if allowed_origins_raw:
            try:
                allowed_origins = json.loads(allowed_origins_raw)
                if allowed_origins and origin:
                    if origin not in allowed_origins and "*" not in allowed_origins:
                        raise PermissionError(f"Origin '{origin}' is not allowed for this ticket")
            except json.JSONDecodeError:
                pass

        # 3. 生成短期 Session Token
        session_token = f"emb_ses_{secrets.token_urlsafe(32)}"
        manager = get_api_key_manager()
        hashed_token = manager.hash_api_key(session_token)
        cache_key = f"auth:api_key:{hashed_token}"

        user_session_data = {
            "user_id": str(ticket_data["user_id"]),
            "user_name": ticket_data["user_name"],
            "real_name": ticket_data.get("real_name") or ticket_data["user_name"],
            "role": ticket_data.get("role", "user"),
            "dept_code": ticket_data.get("dept_code", ""),
            "org_path": ticket_data.get("org_path", ""),
            "extra_data": ticket_data.get("extra_data", ""),
            "remark": "Embed Session",
            "status": "1",
            "session_type": "embed",
            "agent_id": ticket_data.get("agent_id", ""),
            "created_by_user_id": ticket_data.get("created_by_user_id", ""),
        }

        # 4. 写入鉴权缓存，设置 24 小时 TTL (请求时会自动滑动续期)
        await redis.hset(cache_key, mapping=user_session_data)
        await redis.expire(cache_key, SESSION_TOKEN_TTL_SECONDS)

        logger.info(
            "Embed ticket exchanged successfully: ticket=%s user=%s session_token_prefix=%s ttl=%ds",
            ticket,
            ticket_data["user_name"],
            session_token[:12],
            SESSION_TOKEN_TTL_SECONDS,
        )

        return {
            "session_token": session_token,
            "expires_in": SESSION_TOKEN_TTL_SECONDS,
            "user_info": {
                "user_id": int(ticket_data["user_id"]),
                "user_name": ticket_data["user_name"],
                "real_name": ticket_data.get("real_name") or ticket_data["user_name"],
                "role": ticket_data.get("role", "user"),
            },
            "agent_id": ticket_data.get("agent_id") or None,
        }

    @staticmethod
    async def issue_session_from_user(
        user: Dict[str, Any],
        agent_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """由**已完成鉴权**的用户信息签发 embed 会话令牌。

        用于「真实 API Key 校验通过后换发短期令牌」的路径：调用方拿到 session_token 后
        即可丢弃原凭据，避免长期 Key 在客户端或内存中长期驻留。

        令牌写入既有的 auth 鉴权缓存，因此 `verify_api_key` 无需任何前缀分支即可校验；
        `session_type="embed"` 会让它参与滑动续期（TTL 24 小时）。

        Redis 不可用时返回 None（Fail-Open）：调用方据此回退为继续使用原凭据，
        鉴权本身不受影响，不会因为缓存故障阻断嵌入场景。
        """
        redis = await get_redis()
        if not redis:
            logger.warning("[Embed] Redis unavailable, skip session token issuance")
            return None

        session_token = f"emb_ses_{secrets.token_urlsafe(32)}"
        manager = get_api_key_manager()
        hashed_token = manager.hash_api_key(session_token)
        cache_key = f"auth:api_key:{hashed_token}"

        # 只搬运鉴权必需字段；刻意不透传 user["api_key"]，避免真实凭据被写进会话缓存
        user_session_data = {
            "user_id": str(user.get("user_id", "")),
            "user_name": user.get("user_name", ""),
            "real_name": user.get("real_name") or user.get("user_name", ""),
            "role": user.get("role", "user"),
            "dept_code": user.get("dept_code") or "",
            "org_path": user.get("org_path") or "",
            "extra_data": user.get("extra_data") or "",
            "remark": "Embed Session",
            "status": "1",
            "session_type": "embed",
            "agent_id": agent_id or "",
            "created_by_user_id": str(user.get("user_id", "")),
        }

        await redis.hset(cache_key, mapping=user_session_data)
        await redis.expire(cache_key, SESSION_TOKEN_TTL_SECONDS)

        logger.info(
            "Embed session issued from authenticated user: user=%s session_token_prefix=%s ttl=%ds",
            user.get("user_name"),
            session_token[:12],
            SESSION_TOKEN_TTL_SECONDS,
        )

        return {
            "session_token": session_token,
            "expires_in": SESSION_TOKEN_TTL_SECONDS,
        }
