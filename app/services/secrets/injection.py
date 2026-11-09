"""注入接线：把「解析出来的凭据」放进真正发出去的那条请求里。

``resolver.py`` 只负责判定和取值，这一层负责**落在哪个字段上**。目前接了
两个场景：

* ``api_tool`` —— 通用 API 工具的请求头（``generic_api._perform_request``）
* ``mcp``      —— MCP 出站请求头（``mcp_client`` 建会话时）

两个场景都遵守同一条规则：**只填「本来就没有值」的位置**。手填了真实值的
header 一律不动——用户既然手写了，说明他要的就是那个值；想换成凭据中心的值，
先把那格清空再绑定。

脚本 env 与定时任务还没接，原因写在 ``docs/superpowers/specs/`` 的分期里：
脚本能 ``print(os.environ)``，值一旦进子进程就没有任何技术手段能拦住它回显，
必须在「凭据中心已经稳定跑一段时间」之后再开，而且默认关。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping
from urllib.parse import urlparse

from app.services.secrets.policy import (
    CONSUMER_API_TOOL,
    CONSUMER_MCP,
    SCENE_API_TOOL,
    SCENE_MCP,
)
from app.services.secrets.resolver import secret_resolver
from app.services.ai.tools.tool_credentials import CREDENTIAL_PLACEHOLDER_PREFIX

logger = logging.getLogger(__name__)

#: 这些值等于「这里还没填」，值得去解析绑定的凭据来补。
_EMPTY_MARKERS = frozenset({"", "****", "******", "••••••••••••"})

#: 拒绝原因 → 给人看的一句话。
#:
#: 光在日志里记一笔是不够的：调用方拿到的是对方的 401，看不出是自己那条凭据
#: **过期了**还是**被停用了**。所以注入层要把原因带回去，拼进最终错误信息。
DENIAL_REASONS = {
    "disabled": "凭据已停用",
    "expired": "凭据已过期",
    "denied_by_scenario": "这条凭据没有授权给当前场景",
    "denied_by_host": "目标域名不在白名单里",
    "denied_by_grant": "凭据还没有授权给你",
    "denied_by_script_policy": "平台或你的偏好关闭了脚本环境变量注入",
    "denied_by_binding": "没有找到绑定",
}


def denial_text(reason: str | None) -> str:
    if not reason:
        return "未绑定凭据"
    return DENIAL_REASONS.get(reason, reason)


def _record(denials: dict[str, str] | None, slot: str, reason: str | None) -> None:
    """把「这个槽位为什么没注入」记下来，由调用方决定怎么报。

    **不记「没有绑定」**：那是常态（绝大多数 header 本来就不绑凭据），记下来会
    让调用方误以为出了问题。调用方关心的是「绑了却用不了」——停用、过期、
    场景不符、域名不在白名单。
    """
    if denials is None or reason in (None, "denied_by_binding"):
        return
    denials[slot] = denial_text(reason)


def _looks_unfilled(value: Any) -> bool:
    """判断一个 header 值是不是「待填」。"""
    if value is None:
        return True
    text = str(value).strip()
    if text in _EMPTY_MARKERS:
        return True
    if text.startswith(CREDENTIAL_PLACEHOLDER_PREFIX):
        return True
    # 全是掩码字符（前端回显的脱敏值）
    return bool(text) and set(text) <= {"*", "•"}


def host_of(url: str | None) -> str | None:
    """从 URL 模板里取出目标域名，供域名白名单校验。"""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    return parsed.hostname or None


def _current_user(user_info: Mapping[str, Any] | None) -> tuple[str, str]:
    """取 (user_id, dept_code)。显式传入优先，否则按项目既有顺序读上下文。

    读法与 ``user_placeholders.current_user_variables`` 保持一致：先
    ``AgentContext.user_dimensions``（后台执行、定时任务的载体），再
    ``current_user_info``（HTTP 请求的载体）。

    只读一处是不够的：两条链路各自只设其中一个。漏掉的那条不会报错，只会
    拿不到 user_id，于是绑定全部解析失败、工具退回「凭据没填」——症状看起来
    像用户没配凭据，实际是上下文没接上。
    """
    if user_info:
        return (
            str(user_info.get("user_id") or ""),
            str(user_info.get("dept_code") or ""),
        )

    try:
        from app.core.context import get_current_agent_context

        context = get_current_agent_context()
        dimensions = getattr(context, "user_dimensions", None) if context else None
        if dimensions:
            # dimensions 是展平的：key 直接是 user_id / dept_code，不是嵌套结构
            uid = dimensions.get("user_id")
            if uid in (None, ""):
                uid = dimensions.get("id")
            if uid not in (None, ""):
                return str(uid), str(dimensions.get("dept_code") or "")
    except Exception:  # noqa: BLE001
        logger.debug("[SecretInject] 读取 AgentContext 用户维度失败", exc_info=True)

    try:
        from app.utils.context import current_user_info

        info = current_user_info.get() or {}
        return (
            str(info.get("user_id") or ""),
            str(info.get("dept_code") or ""),
        )
    except Exception:  # noqa: BLE001 - 没有上下文就是没有，不用报错
        return "", ""


def _current_trace_id() -> str | None:
    try:
        from app.core.context import get_current_agent_context

        context = get_current_agent_context()
        return getattr(context, "trace_id", None) if context else None
    except Exception:  # noqa: BLE001
        return None


async def _max_inject_per_call() -> int:
    """单次调用最多注入几条凭据（平台策略 ``secret_policy_max_inject_per_call``）。

    这个值以前只写在系统参数页上、没有任何代码读它——界面上写着「防止一次请求
    把全部凭据一起带走」，实际一条不限。配置项不生效比没有更危险：它给出的是
    虚假的安全感。读失败退回默认 3，绝不放行。
    """
    from app.services.secrets.resolver import DEFAULT_MAX_INJECT, SecretResolver

    try:
        _, _, limit = await SecretResolver._platform_flags()
        return limit
    except Exception:  # noqa: BLE001
        logger.warning("[SecretInject] 读取注入条数上限失败，按默认值处理", exc_info=True)
        return DEFAULT_MAX_INJECT


async def apply_api_tool_bindings(
    config: Any,
    headers: dict[str, str],
    *,
    user_info: Mapping[str, Any] | None = None,
    denials: dict[str, str] | None = None,
) -> dict[str, str]:
    """把绑定的凭据填进 API 工具的请求头。

    只处理「值是空的 / 是 ``${CREDENTIAL:...}`` 占位符」且有绑定记录的 header。
    任何异常都只告警、返回原 headers——凭据中心挂了不能把工具调用一起拖下水，
    它会退回「凭据未填」的既有报错路径，用户能看到明确原因。
    """
    if not headers:
        return headers

    user_id, dept_code = _current_user(user_info)
    if not user_id:
        return headers

    target = host_of(getattr(config, "url_template", None))
    trace_id = _current_trace_id()
    resolved = dict(headers)
    limit = await _max_inject_per_call()
    injected = 0

    for name in list(resolved):
        if injected >= limit:
            logger.warning(
                "[SecretInject] API 工具 %s 命中注入上限 %s 条，其余 header 不再注入",
                getattr(config, "name", "?"),
                limit,
            )
            break
        if not _looks_unfilled(resolved.get(name)):
            continue
        try:
            result = await secret_resolver.resolve(
                user_id=user_id,
                scene=SCENE_API_TOOL,
                consumer_type=CONSUMER_API_TOOL,
                consumer_id=str(getattr(config, "id", "") or ""),
                slot=f"header:{name}",
                target_host=target,
                trace_id=trace_id,
                dept_code=dept_code,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SecretInject] API 工具 %s 的 %s 解析失败: %s", getattr(config, "name", "?"), name, exc)
            continue

        if result.ok and result.value is not None:
            resolved[name] = result.value.reveal()
            injected += 1
        else:
            # 「没有绑定」是常态，不必刷日志；其它拒绝原因值得记一笔，
            # 否则用户只会看到「凭据没填」，完全不知道是域名/场景/授权拦下的。
            if result.denial not in (None, "denied_by_binding"):
                logger.info(
                    "[SecretInject] API 工具 %s 的 %s 被拒绝: %s",
                    getattr(config, "name", "?"),
                    name,
                    result.denial,
                )
            # 原因要带回调用层：停用/过期和「没绑」看起来都是「值是空的」，
            # 但用户要做的动作完全不同。
            _record(denials, f"header:{name}", result.denial)

    return resolved


async def apply_api_tool_param_bindings(
    config: Any,
    params: dict[str, Any],
    *,
    method: str,
    user_info: Mapping[str, Any] | None = None,
    denials: dict[str, str] | None = None,
) -> dict[str, Any]:
    """把「声明为来自凭据」的参数补上值。

    header 是配置里的固定行，绑定天然；参数不行——它的值由模型运行时填，
    平台分不清「模型没填」和「模型编了一个值」。所以只有那些在
    ``x-nanzi-system-params`` 里标了 ``source: credential`` 的参数才走这里，
    它们同时已从模型可见的 properties 里消失，模型既看不到也改不了。

    槽位按 HTTP 语义落成 ``query:名字``（GET）或 ``body:名字``（其它方法），
    和绑定表里存的一致。
    """
    from app.services.ai.tools.system_params import credential_param_names

    names = credential_param_names(getattr(config, "parameter_schema", None))
    if not names:
        return params

    user_id, dept_code = _current_user(user_info)
    if not user_id:
        return params

    prefix = "query" if (method or "GET").upper() == "GET" else "body"
    target = host_of(getattr(config, "url_template", None))
    trace_id = _current_trace_id()
    resolved = dict(params)
    limit = await _max_inject_per_call()
    injected = 0

    for name in names:
        if injected >= limit:
            logger.warning(
                "[SecretInject] API 工具 %s 命中注入上限 %s 条，参数 %s 未注入",
                getattr(config, "name", "?"), limit, name,
            )
            break
        try:
            result = await secret_resolver.resolve(
                user_id=user_id,
                scene=SCENE_API_TOOL,
                consumer_type=CONSUMER_API_TOOL,
                consumer_id=str(getattr(config, "id", "") or ""),
                slot=f"{prefix}:{name}",
                target_host=target,
                trace_id=trace_id,
                dept_code=dept_code,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SecretInject] API 工具 %s 的参数 %s 解析失败: %s",
                getattr(config, "name", "?"), name, exc,
            )
            continue

        if result.ok and result.value is not None:
            resolved[name] = result.value.reveal()
            injected += 1
        else:
            if result.denial not in (None, "denied_by_binding"):
                logger.info(
                    "[SecretInject] API 工具 %s 的参数 %s 被拒绝: %s",
                    getattr(config, "name", "?"), name, result.denial,
                )
            _record(denials, f"{prefix}:{name}", result.denial)

    return resolved


def _set_case_insensitive(headers: dict[str, str], name: str, value: str) -> None:
    """按大小写不敏感写入：命中已有 key 就覆盖它，否则新增。

    槽位名来自绑定表、Header 名来自服务配置，两者大小写不保证一致
    （``header:x-tenant`` vs ``X-Tenant``）。不一致时若直接 ``headers[name] = value``，
    对端会收到两个名字只差大小写的 Header，值是哪个全看它怎么解析。
    """
    for existing in list(headers):
        if existing.casefold() == name.casefold():
            headers[existing] = value
            return
    headers[name] = value


async def apply_mcp_bindings(
    server: Any,
    headers: dict[str, str],
    *,
    user_info: Mapping[str, Any] | None = None,
    denials: dict[str, str] | None = None,
    used: dict[str, Any] | None = None,
) -> dict[str, str]:
    """把绑定的凭据填进 MCP 出站请求头。

    **绑定优先，且无条件覆盖。** MCP 的 ``fixed_token`` 和「其他 Header」的值都
    只是「没人绑时的默认值」：用户一旦显式绑定，就必须盖过它们，否则绑定永远
    不会生效。

    为什么不能沿用「只补空位」或「只认像凭据头的名字」的过滤：绑定是用户明确
    表达的意图，而配置里的旧值往往正是他要替换的东西。旧实现遇到「值非空 +
    名字不含 token/key/auth」的 Header 会直接跳过——连库都不查，使用记录里一条
    都没有，用户只看到「绑了但没生效」。更糟的是前端绑定后值输入框被隐藏，
    他没法自己把旧值清空，等于死锁。

    遍历的是**绑定槽位**而不是 headers：绑定表才是「谁要用凭据」的唯一事实
    来源，配置里有没有这个 Header 与此无关（没有就补上）。
    """
    if not headers:
        return headers

    user_id, dept_code = _current_user(user_info)
    if not user_id:
        return headers

    server_id = str(getattr(server, "id", "") or "")
    if not server_id:
        return headers

    slots = await list_bound_slots(
        CONSUMER_MCP, server_id, prefix="header:", user_id=user_id
    )
    if not slots:
        return headers

    # 回传给调用方：这个服务对这个用户**有**绑定，出站连接就不能跨用户复用。
    # SSE 的 Header 在建连时冻结，共用会话等于把先建连那个人的凭据发给所有人。
    if used is not None:
        used["user_id"] = user_id
        used["slots"] = list(slots)

    target = host_of(getattr(server, "sse_url", None))
    trace_id = _current_trace_id()
    resolved = dict(headers)
    limit = await _max_inject_per_call()
    injected = 0

    for slot in slots:
        if injected >= limit:
            logger.warning(
                "[SecretInject] MCP 服务 %s 命中注入上限 %s 条，其余槽位不再注入",
                getattr(server, "server_name", None) or server_id,
                limit,
            )
            break
        name = slot.partition(":")[2]
        if not name:
            continue
        try:
            result = await secret_resolver.resolve(
                user_id=user_id,
                scene=SCENE_MCP,
                consumer_type=CONSUMER_MCP,
                consumer_id=server_id,
                slot=slot,
                target_host=target,
                trace_id=trace_id,
                dept_code=dept_code,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SecretInject] MCP %s 的 %s 解析失败: %s", server_id, slot, exc)
            continue

        if result.ok and result.value is not None:
            _set_case_insensitive(resolved, name, result.value.reveal())
            injected += 1
        else:
            _record(denials, slot, result.denial)

    return resolved


def shell_quote(value: str) -> str:
    """把一个值渲染成 POSIX shell 单引号字面量。

    **这不是洁癖，是防注入**。脚本环境变量文件是用 ``. file`` 加载的，而 ``.`` 会
    执行命令替换——凭据值里只要出现 ``$(...)`` 或反引号，就等于让提交凭据的人
    在别人的脚本里执行任意命令。单引号包起来之后 ``$`` 和反引号都不展开，只有
    单引号本身需要特殊处理（``'\''``：结束、转义一个、重开）。
    """
    return "'" + str(value).replace("'", "'\\''") + "'"


def build_env_file_content(env: Mapping[str, str]) -> str:
    """把环境变量渲染成可被 ``. file`` 安全加载的内容。"""
    return "\n".join(f"{name}={shell_quote(value)}" for name, value in env.items()) + "\n"


#: 查绑定失败后的熔断截止时刻。
#:
#: 表不存在（迁移没执行）时每次调用都会抛一次异常。没有熔断的话，脚本工具每跑
#: 一次就刷一条 WARNING、多一次必然失败的查询——日志被淹没之后，真正的故障反而
#: 看不见了。
_bindings_unavailable_until = 0.0
_BINDINGS_COOLDOWN_SECONDS = 60.0


async def list_bound_slots(
    consumer_type: str,
    consumer_id: str,
    *,
    prefix: str,
    user_id: str,
) -> list[str]:
    """列出某个消费者上「属于我」的绑定槽位。

    ``sys_secret_bindings`` 里既有管理员做的全局绑定（``bound_by_user_id IS NULL``）,
    也有别人做的个人绑定。后者必须排除——A 把凭据绑到共享脚本上，B 不该跟着用。
    """
    from sqlalchemy import select

    from app.core.orm import AsyncSessionLocal
    from app.models.secret import SecretBinding

    global _bindings_unavailable_until

    if not consumer_type or not consumer_id:
        return []
    if time.monotonic() < _bindings_unavailable_until:
        return []
    try:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(SecretBinding).where(
                        SecretBinding.consumer_type == consumer_type,
                        SecretBinding.consumer_id == consumer_id,
                    )
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001 - 表不存在/库抖动一律按「没有绑定」
        _bindings_unavailable_until = time.monotonic() + _BINDINGS_COOLDOWN_SECONDS
        logger.warning(
            "[SecretInject] 读取绑定失败，%d 秒内不再重试（凭据注入暂停）: %s/%s",
            int(_BINDINGS_COOLDOWN_SECONDS),
            consumer_type,
            consumer_id,
            exc_info=True,
        )
        return []

    slots: list[str] = []
    for row in rows:
        if row.bound_by_user_id not in (None, user_id):
            continue
        if not str(row.slot or "").startswith(prefix):
            continue
        slots.append(row.slot)
    return sorted(set(slots))


async def apply_script_env_bindings(
    tool_config: Any,
    *,
    user_info: Mapping[str, Any] | None = None,
    denials: dict[str, str] | None = None,
) -> dict[str, str]:
    """解析脚本工具要注入的环境变量，返回 ``{变量名: 值}``。

    **默认拿不到任何东西**：``SCENE_SCRIPT_ENV`` 要求平台策略
    (``secret_policy_allow_script_env``) 与用户偏好 (``sys_secret_user_prefs``)
    同时为真，两层默认都是关。闸门在 ``resolver.evaluate_secret`` 里，不在这里——
    这样「开关在哪、谁拦的」只有一个答案。

    值会写进会话工作区里的一个 0600 文件再由 shell ``.`` 加载，**不进命令行**：
    ``ps aux`` 和容器进程列表都看得到命令行，看得见就等于泄露。
    """
    from app.services.secrets.policy import CONSUMER_SCRIPT, SCENE_SCRIPT_ENV

    user_id, dept_code = _current_user(user_info)
    if not user_id:
        return {}

    tool_id = str(getattr(tool_config, "id", "") or "")
    if not tool_id:
        return {}

    slots = await list_bound_slots(
        CONSUMER_SCRIPT, tool_id, prefix="env:", user_id=user_id
    )
    if not slots:
        return {}

    trace_id = _current_trace_id()
    resolved: dict[str, str] = {}
    limit = await _max_inject_per_call()
    if len(slots) > limit:
        logger.warning(
            "[SecretInject] 脚本 %s 绑了 %s 个环境变量，超过单次注入上限 %s，只注入前 %s 个",
            tool_id,
            len(slots),
            limit,
            limit,
        )
        slots = slots[:limit]

    for slot in slots:
        name = slot.partition(":")[2]
        if not name:
            continue
        try:
            result = await secret_resolver.resolve(
                user_id=user_id,
                scene=SCENE_SCRIPT_ENV,
                consumer_type=CONSUMER_SCRIPT,
                consumer_id=tool_id,
                slot=slot,
                trace_id=trace_id,
                dept_code=dept_code,
            )
        except Exception:  # noqa: BLE001
            logger.warning("[SecretInject] 脚本 %s 的 %s 解析失败", tool_id, slot, exc_info=True)
            continue
        if result.ok and result.value is not None:
            resolved[name] = result.value.reveal()
        else:
            _record(denials, slot, result.denial)
    return resolved
