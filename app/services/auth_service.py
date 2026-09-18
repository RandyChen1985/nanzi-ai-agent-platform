import hashlib
import logging
import re
import secrets
import time
import httpx
from datetime import datetime
from typing import Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from app.core.redis import get_redis
from app.core.orm import AsyncSessionLocal
from app.core.config import get_settings
from app.models.user import User
from app.utils.encryption import get_api_key_manager
from passlib.context import CryptContext

logger = logging.getLogger(__name__)
settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Bcrypt Monkeypatch for Passlib Compatibility ---
import bcrypt
if not hasattr(bcrypt, "__about__"):
    class BcryptAbout:
        __version__ = getattr(bcrypt, "__version__", "4.0.1")
    bcrypt.__about__ = BcryptAbout()
# ---------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class AuthService:
    @staticmethod
    async def _get_session(db: Optional[AsyncSession] = None):
        """Helper to get a session if not provided"""
        if db:
            return db, False
        session = AsyncSessionLocal()
        return session, True

    @staticmethod
    async def generate_api_key(
        user_name: str,
        role: str = "user",
        real_name: str = None,
        remark: str = None,
        dept_code: str = None,
        org_path: str = None,
        extra_data: str = None,
        user_id: Optional[int] = None,
        db: Optional[AsyncSession] = None
    ) -> str:
        """
        生成 API 密钥 (ORM Version)
        """
        session, is_local = await AuthService._get_session(db)
        try:
            manager = get_api_key_manager()
            api_key, encrypted_key, hashed_key = manager.generate_api_key()
            
            new_user = User(
                user_name=user_name,
                real_name=real_name,
                api_key_encrypted=encrypted_key,
                api_key_hash=hashed_key,
                role=role,
                dept_code=dept_code,
                org_path=org_path,
                extra_data=extra_data,
                remark=remark,
                status=1
            )
            if user_id is not None:
                new_user.id = user_id
            session.add(new_user)
            await session.commit()
            return api_key
        except Exception:
            await session.rollback()
            raise
        finally:
            if is_local:
                await session.close()

    # --- 门户会话令牌 ---
    # 浏览器只持有这类不透明随机令牌，真实 API Key 不再下发到前端。
    # 令牌写入 auth:api_key:{sha256(token)} —— 与真实 API Key 共用键空间，
    # 因此 verify_api_key 无需任何分支即可校验，下游 ctx.api_key 也自动继续可用。
    PORTAL_SESSION_PREFIX = "sess_"
    # 嵌入会话令牌前缀（由 EmbedService 签发）。登记到同一套索引，改用户状态时一并吊销。
    EMBED_SESSION_PREFIX = "emb_ses_"
    # 与 admin_token cookie 的 max_age 一致。滑动续期复用 verify_api_key 中的会话分支，
    # 该分支同样服务于 embed（其 TTL 亦为 86400）；若要调整，需同步确认两者。
    PORTAL_SESSION_TTL_SECONDS = 86400
    PORTAL_SESSION_TYPE = "portal"

    # --- 会话按用户吊销索引 ---
    # 会话缓存键是 auth:api_key:{sha256(token)}，单向哈希无法从 user_id 反查，
    # 所以签发时额外把缓存键登记进 auth:user_sessions:{user_id} 集合。
    # 禁用/删除用户、改角色、重置 Key 时据此成批吊销，否则这些会话会一直有效
    # （verify_api_key 缓存命中不查库，且会话还参与 24h 滑动续期）。
    SESSION_INDEX_PREFIX = "auth:user_sessions:"
    # 索引比单个会话活得久即可：会话滑动续期时会同步续期索引，多删不存在的键无害。
    SESSION_INDEX_TTL_SECONDS = 86400 * 7

    @staticmethod
    def _session_cache_key(token: str) -> str:
        return f"auth:api_key:{get_api_key_manager().hash_api_key(token)}"

    @staticmethod
    def _session_index_key(user_id) -> str:
        return f"{AuthService.SESSION_INDEX_PREFIX}{user_id}"

    @staticmethod
    async def _index_session(redis, user_id, cache_key: str) -> None:
        """把会话缓存键登记到该用户的索引集合（尽力而为，失败不影响签发）。"""
        if redis is None or not user_id:
            return
        try:
            index_key = AuthService._session_index_key(user_id)
            await redis.sadd(index_key, cache_key)
            await redis.expire(index_key, AuthService.SESSION_INDEX_TTL_SECONDS)
        except Exception as exc:
            logger.warning("登记会话索引失败（不影响本次签发）: %s", exc)

    @staticmethod
    def _as_text(value) -> str:
        """统一处理 Redis decode_responses 开关下的 bytes / str。"""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return "" if value is None else str(value)

    @staticmethod
    async def revoke_sessions_for_user(user_id, redis=None) -> int:
        """吊销某用户的全部会话令牌，返回删除的键数。

        只删会话（sess_/emb_ses_ 写入的 auth:api_key 键），不碰真实 API Key 的
        认证缓存——后者由 invalidate_user_auth_cache 单独处理。
        """
        if not user_id:
            return 0
        redis = redis or await get_redis()
        if not redis:
            return 0
        index_key = AuthService._session_index_key(user_id)
        removed = 0
        try:
            cache_keys = await redis.smembers(index_key)
            keys = [
                k.decode("utf-8", errors="replace") if isinstance(k, bytes) else str(k)
                for k in (cache_keys or [])
            ]
            if keys:
                removed = int(await redis.delete(*keys) or 0)
            await redis.delete(index_key)
        except Exception as exc:
            logger.warning("按用户吊销会话失败: %s", exc)
        return removed

    @staticmethod
    def _user_model_to_data(user) -> Dict:
        """User ORM -> 认证缓存 / 会话的统一数据结构（单一来源，避免字段漂移）。"""
        return {
            "user_id": str(user.id),
            "user_name": user.user_name,
            "real_name": user.real_name or user.user_name,
            "role": user.role,
            "dept_code": user.dept_code or "",
            "org_path": user.org_path or "",
            "extra_data": user.extra_data or "",
            "created_at": user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else None,
            "remark": user.remark or "",
            "status": str(user.status),
        }

    @staticmethod
    async def create_portal_session(user_id: int, db: Optional[AsyncSession] = None) -> Optional[str]:
        """按 user_id 签发不透明会话令牌。

        Redis 不可用时返回 None，调用方回退到真实 API Key —— 登录不能被缓存层拖垮。
        """
        redis = await get_redis()
        if not redis:
            logger.warning("Redis 不可用，无法签发会话令牌，本次回退到真实 API Key")
            return None

        session, is_local = await AuthService._get_session(db)
        try:
            result = await session.execute(select(User).where(User.id == int(user_id)))
            user = result.scalar_one_or_none()
            if not user or user.status != 1:
                return None
            user_data = AuthService._user_model_to_data(user)
        finally:
            if is_local:
                await session.close()

        token = f"{AuthService.PORTAL_SESSION_PREFIX}{secrets.token_urlsafe(32)}"
        payload = {
            **user_data,
            "session_type": AuthService.PORTAL_SESSION_TYPE,
            # 签发即视为刚复核过，避免登录后第一次请求就回源查库
            "verified_at": str(int(time.time())),
        }
        cache_key = AuthService._session_cache_key(token)
        try:
            await redis.hset(cache_key, mapping=payload)
            await redis.expire(
                cache_key,
                AuthService.PORTAL_SESSION_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning("写入会话令牌失败，本次回退到真实 API Key: %s", exc)
            return None

        # 登记进用户索引，供禁用/删号/降权/重置 Key 时成批吊销
        await AuthService._index_session(redis, user_data.get("user_id"), cache_key)

        return token

    @staticmethod
    async def revoke_portal_session(token: str) -> bool:
        """吊销单个会话令牌（登出即生效）。

        仅对会话令牌（sess_ 门户 / emb_ses_ 嵌入）生效，避免误删真实 API Key
        的认证缓存。
        """
        if not token or not token.startswith(
            (AuthService.PORTAL_SESSION_PREFIX, AuthService.EMBED_SESSION_PREFIX)
        ):
            return False

        redis = await get_redis()
        if not redis:
            return False

        cache_key = AuthService._session_cache_key(token)
        try:
            # 先取所属用户，便于把索引里的死键一并摘掉
            owner_id = None
            try:
                cached = await redis.hgetall(cache_key)
                if cached:
                    raw_uid = cached.get("user_id")
                    owner_id = (
                        raw_uid.decode("utf-8", errors="replace")
                        if isinstance(raw_uid, bytes)
                        else raw_uid
                    )
            except Exception:
                owner_id = None

            await redis.delete(cache_key)
            if owner_id:
                try:
                    await redis.srem(AuthService._session_index_key(owner_id), cache_key)
                except Exception:
                    pass
            return True
        except Exception as exc:
            logger.warning("吊销会话令牌失败: %s", exc)
            return False

    # --- 登录失败锁定 ---
    # 密码登录是在线暴力破解的目标；按用户名计数，达到阈值后在窗口内拒绝继续尝试。
    LOGIN_FAILURE_LIMIT = 5
    LOGIN_FAILURE_WINDOW_SECONDS = 900  # 15 分钟

    @staticmethod
    def _login_failure_key(username: str) -> str:
        return f"auth:login_fail:{(username or '').strip().lower()}"

    @staticmethod
    async def _login_guard_redis():
        """获取 Redis 连接；不可用时返回 None。

        登录限流必须是 fail-open：Redis 抖动或未配置时如果抛错，
        会把整个平台变成谁都登不进来。
        """
        try:
            return await get_redis()
        except Exception as exc:
            logger.warning("登录失败计数暂不可用，本次跳过限流: %s", exc)
            return None

    @staticmethod
    async def get_login_failure_count(username: str) -> int:
        redis = await AuthService._login_guard_redis()
        if not redis:
            return 0
        try:
            value = await redis.get(AuthService._login_failure_key(username))
        except Exception as exc:
            logger.warning("读取登录失败次数失败: %s", exc)
            return 0
        if value is None:
            return 0
        text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        try:
            return int(text)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    async def is_login_locked(username: str) -> bool:
        """该用户名是否已因失败次数过多被临时锁定。"""
        count = await AuthService.get_login_failure_count(username)
        return count >= AuthService.LOGIN_FAILURE_LIMIT

    @staticmethod
    async def record_login_failure(username: str) -> int:
        """记录一次登录失败，返回累计次数（Redis 不可用时返回 0）。"""
        redis = await AuthService._login_guard_redis()
        if not redis:
            return 0
        key = AuthService._login_failure_key(username)
        try:
            count = int(await redis.incr(key))
            if count == 1:
                await redis.expire(key, AuthService.LOGIN_FAILURE_WINDOW_SECONDS)
            elif hasattr(redis, "ttl"):
                try:
                    ttl = await redis.ttl(key)
                    if ttl == -1:
                        await redis.expire(key, AuthService.LOGIN_FAILURE_WINDOW_SECONDS)
                except Exception:
                    pass
            return count
        except Exception as exc:
            logger.warning("记录登录失败次数失败: %s", exc)
            return 0

    @staticmethod
    async def clear_login_failures(username: str) -> None:
        """登录成功后清零失败计数。"""
        redis = await AuthService._login_guard_redis()
        if not redis:
            return
        try:
            await redis.delete(AuthService._login_failure_key(username))
        except Exception as exc:
            logger.warning("清除登录失败次数失败: %s", exc)

    @staticmethod
    async def verify_api_key(api_key: str, db: Optional[AsyncSession] = None) -> Optional[Dict]:
        """
        校验 API Key (Redis -> ORM)
        """
        manager = get_api_key_manager()
        hashed_key = manager.hash_api_key(api_key)
        cache_key = f"auth:api_key:{hashed_key}"
        
        # 1. Redis Cache
        redis = await get_redis()
        if redis:
            cached_user = await redis.hgetall(cache_key)
            if cached_user:
                 # 增强校验：检查 status 字段
                 # 如果 status 缺失（旧缓存）或者 status不是 "1"，视为无效/需要重新验证
                 if cached_user.get("status") != "1":
                     pass # Fall through to DB
                 else:
                     if cached_user.get("session_type") in ("embed", "portal"):
                         # 会话令牌：低频回源复核 status/role。
                         # 吊销索引已覆盖全部管理路径，这里是兜底——万一有入口直接改了库，
                         # 最迟 SESSION_REVERIFY_SECONDS 后也会失效，而不是拖到 24h。
                         now_ts = int(time.time())
                         last_verified = 0
                         try:
                             last_verified = int(
                                 AuthService._as_text(cached_user.get("verified_at")) or 0
                             )
                         except (TypeError, ValueError):
                             last_verified = 0

                         if now_ts - last_verified > AuthService.SESSION_REVERIFY_SECONDS:
                             refreshed = await AuthService._reverify_session(
                                 redis, cache_key, cached_user, db
                             )
                             if refreshed is None:
                                 return None
                             refreshed["verified_at"] = str(now_ts)
                             cached_user = refreshed
                             try:
                                 await redis.hset(
                                     cache_key,
                                     mapping={
                                         k: AuthService._as_text(v)
                                         for k, v in cached_user.items()
                                         if v is not None
                                     },
                                 )
                             except Exception as exc:
                                 logger.warning("刷新会话缓存失败，本次仍按复核结果放行: %s", exc)

                         # 活跃即滑动续期（会话 TTL 与索引 TTL 同步顺延）
                         try:
                             from app.services.embed_service import SESSION_TOKEN_TTL_SECONDS
                             await redis.expire(cache_key, SESSION_TOKEN_TTL_SECONDS)
                             await redis.expire(
                                 AuthService._session_index_key(cached_user.get("user_id")),
                                 AuthService.SESSION_INDEX_TTL_SECONDS,
                             )
                         except Exception:
                             pass
                     return cached_user

        # 2. DB Query
        session, is_local = await AuthService._get_session(db)
        try:
            result = await session.execute(
                select(User).where(User.api_key_hash == hashed_key)
            )
            user = result.scalar_one_or_none()
            
            user_data = None
            if user and user.status == 1:
                user_data = AuthService._user_model_to_data(user)
            
            # 3. Cache to Redis
            if user_data and redis:
                await redis.hset(cache_key, mapping=user_data)
                await redis.expire(cache_key, 3600)

            return user_data
        finally:
            if is_local:
                await session.close()

    # 会话令牌回源复核间隔（秒）。取值要权衡：越短越能及时反映禁用/降权，
    # 越长则 DB 压力越小。60s 对活跃会话意味着每用户每分钟一次单行查询。
    SESSION_REVERIFY_SECONDS = 60

    @staticmethod
    async def _reverify_session(
        redis,
        cache_key: str,
        cached_user: Dict,
        db: Optional[AsyncSession] = None,
    ) -> Optional[Dict]:
        """按 user_id 回源复核会话用户的 status/role，返回刷新后的会话数据。

        用户已被删除或禁用时，就地吊销该会话并返回 None —— 会话缓存命中路径
        默认不查库，没有这一步，禁用/降权会因为缓存而迟迟不生效。
        """
        user_id_text = AuthService._as_text(cached_user.get("user_id")).strip()
        if not user_id_text.isdigit():
            # 缓存里没有可用 user_id（老数据）时按原样放行，交由吊销索引处理
            return cached_user

        session, is_local = await AuthService._get_session(db)
        try:
            user = await session.get(User, int(user_id_text))
        except Exception as exc:
            # 复核失败不应把在线用户全踢下线（fail-open），沿用缓存
            logger.warning("会话回源复核失败，本次沿用缓存: %s", exc)
            return cached_user
        finally:
            if is_local:
                await session.close()

        if not user or user.status != 1:
            try:
                await redis.delete(cache_key)
                await redis.srem(AuthService._session_index_key(user_id_text), cache_key)
            except Exception as exc:
                logger.warning("吊销失效会话失败: %s", exc)
            logger.info("会话所属用户已被删除或禁用，已吊销其会话: user_id=%s", user_id_text)
            return None

        # 用库里的最新身份字段覆盖缓存，保留会话自有字段
        refreshed = AuthService._user_model_to_data(user)
        for key in ("session_type", "agent_id", "created_by_user_id"):
            value = cached_user.get(key)
            if value is not None:
                refreshed[key] = AuthService._as_text(value)
        return refreshed

    @staticmethod
    async def resolve_user_by_username(username: str, db: AsyncSession) -> Optional[Dict]:
        """
        按登录名解析启用用户，返回与 verify_api_key 一致的业务字段（不含 api_key）。
        供无需 API Key 的受控接口（如 ChatBI sql/checkauth）使用。
        """
        name = (username or "").strip()
        if not name:
            return None
        result = await db.execute(select(User).where(User.user_name == name))
        user = result.scalar_one_or_none()
        if not user or user.status != 1:
            return None
        return {
            "user_id": str(user.id),
            "user_name": user.user_name,
            "real_name": user.real_name or user.user_name,
            "role": user.role,
            "dept_code": user.dept_code or "",
            "org_path": user.org_path or "",
            "extra_data": user.extra_data or "",
            "created_at": user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else None,
            "remark": user.remark or "",
        }

    @staticmethod
    async def reset_api_key(user_id: int, db: Optional[AsyncSession] = None) -> Optional[str]:
        """
        重置 API Key
        """
        session, is_local = await AuthService._get_session(db)
        try:
            manager = get_api_key_manager()
            api_key, encrypted_key, hashed_key = manager.generate_api_key()
            
            # Fetch user first to get old hash for cache clearing
            user = await session.get(User, user_id)
            if not user:
                return None
            
            old_hash = user.api_key_hash
            
            # Update
            user.api_key_encrypted = encrypted_key
            user.api_key_hash = hashed_key
            await session.commit()
            
            # Clear Cache
            if old_hash:
                cache_key = f"auth:api_key:{old_hash}"
                redis = await get_redis()
                if redis:
                    await redis.delete(cache_key)
            
            return api_key
        except Exception:
            await session.rollback()
            raise
        finally:
            if is_local:
                await session.close()
    
    @staticmethod
    async def get_decrypted_api_key(user_id: int, db: Optional[AsyncSession] = None) -> Optional[str]:
        """
        获取解密 API Key
        """
        session, is_local = await AuthService._get_session(db)
        try:
            user = await session.get(User, user_id)
            if not user or not user.api_key_encrypted:
                return None
            
            manager = get_api_key_manager()
            return manager.decrypt_api_key(user.api_key_encrypted)
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def register_online_state(api_key: str, user_data: Dict):
        """
        主动注册在线状态到 Redis
        """
        manager = get_api_key_manager()
        hashed_key = manager.hash_api_key(api_key)
        cache_key = f"auth:api_key:{hashed_key}"
        
        redis = await get_redis()
        if redis:
            clean_data = {}
            for k, v in user_data.items():
                if v is None:
                    continue
                elif isinstance(v, bool):
                    clean_data[k] = "1" if v else "0"
                elif isinstance(v, (str, int, float, bytes)):
                    clean_data[k] = v
                else:
                    clean_data[k] = str(v)

            # 确保 status 字段存在
            if "status" not in clean_data:
                clean_data["status"] = "1"
            
            await redis.hset(cache_key, mapping=clean_data)
            await redis.expire(cache_key, 3600)
    
    @staticmethod
    async def verify_admin_login(api_key: str, db: Optional[AsyncSession] = None) -> Optional[Dict]:
        """校验管理员登录"""
        user = await AuthService.verify_api_key(api_key, db)
        if not user or user.get("role") != "admin":
            return None
        return user

    @staticmethod
    async def expire_api_key(api_key: str):
        """退出登录 (仅清缓存)"""
        manager = get_api_key_manager()
        hashed_key = manager.hash_api_key(api_key)
        cache_key = f"auth:api_key:{hashed_key}"
        redis = await get_redis()
        if redis:
            await redis.delete(cache_key)

    @staticmethod
    async def invalidate_user_auth_cache(
        user_id: int,
        db: Optional[AsyncSession] = None,
        api_key_hash: Optional[str] = None,
    ) -> None:
        """
        按用户清除认证缓存（真实 API Key 的缓存 + 该用户的全部会话令牌）。

        系统角色 / 启用状态变更后必须调用，否则 require_permission 会在最长 1 小时内
        继续读到旧的 role/status。

        会话令牌（sess_ / emb_ses_）的缓存键是「令牌哈希」而非「用户 api_key_hash」，
        只删后者不会让已有会话失效——必须再按 user_id 索引成批吊销。这一步在
        禁用 / 删除用户 / 重置 Key / 改角色时都不可或缺，否则旧会话既不会失效，
        还会随活跃调用持续滑动续期。
        """
        hashed = (api_key_hash or "").strip()
        if not hashed:
            session, is_local = await AuthService._get_session(db)
            try:
                user = await session.get(User, user_id)
                hashed = (user.api_key_hash or "").strip() if user else ""
            finally:
                if is_local:
                    await session.close()

        redis = await get_redis()
        if not redis:
            return

        if hashed:
            try:
                await redis.delete(f"auth:api_key:{hashed}")
            except Exception as e:
                logger.warning(
                    "Failed to invalidate auth api_key cache for user %s: %s",
                    user_id,
                    e,
                )

        # 与 hashed 是否存在无关：删除用户时可能拿不到旧 hash，但会话仍须吊销
        await AuthService.revoke_sessions_for_user(user_id, redis)

    @staticmethod
    def verify_password_hash(plain_password: str, hashed_password: str) -> bool:
        if len(plain_password.encode('utf-8')) > 72:
            plain_password = plain_password.encode('utf-8')[:72].decode('utf-8', errors='ignore')
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    def get_password_hash(password: str) -> str:
        if len(password.encode('utf-8')) > 72:
            password = password.encode('utf-8')[:72].decode('utf-8', errors='ignore')
        return pwd_context.hash(password)

    @staticmethod
    async def verify_user_password(username: str, password: str, db: Optional[AsyncSession] = None) -> dict:
        """校验用户名密码"""
        session, is_local = await AuthService._get_session(db)
        try:
            result = await session.execute(select(User).where(User.user_name == username))
            user = result.scalar_one_or_none()
            
            if not user:
                return {"status": "fail", "message": "用户名或密码错误"}
            
            if user.status != 1:
                 return {"status": "fail", "message": "账户已被禁用"}

            if not user.password_hash:
                return {"status": "error_no_password", "message": "尚未设置密码，请先使用 API Key 登录并设置密码"}
            
            if AuthService.verify_password_hash(password, user.password_hash):
                return {
                    "status": "success",
                    "user": {
                         "user_id": str(user.id),
                         "user_name": user.user_name,
                         "real_name": user.real_name or user.user_name,
                         "role": user.role,
                         "dept_code": user.dept_code or "",
                         "org_path": user.org_path or "",
                         "extra_data": user.extra_data or "",
                         "created_at": user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else None,
                         "remark": user.remark or "",
                         "two_factor_enabled": bool(user.two_factor_enabled)
                    }
                }
            else:
                return {"status": "fail", "message": "用户名或密码错误"}
        finally:
            if is_local:
                await session.close()

    @staticmethod
    def validate_password_complexity(password: str, username: Optional[str] = None) -> tuple[bool, str]:
        """
        验证密码是否符合等保二级/三级复杂度要求：
        1. 长度为 8 到 32 个字符
        2. 不能包含空格或不可见空白字符
        3. 必须至少包含以下 4 种字符类别中的 3 种：
           - 大写英文字母 (A-Z)
           - 小写英文字母 (a-z)
           - 数字 (0-9)
           - 特殊符号 (~!@#$%^&*()_+-=[]{}|;:,.<>?/ 等)
        4. 若提供 username 且长度 >= 3，密码不能包含用户名（不区分大小写）
        """
        if not password:
            return False, "密码不能为空"

        if len(password) < 8 or len(password) > 32:
            return False, "密码长度必须为 8 到 32 个字符"

        # Bcrypt 编码防溢出（最大 72 字节）
        if len(password.encode("utf-8")) > 72:
            return False, "密码字节长度超过系统上限（最大 72 字节）"

        if any(c.isspace() for c in password):
            return False, "密码不能包含空格或空白字符"

        has_upper = bool(re.search(r"[A-Z]", password))
        has_lower = bool(re.search(r"[a-z]", password))
        has_digit = bool(re.search(r"[0-9]", password))
        has_special = bool(re.search(r"[^A-Za-z0-9]", password))

        categories_count = sum([has_upper, has_lower, has_digit, has_special])
        if categories_count < 3:
            return False, "密码复杂度不符合等保要求：必须至少包含大写字母、小写字母、数字、特殊符号中的 3 种"

        if username and len(username.strip()) >= 3:
            clean_username = username.strip().lower()
            if clean_username in password.lower():
                return False, "密码不能包含用户名"

        return True, "密码符合等保复杂度要求"

    @staticmethod
    async def set_user_password(user_id: int, password: str, db: Optional[AsyncSession] = None) -> bool:
        """设置用户密码"""
        session, is_local = await AuthService._get_session(db)
        try:
            hashed = AuthService.get_password_hash(password)
            stmt = update(User).where(User.id == user_id).values(
                password_hash=hashed,
                password_updated_at=datetime.now()
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0
        except Exception:
            await session.rollback()
            raise
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def authenticate_sso_user(username: str, password: str, db: Optional[AsyncSession] = None) -> dict:
        """
        SSO 认证逻辑
        1. 调用远程 SSO 接口
        2. 认证通过后，查询本地数据库映射权限
        """
        # 1. 调用 SSO 接口
        api_request = {
            'requestSystem': settings.SSO_REQUEST_SYSTEM,
            'requestBusiness': settings.SSO_REQUEST_BUSINESS,
            'operationType': 'LOGIN',
            'userName': username,
            'password': password
        }
        
        headers = {
            'YOVOLE-LAPLACE-API-ACCESS-TOKEN': settings.SSO_ACCESS_TOKEN,
            'Content-Type': 'application/json;charset=UTF-8'
        }

        try:
            async with httpx.AsyncClient(timeout=settings.SSO_TIMEOUT, verify=False) as client:
                response = await client.post(settings.SSO_API_URL, headers=headers, json=api_request)
                if response.status_code != 200:
                    return {"status": "fail", "message": f"SSO 服务响应异常: {response.status_code}"}
                
                resp_data = response.json()
                if not resp_data.get('data'):
                    return {"status": "fail", "message": "SSO 认证失败: 用户名或密码错误"}
        except httpx.RequestError as e:
            return {"status": "fail", "message": f"连接 SSO 服务失败: {str(e)}"}
        except Exception as e:
            return {"status": "fail", "message": f"SSO 认证过程发生错误: {str(e)}"}

        # 2. 认证通过后，查询本地数据库映射权限
        session, is_local = await AuthService._get_session(db)
        try:
            result = await session.execute(select(User).where(User.user_name == username))
            user = result.scalar_one_or_none()
            
            if not user:
                return {"status": "error_not_found", "message": "请联系管理员开通系统权限"}
            
            if user.status != 1:
                 return {"status": "error_disabled", "message": "账户已被禁用"}

            return {
                "status": "success",
                "user": {
                     "user_id": str(user.id),
                     "user_name": user.user_name,
                     "real_name": user.real_name or user.user_name,
                     "role": user.role,
                     "dept_code": user.dept_code or "",
                     "org_path": user.org_path or "",
                     "extra_data": user.extra_data or "",
                     "created_at": user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else None,
                     "remark": user.remark or "",
                     "two_factor_enabled": bool(user.two_factor_enabled)
                }
            }
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def get_user_2fa_status(user_id: int, db: Optional[AsyncSession] = None) -> bool:
        """获取用户 2FA 启用状态"""
        session, is_local = await AuthService._get_session(db)
        try:
            user = await session.get(User, user_id)
            return bool(user.two_factor_enabled) if user else False
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def get_user_2fa_secret(user_id: int, db: Optional[AsyncSession] = None) -> Optional[str]:
        """获取用户解密后的 2FA TOTP 密钥"""
        session, is_local = await AuthService._get_session(db)
        try:
            user = await session.get(User, user_id)
            if not user or not user.two_factor_secret:
                return None
            try:
                manager = get_api_key_manager()
                return manager.decrypt_api_key(user.two_factor_secret)
            except Exception:
                # 兼容未加密保存的 Base32 明文
                return user.two_factor_secret
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def enable_user_2fa(user_id: int, secret: str, db: Optional[AsyncSession] = None) -> bool:
        """正式启用 2FA 并加密存储密钥"""
        session, is_local = await AuthService._get_session(db)
        try:
            manager = get_api_key_manager()
            encrypted_secret = manager.encrypt_api_key(secret)
            stmt = (
                update(User)
                .where(User.id == user_id)
                .values(two_factor_enabled=True, two_factor_secret=encrypted_secret)
            )
            await session.execute(stmt)
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def disable_user_2fa(user_id: int, db: Optional[AsyncSession] = None) -> bool:
        """关闭 2FA 并清除密钥"""
        session, is_local = await AuthService._get_session(db)
        try:
            stmt = (
                update(User)
                .where(User.id == user_id)
                .values(two_factor_enabled=False, two_factor_secret=None)
            )
            await session.execute(stmt)
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def create_2fa_setup_cache(user_id: int, secret: str, ttl: int = 600) -> None:
        """在 Redis 中暂存待激活绑定的 2FA 密钥（10分钟）"""
        redis = await get_redis()
        if redis:
            await redis.setex(f"auth:2fa_setup:{user_id}", ttl, secret)

    @staticmethod
    async def get_2fa_setup_cache(user_id: int) -> Optional[str]:
        """获取待绑定的 2FA 密钥"""
        redis = await get_redis()
        if not redis:
            return None
        val = await redis.get(f"auth:2fa_setup:{user_id}")
        return val.decode("utf-8") if isinstance(val, bytes) else val

    @staticmethod
    async def clear_2fa_setup_cache(user_id: int) -> None:
        """清理待绑定的 2FA 密钥"""
        redis = await get_redis()
        if redis:
            await redis.delete(f"auth:2fa_setup:{user_id}")

    @staticmethod
    async def create_2fa_pending_token(user_id: int, ttl: int = 300) -> str:
        """创建两阶段登录中待二次验证的临时票据（5分钟）"""
        token = secrets.token_urlsafe(32)
        redis = await get_redis()
        if redis:
            key = f"auth:2fa_pending:{token}"
            await redis.hset(key, mapping={"user_id": str(user_id), "attempts": "0"})
            await redis.expire(key, ttl)
        return token

    @staticmethod
    async def verify_and_consume_2fa_pending_token(token: str, code: str, db: Optional[AsyncSession] = None) -> Optional[Dict]:
        """
        验证 2FA 临时票据和动态验证码，成功则返回完整的 user 字典并销毁 pending 状态
        """
        from app.services.totp_service import TotpService
        redis = await get_redis()
        if not redis:
            return None
        
        key = f"auth:2fa_pending:{token}"
        data = await redis.hgetall(key)
        if not data:
            return None
        
        # Redis 获取到的字段可能是 bytes 或 str
        user_id_val = data.get(b"user_id") or data.get("user_id")
        attempts_val = data.get(b"attempts") or data.get("attempts") or "0"
        if not user_id_val:
            return None
            
        user_id_str = user_id_val.decode("utf-8") if isinstance(user_id_val, bytes) else str(user_id_val)
        attempts = int(attempts_val.decode("utf-8") if isinstance(attempts_val, bytes) else attempts_val)
        
        if attempts >= 5:
            await redis.delete(key)
            return None

        user_id = int(user_id_str)
        secret = await AuthService.get_user_2fa_secret(user_id, db=db)
        if not secret:
            await redis.delete(key)
            return None
        
        if not TotpService.verify_code(secret, code):
            # 失败次数累加
            await redis.hset(key, "attempts", str(attempts + 1))
            return None
        
        # 验证成功，删除票据
        await redis.delete(key)

        # 获取当前用户数据
        session, is_local = await AuthService._get_session(db)
        try:
            user = await session.get(User, user_id)
            if not user or user.status != 1:
                return None
            return {
                "user_id": str(user.id),
                "user_name": user.user_name,
                "real_name": user.real_name or user.user_name,
                "role": user.role,
                "dept_code": user.dept_code or "",
                "org_path": user.org_path or "",
                "extra_data": user.extra_data or "",
                "created_at": user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else None,
                "remark": user.remark or "",
                "two_factor_enabled": bool(user.two_factor_enabled)
            }
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def record_user_login(user_id: int, db: Optional[AsyncSession] = None) -> None:
        """
        记录用户登录时间
        """
        session, is_local = await AuthService._get_session(db)
        try:
            user = await session.get(User, user_id)
            if user:
                user.last_login_at = datetime.now()
                await session.commit()
        except Exception as e:
            logger.warning(f"Failed to record last_login_at for user {user_id}: {e}")
            try:
                await session.rollback()
            except Exception:
                pass
        finally:
            if is_local:
                await session.close()