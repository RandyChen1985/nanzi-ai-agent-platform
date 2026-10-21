"""会话级项目资源范围：仅保存用户主动挂载的资源。"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Iterable

from app.core.redis import get_redis
from app.services.ai.conversation_identity import require_user_id

logger = logging.getLogger(__name__)

#: 单次重写/清理最多同步的会话数：管理侧的重命名/删除是低频操作，不该做成全库扫描。
#: 超出后停止并告警，剩下的会话由用户重新挂载技能即可（引用清理永远优于 500）。
MAX_MOUNT_REWRITE = 2000

#: 会话资源范围的 Redis 存活时间。集中在这里是**为了不再被漏写**：技能重命名当初另写了
#: 一处 ``redis.set`` 没带 TTL，把 30 天的键改成了永不过期。
TTL_SECONDS = 2592000


class MountSyncUnavailable(RuntimeError):
    """Redis 不可用，无法同步会话挂载。

    ``strict_unavailable=True`` 时抛出，让调用方（删除/下架）能把「这一处没同步」写进
    warnings；重命名链路历史上是静默降级（返回 0），因此默认不抛，避免改变既有语义。
    """


class ConversationResourceService:
    KEY_PREFIX = "conversation"
    SUFFIX = "resource_scope_v1"
    # 保留类属性形态：外部（重命名/删除链路）按类取用同一个常量
    TTL_SECONDS = TTL_SECONDS
    MAX_MOUNT_REWRITE = MAX_MOUNT_REWRITE
    _LIST_KEYS = ("datasets", "knowledge_bases", "skills", "mcp_tools")

    @classmethod
    def _key(cls, user_id: Any, conversation_id: str) -> str:
        return f"{cls.KEY_PREFIX}:{require_user_id(user_id)}:{conversation_id}:{cls.SUFFIX}"

    @classmethod
    def _empty_scope(cls) -> Dict[str, Any]:
        return {
            "project_name": "",
            "datasets": [],
            "knowledge_bases": [],
            "skills": [],
            "mcp_tools": [],
        }

    @classmethod
    def _decode_scope(cls, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        result: Dict[str, Any] = {"project_name": str(value.get("project_name") or "").strip()}
        result.update({
            key: [item for item in value.get(key, []) if isinstance(item, dict)]
            for key in cls._LIST_KEYS
        })
        return result

    @classmethod
    async def get(cls, user_id: Any, conversation_id: str) -> Dict[str, Any]:
        try:
            redis = await get_redis()
        except Exception:
            redis = None
        if not redis:
            return cls._empty_scope()
        try:
            raw = await redis.get(cls._key(user_id, conversation_id))
        except Exception:
            return cls._empty_scope()
        return cls._decode_scope(raw)

    @classmethod
    async def get_many(cls, user_id: Any, conversation_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """批量读取会话范围，历史列表不要为每条记录单独访问 Redis。"""
        ids = [str(item) for item in conversation_ids if str(item or "").strip()]
        if not ids:
            return {}
        try:
            redis = await get_redis()
        except Exception:
            redis = None
        if not redis:
            return {cid: cls._empty_scope() for cid in ids}
        keys = [cls._key(user_id, cid) for cid in ids]
        try:
            if hasattr(redis, "mget"):
                raw_values = await redis.mget(keys)
            else:
                raw_values = [await redis.get(key) for key in keys]
        except Exception:
            return {cid: cls._empty_scope() for cid in ids}
        return {
            cid: cls._decode_scope(raw)
            for cid, raw in zip(ids, raw_values)
        }

    @classmethod
    async def get_many_for_owners(
        cls,
        owner_conversations: Iterable[tuple[Any, str]],
    ) -> Dict[tuple[str, str], Dict[str, Any]]:
        """按归属用户批量读取范围，供管理员历史列表使用。"""
        pairs = [(str(owner), str(cid)) for owner, cid in owner_conversations if str(cid or "").strip()]
        if not pairs:
            return {}
        try:
            redis = await get_redis()
        except Exception:
            redis = None
        if not redis:
            return {(owner, cid): cls._empty_scope() for owner, cid in pairs}
        keys = [cls._key(owner, cid) for owner, cid in pairs]
        try:
            raw_values = await redis.mget(keys) if hasattr(redis, "mget") else [await redis.get(key) for key in keys]
        except Exception:
            return {(owner, cid): cls._empty_scope() for owner, cid in pairs}
        return {
            pair: cls._decode_scope(raw)
            for pair, raw in zip(pairs, raw_values)
        }

    @classmethod
    async def replace(cls, user_id: Any, conversation_id: str, scope: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        normalized: Dict[str, Any] = {"project_name": str(scope.get("project_name") or "").strip()[:100]}
        normalized.update({
            key: [item for item in scope.get(key, []) if isinstance(item, dict) and item.get("id")]
            for key in cls._LIST_KEYS
        })
        try:
            redis = await get_redis()
            if redis:
                await redis.set(
                    cls._key(user_id, conversation_id),
                    json.dumps(normalized, ensure_ascii=False),
                    ex=cls.TTL_SECONDS,
                )
        except Exception:
            pass
        return normalized

    @staticmethod
    def _mount_scope_matches(item: Dict[str, Any], scope: str | None) -> bool:
        """挂载项是否属于指定 scope。

        ``scope=None`` 表示不区分（重命名个人技能时历史上就是这样）。历史数据可能没有
        ``scope`` 字段，此时按「可以匹配」处理——宁可多同步一处，也不要漏掉真正的引用。
        """
        if not scope:
            return True
        value = str(item.get("scope") or "").strip().lower()
        return value in ("", scope)

    @classmethod
    async def rewrite_skill_mounts(
        cls,
        *,
        skill_id: str,
        new_skill_id: str | None = None,
        scope: str | None = None,
        max_sessions: int = MAX_MOUNT_REWRITE,
        strict_unavailable: bool = False,
        owner_id: Any | None = None,
    ) -> int:
        """批量改写（``new_skill_id`` 非空）或移除（``None``）各会话挂载的技能，返回改动数。

        技能重命名与删除共用这一份实现，**写入一定带 ``ex=TTL_SECONDS``**。此前重命名自己
        写了一份 ``redis.set`` 不带 ``ex``，把会话范围的 30 天有效期抹成永久键：既不符合
        产品语义，也让 Redis 里堆着一批永不回收的会话键。

        尽力而为：单个 key 不是合法 JSON、写入中途失败都只记日志，绝不向上抛——调用方的
        主操作（目录已改名/已删除）早已完成。唯一例外是 ``strict_unavailable=True`` 时
        Redis 整体不可用会抛 :class:`MountSyncUnavailable`，让删除/下架链路能把「这一处
        没同步」写进 warnings，而不是混在「改了 0 个会话」里静默消失。

        ``owner_id`` 把扫描范围收窄到**该用户自己的会话键**（``conversation:<user_id>:*``）。
        个人技能的重命名/删除**必须**带它：技能 id 就是目录名，用户 A 完全可以建一个 id
        恰好等于某个公共技能的个人技能（平台允许同名），而历史挂载项没有 ``scope`` 字段时
        :meth:`_mount_scope_matches` 会判为"匹配"，于是 A 的操作会删改 **B 会话里**的挂载项
        （跨用户写会话资源）。平台技能（``scope="global"``）的重命名/下架则**必须**扫全库：
        那是平台内容，所有用户的挂载都要跟着改。
        """
        old_id = str(skill_id or "").strip()
        if not old_id:
            return 0
        replacement = None if new_skill_id is None else str(new_skill_id).strip()
        if new_skill_id is not None and not replacement:
            return 0

        try:
            redis = await get_redis()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ConversationResource] Redis 不可用，跳过技能挂载同步: %s", exc)
            if strict_unavailable:
                raise MountSyncUnavailable(str(exc)) from exc
            return 0
        if not redis:
            if strict_unavailable:
                raise MountSyncUnavailable("Redis 客户端不可用")
            return 0

        if owner_id is not None:
            # user_id 段必须与 _key() 完全同构，否则会漏扫或扫到别人
            try:
                owner_segment = require_user_id(owner_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[ConversationResource] 无法确定挂载同步的用户边界，跳过以免误改他人会话: %s",
                    exc,
                )
                return 0
            pattern = f"{cls.KEY_PREFIX}:{owner_segment}:*:{cls.SUFFIX}"
        else:
            pattern = f"{cls.KEY_PREFIX}:*:*:{cls.SUFFIX}"
        updated = 0
        cursor = 0
        try:
            while True:
                cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=200)
                for key in keys:
                    raw = await redis.get(key)
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        value = json.loads(raw or "{}")
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(value, dict):
                        continue
                    skills = value.get("skills")
                    if not isinstance(skills, list):
                        continue
                    rewritten: List[Dict[str, Any]] = []
                    changed = False
                    for item in skills:
                        if (
                            isinstance(item, dict)
                            and str(item.get("id") or "") == old_id
                            and cls._mount_scope_matches(item, scope)
                        ):
                            changed = True
                            if replacement is not None:
                                # 复制一份再改：就地改 item 会连带改到别的引用
                                updated_item = dict(item)
                                updated_item["id"] = replacement
                                rewritten.append(updated_item)
                            continue
                        rewritten.append(item)
                    if not changed:
                        continue
                    value["skills"] = rewritten
                    await redis.set(
                        key,
                        json.dumps(value, ensure_ascii=False),
                        ex=cls.TTL_SECONDS,
                    )
                    updated += 1
                    if updated >= max_sessions:
                        logger.warning(
                            "[ConversationResource] 技能挂载同步达到上限 %s，其余会话请用户重新挂载技能",
                            max_sessions,
                        )
                        return updated
                if cursor == 0:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ConversationResource] 技能挂载同步中断（已同步 %s 个）: %s", updated, exc
            )
        return updated

    @classmethod
    async def rewrite_mcp_tool_name(
        cls,
        *,
        owner_id: Any,
        tool_id: str,
        new_name: str,
        max_sessions: int = MAX_MOUNT_REWRITE,
    ) -> int:
        """把某个 MCP 工具缓存 id 在会话资源里的名字改成新名字，返回改动数。

        MCP 工具名是 ``server_name:tool``。服务改名、或历史数据把两个服务写成同名时，
        已挂载的会话里仍存着旧名，运行时按名字反查工具会落空（模型看得见清单却调不到）。
        这里按**工具缓存 id** 精确匹配，绝不按名字批量改——同名平台工具有自己的 id。

        Args:
            owner_id: 个人服务的属主；``None`` 表示平台服务改名，需要扫全库（低频操作）。
        """
        target = str(tool_id or "").strip()
        name = str(new_name or "").strip()
        if not target or not name:
            return 0

        try:
            redis = await get_redis()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ConversationResource] Redis 不可用，跳过 MCP 工具名同步: %s", exc)
            return 0
        if not redis:
            return 0

        if owner_id is None:
            # 平台服务属于所有用户，改名前后的引用分散在各会话键里
            pattern = f"{cls.KEY_PREFIX}:*:*:{cls.SUFFIX}"
        else:
            try:
                owner_segment = require_user_id(owner_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ConversationResource] 无法确定 MCP 工具名同步的用户边界: %s", exc)
                return 0
            pattern = f"{cls.KEY_PREFIX}:{owner_segment}:*:{cls.SUFFIX}"
        updated = 0
        cursor = 0
        try:
            while True:
                cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=200)
                for key in keys:
                    raw = await redis.get(key)
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        value = json.loads(raw or "{}")
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(value, dict):
                        continue
                    items = value.get("mcp_tools")
                    if not isinstance(items, list):
                        continue
                    changed = False
                    for item in items:
                        if (
                            isinstance(item, dict)
                            and str(item.get("id") or "") == target
                            and item.get("name") != name
                        ):
                            item["name"] = name
                            changed = True
                    if not changed:
                        continue
                    await redis.set(
                        key,
                        json.dumps(value, ensure_ascii=False),
                        ex=cls.TTL_SECONDS,
                    )
                    updated += 1
                    if updated >= max_sessions:
                        return updated
                if cursor == 0:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ConversationResource] MCP 工具名同步中断（已同步 %s 个）: %s", updated, exc
            )
        return updated

    @classmethod
    async def delete(cls, user_id: Any, conversation_id: str) -> None:
        try:
            redis = await get_redis()
            if redis:
                await redis.delete(cls._key(user_id, conversation_id))
        except Exception:
            pass
