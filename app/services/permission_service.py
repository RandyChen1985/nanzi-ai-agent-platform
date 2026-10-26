from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, distinct, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from app.models.permission import ResourcePermission, Role, UserRoleRelation
from app.models.user import User
from app.models.agent import AIAgent
from app.models.ai_model import AIModel
from app.models.metadata import MetaDataset
from app.models.knowledge import KnowledgeBaseMetadata
from app.models.tool import SysApiTool
from app.schemas.permission import PermissionUpdate, UserPermissionsResponse, PermissionSet, PermissionSetDetail, ResourceDetail
from app.core.redis import get_redis
from collections.abc import Iterable
from typing import List, Optional
import json
import logging
import time

logger = logging.getLogger(__name__)

# 智能体级作用域缺少迁移时的统一提示（MySQL V168 / PostgreSQL V69）。
_AGENT_SCOPE_MIGRATION_HINT = (
    "ai_agent_resource_permissions.agent_id is missing; "
    "run migration db-prod/V168 (MySQL) or db-prod-pg/V69 (PostgreSQL) first"
)

# 进程级缓存：ai_agent_resource_permissions.agent_id 是否已由迁移加入。
#   None  = 尚未探测出结论；
#   True  = 已迁移（可永久缓存）；
#   False = **确定**未迁移（数据库明确报「无此列」，可永久缓存）。
# 探测「不确定」时（权限不足、连接抖动、超时等）**不写死**这个值，只记时间戳做节流重试：
# 否则一次偶发失败会让整个进程永久按「未迁移」运行，而「未迁移」的语义是**完全不加作用域
# 条件**——那会跨智能体读到别人的限制行，并让下一次全局保存的 DELETE 连带删掉智能体级数据。
_AGENT_SCOPE_COLUMN_STATE: Optional[bool] = None
_AGENT_SCOPE_PROBE_FAILED_AT: float = 0.0
# 探测不确定时的重试间隔：足够短以尽快恢复，足够长以免每个请求都打一次失败查询。
_AGENT_SCOPE_PROBE_RETRY_SECONDS = 30.0

# 「确定无此列」虽然结论明确，但**不能永久有效**：正常上线顺序是代码先部署（此时确实
# 没有列）→ DBA 再执行迁移。若把 False 永久缓存，未重启的旧进程会一直按"没有列"运行，
# 而"没有列"的语义是**完全不加作用域条件**——它的一次「改菜单/改角色权限」保存就会把
# 新进程刚写入的智能体级限制静默 DELETE 掉。所以给同一个结论加过期时间，迁移完成后
# 无需重启即可自动启用智能体级作用域。
_AGENT_SCOPE_ABSENT_AT: float = 0.0
_AGENT_SCOPE_ABSENT_RETRY_SECONDS = 300.0

# 各数据库「列不存在」的错误码：MySQL 1054 / PostgreSQL 42703。
_AGENT_SCOPE_UNKNOWN_COLUMN_CODES = ("1054", "42703")

# 权限 id 前缀决定 resource_type：`menu:*` 是菜单/页面入口，`element:*` 是页面内功能点。
# 旧前端「勾菜单 = 全选子项」的级联把子菜单 id 也推进了 elements，于是库里出现
# resource_type='element' + resource_id='menu:system:config' 这样的行：权限树按 id 查
# 会显示「已勾选」，但侧栏 `hasMenuPerm()` 与后端 `require_permission("menu", ...)`
# 只查 menus，结果是「勾了页面、菜单不显示、接口 403」。这里统一按前缀纠正，
# 读取（_aggregate_permissions）与两次写入（用户/角色）共用同一判据。
def is_menu_permission_id(resource_id: object) -> bool:
    return str(resource_id or "").strip().startswith("menu:")


#: 菜单分组码前缀。分组只是侧栏/权限树的展示容器，本身不是可授权资源：
#: 授权表里出现 `group:*` 行不会让任何菜单变可见，只会污染角色权限页的勾选状态。
MENU_GROUP_ID_PREFIX = "group:"


def is_menu_group_id(resource_id: object) -> bool:
    return str(resource_id or "").strip().startswith(MENU_GROUP_ID_PREFIX)


def normalize_permission_resource_type(resource_id: object, default_type: str) -> str:
    """按 id 前缀纠正 resource_type；前缀不认识时沿用字段默认类型。"""
    rid = str(resource_id or "").strip()
    if rid.startswith("menu:"):
        return "menu"
    if rid.startswith("element:"):
        return "element"
    return default_type


# ---------------------------------------------------------------- 知识库可见范围
#
# 三档语义（`knowledge_base_metadata.visibility`）：
#   public  —— 公共级：**需要显式授权**才能访问（创建人自动可用），可继续分配给用户/角色；
#   team    —— 部门级：业务归属部门（含下级部门）成员自动**只读**，也可继续分配；
#   private —— 个人私有：仅创建人可访问，**不允许**分配授权，已有授权记录一律不生效。
#
# 空值/未知值一律按 public 处理：历史数据可能缺列或写入过脏值，按 public 兜底
# 等于保持改造前的行为（授权即可见），不会因为读到一个陌生值把人的权限收紧。

VISIBILITY_PUBLIC = "public"
VISIBILITY_TEAM = "team"
VISIBILITY_PRIVATE = "private"


def normalize_knowledge_visibility(value: object) -> str:
    """归一化可见范围；空值/未知值按 public 处理。"""
    token = str(value or "").strip().lower()
    if token in (VISIBILITY_TEAM, VISIBILITY_PRIVATE):
        return token
    return VISIBILITY_PUBLIC


def resolve_knowledge_dataset_access(
    *,
    dataset_rows: Iterable[tuple],
    granted_ids: Iterable[str],
    my_dept_paths: Iterable[str],
    owner_dept_paths: dict,
    default_ids: Iterable[str],
    user_name: Optional[str],
) -> dict:
    """按三档可见范围计算可读/可写集合（纯函数，便于穷举边界）。

    ``dataset_rows`` 每项为 ``(ragflow_dataset_id, visibility, owner_dept_id, created_by)``。

    规则（按序判定，命中即止）：

    1. ``created_by == user_name``：可读可写，且不受 visibility 影响；
    2. ``private``：只有创建人，**授权记录被刻意忽略**；
    3. ``dataset_id`` 在授权集合里：可读（public / team 都适用）；
    4. ``team`` 且归属部门非空：当前用户的任一部门位于该部门子树内即可读
       —— ``path`` 前缀匹配天然包含下级部门，且 ``/1/7/`` 不会误匹配 ``/1/70/``；
    5. 元数据缺失（RAGFlow 有、本地尚未同步）的库保持改造前口径：授权即可见；
    6. 系统级默认库（``knowledge_ragflow_dataset_ids``）对所有人可读，**但 ``private`` 除外**：
       否则把某个私有库配进默认列表就等于把它公开给全平台，与「仅创建人」直接矛盾。

    写权限分两级：

    - ``writable_ids``（管理级）：仅「自己创建的」+ 管理员。改元数据、改可见范围、
      改归属部门、删库、分配/撤销授权、查看授权名单都走这一级；
    - ``content_writable_ids``（内容级）：管理级 **+ team 库的归属部门成员**。
      只用于维护文档内容（上传、删除、解析、查看切片明细）。部门成员能改库里
      的文档，但不能改这个库"归谁看"。

    授权成员（被显式分配给某人/角色）恒为只读，两级都不含他们。
    """
    granted = {str(token).strip() for token in granted_ids or [] if str(token).strip()}
    defaults = {str(token).strip() for token in default_ids or [] if str(token).strip()}
    paths = [str(path) for path in my_dept_paths or [] if str(path or "").strip()]

    accessible: set[str] = set()
    writable: set[str] = set()
    content_writable: set[str] = set()
    known_ids: set[str] = set()
    private_ids: set[str] = set()

    for row in dataset_rows or []:
        dataset_id, raw_visibility, raw_owner_dept_id, created_by = row
        dataset_id = str(dataset_id or "").strip()
        if not dataset_id:
            continue
        known_ids.add(dataset_id)

        if user_name and created_by and str(created_by) == str(user_name):
            accessible.add(dataset_id)
            writable.add(dataset_id)
            content_writable.add(dataset_id)
            continue

        visibility = normalize_knowledge_visibility(raw_visibility)
        if visibility == VISIBILITY_PRIVATE:
            # 个人私有：即使历史上有授权记录也不生效；
            # 记下来是因为下面的「系统默认库」合并也必须跳过它
            private_ids.add(dataset_id)
            continue

        if dataset_id in granted:
            accessible.add(dataset_id)
            # 刻意不 continue：同一个人可能既是授权成员又是归属部门成员，
            # 后者应该拿到内容级写权限，不能被「已授权」提前短路掉。

        if visibility == VISIBILITY_TEAM and raw_owner_dept_id:
            owner_path = (owner_dept_paths or {}).get(int(raw_owner_dept_id))
            if owner_path and any(path.startswith(str(owner_path)) for path in paths):
                accessible.add(dataset_id)
                # 部门（含下级部门）成员可以维护文档内容，但不能改库的设置
                content_writable.add(dataset_id)

    # 未同步到元数据表的库：沿用旧口径（有授权即可见），避免刚建/刚同步的库突然不可见
    accessible |= {dataset_id for dataset_id in granted if dataset_id not in known_ids}
    # 默认库对全员可读，但私有库不能借此"被公开"：private 的语义是有且仅有创建人
    accessible |= {dataset_id for dataset_id in defaults if dataset_id not in private_ids}

    return {
        "accessible_ids": accessible,
        "writable_ids": writable,
        "content_writable_ids": content_writable,
    }


class PermissionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._redis = None

    async def _get_redis(self):
        if not self._redis:
            self._redis = await get_redis()
        return self._redis

    async def _get_cache_key(self, user_id: int, agent_id: Optional[str] = None) -> str:
        # v7: 修复「menu:* 被旧前端写成 element」的读取口径。旧缓存里 elements 混着菜单 id、
        # menus 缺对应项，命中旧缓存会让侧栏继续缺菜单（TTL 内看起来像没修好），必须升版重建。
        # v6: 新增智能体级作用域（agent_id）。同一用户在不同智能体里的有效禁用集合不同，
        # 缓存键必须带上作用域，否则「在 A 智能体里禁了 X」会串到 B 智能体（或反之漏禁）。
        # v5 的理由同样成立：旧缓存缺 forbidden_skills 会解析成空列表 = 未禁用。
        # 版本沿革：v2（仍在失效列表里兼容清理）、v4（新增 models）、v5（新增禁用技能）、
        # v6（新增智能体级作用域）、v7（菜单 id 归位）。scope 段：全局用 "-"，智能体级用其 agent_id。
        scope = str(agent_id).strip() if agent_id is not None else ""
        return f"sys:auth:permissions:v7:user:{user_id}:agent:{scope or '-'}"

    async def _supports_agent_scope(self) -> bool:
        """``ai_agent_resource_permissions.agent_id`` 是否已由迁移加入（进程内缓存一次）。

        代码可能**先于迁移**上线。若直接引用不存在的列，SQL 会报错、端点转成 5xx，
        结果是"禁用资源"整块功能不可用——用一个新功能把老功能打挂是不可接受的。
        因此未迁移时降级为"只看全局行"（此时库里也不可能有智能体级数据），迁移后自动启用。

        判据是"能不能真的引用这一列"（``SELECT ... WHERE agent_id IS NULL``），**不查**
        ``information_schema``：后者在 MySQL 里覆盖整台实例上该用户可见的所有 schema，同实例
        别的库迁移过就会被误判成本库已迁移。

        探测结论分三类：确定已迁移与非迁移都永久缓存；**不确定**（权限不足、连接抖动等）
        只在节流窗口内按未迁移处理并稍后重试——因为"未迁移"的语义是**不加任何作用域条件**，
        一旦被永久写死，就会跨智能体读到别人的限制、并让下一次全局保存误删智能体级数据。
        """
        global _AGENT_SCOPE_COLUMN_STATE, _AGENT_SCOPE_PROBE_FAILED_AT, _AGENT_SCOPE_ABSENT_AT
        now = time.monotonic()
        if _AGENT_SCOPE_COLUMN_STATE is True:
            return True
        if _AGENT_SCOPE_COLUMN_STATE is False:
            # `_AGENT_SCOPE_ABSENT_AT == 0.0` 表示状态不是探测得来的（由测试/调用方直接注入），
            # 此时按「刚探测过」处理、不回源，否则注入的状态会被立刻覆盖掉。
            if _AGENT_SCOPE_ABSENT_AT == 0.0:
                return False
            # 已知没有列，但要留过期时间：迁移可能刚执行完而进程还没重启。
            if now - _AGENT_SCOPE_ABSENT_AT < _AGENT_SCOPE_ABSENT_RETRY_SECONDS:
                return False
        if (
            _AGENT_SCOPE_PROBE_FAILED_AT
            and now - _AGENT_SCOPE_PROBE_FAILED_AT < _AGENT_SCOPE_PROBE_RETRY_SECONDS
        ):
            # 上次探测不确定，节流期内先按未迁移处理，等下一次请求再试。
            return False

        from sqlalchemy import text

        try:
            # 直接试引用该列，而不是查 information_schema：后者在 MySQL 里覆盖整台实例上
            # 该用户可见的**所有** schema，同实例上别的库迁移过就会被误判成本库已迁移，
            # 此后每条权限 SQL 都报 1054，把「禁用资源」整块连同登录链路一起打挂。
            # 放进 SAVEPOINT：失败只回滚这一小段，不会污染调用方的事务。
            async with self.db.begin_nested():
                await self.db.execute(
                    text(
                        "SELECT 1 FROM ai_agent_resource_permissions "
                        "WHERE agent_id IS NULL LIMIT 1"
                    )
                )
        except Exception as ex:  # noqa: BLE001
            message = str(ex)
            if any(code in message for code in _AGENT_SCOPE_UNKNOWN_COLUMN_CODES):
                # 数据库明确回答「没有这一列」——这是确定的结论，可以缓存。
                _AGENT_SCOPE_COLUMN_STATE = False
                _AGENT_SCOPE_ABSENT_AT = now
                logger.warning(
                    "[agent-scope] agent_id column is absent; falling back to "
                    "global-only scope until migration is applied (%s): %s",
                    _AGENT_SCOPE_MIGRATION_HINT,
                    ex,
                )
            else:
                # 结论不确定（权限不足 / 连接抖动 / 超时…）。绝不能写死成 False：
                # 那会让进程永久按「不加作用域」运行，导致跨智能体读取与保存时误删。
                _AGENT_SCOPE_PROBE_FAILED_AT = now
                logger.warning(
                    "[agent-scope] Probe for agent_id column was inconclusive; "
                    "will retry in %.0fs and keep global-only scope meanwhile: %s",
                    _AGENT_SCOPE_PROBE_RETRY_SECONDS,
                    ex,
                )
        else:
            _AGENT_SCOPE_COLUMN_STATE = True
            _AGENT_SCOPE_PROBE_FAILED_AT = 0.0
            _AGENT_SCOPE_ABSENT_AT = 0.0
            logger.info("[agent-scope] agent_id column detected; agent scope enabled")

        return bool(_AGENT_SCOPE_COLUMN_STATE)

    async def _agent_scope_filters(self, agent_id: Optional[str]):
        """权限行的作用域过滤条件（原生 SQL，因为 ORM **刻意不映射** agent_id）。

        - 未迁移（无 ``agent_id`` 列）：返回空条件，即"不过滤"——此时库里不可能有
          智能体级行，等价于既有行为；
        - ``agent_id=None``（全局视角）：只取 ``agent_id IS NULL`` 的行，与历史行为一致；
        - 指定智能体：取「全局行 ∪ 该智能体行」，即把两层禁用合并成"该智能体内的有效禁用"。

        合并放在这里而不是各调用方，是为了避免出现"有的入口并了、有的没并"——
        同一个用户在不同智能体里的边界必须是同一套判定。
        """
        if not await self._supports_agent_scope():
            return ()
        from sqlalchemy import text

        scope = str(agent_id).strip() if agent_id is not None else ""
        if not scope:
            return (text("agent_id IS NULL"),)
        return (
            text("(agent_id IS NULL OR agent_id = :agent_scope)").bindparams(
                agent_scope=scope
            ),
        )

    async def get_user_permissions(
        self, user_id: int, agent_id: Optional[str] = None
    ) -> UserPermissionsResponse:
        """用户在该作用域下的有效权限。

        ``agent_id=None`` 返回**全局**有效权限（与历史行为一致）；传入某个 agent_id 时返回
        **该智能体内**的有效权限 = 全局禁用 ∪ 该智能体级禁用——调用方不需要自己合并，
        避免"有的入口并了、有的没并"这类漂移。
        """
        try:
            parsed_uid = int(user_id)
        except (TypeError, ValueError):
            parsed_uid = user_id
        # 1. Try Cache
        redis = await self._get_redis()
        cache_key = await self._get_cache_key(parsed_uid, agent_id)
        if redis:
            cached_data = await redis.get(cache_key)
            if cached_data:
                try:
                    data = json.loads(cached_data)
                    cached = UserPermissionsResponse(**data)
                    # 资源 ID 列表走缓存；展示名（尤其知识库）随元数据表更新，每次命中后刷新 details
                    cached.details = await self._fetch_permission_details(cached.permissions)
                    return cached
                except Exception as e:
                    logger.warning(f"Failed to parse permission cache for user {user_id}: {e}")

        # 2. DB Query
        perm_set = PermissionSet()
        user_roles_list = []

        # A. Fetch User & System Role
        user_stmt = select(User).where(User.id == parsed_uid)
        user_result = await self.db.execute(user_stmt)
        user = user_result.scalar_one_or_none()

        if user and user.role:
            user_roles_list.append(user.role) # Add 'admin' or 'user'

        # B. Fetch Business Roles
        role_stmt = select(Role.code).join(UserRoleRelation, Role.id == UserRoleRelation.role_id).where(UserRoleRelation.user_id == parsed_uid)
        role_result = await self.db.execute(role_stmt)
        business_roles = role_result.scalars().all()
        user_roles_list.extend(business_roles)

        # C. Fetch Direct Permissions
        direct_stmt = select(ResourcePermission).where(
            ResourcePermission.user_id == parsed_uid,
            ResourcePermission.enabled == True,
            *(await self._agent_scope_filters(agent_id)),
        )
        direct_perms = (await self.db.execute(direct_stmt)).scalars().all()
        self._aggregate_permissions(perm_set, direct_perms)

        # D. Fetch Role Permissions
        # Join: UserRoleRelation -> ResourcePermission (via role_id)
        role_perm_stmt = select(ResourcePermission).join(
            UserRoleRelation, ResourcePermission.role_id == UserRoleRelation.role_id
        ).where(
            UserRoleRelation.user_id == parsed_uid,
            ResourcePermission.enabled == True,
            *(await self._agent_scope_filters(agent_id)),
        )
        role_perms = (await self.db.execute(role_perm_stmt)).scalars().all()
        self._aggregate_permissions(perm_set, role_perms)

        # E. Fetch Details
        details = await self._fetch_permission_details(perm_set)

        response = UserPermissionsResponse(
            roles=list(set(user_roles_list)), # Dedup
            permissions=perm_set,
            details=details
        )

        # 3. Set Cache (TTL 1 hour)
        if redis:
            await redis.set(
                cache_key,
                response.model_dump_json(),
                ex=3600
            )

        return response

    async def get_user_direct_permissions(self, user_id: int) -> PermissionSet:
        """仅该用户直配的**全局**权限（不含角色继承，也不含智能体级作用域）。

        禁用资源页必须基于它回显与提交：``get_user_permissions`` 返回的是「用户 ∪ 角色」的
        有效权限，拿它整份写回会把角色级禁用固化成用户级记录——角色侧随后取消禁用，
        用户依旧被禁（黑名单收不回来）。

        同理，智能体级行也不该混进全局禁用页的直配集合：页面上"来自角色"与"用户直配"两栏
        都只讲全局；智能体级限制由智能体卡片上的「功能限制」弹框单独读写。
        """
        try:
            parsed_uid = int(user_id)
        except (TypeError, ValueError):
            parsed_uid = user_id
        stmt = select(ResourcePermission).where(
            ResourcePermission.user_id == parsed_uid,
            ResourcePermission.enabled == True,
            *(await self._agent_scope_filters(None)),
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        perm_set = PermissionSet()
        self._aggregate_permissions(perm_set, rows)
        return perm_set

    async def _role_derived_forbidden_ids(self, user_id: int, resource_type: str) -> set[str]:
        """该用户经角色继承到的**全局**禁用项（用于避免在用户级重复落库）。

        只取 ``agent_id IS NULL`` 的行：智能体级限制是另一套作用域，不该影响
        全局禁用页"用户直配 vs 角色继承"的判定。
        """
        stmt = (
            select(ResourcePermission.resource_id)
            .join(UserRoleRelation, ResourcePermission.role_id == UserRoleRelation.role_id)
            .where(
                UserRoleRelation.user_id == user_id,
                ResourcePermission.resource_type == resource_type,
                ResourcePermission.enabled == True,
                *(await self._agent_scope_filters(None)),
            )
        )
        return {str(value) for value in (await self.db.execute(stmt)).scalars().all()}

    async def get_agent_scoped_forbidden_ids(
        self, *, user_id: Optional[int] = None, role_id: Optional[int] = None, agent_id: str
    ) -> dict[str, list[str]]:
        """某一「主体 × 智能体」自己配置的禁用项（**不含**全局行）。

        供「功能限制」弹框回显：弹框要区分"我在这里禁的"与"全局已经禁了的"（后者只读），
        所以这里不能走 ``get_user_permissions`` 的并集。

        走原生 SQL 的理由见 ``app/models/permission.py`` 的注释：ORM 不映射 agent_id，
        否则未迁移的库会在 SELECT 的字段列表上报 Unknown column。
        """
        scope = str(agent_id).strip()
        if not scope:
            return {"forbidden_tools": [], "forbidden_skills": []}
        if not await self._supports_agent_scope():
            raise RuntimeError(_AGENT_SCOPE_MIGRATION_HINT)
        if user_id is None and role_id is None:
            return {"forbidden_tools": [], "forbidden_skills": []}

        from sqlalchemy import text

        subject_column = "user_id" if user_id is not None else "role_id"
        subject_value = user_id if user_id is not None else role_id
        stmt = text(
            f"SELECT resource_type, resource_id FROM ai_agent_resource_permissions "
            f"WHERE {subject_column} = :subject AND agent_id = :scope "
            # 写 TRUE 而不是 1：PG 里本列是 BOOLEAN，BOOLEAN 与 INTEGER 之间没有隐式转换，
            # 用整型字面量比较会直接报 operator does not exist: boolean = integer；
            # 而 MySQL 把 TRUE 当 1 的别名，两套库都能跑。
            f"AND enabled = TRUE AND resource_type IN ('forbidden_tool', 'forbidden_skill')"
        ).bindparams(subject=int(subject_value), scope=scope)
        rows = (await self.db.execute(stmt)).all()
        result: dict[str, list[str]] = {"forbidden_tools": [], "forbidden_skills": []}
        for resource_type, resource_id in rows:
            if resource_type == "forbidden_tool" and resource_id not in result["forbidden_tools"]:
                result["forbidden_tools"].append(resource_id)
            elif resource_type == "forbidden_skill" and resource_id not in result["forbidden_skills"]:
                result["forbidden_skills"].append(resource_id)
        return result

    @staticmethod
    def _drop_unusable_skill_ids(values: Iterable[str], agent_id: str = "") -> list[str]:
        """剔除"运行时不读"的技能 id（纯个人技能，或已不存在的 id）。

        个人技能永不受限（产品口径是"禁的是平台发布的那一份"），所以禁一个只属于个人的
        技能 id 会**落库成功、弹框回显已禁，但运行时三处过滤都不读它**——典型的"存了但
        无效"，管理员以为已经限住，实际什么都没发生。

        同名 id 会被保留：那时被禁的是公共那一份，运行时按 scope 排掉个人技能、公共技能
        照旧被禁，语义正确。

        两条**绝不剥离**的保护：目录读失败、或公共清单为空（未挂载）。此时宁可存下一条
        无效项，也不能把有效项误删——后者的代价是"管理员设的限制凭空消失"。
        """
        items = [str(value).strip() for value in values if str(value).strip()]
        if not items:
            return []
        try:
            from app.services.ai.skill_resolver import list_skill_metas

            public_ids = {
                str(meta.get("id") or "").strip() for meta in list_skill_metas()
            }
        except Exception:  # noqa: BLE001 —— 目录不可用不是拒绝写入的理由
            logger.debug("[agent-scope] skill catalog unreadable; skip pruning", exc_info=True)
            return items
        if not public_ids:
            return items

        dropped = [value for value in items if value not in public_ids]
        if dropped:
            logger.warning(
                "[agent-scope] ignoring %d skill id(s) %s for agent %s: not a platform "
                "(public) skill, the runtime never reads them",
                len(dropped),
                dropped,
                agent_id or "<unknown>",
            )
        return [value for value in items if value in public_ids]

    async def update_agent_scoped_forbidden_ids(
        self,
        *,
        agent_id: str,
        user_id: Optional[int] = None,
        role_id: Optional[int] = None,
        forbidden_tools: Optional[Iterable[str]] = None,
        forbidden_skills: Optional[Iterable[str]] = None,
    ) -> dict[str, list[str]]:
        """覆盖式写入某「主体 × 智能体」的禁用项（原生 SQL）。

        只动 ``agent_id`` 等于该值的行——全局禁用行（``agent_id IS NULL``）与**其它智能体**
        的行都必须原样保留；后者尤其容易写错成"整表重建"，那会把别的智能体的限制一起清掉。
        传 ``None`` 表示不修改该类别；传空列表表示清空该类别。
        """
        scope = str(agent_id).strip()
        if not scope:
            raise ValueError("agent_id is required for agent-scoped restrictions")
        if not await self._supports_agent_scope():
            raise RuntimeError(_AGENT_SCOPE_MIGRATION_HINT)
        if user_id is None and role_id is None:
            raise ValueError("either user_id or role_id is required")

        from sqlalchemy import text

        subject_column = "user_id" if user_id is not None else "role_id"
        subject_value = int(user_id if user_id is not None else role_id)

        payload: list[tuple[str, Iterable[str]]] = []
        if forbidden_tools is not None:
            payload.append(("forbidden_tool", forbidden_tools))
        if forbidden_skills is not None:
            payload.append(("forbidden_skill", forbidden_skills))

        for resource_type, values in payload:
            # 防御：调用方若直接传字符串，下面的 `for value in values` 会按**字符**拆开，
            # 落库一堆单字符垃圾行、同时把原有禁用项 DELETE 掉。端点已用 schema 拦住，
            # 但这是公共方法，不该假设每个调用方都规矩。
            if isinstance(values, str):
                raise ValueError(
                    f"{resource_type} must be an iterable of strings, not a bare string"
                )
            if resource_type == "forbidden_skill":
                values = self._drop_unusable_skill_ids(values, scope)
            await self.db.execute(
                text(
                    f"DELETE FROM ai_agent_resource_permissions "
                    f"WHERE {subject_column} = :subject AND agent_id = :scope "
                    f"AND resource_type = :rtype"
                ).bindparams(subject=subject_value, scope=scope, rtype=resource_type)
            )
            seen: set[str] = set()
            for value in values:
                clean = str(value).strip()
                if not clean or clean in seen:
                    continue
                seen.add(clean)
                await self.db.execute(
                    text(
                        f"INSERT INTO ai_agent_resource_permissions "
                        f"({subject_column}, agent_id, resource_type, resource_id, enabled, created_at, updated_at) "
                        # TRUE 而非 1，理由同上面的读取查询（PG 的 BOOLEAN 不接受整型字面量）
                        f"VALUES (:subject, :scope, :rtype, :rid, TRUE, NOW(), NOW())"
                    ).bindparams(
                        subject=subject_value, scope=scope, rtype=resource_type, rid=clean
                    )
                )
        # 必须先提交再失效：反过来会出现「缓存已清、数据未提交」的窗口，此刻并发请求
        # 读到旧行并把旧结果写回 Redis（TTL 1 小时），管理员刚保存的限制在 1 小时内不生效，
        # 且不会再有一次失效来纠正。其它 update_*（用户权限、角色权限、用户角色）也都是
        # 先 commit 再失效，这里保持一致；同时本方法自己提交，调用方无需再 commit。
        await self.db.commit()

        if user_id is not None:
            await self._invalidate_user_cache(int(user_id))
        else:
            await self._invalidate_role_cache(int(role_id))
        return await self.get_agent_scoped_forbidden_ids(
            user_id=user_id, role_id=role_id, agent_id=scope
        )

    async def get_accessible_dataset_ids_csv(self, user_id: int) -> str:
        """
        RagFlow 知识库 ID 列表（逗号分隔），供 OpenClaw AUTH_CONTEXT 等使用。

        口径说明：
        - 数据集权限在系统里通常以 ``resource_type == "metadata"`` 存储（resource_id 对应 MetaDataset.id）
          需要再映射为 MetaDataset.rag_dataset_id（RagFlow 侧 knowledge base id）
        - 为兼容历史数据，同时并集 ``resource_type == "dataset"`` 直接给出的 resource_id（若其本身即为 rag_dataset_id）

        ``get_user_permissions`` 已聚合 **用户直接授权** 与 **用户所属角色授权** 的并集。
        """
        perms = await self.get_user_permissions(user_id)
        ids: set[str] = set()
        # 1) 兼容：resource_type == "dataset" 时的直接写入（假设其已是 RagFlow KB id）
        for d in perms.permissions.datasets:
            s = (d or "").strip()
            if s:
                ids.add(s)

        # 2) 主口径：resource_type == "metadata" -> MetaDataset.id -> MetaDataset.rag_dataset_id
        meta_ids: list[int] = []
        for mid in perms.permissions.metadata:
            s = (mid or "").strip()
            if s.isdigit():
                meta_ids.append(int(s))

        if meta_ids:
            stmt = select(MetaDataset).where(MetaDataset.id.in_(meta_ids))
            metas = (await self.db.execute(stmt)).scalars().all()
            for m in metas:
                rag_id = (getattr(m, "rag_dataset_id", None) or "").strip()
                if rag_id:
                    ids.add(rag_id)

        return ",".join(sorted(ids))

    @staticmethod
    def is_admin_user(roles: Iterable[str]) -> bool:
        return "admin" in set(roles or [])

    async def get_knowledge_base_access(
        self,
        user_id: int,
        user_name: Optional[str] = None,
    ) -> dict:
        """
        知识库（RAGFlow Dataset）访问口径（三档可见范围，见模块顶部常量）：

        - 管理员：全部可访问、可写，**包括他人创建的个人私有库**。
          这是明确的产品决策（运维排障需要能看到/清理全平台的知识库），
          不是漏判：``accessible_ids`` 返回 ``None`` 即代表"不受限"，
          调用方必须先判 ``is_admin`` 再用集合，别把 ``None`` 当空权限。
        - ``public``（公共级）：创建人 + 显式授权的用户/角色；
        - ``team``（部门级）：在 public 的基础上，业务归属部门（**含下级部门**）
          的成员自动只读；
        - ``private``（个人私有）：只有创建人，授权记录不生效（也不允许新增授权）；
        - 任何情况下都叠加：自己创建的库可读可写、系统默认库全员可读、管理员全量。

        ``writable_ids``（管理级）恒为「自己创建的库」：改元数据/可见范围/归属部门、
        删库、分配授权、查看授权名单都要求这一级。``content_writable_ids``（内容级）
        在此基础上加上 team 库的归属部门成员 —— 他们能上传/删除/解析文档、看切片，
        但不能改变这个库的可见范围。授权成员两级都只有读权限。
        """
        perms = await self.get_user_permissions(user_id)
        if self.is_admin_user(perms.roles):
            return {
                "is_admin": True,
                "accessible_ids": None,
                "writable_ids": None,
                "content_writable_ids": None,
            }

        # 一次取出判定所需的全部列：库数量是几十到几百量级，逐库回查反而更慢
        rows = (
            await self.db.execute(
                select(
                    KnowledgeBaseMetadata.ragflow_dataset_id,
                    KnowledgeBaseMetadata.visibility,
                    KnowledgeBaseMetadata.owner_dept_id,
                    KnowledgeBaseMetadata.created_by,
                ).where(KnowledgeBaseMetadata.status != "deleted")
            )
        ).all()

        # 只有存在 team 库时才去查部门，普通用户的常规路径不额外付一次查询
        team_owner_ids = {
            int(owner_dept_id)
            for _, visibility, owner_dept_id, _ in rows
            if owner_dept_id and normalize_knowledge_visibility(visibility) == VISIBILITY_TEAM
        }
        owner_dept_paths: dict[int, str] = {}
        my_dept_paths: list[str] = []
        if team_owner_ids:
            from app.models.department import (
                DEPARTMENT_STATUS_ENABLED,
                Department,
                UserDepartment,
            )

            # 停用部门（status=0）不再构成可见范围：与 user_dimension_service 的
            # 部门遍历口径保持一致，否则「维度里看不到这个部门」但「它的 team 库
            # 仍然自动可见」，两边对不上。
            dept_rows = (
                await self.db.execute(
                    select(Department.id, Department.path).where(
                        Department.id.in_(team_owner_ids),
                        Department.status == DEPARTMENT_STATUS_ENABLED,
                    )
                )
            ).all()
            owner_dept_paths = {
                int(dept_id): str(path or "") for dept_id, path in dept_rows if path
            }
            my_rows = (
                await self.db.execute(
                    select(Department.path)
                    .select_from(UserDepartment)
                    .join(Department, Department.id == UserDepartment.dept_id)
                    .where(
                        UserDepartment.user_id == int(user_id),
                        Department.status == DEPARTMENT_STATUS_ENABLED,
                    )
                )
            ).all()
            my_dept_paths = [str(path) for (path,) in my_rows if path]

        # 合并系统级配置的默认公开知识库（与 visibility 无关，全员可读）
        from app.services.config_service import ConfigService

        default_ids_str = await ConfigService.get("knowledge_ragflow_dataset_ids")
        default_ids = [item.strip() for item in (default_ids_str or "").split(",") if item.strip()]

        access = resolve_knowledge_dataset_access(
            dataset_rows=[tuple(row) for row in rows],
            granted_ids=perms.permissions.datasets or [],
            my_dept_paths=my_dept_paths,
            owner_dept_paths=owner_dept_paths,
            default_ids=default_ids,
            user_name=user_name,
        )
        return {"is_admin": False, **access}

    async def find_private_datasets_not_owned(
        self,
        dataset_ids: Iterable[str],
        user_name: Optional[str],
    ) -> set[str]:
        """返回给定集合里「个人私有且当前用户不是创建人」的库 ID。

        **检索侧专用**。智能体绑定的知识库会跳过使用者权限校验（智能体作者
        已经决定了它的知识范围），但 ``private`` 不能跟着被代理放行：
        它的语义是「有且仅有创建人」，否则任何被授权过某库的人都能建个智能体
        把它二次分发给全体使用者。因此调用方应把这批 ID 从「智能体绑定」里摘掉，
        让它们回落到正常的用户权限判定（那里 private 只对创建人成立）。

        元数据里查不到的库不返回 —— 与「未同步库按授权放行」的既有口径一致。
        """
        ids = {str(dataset_id).strip() for dataset_id in dataset_ids or [] if str(dataset_id).strip()}
        if not ids:
            return set()

        rows = (
            await self.db.execute(
                select(
                    KnowledgeBaseMetadata.ragflow_dataset_id,
                    KnowledgeBaseMetadata.visibility,
                    KnowledgeBaseMetadata.created_by,
                ).where(
                    KnowledgeBaseMetadata.ragflow_dataset_id.in_(ids),
                    # 软删的库按「查不到」处理（与 docstring 的口径一致）：
                    # 它已经不会被检索到，不必再参与私有库摘除判定。
                    KnowledgeBaseMetadata.status != "deleted",
                )
            )
        ).all()

        denied: set[str] = set()
        for dataset_id, raw_visibility, created_by in rows:
            if normalize_knowledge_visibility(raw_visibility) != VISIBILITY_PRIVATE:
                continue
            # 创建人本人照常可用；created_by 缺失（无主私有库）时按拒绝处理
            if user_name and created_by and str(created_by) == str(user_name):
                continue
            denied.add(str(dataset_id))
        return denied

    async def filter_knowledge_dataset_ids(
        self,
        user_id: int,
        user_name: Optional[str],
        dataset_ids: List[str],
    ) -> List[str]:
        access = await self.get_knowledge_base_access(user_id, user_name)
        if access["is_admin"]:
            return list(dataset_ids)
        allowed = access["accessible_ids"] or set()
        return [d for d in dataset_ids if d in allowed]

    async def get_accessible_ragflow_meta_datasets(self, user_id: int) -> list[dict[str, str]]:
        """
        返回用户可访问的、已同步到 RagFlow 的元数据数据集信息（供 OpenClaw AUTH_CONTEXT.datasets 使用）。

        输出元素结构：
        - ragflow_meta_id: MetaDataset.rag_dataset_id
        - ragflow_meta_name: MetaDataset.name
        - ragflow_meta_desc: MetaDataset.display_name

        权限来源复用 ``get_user_permissions``（个人中心/权限服务口径，聚合用户直连 + 角色）。
        """
        perms = await self.get_user_permissions(user_id)
        meta_ids: list[int] = []
        for mid in perms.permissions.metadata:
            s = (mid or "").strip()
            if s.isdigit():
                meta_ids.append(int(s))

        if not meta_ids:
            return []

        stmt = (
            select(MetaDataset)
            .where(MetaDataset.id.in_(meta_ids))
            .where(MetaDataset.status == 1)
        )
        metas = (await self.db.execute(stmt)).scalars().all()

        datasets: list[dict[str, str]] = []
        for m in metas:
            rag_id = (getattr(m, "rag_dataset_id", None) or "").strip()
            if not rag_id:
                continue
            datasets.append(
                {
                    "ragflow_meta_id": rag_id,
                    "ragflow_meta_name": (m.name or "").strip(),
                    "ragflow_meta_desc": (m.display_name or "").strip(),
                }
            )
        datasets.sort(key=lambda x: x.get("ragflow_meta_id") or "")
        return datasets

    async def _load_menu_display_names(self) -> dict[str, str]:
        """权限码 → 菜单显示名，数据源是 `ai_agent_menus`。

        单独一个方法是为了把「读菜单表」的失败面收敛在调用处：菜单表是后加的，
        老库上可能还不存在，权限详情查询不能因此整条失败。
        """
        from app.services.menu_service import MenuService

        nodes = await MenuService(self.db).get_nodes()
        names: dict[str, str] = {}
        for node in nodes:
            code = (node.get("permission_code") or "").strip()
            label = (node.get("label") or "").strip()
            if code and label:
                names[code] = label
        return names

    async def _fetch_permission_details(self, perm_set: PermissionSet) -> PermissionSetDetail:
        details = PermissionSetDetail()

        # 完整权限 ID 精确中文映射表（最高优先级匹配）
        exact_permission_mapping = {
            # 菜单类
            "menu:dashboard": "系统概览",
            "menu:ai_chat": "智能助手",
            "menu:data_sources": "数据源管理",
            "menu:metadata": "元数据管理",
            "menu:agent_management": "智能体中心",
            "menu:skills_management": "技能工作台",
            "menu:mcp_management": "MCP 工具集",
            "menu:mcp_service": "MCP 服务台",
            "menu:memory_management": "记忆工作台",
            "menu:chatbi_examples": "案例集管理",
            "menu:knowledge_management": "知识库管理",
            "menu:knowledge_retrieval_test": "检索测试",
            "menu:agent_debug": "智能体调试",
            "menu:playground": "接口调试台",
            "menu:chat_logs": "聊天日志",
            "menu:system": "系统管理",
            "menu:system:users": "用户管理",
            "menu:system:roles": "角色管理",
            "menu:system:config": "系统配置",
            "menu:system:audit": "审计日志",
            "menu:prompts": "提示词工坊",
            "menu:task_center": "任务调度台",
            "menu:widget_debug": "组件调试台",

            # MCP 服务台四段式功能点
            "element:mcp_service:overview:read": "MCP服务台-查看服务总览",
            "element:mcp_service:config:read": "MCP服务台-查看服务配置",
            "element:mcp_service:config:edit": "MCP服务台-修改服务开关",
            "element:mcp_service:client:read": "MCP服务台-查看外部Client",
            "element:mcp_service:client:manage": "MCP服务台-管理外部Client",
            "element:mcp_service:client:secret_reset": "MCP服务台-重置Client Secret",
            "element:mcp_service:client:token_issue": "MCP服务台-生成用户AccessToken",
            "element:mcp_service:capability:read": "MCP服务台-查看能力与Scope",
            "element:mcp_service:capability:manage": "MCP服务台-管理能力与Scope",
            "element:mcp_service:grant:read": "MCP服务台-查看用户授权",
            "element:mcp_service:grant:revoke": "MCP服务台-撤销用户授权",
            "element:mcp_service:audit:read": "MCP服务台-查看调用审计",

            # 技能工作台
            "element:skills:admin": "技能工作台-平台管理与审核",

            # 任务调度台
            "element:task:manage": "任务调度台-任务管理",

            # 记忆工作台
            "element:memory:config_save": "记忆工作台-保存服务配置",
            "element:memory:config_index": "记忆工作台-索引检查与重建",
            "element:memory:view_data": "记忆工作台-查看记忆数据",
            "element:memory:delete": "记忆工作台-删除记忆",
            "element:memory:view_all_users": "记忆工作台-按任意用户筛选",
            "element:memory:test_search": "记忆工作台-记忆检索测试",

            # 提示词工坊与聊天日志
            "element:prompts:optimize": "提示词工坊-AI优化建议",
            "element:chat_logs:export": "聊天日志-导出日志",

            # 数据源与元数据
            "element:metadata:import": "元数据管理-智能导入",
            "element:metadata:sync": "元数据管理-同步至RAGFlow",
            "element:metadata:edit": "元数据管理-编辑数据集",
            "element:metadata:delete": "元数据管理-删除数据集",
            "element:metadata:view_yaml": "元数据管理-查看YAML",
            "element:metadata:edit_table": "元数据管理-配置表结构",
            "element:metadata:delete_table": "元数据管理-删除物理表",

            # 案例集
            "element:chatbi_example:audit": "案例集管理-审核反馈",
            "element:chatbi_example:sync": "案例集管理-同步至RAGFlow",
            "element:chatbi_example:delete": "案例集管理-废弃删除",
        }

        # 菜单名以菜单表为准：管理员在「菜单管理」里改名后，个人中心的权限展示要跟着变。
        # 表不存在时 get_nodes() 会回退内置种子，这里的合并是幂等的，不会让展示名缺项。
        try:
            exact_permission_mapping.update(await self._load_menu_display_names())
        except Exception:  # noqa: BLE001 - 展示名永远不该打断权限查询
            logger.warning("加载菜单显示名失败，回退内置映射", exc_info=True)

        # 中文映射字典 (支持多种可能的键名格式)
        menu_name_mapping = {
            'dashboard': '仪表盘',
            'Dashboard': '仪表盘',
            'ai_chat': '智能助手',
            'metadata': '元数据管理',
            'Metadata': '元数据管理',
            'data_sources': '数据源管理',
            'agent_management': '智能体中心',
            'Agent_management': '智能体中心',
            'skills_management': '技能工作台',
            'mcp_management': 'MCP 工具集',
            'mcp_service': 'MCP 服务台',
            'agent_debug': '智能体调试',
            'playground': '接口调试台',
            'chat_logs': '聊天日志',
            'chatbi_examples': '案例集管理',
            'users': '用户管理',
            'roles': '角色管理',
            'config': '系统配置',
            'audit': '审计日志',
            'prompts': '提示词工坊',
            'widget_debug': '组件调试台',
            'task_center': '任务调度台',
            'knowledge_management': '知识库管理',
            'knowledge_retrieval_test': '检索测试',
            'memory_management': '记忆工作台'
        }

        element_module_mapping = {
            'metadata': '元数据管理',
            'agent': '智能体中心',
            'chat_logs': '聊天日志',
            'prompts': '提示词工坊',
            'user': '用户管理',
            'role': '角色管理',
            'system': '系统管理',
            'chatbi_example': '案例集管理',
            'knowledge': '知识库开发平台',
            'memory': '记忆工作台',
            'skills': '技能工作台',
            'mcp_service': 'MCP服务台',
            'task': '任务调度台'
        }

        element_action_mapping = {
            'import': '导入',
            'edit': '编辑',
            'delete': '删除',
            'create': '创建',
            'view_yaml': '查看YAML',
            'edit_table': '编辑表',
            'delete_table': '删除表',
            'export': '导出',
            'optimize': '优化',
            'view_key': '查看密钥',
            'reset_key': '重置密钥',
            'config_save': '保存配置',
            'audit': '审核',
            'sync': '同步',
            'upload_document': '上传文档',
            'delete_document': '删除文档',
            'parse_document': '解析文档',
            'test_retrieval': '检索测试',
            'admin': '管理与审核',
            'manage': '管理',
            'overview': '服务总览',
            'config': '配置',
            'client': '外部Client',
            'capability': '能力与Scope',
            'grant': '用户授权',
            'read': '查看'
        }

        # 1. Agents
        if perm_set.agents:
            stmt = select(AIAgent).where(AIAgent.id.in_(perm_set.agents))
            agents = (await self.db.execute(stmt)).scalars().all()
            details.agents = [
                ResourceDetail(
                    id=a.id,
                    name=a.name,
                    display_name=a.display_name,
                    description=a.description
                ) for a in agents
            ]

        # 2. Metadata (Datasets)
        if perm_set.metadata:
            # Metadata IDs are Strings in permission set but Ints in DB
            meta_ids = []
            for mid in perm_set.metadata:
                if mid.isdigit():
                    meta_ids.append(int(mid))

            if meta_ids:
                stmt = select(MetaDataset).where(MetaDataset.id.in_(meta_ids))
                metas = (await self.db.execute(stmt)).scalars().all()
                details.metadata = [
                    ResourceDetail(
                        id=str(m.id),
                        name=m.name,
                        display_name=m.display_name,
                        description=m.description or ""
                    ) for m in metas
                ]

        # 3. APIs
        if perm_set.apis:
            stmt = select(SysApiTool).where(SysApiTool.id.in_(perm_set.apis))
            apis = (await self.db.execute(stmt)).scalars().all()
            details.apis = [
                ResourceDetail(
                    id=a.id,
                    name=a.name,
                    display_name=a.name, # API tools might not have separate display_name
                    description=a.description
                ) for a in apis
            ]

        # 4. 知识库（RAGFlow dataset id → knowledge_base_metadata 名称）
        if perm_set.datasets:
            ds_ids = [(d or "").strip() for d in perm_set.datasets if (d or "").strip()]
            meta_by_rag_id: dict[str, KnowledgeBaseMetadata] = {}
            if ds_ids:
                stmt = select(KnowledgeBaseMetadata).where(
                    KnowledgeBaseMetadata.ragflow_dataset_id.in_(ds_ids),
                    KnowledgeBaseMetadata.status != "deleted",
                )
                rows = (await self.db.execute(stmt)).scalars().all()
                meta_by_rag_id = {
                    m.ragflow_dataset_id: m for m in rows if m.ragflow_dataset_id
                }

            details.datasets = []
            for ds_id in perm_set.datasets:
                token = (ds_id or "").strip()
                if not token:
                    continue
                local = meta_by_rag_id.get(token)
                if local:
                    display_name = (local.name or "").strip() or token
                    description = (local.description or "").strip() or None
                else:
                    display_name = token
                    description = "未同步平台元数据"
                details.datasets.append(
                    ResourceDetail(
                        id=token,
                        name=token,
                        display_name=display_name,
                        description=description,
                    )
                )

        # 5. Menus (Static or logical IDs)
        if perm_set.menus:
            details.menus = []
            for m_id in perm_set.menus:
                # 优先查全量精确映射表
                if m_id in exact_permission_mapping:
                    display_name = exact_permission_mapping[m_id]
                else:
                    # 提取菜单键名
                    menu_key = m_id.split(':')[-1] if ':' in m_id else m_id
                    display_name = menu_name_mapping.get(menu_key, menu_name_mapping.get(m_id, menu_key.replace('_', ' ').capitalize()))
                details.menus.append(
                    ResourceDetail(
                        id=m_id,
                        name=m_id,
                        display_name=display_name
                    )
                )

        # 6. Elements
        if perm_set.elements:
            details.elements = []
            for e_id in perm_set.elements:
                # 1. 优先查全量精确映射表
                if e_id in exact_permission_mapping:
                    display_name = exact_permission_mapping[e_id]
                else:
                    # 2. 动态多段式规则拆分: element:模块:操作[:子操作...]
                    parts = e_id.split(':')
                    if len(parts) >= 3:
                        module = parts[1]  # 模块
                        action_tokens = parts[2:]  # 操作与子操作
                        module_name = element_module_mapping.get(module, module)

                        # 对每个操作词进行翻译并拼接
                        translated_actions = [element_action_mapping.get(tok, tok) for tok in action_tokens]
                        action_name = "".join(translated_actions) if len(translated_actions) > 1 else translated_actions[0]
                        display_name = f"{module_name}-{action_name}"
                    else:
                        display_name = e_id.split(':')[-1]

                details.elements.append(
                    ResourceDetail(
                        id=e_id,
                        name=e_id,
                        display_name=display_name
                    )
                )

        # 7. Forbidden Tools
        if perm_set.forbidden_tools:
            details.forbidden_tools = [
                ResourceDetail(id=t_id, name=t_id, display_name=t_id)
                for t_id in perm_set.forbidden_tools
            ]

        # 8. Forbidden Commands
        if perm_set.forbidden_commands:
            details.forbidden_commands = [
                ResourceDetail(id=cmd, name=cmd, display_name=cmd)
                for cmd in perm_set.forbidden_commands
            ]

        # 9. Forbidden Skills（禁用公共技能）
        if perm_set.forbidden_skills:
            details.forbidden_skills = [
                ResourceDetail(id=skill_id, name=skill_id, display_name=skill_id)
                for skill_id in perm_set.forbidden_skills
            ]

        if perm_set.models:
            model_stmt = select(AIModel.model_id, AIModel.name).where(
                AIModel.model_id.in_(perm_set.models)
            )
            for model_id, model_name in (await self.db.execute(model_stmt)).all():
                label = str(model_name or model_id)
                details.models.append(
                    ResourceDetail(id=str(model_id), name=label, display_name=label)
                )

        return details

    def _aggregate_permissions(self, perm_set: PermissionSet, perms: list[ResourcePermission]):
        for p in perms:
            if p.resource_type == "agent":
                if p.resource_id not in perm_set.agents:
                    perm_set.agents.append(p.resource_id)
            elif p.resource_type == "dataset":
                if p.resource_id not in perm_set.datasets:
                    perm_set.datasets.append(p.resource_id)
            elif p.resource_type == "api":
                if p.resource_id not in perm_set.apis:
                    perm_set.apis.append(p.resource_id)
            elif p.resource_type == "metadata":
                if p.resource_id not in perm_set.metadata:
                    perm_set.metadata.append(p.resource_id)
            elif p.resource_type == "menu":
                if p.resource_id not in perm_set.menus:
                    perm_set.menus.append(p.resource_id)
            elif p.resource_type == "element":
                # 历史脏数据兼容：旧前端「勾菜单 = 全选」的级联把子菜单 id
                # （`menu:*`）一并写进了 elements。侧栏（`hasMenuPerm`）与后端
                # `require_permission("menu", ...)` 都只查 menus，若不在这里归位，
                # 就会「权限树里显示已勾选、菜单却不显示、接口 403」。
                # 数据修复迁移（V175 / V76）执行前靠这一层兜底，执行后是空操作。
                if is_menu_permission_id(p.resource_id):
                    if p.resource_id not in perm_set.menus:
                        perm_set.menus.append(p.resource_id)
                elif p.resource_id not in perm_set.elements:
                    perm_set.elements.append(p.resource_id)
            elif p.resource_type == "forbidden_tool":
                if p.resource_id not in perm_set.forbidden_tools:
                    perm_set.forbidden_tools.append(p.resource_id)
            elif p.resource_type == "forbidden_command":
                if p.resource_id not in perm_set.forbidden_commands:
                    perm_set.forbidden_commands.append(p.resource_id)
            elif p.resource_type == "forbidden_skill":
                if p.resource_id not in perm_set.forbidden_skills:
                    perm_set.forbidden_skills.append(p.resource_id)
            elif p.resource_type == "model":
                # 聊天模型白名单：空 = 不限制（由 _resolve_allowed_model_ids 统一判定）
                if p.resource_id not in perm_set.models:
                    perm_set.models.append(p.resource_id)

    @staticmethod
    def _resolve_allowed_model_ids(user_role: str, model_ids: list[str]) -> Optional[set[str]]:
        """聊天模型白名单唯一判据：None = 不限制。

        与 element:/menu: 的「列出才有权限」相反——未配置（含并集为空）表示不限制；
        管理员直通，避免管理员把自己锁在门外。
        """
        if str(user_role or "").strip() == "admin":
            return None
        normalized = {str(model_id).strip() for model_id in model_ids if str(model_id).strip()}
        return normalized or None

    async def get_chat_allowed_model_ids(self, user_id) -> Optional[set[str]]:
        """返回该用户聊天可手动切换的模型集合；None 表示不限制（未配置）。"""
        try:
            parsed_uid = int(user_id)
        except (TypeError, ValueError):
            parsed_uid = user_id

        role_stmt = select(User.role).where(User.id == parsed_uid)
        user_role = (await self.db.execute(role_stmt)).scalar_one_or_none()
        if str(user_role or "").strip() == "admin":
            return None

        # 与其它读取入口一样限定**全局**作用域。model 是**白名单**（空 = 不限制，与
        # menu/element 的方向相反），所以这里的口径必须谨慎：加上 `agent_id IS NULL` 意味着
        # 智能体级 model 行**被忽略**——方向是"更严"，不会把用户在 A 智能体获准的模型
        # 带进 B。若将来真要支持智能体级模型白名单，必须同时把作用域透传进来（而不是
        # 像现在这样把各智能体的白名单并集成"处处可用"——那是越权方向）。
        model_stmt = (
            select(distinct(ResourcePermission.resource_id))
            .outerjoin(
                UserRoleRelation,
                ResourcePermission.role_id == UserRoleRelation.role_id,
            )
            .where(
                ResourcePermission.enabled == True,  # noqa: E712 - 与文件内既有写法一致
                ResourcePermission.resource_type == "model",
                or_(
                    ResourcePermission.user_id == parsed_uid,
                    UserRoleRelation.user_id == parsed_uid,
                ),
                *(await self._agent_scope_filters(None)),
            )
        )
        rows = (await self.db.execute(model_stmt)).scalars().all()
        return self._resolve_allowed_model_ids(user_role, list(rows))

    async def _exclude_private_datasets(self, dataset_ids):
        """剔除「个人私有」知识库 id：授权对它无效，写进去只会留下永不生效的记录。

        判定侧本来就会跳过 private（见 resolve_knowledge_dataset_access），
        前端也已禁用这类项的勾选；这里再兜一层，挡住旧前端/脚本直接调接口。
        """
        ids = [str(i) for i in (dataset_ids or []) if str(i).strip()]
        if not ids:
            return []
        rows = (
            await self.db.execute(
                select(
                    KnowledgeBaseMetadata.ragflow_dataset_id,
                    KnowledgeBaseMetadata.visibility,
                ).where(
                    KnowledgeBaseMetadata.ragflow_dataset_id.in_(ids),
                    KnowledgeBaseMetadata.status != "deleted",
                )
            )
        ).all()
        private_ids = {
            str(row[0])
            for row in rows
            if normalize_knowledge_visibility(row[1]) == VISIBILITY_PRIVATE
        }
        if not private_ids:
            return ids
        return [i for i in ids if i not in private_ids]

    async def update_user_permissions(self, user_id: int, updates: PermissionUpdate):
        """Update DIRECT user permissions"""
        try:
            types_to_update = ["agent", "dataset", "api", "metadata", "menu", "element"]
            # 未显式提交的字段一律不触碰对应记录：旧 bundle / 外部脚本整份 PUT 时会缺这些字段，
            # 若照旧重建就会静默清空禁用项与模型白名单（这两类"少了等于放开"，代价最大）。
            for field, resource_type in (
                ("forbidden_tools", "forbidden_tool"),
                ("forbidden_commands", "forbidden_command"),
                ("forbidden_skills", "forbidden_skill"),
            ):
                if field in updates.model_fields_set:
                    types_to_update.append(resource_type)
            if "models" in updates.model_fields_set:
                types_to_update = [*types_to_update, "model"]
            # 只删**全局**行：ORM 刻意不映射 agent_id（防"代码先于迁移上线"时接口整体 503），
            # 生成的 DELETE 因而没有作用域条件。迁移执行后若不显式加 agent_id IS NULL，
            # 这一句会把该用户在所有智能体上的限制一起删掉——管理员改一次菜单就静默丢失。
            await self.db.execute(
                delete(ResourcePermission).where(
                    ResourcePermission.user_id == user_id,
                    ResourcePermission.resource_type.in_(types_to_update),
                    *(await self._agent_scope_filters(None)),
                )
            )

            new_perms = []
            for agent_id in updates.agents:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="agent", resource_id=agent_id))
            for dataset_id in await self._exclude_private_datasets(updates.datasets):
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="dataset", resource_id=dataset_id))
            for api_id in updates.apis:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="api", resource_id=api_id))
            for meta_id in updates.metadata:
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="metadata", resource_id=str(meta_id)))
            # 同一个 id 可能同时出现在 menus 与 elements（旧前端整份提交时会把并集都放进去），
            # 归一后类型相同，必须去重，否则一次保存会写出两条一样的授权行。
            seen_menu_element_ids: set[str] = set()
            for menu_id in updates.menus:
                if is_menu_group_id(menu_id):
                    # 旧前端/脚本会把分组码一起提交上来，这里丢弃而不是落库
                    continue
                if menu_id in seen_menu_element_ids:
                    continue
                seen_menu_element_ids.add(menu_id)
                new_perms.append(ResourcePermission(
                    user_id=user_id,
                    resource_type=normalize_permission_resource_type(menu_id, "menu"),
                    resource_id=menu_id,
                ))
            for element_id in updates.elements:
                if is_menu_group_id(element_id):
                    continue
                if element_id in seen_menu_element_ids:
                    continue
                seen_menu_element_ids.add(element_id)
                new_perms.append(ResourcePermission(
                    user_id=user_id,
                    resource_type=normalize_permission_resource_type(element_id, "element"),
                    resource_id=element_id,
                ))
            # 防御旧前端：它回显「用户 ∪ 角色」的并集并整份提交，若不剔除角色级已有的项，
            # 角色级禁用会被固化成用户级记录（角色侧取消后用户仍被禁）。新前端只提交直配项，
            # 因此这里的剔除对它是空操作；对旧前端则是"不制造不可回收的黑名单"的兜底。
            _uid = int(user_id) if str(user_id).isdigit() else user_id
            derived_forbidden = {
                rt: await self._role_derived_forbidden_ids(_uid, rt)
                for rt in ("forbidden_tool", "forbidden_command", "forbidden_skill")
            }
            for t_id in updates.forbidden_tools:
                if t_id in derived_forbidden["forbidden_tool"]:
                    continue
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="forbidden_tool", resource_id=t_id))
            for cmd in updates.forbidden_commands:
                if cmd in derived_forbidden["forbidden_command"]:
                    continue
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="forbidden_command", resource_id=cmd))
            for skill_id in updates.forbidden_skills:
                if skill_id in derived_forbidden["forbidden_skill"]:
                    continue
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="forbidden_skill", resource_id=skill_id))
            for model_id in (updates.models if "models" in updates.model_fields_set else []):
                new_perms.append(ResourcePermission(user_id=user_id, resource_type="model", resource_id=str(model_id)))

            if new_perms:
                self.db.add_all(new_perms)

            await self.db.commit()
            await self._invalidate_user_cache(user_id)

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update permissions for user {user_id}: {e}")
            raise e

    async def update_user_roles(self, user_id: int, role_ids: list[int]):
        """Update User Business Roles"""
        try:
            # Delete old relations
            await self.db.execute(
                delete(UserRoleRelation).where(UserRoleRelation.user_id == user_id)
            )

            # Insert new
            relations = [UserRoleRelation(user_id=user_id, role_id=rid) for rid in role_ids]
            if relations:
                self.db.add_all(relations)

            await self.db.commit()
            await self._invalidate_user_cache(user_id)
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update roles for user {user_id}: {e}")
            raise e

    async def get_role_permissions(self, role_id: int) -> UserPermissionsResponse:
        """Get permissions assigned to a Role

        只返回**全局**权限（``agent_id IS NULL``）：这是角色权限弹框的回显来源，
        智能体级行一旦混进来，管理员点一次保存就会把"只禁某个智能体"固化成
        "该角色全局禁用"——黑名单收不回来。作用域过滤与
        ``get_user_permissions`` / ``get_user_direct_permissions`` 保持同一实现。
        """
        stmt = select(ResourcePermission).where(
            ResourcePermission.role_id == role_id,
            ResourcePermission.enabled == True,
            *(await self._agent_scope_filters(None)),
        )
        perms = (await self.db.execute(stmt)).scalars().all()

        perm_set = PermissionSet()
        self._aggregate_permissions(perm_set, perms)

        # Get role code/name
        role = await self.db.get(Role, role_id)
        role_name = [role.code] if role else []

        return UserPermissionsResponse(
            roles=role_name,
            permissions=perm_set
        )

    async def update_role_permissions(self, role_id: int, updates: PermissionUpdate):
        """Update Role Permissions"""
        try:
            types_to_update = ["agent", "dataset", "api", "metadata", "menu", "element"]
            # 未显式提交的字段一律不触碰对应记录：旧 bundle / 外部脚本整份 PUT 时会缺这些字段，
            # 若照旧重建就会静默清空禁用项与模型白名单（这两类"少了等于放开"，代价最大）。
            for field, resource_type in (
                ("forbidden_tools", "forbidden_tool"),
                ("forbidden_commands", "forbidden_command"),
                ("forbidden_skills", "forbidden_skill"),
            ):
                if field in updates.model_fields_set:
                    types_to_update.append(resource_type)
            if "models" in updates.model_fields_set:
                types_to_update = [*types_to_update, "model"]
            # 同上：角色侧同样只能删全局行，否则会连带删掉该角色在各智能体上的限制。
            await self.db.execute(
                delete(ResourcePermission).where(
                    ResourcePermission.role_id == role_id,
                    ResourcePermission.resource_type.in_(types_to_update),
                    *(await self._agent_scope_filters(None)),
                )
            )

            new_perms = []
            for agent_id in updates.agents:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="agent", resource_id=agent_id))
            for dataset_id in await self._exclude_private_datasets(updates.datasets):
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="dataset", resource_id=dataset_id))
            for api_id in updates.apis:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="api", resource_id=api_id))
            for meta_id in updates.metadata:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="metadata", resource_id=str(meta_id)))
            seen_menu_element_ids: set[str] = set()
            for menu_id in updates.menus:
                if is_menu_group_id(menu_id):
                    # 旧前端/脚本会把分组码一起提交上来，这里丢弃而不是落库
                    continue
                if menu_id in seen_menu_element_ids:
                    continue
                seen_menu_element_ids.add(menu_id)
                new_perms.append(ResourcePermission(
                    role_id=role_id,
                    resource_type=normalize_permission_resource_type(menu_id, "menu"),
                    resource_id=menu_id,
                ))
            for element_id in updates.elements:
                if is_menu_group_id(element_id):
                    continue
                if element_id in seen_menu_element_ids:
                    continue
                seen_menu_element_ids.add(element_id)
                new_perms.append(ResourcePermission(
                    role_id=role_id,
                    resource_type=normalize_permission_resource_type(element_id, "element"),
                    resource_id=element_id,
                ))
            for t_id in updates.forbidden_tools:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="forbidden_tool", resource_id=t_id))
            for cmd in updates.forbidden_commands:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="forbidden_command", resource_id=cmd))
            for skill_id in updates.forbidden_skills:
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="forbidden_skill", resource_id=skill_id))
            for model_id in (updates.models if "models" in updates.model_fields_set else []):
                new_perms.append(ResourcePermission(role_id=role_id, resource_type="model", resource_id=str(model_id)))

            if new_perms:
                self.db.add_all(new_perms)

            await self.db.commit()

            # Heavy operation: Invalidate cache for ALL users who have this role
            # For now, we might just accept 1 hour delay or try to find them.
            # Efficient way: Select user_ids from UserRoleRelation where role_id = role_id
            user_ids_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == role_id)
            user_ids = (await self.db.execute(user_ids_stmt)).scalars().all()

            for uid in user_ids:
                await self._invalidate_user_cache(uid)

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update permissions for role {role_id}: {e}")
            raise e

    @staticmethod
    async def purge_all_permission_cache() -> int:
        """清空所有用户的权限缓存（含各智能体作用域键）。

        只用于「权限码本身改名」这类全局性变更：外链菜单改 slug 时 `menu:ext:<slug>`
        跟着改名，授权行已一并迁移，但每个用户缓存里的 `menus` 仍是旧码，残留到 TTL
        过期（1 小时）就会表现为「改完 slug，普通用户侧栏里的菜单凭空消失」。
        这里无法低成本枚举"谁持有这条授权"，因此整体清空——改 slug 是低频管理操作，
        每人重建一次缓存（一条 SQL）的比重建一个错误状态划算。
        """
        redis = await get_redis()
        if not redis:
            return 0
        deleted = 0
        for pattern in (
            "sys:auth:permissions:v7:user:*",
            "sys:auth:permissions:v6:user:*",
            "sys:auth:permissions:v5:user:*",
            "sys:auth:permissions:v4:user:*",
            "sys:auth:permissions:v2:user:*",
        ):
            # SCAN 而非 KEYS：大 key 空间下 KEYS 会阻塞 Redis
            async for key in redis.scan_iter(match=pattern, count=500):
                await redis.delete(key)
                deleted += 1
        return deleted

    async def _invalidate_user_cache(self, user_id: int):
        redis = await self._get_redis()
        if redis:
            # v7 全局键（v7 起：菜单 id 归位，旧缓存需重建）
            await redis.delete(await self._get_cache_key(user_id))
            # v7 的智能体作用域键是一族（同一用户 × N 个智能体），必须整族清掉：
            # 只删全局键会让「某智能体里刚取消的禁用」在 TTL 内继续生效。
            # 用 SCAN 而不是 KEYS —— 后者在大 key 空间会阻塞 Redis。
            pattern = f"sys:auth:permissions:v7:user:{user_id}:agent:*"
            async for key in redis.scan_iter(match=pattern, count=200):
                await redis.delete(key)
            # 兼容历史版本 key，避免旧缓存继续命中（v6 里 elements 仍混着菜单 id）
            await redis.delete(f"sys:auth:permissions:v2:user:{user_id}")
            await redis.delete(f"sys:auth:permissions:v4:user:{user_id}")
            await redis.delete(f"sys:auth:permissions:v5:user:{user_id}")
            await redis.delete(f"sys:auth:permissions:v6:user:{user_id}")
            try:
                from app.services.ai.config import AgentConfigProvider
                await AgentConfigProvider.invalidate_dataset_menu_cache(user_id=user_id)
            except Exception as ex:
                logger.warning(f"Failed to invalidate dataset menu cache for user {user_id}: {ex}")
        # 技能禁用是**另一套**进程内缓存（policy.py，TTL 60s、prime 节流 10s），
        # Redis 权限键清掉不会碰它。不在这里清，管理员刚禁用的公共技能在最长 60 秒内
        # 其正文仍可被读取——"禁了还在生效"的窗口虽短，但语义上是漏洞。
        try:
            from app.services.ai.skills.policy import clear_forbidden_skill_cache

            clear_forbidden_skill_cache()
        except Exception as ex:
            logger.warning(f"Failed to clear forbidden skill cache: {ex}")
        try:
            from app.services.auth_service import AuthService
            # 只失效认证缓存（角色/权限变更立即生效），**不吊销会话**：这里服务的是
            # 日常权限变更（含用户新建数据集时给自己授权、按角色批量授权），吊销会话
            # 会把操作者自己乃至整批用户踢到登录页。需要立即登出的管理路径各自显式传
            # revoke_sessions=True。
            await AuthService.invalidate_user_auth_cache(
                user_id, db=self.db, revoke_sessions=False
            )
        except Exception as ex:
            logger.warning(f"Failed to invalidate auth api_key cache for user {user_id}: {ex}")

    async def _invalidate_role_cache(self, role_id: int) -> None:
        """角色级变更后，失效该角色**所有成员**的权限缓存。

        角色级智能体限制只影响拥有该角色的用户，因此按成员精确失效而不是清全局——
        同时也要覆盖他们的智能体作用域键族（见 ``_invalidate_user_cache``）。
        """
        user_ids_stmt = select(UserRoleRelation.user_id).where(
            UserRoleRelation.role_id == int(role_id)
        )
        for uid in (await self.db.execute(user_ids_stmt)).scalars().all():
            await self._invalidate_user_cache(uid)

    async def invalidate_cached_permissions_for_users(self, user_ids: Iterable[int]) -> None:
        """Clear permission cache for users (e.g. after role membership changes outside update_user_roles)."""
        for uid in set(user_ids):
            await self._invalidate_user_cache(uid)

    async def check_permission(self, user_id: int, resource_type: str, resource_id: str) -> bool:
        """
        Check if user has access to a specific resource.
        Admins bypass this check.
        """
        perms = await self.get_user_permissions(user_id)

        # Admin bypass
        if "admin" in perms.roles:
            return True

        # Check specific permission
        if resource_type == "agent":
            # 1. Explicit Permission Check (for System Agents)
            if resource_id in perms.permissions.agents:
                return True

            # 2. Ownership Check (for User Created Agents)
            agent = await self.db.get(AIAgent, resource_id)
            user = await self.db.get(User, user_id)

            if agent and user:
                # If System Agent: Must have explicit permission (already failed above)
                if agent.is_system:
                    return False

                # If User Agent: Must be Owner AND Enabled
                if agent.created_by == user.user_name and agent.is_enabled:
                    return True

            return False

        elif resource_type == "dataset":
            return resource_id in perms.permissions.datasets
        elif resource_type == "api":
            from app.core.v1_api_access import expand_api_permission_candidates

            granted = set(perms.permissions.apis or [])
            if not granted:
                return False
            return bool(granted & expand_api_permission_candidates(resource_id))
        elif resource_type == "metadata":
            return resource_id in perms.permissions.metadata
        elif resource_type == "element":
            return resource_id in perms.permissions.elements
        elif resource_type == "menu":
            return resource_id in perms.permissions.menus

        return False

    async def check_permission_by_api_key(self, api_key: str, resource_type: str, resource_id: str) -> bool:
        """
        Check if user has access to a specific resource using API Key.
        Admins bypass this check.
        """
        from app.services.auth_service import AuthService

        user = await AuthService.verify_api_key(api_key, self.db)
        if not user:
            return False

        return await self.check_permission(
            user_id=int(user["user_id"]),
            resource_type=resource_type,
            resource_id=resource_id
        )
