"""技能重命名 / 删除后的引用同步。

技能 ID 就是目录名，因此「重命名」在磁盘上是一次 ``os.rename``；真正麻烦的是
**引用**——同一个 ID 散落在四处：

* ``skill_publications.source_personal_skill_id`` / ``platform_skill_id``：发布谱系
  （删源判定、重复提交复用、下架能力都依赖它）；
* ``ai_agent_versions.skills``：智能体自定义公共技能白名单（JSON 列表）；
* ``ai_agent_resource_permissions`` 里 ``resource_type='forbidden_skill'`` 的行：
  「禁用技能」按 ``resource_id``（技能 ID）匹配，改名不同步就是**权限旁路**（换个 ID
  就重新可用），见 :func:`sync_forbidden_skill_permissions`；
* Redis 的会话资源范围 ``conversation:<uid>:<cid>:resource_scope_v1`` 里的
  ``skills`` 列表（用户挂载的技能 chip，TTL 30 天）。

只改目录不碰这三处，会得到「技能还在但哪都挂不上、发布记录变成孤儿」的静默断链，
比不支持重命名更糟。**删除（含下架）面对的是同一套引用**，所以这里同时提供 rename 与
delete 两条链路，并把同步结果如实回报给调用方。

本模块还是**技能 ID 校验的唯一实现**（:func:`validate_skill_id`）：格式、长度与保留字
此前散落在端点、工具、发布服务里各写一份，口径一漂移就会造出「管理页看得见、运行时
永远看不到」的幽灵技能。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from typing import Any, Dict, List

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.conversation_resource_service import ConversationResourceService

logger = logging.getLogger(__name__)

#: 技能 ID 长度上限，对齐 ``skill_publications.source_personal_skill_id`` 的 VARCHAR(128)。
#: 单独抽成常量是因为「自动加后缀」的调用方（如分享落地）必须按它截断，不能再写一份 128。
SKILL_ID_MAX_LENGTH = 128

#: 技能 ID 白名单（与运行时 skill_resolver、发布服务同一口径）
SKILL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,%d}$" % SKILL_ID_MAX_LENGTH)

#: 与 /skills 下更早注册的字面路由同名的 ID 会被遮蔽（``GET /skills/stats`` 命中统计接口，
#: 技能详情永远打不开），因此禁止创建/重命名/导入。``publications`` 是下架列表接口占用的
#: 字面路由，``export``/``import`` 与文件级路由同名；``share-targets`` 是个人技能下
#: 「搜索可接收分享的用户」的字面路由（/personal-skills/share-targets 注册在 /{skill_id}
#: 之前，同 ID 的技能详情页永远打不开）。
RESERVED_SKILL_IDS = frozenset(
    {
        "personal",
        "stats",
        "bindings",
        "publication-requests",
        "publications",
        "share-targets",
        "import",
        "export",
    }
)

#: 单次重命名/删除最多同步的会话数（Redis 会话范围），防止把低频管理操作做成全库扫描。
#: 取值与实现同在 ConversationResourceService，这里只做别名，避免两处数字各自漂移。
MAX_SESSION_SYNC = ConversationResourceService.MAX_MOUNT_REWRITE


class SkillRenameError(ValueError):
    """技能 ID 或目录操作被拒绝（ID 非法、目标已存在、源不存在等）。"""


def validate_skill_id(skill_id: str) -> str:
    """校验技能 ID，返回去除首尾空白后的合法值；不合法则抛 :class:`SkillRenameError`。

    这是全平台**唯一**的技能 ID 口径：``create`` / ``rename`` / ``delete`` / 导入 /
    上传 / 发布快照全部复用它。长度上限 128 对齐
    ``skill_publications.source_personal_skill_id`` 的 ``VARCHAR(128)``。
    """
    candidate = str(skill_id or "").strip()
    if not candidate:
        raise SkillRenameError("技能 ID 不能为空")
    if not SKILL_ID_PATTERN.match(candidate):
        raise SkillRenameError("非法技能 ID 格式：仅允许字母、数字、下划线与连字符，长度 1-128")
    if candidate.lower() in RESERVED_SKILL_IDS:
        raise SkillRenameError(f"技能 ID「{candidate}」为系统保留字，请更换")
    return candidate


def validate_new_skill_id(new_skill_id: str) -> str:
    """校验重命名的新技能 ID，返回去空白后的合法值。"""
    candidate = str(new_skill_id or "").strip()
    if not candidate:
        raise SkillRenameError("缺少新的技能 ID")
    return validate_skill_id(candidate)


def _remap_skill_dir(path: str | None, old_skill_id: str, new_skill_id: str) -> str | None:
    """把记录里的技能目录路径改名为新 ID（只改最后一层目录名，其余原样保留）。"""
    if not path:
        return path
    parent, name = os.path.split(str(path))
    if name != old_skill_id:
        return path
    return os.path.join(parent, new_skill_id)


async def sync_publication_lineage(
    session: AsyncSession,
    *,
    old_skill_id: str,
    new_skill_id: str,
    scope: str = "personal",
    owner_id: Any = None,
) -> int:
    """把发布谱系里的技能 ID 改为新 ID，返回受影响记录数。

    * ``scope="personal"``：改来源个人技能 ID（``source_personal_skill_id``），
      **必须同时按 ``source_user_id`` 限定作者**（见下方注释）；
    * ``scope="global"``：改**平台技能 ID**（``platform_skill_id``，即公共技能库目录名），
      同时把已物化版本记录的 ``materialized_path`` 目录名一起改掉。

    只改磁盘目录不改这两处，老发布记录会指向一个不存在的技能：审核端看到的
    「已发布」技能点不开，下架/删除会拿着旧 ID 去删一个不存在的目录（真目录反而留下来），
    而唯一索引 ``ux_publication_platform_skill_id`` 还挡着后续复用同一个 ID。
    """
    from app.models.skill_publication import SkillPublication, SkillPublicationVersion

    if scope == "global":
        result = await session.execute(
            select(SkillPublication).where(SkillPublication.platform_skill_id == old_skill_id)
        )
        publications = list(result.scalars().all())
        if not publications:
            return 0
        for publication in publications:
            publication.platform_skill_id = new_skill_id
        version_rows = await session.execute(
            select(SkillPublicationVersion).where(
                SkillPublicationVersion.publication_id.in_(
                    [publication.id for publication in publications]
                )
            )
        )
        for version in version_rows.scalars().all():
            version.materialized_path = _remap_skill_dir(
                version.materialized_path, old_skill_id, new_skill_id
            )
        await session.commit()
        return len(publications)

    # 个人技能 ID 只是用户自己的命名空间：用户 A 与用户 B 都可能有 ``demo``，
    # 只按 source_personal_skill_id 全表改名会把 B 的发布记录改成 A 的新 ID
    # （B 的技能实际还叫旧名），两边谱系一起断。拿不到作者时宁可不改——
    # 全表改名是跨用户数据污染，比「这一处没同步」严重得多。
    owner_user_id = _coerce_user_id(owner_id)
    if owner_user_id is None:
        logger.warning(
            "[SkillRename] 个人技能谱系同步缺少 owner_id，已跳过（skill=%s）", old_skill_id
        )
        return 0

    result = await session.execute(
        select(SkillPublication).where(
            SkillPublication.source_personal_skill_id == old_skill_id,
            SkillPublication.source_user_id == owner_user_id,
        )
    )
    publications = list(result.scalars().all())
    for publication in publications:
        publication.source_personal_skill_id = new_skill_id
    if publications:
        await session.commit()
    return len(publications)


def _agent_skill_ids(skills: Any) -> List[str]:
    """把 JSON 列读成字符串列表；非列表（历史脏数据）一律当空。"""
    if not isinstance(skills, list):
        return []
    return skills


async def sync_agent_bindings(
    session: AsyncSession, *, old_skill_id: str, new_skill_id: str
) -> List[str]:
    """把智能体自定义技能白名单（``ai_agent_versions.skills``）里的旧 ID 改为新 ID。

    返回被改动的智能体 ID 列表。注意必须**整体赋一个新列表**：JSON 列的原地修改
    SQLAlchemy 不会标记为脏数据，直接 ``append``/下标赋值会被静默丢弃。

    只处理 ``DRAFT`` / ``PUBLISHED`` 版本：``ARCHIVED`` 是历史快照，改它等于篡改历史，
    而它也不参与运行时选技能（见 ``AgentManagerService.get_skill_explicit_bindings``）。
    """
    from app.models.agent import AIAgentVersion

    result = await session.execute(
        select(AIAgentVersion).where(
            AIAgentVersion.skills.isnot(None),
            AIAgentVersion.status.in_(("DRAFT", "PUBLISHED")),
        )
    )
    changed: set[str] = set()
    for version in result.scalars().all():
        skills = _agent_skill_ids(version.skills)
        if old_skill_id not in skills:
            continue
        version.skills = [new_skill_id if item == old_skill_id else item for item in skills]
        changed.add(str(version.agent_id or ""))
    if changed:
        await session.commit()
    return sorted(item for item in changed if item)


async def sync_forbidden_skill_permissions(
    session: AsyncSession, *, old_skill_id: str, new_skill_id: str
) -> int:
    """把「禁用技能」权限行里的旧技能 ID 改为新 ID，返回被改动的行数。

    ``ai_agent_resource_permissions`` 中 ``resource_type='forbidden_skill'`` 的行按
    ``resource_id``（技能 ID）匹配，而重命名改的恰好就是这个 ID：不同步的话，管理员
    禁用某个平台技能之后，把它改个名字，禁用规则就**静默失配**——同一个技能换个 ID
    立刻对所有人重新可用（fail-open）。这是权限旁路，不是显示问题，所以必须同步。

    只做「改名」，**不做删除**：下架/删除平台技能时 ``platform_skill_id`` 是被刻意保留的
    （重新上架复用同一个 ID，见 ``revoke_published_version``），此时删掉禁用行会让
    "重新上架后禁用自动消失"；留着最多是一条暂时匹配不到的行（fail-closed），管理员在
    权限配置里取消勾选即可。

    只对平台技能（``scope="global"``）调用：个人技能 ID 是用户自己的命名空间，
    用户 A 的个人技能与用户 B 会话里的同名引用之间没有任何关系，全库改行会跨用户
    篡改别人的禁用配置。
    """
    from app.models.permission import ResourcePermission

    result = await session.execute(
        update(ResourcePermission)
        .where(
            ResourcePermission.resource_type == "forbidden_skill",
            ResourcePermission.resource_id == old_skill_id,
        )
        .values(resource_id=new_skill_id)
    )
    try:
        changed = int(getattr(result, "rowcount", 0) or 0)
    except (TypeError, ValueError):
        # 轻量替身（AsyncMock 等）拿不到 rowcount：按 0 处理，不能因此让改名整体失败
        changed = 0
    if changed:
        await session.commit()
        # 禁用集合有 60s 的同步读缓存：不清理的话"旧 ID 仍被拦、新 ID 短暂放行"会持续到过期
        try:
            from app.services.ai.skills.policy import clear_forbidden_skill_cache

            clear_forbidden_skill_cache()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SkillRename] 禁用技能缓存清理失败: %s", exc)
    return changed


async def remove_agent_skill_bindings(session: AsyncSession, *, skill_id: str) -> List[str]:
    """把该技能从智能体自定义白名单里移除，返回受影响的智能体 ID 列表。

    删除/下架公共技能时使用：白名单里留着已消失的 ID，智能体会「勾着一个不存在的技能」，
    运行时候选清单里静默少一个，管理员却看不出原因。同样按整体赋新列表处理。
    """
    from app.models.agent import AIAgentVersion

    result = await session.execute(
        select(AIAgentVersion).where(
            AIAgentVersion.skills.isnot(None),
            AIAgentVersion.status.in_(("DRAFT", "PUBLISHED")),
        )
    )
    changed: set[str] = set()
    for version in result.scalars().all():
        skills = _agent_skill_ids(version.skills)
        if skill_id not in skills:
            continue
        version.skills = [item for item in skills if item != skill_id]
        changed.add(str(version.agent_id or ""))
    if changed:
        await session.commit()
    return sorted(item for item in changed if item)


async def sync_conversation_mounts(
    *,
    old_skill_id: str,
    new_skill_id: str,
    scope: str | None = None,
    owner_id: Any = None,
) -> int:
    """把 Redis 里各会话挂载的技能 ID 改为新 ID，返回被改动的会话数。

    尽力而为：Redis 不可用或个别 key 解析失败都不应让重命名整体失败——目录已经改好，
    会话里的旧引用最多表现为「重新选一次技能」。

    写回统一走 :meth:`ConversationResourceService.rewrite_skill_mounts`，因此一定带
    ``ex=TTL_SECONDS``：这里此前直接 ``redis.set`` 不带 TTL，把 replace() 写入的 30 天
    有效期抹成了**永不过期**。
    """
    try:
        return await ConversationResourceService.rewrite_skill_mounts(
            skill_id=old_skill_id,
            new_skill_id=new_skill_id,
            scope=scope,
            max_sessions=MAX_SESSION_SYNC,
            owner_id=owner_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SkillRename] 会话挂载同步中断: %s", exc)
        return 0


async def remove_conversation_mounts(
    *, skill_id: str, scope: str | None = None, owner_id: Any = None
) -> int:
    """把 Redis 会话挂载里的该技能移除，返回被改动的会话数。

    与重命名共用同一个重写 helper，所以同样是「带 30 天 TTL 写回」而不是永久键。
    与 :func:`sync_conversation_mounts` 的区别是**不吞掉 Redis 不可用**：删除/下架必须能把
    这一处没同步写进 warnings（用户需要重新挂载技能），抛给调用方处理。
    """
    return await ConversationResourceService.rewrite_skill_mounts(
        skill_id=skill_id,
        new_skill_id=None,
        scope=scope,
        max_sessions=MAX_SESSION_SYNC,
        strict_unavailable=True,
        owner_id=owner_id,
    )


def _coerce_user_id(value: Any) -> int | None:
    """把用户 ID 归一成 int；空值/脏值返回 None（调用方据此判断「拿不到作者」）。"""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _actor_user_id(actor: Any) -> int | None:
    raw = None
    if isinstance(actor, dict):
        raw = actor.get("user_id") or actor.get("id")
    return _coerce_user_id(raw)


async def remove_skill_references(
    session: AsyncSession | None,
    *,
    skill_id: str,
    scope: str,
    actor: Any = None,
) -> Dict[str, Any]:
    """技能被删除/下架后清理三处引用，返回 ``{"synced": ..., "warnings": [...]}``。

    与 :func:`rename_skill` 同一风格：主操作（目录已删 / 状态已改）不回滚，引用同步
    **尽力而为并如实回报**——静默留下断链比不删更糟，用户至少要知道哪一处要人工处理。

    ``scope="global"``：清智能体白名单 + 会话挂载 + 把发布血缘标记为已下架；
    ``scope="personal"``：清会话挂载，并在该个人技能仍有平台副本时给出提示
    （删除个人副本**不会**下架平台技能，这是产品语义，不能悄悄替用户下架）。
    """
    report: Dict[str, Any] = {
        "skill_id": skill_id,
        "scope": scope,
        "synced": {},
        "warnings": [],
    }
    # 少数调用方（老单测直接调用端点函数）拿到的是 Depends 占位对象而非真实会话：
    # 此时跳过依赖数据库的同步并如实警告，不能因此 500，也不能假装同步成功。
    db_available = hasattr(session, "execute")
    if not db_available:
        report["warnings"].append("当前调用没有可用的数据库会话，智能体绑定与发布血缘未同步")
    elif scope == "global":
        try:
            report["synced"]["agents"] = await remove_agent_skill_bindings(
                session, skill_id=skill_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillRename] 智能体绑定清理失败: %s", exc)
            report["warnings"].append("部分智能体的技能白名单未能同步，请在智能体配置里重新勾选")
        try:
            from app.services.skill_publication_service import (
                mark_platform_skill_publications_revoked,
            )

            report["synced"]["publications"] = await mark_platform_skill_publications_revoked(
                session, platform_skill_id=skill_id, actor_user_id=_actor_user_id(actor)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillRename] 发布血缘标记失败: %s", exc)
            report["warnings"].append("发布记录未能标记为已下架，请到技能发布列表人工核对")
    else:
        user_id = _actor_user_id(actor)
        if user_id is not None:
            try:
                from app.services.skill_publication_service import (
                    list_active_platform_skills_for_personal_skill,
                )

                active = await list_active_platform_skills_for_personal_skill(
                    session, user_id=user_id, skill_id=skill_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SkillRename] 平台副本查询失败: %s", exc)
                active = []
            if active:
                report["warnings"].append(
                    "该个人技能已发布为平台技能（"
                    + "、".join(active)
                    + "）：删除个人副本不会下架平台技能，如需下架请到技能管理端执行下架"
                )

    try:
        report["synced"]["conversations"] = await remove_conversation_mounts(
            skill_id=skill_id,
            scope=scope,
            owner_id=_actor_user_id(actor) if scope == "personal" else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SkillRename] 会话挂载清理失败: %s", exc)
        report["warnings"].append("会话挂载未能同步（Redis 不可用），用户需要重新挂载该技能")
    return report


def rename_skill_directory(current_dir: str, *, new_skill_id: str) -> str:
    """把技能目录改名为 ``new_skill_id``，返回新目录绝对路径。

    ``os.rename`` 在同一文件系统内是原子的：失败时原目录原样保留，不会出现「半个技能」。
    目标已存在时明确拒绝，而不是覆盖——覆盖会直接毁掉另一个技能。
    """
    current_dir = os.path.abspath(current_dir)
    if not os.path.isdir(current_dir):
        raise SkillRenameError("技能不存在")
    parent = os.path.dirname(current_dir)
    target_dir = os.path.join(parent, new_skill_id)
    if os.path.abspath(target_dir) == current_dir:
        raise SkillRenameError("新技能 ID 与当前 ID 相同")
    if os.path.exists(target_dir):
        raise SkillRenameError(f"技能 ID {new_skill_id} 已被占用，请换一个")
    if not os.path.isfile(os.path.join(current_dir, "SKILL.md")):
        # 目录不是技能（可能是个残留目录/软链接），改名它没有意义
        raise SkillRenameError("目标目录不是有效的技能目录（缺少 SKILL.md）")
    try:
        os.rename(current_dir, target_dir)
    except OSError as exc:
        raise SkillRenameError(f"目录改名失败：{exc}") from exc
    return target_dir


async def rename_skill(
    session: AsyncSession,
    *,
    current_dir: str,
    old_skill_id: str,
    new_skill_id: str,
    scope: str,
    actor: Any = None,
) -> Dict[str, Any]:
    """执行完整重命名并返回同步报告。

    顺序刻意如此：**先把目录改好**（这是用户真正要的结果，且原子），再尽力同步各处引用。
    引用同步失败不回滚目录改名——否则用户会看到「什么都没发生」，而技能其实完全可用，
    只是个别会话需要重新挂载。

    发布谱系两个 scope 都要同步：个人技能改 ``source_personal_skill_id``，平台技能改
    ``platform_skill_id``（+ 已物化目录记录），漏掉后者的老发布记录会指向不存在的技能。
    """
    new_skill_id = validate_new_skill_id(new_skill_id)
    if new_skill_id == old_skill_id:
        raise SkillRenameError("新技能 ID 与当前 ID 相同")

    new_dir = rename_skill_directory(current_dir, new_skill_id=new_skill_id)
    report: Dict[str, Any] = {
        "skill_id": new_skill_id,
        "previous_skill_id": old_skill_id,
        "scope": scope,
        "directory": new_dir,
        "synced": {},
        "warnings": [],
    }

    try:
        report["synced"]["publications"] = await sync_publication_lineage(
            session,
            old_skill_id=old_skill_id,
            new_skill_id=new_skill_id,
            scope=scope,
            # 个人技能只改自己的发布记录；平台技能必须扫全库（所有用户的谱系都指向它）。
            owner_id=_actor_user_id(actor) if scope == "personal" else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SkillRename] 发布谱系同步失败: %s", exc)
        report["warnings"].append(
            (
                "发布记录中的来源技能 ID 未能同步"
                if scope != "global"
                else "发布记录中的平台技能 ID 未能同步"
            )
            + "，请人工核对发布列表"
        )

    if scope != "personal":
        try:
            report["synced"]["agents"] = await sync_agent_bindings(
                session, old_skill_id=old_skill_id, new_skill_id=new_skill_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillRename] 智能体绑定同步失败: %s", exc)
            report["warnings"].append("部分智能体的技能白名单未能同步，请在智能体配置里重新勾选")

        try:
            report["synced"]["permissions"] = await sync_forbidden_skill_permissions(
                session, old_skill_id=old_skill_id, new_skill_id=new_skill_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillRename] 禁用技能权限行同步失败: %s", exc)
            report["warnings"].append(
                "「禁用技能」权限行未能同步（技能已改名）：旧 ID 的禁用规则会失配，"
                "请到权限配置里重新勾选"
            )

    report["synced"]["conversations"] = await sync_conversation_mounts(
        old_skill_id=old_skill_id,
        new_skill_id=new_skill_id,
        scope=scope,
        # 个人技能只改自己的会话挂载（见 rewrite_skill_mounts 的 owner_id 说明）；
        # 平台技能必须扫全库——所有用户会话里的挂载都要跟着改。
        owner_id=_actor_user_id(actor) if scope == "personal" else None,
    )
    return report


def cleanup_empty_parent(path: str) -> None:
    """尽力清理残留的空目录（导入回滚后可能出现），失败忽略。"""
    try:
        if os.path.isdir(path) and not os.listdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except OSError:  # pragma: no cover - 仅日志
        logger.debug("[SkillRename] 清理空目录失败: %s", path)


__all__ = [
    "MAX_SESSION_SYNC",
    "RESERVED_SKILL_IDS",
    "SKILL_ID_PATTERN",
    "SkillRenameError",
    "cleanup_empty_parent",
    "remove_agent_skill_bindings",
    "remove_conversation_mounts",
    "remove_skill_references",
    "rename_skill",
    "rename_skill_directory",
    "sync_agent_bindings",
    "sync_conversation_mounts",
    "sync_publication_lineage",
    "validate_new_skill_id",
    "validate_skill_id",
]
